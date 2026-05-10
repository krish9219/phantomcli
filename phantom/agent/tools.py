"""Default tool set for the agent loop.

Each tool is a :class:`ToolDefinition`: a JSON schema (the model sees
this) plus a Python callable (the executor invokes this). Adding a
new tool is one new entry in :func:`default_tools`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from phantom.engine import ExecuteBashRequest, execute_bash
from phantom.errors import PhantomError
from phantom.memory import MemoryStore
from phantom.sandbox.policy import ResourceLimits
from phantom.tools.fs import edit_file, list_dir, read_file, write_file
from phantom.tools.web_fetch import web_fetch
from phantom.tools.web_search import web_search

__all__ = ["default_tools"]


# Patterns that look like long-running foreground servers. We don't refuse
# them — the user might genuinely want a one-shot test run that times out
# at the wall-clock cap — but we append a hint to the tool result so the
# model knows to background the next attempt.
_SERVER_START_PATTERNS = (
    re.compile(r"\bpython\s+(\S+\.py|-m\s+\S+)\b"),
    re.compile(r"\bflask\s+run\b"),
    re.compile(r"\buvicorn\s+\S+"),
    re.compile(r"\bgunicorn\s+\S+"),
    re.compile(r"\bnpm\s+(start|run|dev)\b"),
    re.compile(r"\bpnpm\s+(start|run|dev)\b"),
    re.compile(r"\byarn\s+(start|run|dev)\b"),
    re.compile(r"\bnode\s+\S+\.(js|mjs|ts)\b"),
    re.compile(r"\bnext\s+(dev|start)\b"),
    re.compile(r"\brails\s+server\b"),
)


def _looks_like_server_start(cmd: str) -> bool:
    return any(p.search(cmd) for p in _SERVER_START_PATTERNS)


def _start_server(args: dict[str, Any], *, workdir: str) -> str:
    """Launch a long-running server detached. Returns immediately with the
    PID, log path, and a probe of whether the port is listening yet.

    The server's stdout/stderr go to ``$workdir/.phantom_server.log`` so
    the user can tail it. The child is spawned with platform-appropriate
    detach flags so it survives Phantom's exit and isn't bound to the
    sandbox lifetime — this is intentional, since the v1 sandbox runs
    in passthrough mode on Windows and doesn't actually contain
    long-running processes.
    """
    import os as _os
    import platform as _platform
    import socket as _socket
    import subprocess as _subprocess  # noqa: S404 — start_server is the sandbox exception
    import time as _time

    cmd = args.get("command", "")
    if not isinstance(cmd, str) or not cmd.strip():
        return json.dumps({
            "error": "start_server: 'command' must be a non-empty string",
            "hint": (
                'Retry with the server command, e.g. '
                '{"command": "python app.py", "port": 5000}.'
            ),
        })
    requested_port = int(args.get("port", 0)) or _guess_port(cmd) or 5000
    auto_port = bool(args.get("auto_port", True))
    wait_s = float(args.get("wait_s", 3.0))
    wait_s = max(0.0, min(wait_s, 30.0))

    # Auto-port: atomic reservation. With three sequential start_server
    # calls in one turn, naive `_is_port_in_use` checks see "5000 free"
    # for all three because the first child hasn't bound yet. The
    # process-local reservation table closes that race.
    port = requested_port
    port_rewrite = None
    if auto_port:
        # First check whether the requested port is itself free AND
        # not already reserved for another concurrent spawn.
        with _RESERVE_LOCK:
            now = _port_time.monotonic()
            already_reserved = (
                requested_port in _RESERVED_PORTS
                and _RESERVED_PORTS[requested_port] >= now
            )
        if already_reserved or _is_port_in_use(requested_port):
            new_port = _reserve_free_port(requested_port + 1, requested_port + 20)
            if new_port is not None:
                port = new_port
                port_rewrite = (requested_port, new_port)
        else:
            # Reserve the requested port too so a sibling call doesn't
            # race onto it.
            with _RESERVE_LOCK:
                _RESERVED_PORTS[requested_port] = _port_time.monotonic() + 15.0
    if port_rewrite is not None:
        cmd = _rewrite_port(cmd, port_rewrite[0], port_rewrite[1], port_rewrite[1])

    _os.makedirs(workdir, exist_ok=True)
    log_path = _os.path.join(workdir, ".phantom_server.log")
    try:
        # buffering=0 → unbuffered. The child gets its own dup'd fd from
        # Popen, but disabling buffering also avoids "log appears empty
        # immediately after child crash" on Windows where the FILE_FLAG
        # caching can hold writes for up to several seconds even after
        # the writer has exited.
        log = open(log_path, "wb", buffering=0)
    except OSError as e:
        return json.dumps({"error": f"could not open log file: {e}"})

    is_windows = _platform.system() == "Windows"
    popen_kwargs: dict[str, Any] = {
        "cwd": workdir,
        "stdout": log,
        "stderr": _subprocess.STDOUT,
        "stdin": _subprocess.DEVNULL,
    }
    if is_windows:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        popen_kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        popen_kwargs["shell"] = True  # let cmd.exe parse the command
    else:
        popen_kwargs["start_new_session"] = True
        popen_kwargs["shell"] = True  # /bin/sh

    try:
        proc = _subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
    except OSError as e:
        log.close()
        return json.dumps({"error": f"start_server failed to launch: {e}"})

    # Once Popen has dup'd the fd to the child, we can close our copy in
    # the parent — the child writes via its own descriptor. Closing here
    # also forces any lingering parent-side buffer state to release on
    # Windows so a subsequent read of log_path sees the latest bytes.
    try:
        log.close()
    except OSError:
        pass

    # Brief poll for the port. We don't open the user's port; we just
    # connect-and-close to see if anything is listening.
    listening = False
    deadline = _time.time() + wait_s
    while _time.time() < deadline:
        if proc.poll() is not None:
            break  # process exited (probably crashed) — bail early
        try:
            with _socket.create_connection(("127.0.0.1", port), timeout=0.5):
                listening = True
                break
        except (OSError, _socket.timeout):
            _time.sleep(0.25)

    summary: dict[str, Any] = {
        "pid": proc.pid,
        "command": cmd,
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "log": log_path,
        "listening": listening,
        "alive": proc.poll() is None,
    }
    if port_rewrite is not None:
        summary["port_rewrite"] = {
            "requested": port_rewrite[0],
            "actual": port_rewrite[1],
            "reason": (
                f"port {port_rewrite[0]} was already in use; auto-bumped "
                f"to {port_rewrite[1]}. Tell the user the new URL."
            ),
        }
    if not summary["alive"]:
        summary["exit_code"] = proc.returncode
        summary["hint"] = (
            f"Server exited immediately. Check the log: read_file path="
            f"{log_path!r} to see the error. Common causes: missing "
            f"dependencies (run pip install first), port {port} already "
            f"in use, syntax error in app code."
        )
    elif not listening:
        summary["hint"] = (
            f"Process is running (pid {proc.pid}) but nothing is "
            f"listening on port {port} yet. The server may still be "
            f"starting up — try opening {summary['url']} in a moment, "
            f"or read_file {log_path!r} for status."
        )
    else:
        summary["hint"] = (
            f"Server is up. Tell the user to open {summary['url']}. "
            f"The process keeps running after this turn ends; to stop "
            f"it call run_bash with `taskkill /PID {proc.pid} /F` "
            f"(Windows) or `kill {proc.pid}` (POSIX)."
        )
    return json.dumps(summary)


def _is_port_in_use(port: int) -> bool:
    """True if something is listening on 127.0.0.1:port."""
    import socket as _socket
    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except (OSError, _socket.timeout):
        return False


# Process-local port reservation. When the agent emits multiple
# start_server tool calls in one turn, the first child hasn't bound
# its port by the time the second call's port-probe runs — without
# this lock, all calls see the same "free" port and three children
# fight for the same socket. The reservation is held for 15s after
# the spawn, by which time the child should have bound (or crashed,
# in which case the port becomes available again on the next probe).
import threading as _threading
import time as _port_time
_RESERVED_PORTS: dict[int, float] = {}  # port -> expiry epoch
_RESERVE_LOCK = _threading.Lock()


def _reserve_free_port(start: int, end: int) -> int | None:
    """Atomically reserve the first free port in [start, end].

    Excludes ports listed by the OS as in-use AND ports we just
    handed out to a previous start_server call this turn (whose child
    may not have bound yet).
    """
    now = _port_time.monotonic()
    with _RESERVE_LOCK:
        # Drop expired reservations.
        for p in list(_RESERVED_PORTS.keys()):
            if _RESERVED_PORTS[p] < now:
                del _RESERVED_PORTS[p]
        for candidate in range(start, end + 1):
            if candidate in _RESERVED_PORTS:
                continue
            if _is_port_in_use(candidate):
                continue
            _RESERVED_PORTS[candidate] = now + 15.0
            return candidate
    return None


def _rewrite_port(cmd: str, old: int, new: int, env_port: int) -> str:
    """Replace --port=N / -p N / :N occurrences of *old* with *new*. If
    no port flag is found, prepend FLASK_RUN_PORT / PORT env var assignment
    (cmd.exe and /bin/sh both honour `set NAME=VAL && CMD` and `NAME=VAL CMD`
    respectively, so we use a portable wrapper)."""
    import re as _re
    rewrote = False
    for pattern, replace in [
        (rf"--port[= ]+{old}\b", f"--port={new}"),
        (rf"\s-p[= ]+{old}\b", f" -p {new}"),
        (rf":{old}\b", f":{new}"),
    ]:
        new_cmd, n = _re.subn(pattern, replace, cmd)
        if n:
            cmd = new_cmd
            rewrote = True
    if rewrote:
        return cmd
    # Fall through: prepend an env-var hint that Flask + Django + most
    # frameworks honour. Works on cmd.exe and POSIX sh alike.
    import platform as _platform
    if _platform.system() == "Windows":
        return f"set FLASK_RUN_PORT={env_port}&& set PORT={env_port}&& {cmd}"
    return f"FLASK_RUN_PORT={env_port} PORT={env_port} {cmd}"


def _guess_port(cmd: str) -> int:
    """Sniff a likely listening port out of a server-start command.

    Looks for `--port N`, `-p N`, `:N` (uvicorn-style host:port), and
    common defaults baked into known frameworks. Returns 0 when nothing
    matches; the caller falls back to 5000 (Flask's default).
    """
    m = re.search(r"--port[= ]+(\d{2,5})", cmd)
    if m:
        return int(m.group(1))
    m = re.search(r"\s-p[= ]+(\d{2,5})", cmd)
    if m:
        return int(m.group(1))
    m = re.search(r":(\d{4,5})\b", cmd)
    if m:
        return int(m.group(1))
    if "uvicorn" in cmd:
        return 8000
    if "next" in cmd:
        return 3000
    if "rails" in cmd:
        return 3000
    return 0


def _run_bash(args: dict[str, Any], *, workdir: str) -> str:
    cmd = args.get("command", "")
    if not isinstance(cmd, str) or not cmd.strip():
        return json.dumps({
            "error": "run_bash: 'command' must be a non-empty string",
            "hint": (
                "Retry with non-empty 'command'. Example: "
                '{"command": "python --version"} or '
                '{"command": "mkdir -p ./out"}.'
            ),
        })
    timeout_s = float(args.get("timeout", 60.0))
    timeout_s = max(1.0, min(timeout_s, 600.0))  # clamp 1s..10min

    req = ExecuteBashRequest(
        command=cmd,
        workdir=workdir,
        writable_paths=(workdir,),
        network=bool(args.get("network", False)),
        limits=ResourceLimits(
            wall_s=timeout_s,
            cpu_s=min(timeout_s, 60.0),
            rss_mib=512,
        ),
    )
    from phantom.errors import SandboxTimeoutError
    try:
        result = execute_bash(req)
    except SandboxTimeoutError:
        # Sandbox killed the command at the wall-clock cap. Fabricate a
        # result so the model gets actionable feedback (especially with
        # the server-start hint) instead of a turn-killing exception.
        summary: dict[str, Any] = {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"command exceeded {timeout_s:.0f}s wall-clock timeout",
            "tier": 0,
            "wall_s": timeout_s,
            "truncated": True,
        }
        if _looks_like_server_start(cmd):
            summary["hint"] = (
                "This command looks like a long-running server (Flask, "
                "uvicorn, npm start, …). It blocked until the timeout "
                f"({timeout_s:.0f}s) fired. To start a server without "
                "blocking, use background syntax: on Windows "
                "`start /b python app.py`, on POSIX "
                "`nohup python app.py >server.log 2>&1 &`. Then tell "
                "the user the URL and stop calling tools."
            )
        return json.dumps(summary)
    summary = {
        "exit_code": result.exit_code,
        "stdout": result.stdout[-4096:],
        "stderr": result.stderr[-4096:],
        "tier": result.tier,
        "wall_s": round(result.wall_s, 4),
        "truncated": result.truncated,
    }
    if _looks_like_server_start(cmd) and result.wall_s >= timeout_s - 1:
        # Server kept running until the timeout fired — model probably
        # tried to run a long-lived process in the foreground.
        summary["hint"] = (
            "This command looks like a long-running server (Flask, "
            "uvicorn, npm start, …). It hit the wall-clock timeout "
            f"({timeout_s:.0f}s). To start a server without blocking, "
            "use background syntax: on Windows `start /b python app.py`, "
            "on POSIX `nohup python app.py >server.log 2>&1 &`. Then "
            "tell the user the URL and stop calling tools."
        )
    return json.dumps(summary)


def _bad_path_hint(tool: str, example_args: dict[str, Any]) -> str:
    """Return a JSON error blob the model can act on, not just a stack-style
    message that ends the turn. Tells the model what shape the args should
    take and gives a literal example to copy."""
    return json.dumps({
        "error": f"{tool}: 'path' is missing or empty",
        "hint": (
            f"Retry with a non-empty relative path. Example arguments: "
            f"{json.dumps(example_args)}"
        ),
    })


def _write_file(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        return _bad_path_hint("write_file", {"path": "app.py", "text": "print('hi')"})
    text = args.get("text") or args.get("content") or ""
    if not isinstance(text, str):
        return json.dumps({
            "error": "write_file: 'text' must be a string",
            "hint": f"You sent {type(text).__name__}. Pass the file contents as a JSON string under 'text'.",
        })
    result = write_file(path=path, text=text, allowlist=allowlist)
    return json.dumps(result)


def _read_file(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        return _bad_path_hint("read_file", {"path": "app.py"})
    max_bytes = int(args.get("max_bytes", 256 * 1024))
    if max_bytes < 1024:
        max_bytes = 1024
    result = read_file(path=path, allowlist=allowlist, max_bytes=max_bytes)
    return json.dumps(result)


def _list_dir(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        return _bad_path_hint("list_dir", {"path": "."})
    result = list_dir(path=path, allowlist=allowlist)
    return json.dumps(result)


def _edit_file(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        return _bad_path_hint(
            "edit_file",
            {"path": "app.py", "old_string": "Hello", "new_string": "Hello, World"},
        )
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    result = edit_file(
        path=path,
        old_string=old_string,
        new_string=new_string,
        allowlist=allowlist,
        replace_all=replace_all,
    )
    return json.dumps(result)


def _web_search(args: dict[str, Any]) -> str:
    query = args.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return json.dumps({
            "error": "web_search: 'query' must be a non-empty string",
            "hint": 'Retry with: {"query": "GT vs RR cricket score today"}.',
        })
    max_results = int(args.get("max_results", 6))
    try:
        hits = web_search(query=query, max_results=max_results)
    except PhantomError as e:
        return json.dumps({"error": str(e)})
    return json.dumps([
        {"title": h.title, "url": h.url, "snippet": h.snippet}
        for h in hits
    ])


def _web_fetch(args: dict[str, Any]) -> str:
    url = args.get("url", "")
    if not isinstance(url, str) or not url.strip():
        return json.dumps({
            "error": "web_fetch: 'url' must be a non-empty string",
            "hint": 'Retry with: {"url": "https://example.com"}.',
        })
    max_bytes = int(args.get("max_bytes", 256 * 1024))
    if max_bytes < 1024:
        max_bytes = 1024
    result = web_fetch(url=url, max_bytes=max_bytes)
    if not result.ok:
        return json.dumps({"error": result.error or "fetch failed", "url": url})
    body = result.text or ""
    summary = {
        "ok": True,
        "url": result.url,
        "status": result.status,
        "content_type": result.content_type,
        "text": body[:8192],
        "truncated": len(body) > 8192 or result.truncated,
    }
    return json.dumps(summary)


def _memory_search(
    args: dict[str, Any], *, store: MemoryStore, namespace: dict[str, str]
) -> str:
    query = args.get("query", "")
    if not isinstance(query, str) or not query.strip():
        raise PhantomError("memory_search: 'query' must be a non-empty string")
    top_k = int(args.get("top_k", 5))
    out = store.search(
        user=namespace["user"],
        project=namespace["project"],
        session=namespace.get("session") or None,
        query=query,
        top_k=top_k,
    )
    return json.dumps([
        {"id": r.id, "text": r.text, "score": round(r.score, 4)}
        for r in out
    ])


def _memory_add(
    args: dict[str, Any], *, store: MemoryStore, namespace: dict[str, str]
) -> str:
    text = args.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise PhantomError("memory_add: 'text' must be a non-empty string")
    rec = store.add(
        user=namespace["user"],
        project=namespace["project"],
        session=namespace["session"],
        kind=str(args.get("kind", "note")),
        text=text,
    )
    return json.dumps({"id": rec.id})


def default_tools(
    *,
    workdir: str,
    memory: MemoryStore | None = None,
    namespace: dict[str, str] | None = None,
    extra_writable_paths: tuple[str, ...] = (),
) -> list:
    """Return the v4 default tool set bound to a session's resources.

    The shapes returned are :class:`phantom.agent.session.ToolDefinition`
    instances; we import lazily here to avoid a circular import.

    File-tool allowlist defaults to ``(workdir, *extra_writable_paths)``.
    Pass extra paths only when the operator explicitly wants the agent
    to read/write outside the session workdir.
    """
    from phantom.agent.session import ToolDefinition  # local to avoid cycle

    fs_allowlist: tuple[str, ...] = (workdir, *extra_writable_paths)

    # Read-only allowlist — superset of fs_allowlist that adds
    # ~/.phantom/ so the agent can answer questions like "read my
    # profile.json" without being blocked by the workspace boundary.
    # Writes still use the strict fs_allowlist (workspace + extras).
    import os as _os
    _phantom_home = _os.environ.get("PHANTOM_HOME") or _os.path.expanduser("~/.phantom")
    read_allowlist: tuple[str, ...] = (*fs_allowlist, _phantom_home)

    tools: list[ToolDefinition] = [
        ToolDefinition(
            name="run_bash",
            description=(
                "Execute a shell command in a sandbox. The command runs with "
                "no network by default; the working directory is writable but "
                "the host filesystem is read-only with secret paths blocked. "
                "Default timeout is 60 seconds (max 600). "
                "**DO NOT use this for long-running servers** (`python app.py`, "
                "`flask run`, `uvicorn`, `npm start`, etc.) — they block until "
                "timeout fires. Use the **start_server** tool instead, which "
                "spawns the server detached and returns a URL immediately."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "network": {"type": "boolean",
                                "description": "Allow network egress.",
                                "default": False},
                    "timeout": {"type": "number",
                                "description": "Wall-clock seconds before kill (default 60, max 600).",
                                "default": 60},
                },
                "required": ["command"],
            },
            handler=lambda args: _run_bash(args, workdir=workdir),
        ),
        ToolDefinition(
            name="start_server",
            description=(
                "Launch a long-running server (Flask, Django, FastAPI, "
                "Express, Next.js, etc.) detached from the agent loop. "
                "Returns immediately with the PID, URL, log path, and a "
                "probe of whether the port is listening. Use this — "
                "NOT run_bash — for any command that runs a web server or "
                "other long-lived process. Examples of `command`: "
                "`python app.py`, `flask run`, `uvicorn main:app --port 8000`, "
                "`npm start`, `node server.js`. The server keeps running "
                "after this tool call returns; the user can open the URL."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Server-launch command (e.g. 'python app.py').",
                    },
                    "port": {
                        "type": "integer",
                        "description": (
                            "Port the server will bind. Used to probe and to "
                            "build the URL. If omitted, Phantom guesses from "
                            "the command (Flask=5000, Django/uvicorn=8000, "
                            "Next/rails=3000)."
                        ),
                    },
                    "wait_s": {
                        "type": "number",
                        "description": (
                            "Seconds to wait for the port to come up before "
                            "returning. Default 3, max 30."
                        ),
                        "default": 3,
                    },
                    "auto_port": {
                        "type": "boolean",
                        "description": (
                            "If the requested port is already in use, try "
                            "the next 20 ports (requested+1..+20) and start "
                            "there instead. Default true."
                        ),
                        "default": True,
                    },
                },
                "required": ["command"],
            },
            handler=lambda args: _start_server(args, workdir=workdir),
        ),
        ToolDefinition(
            name="write_file",
            description=(
                "Create or overwrite a UTF-8 text file at 'path' with 'text'. "
                "Parent directories are created as needed. Use this ONLY for "
                "(a) creating a new file that doesn't exist yet, or (b) "
                "rewriting more than ~80% of an existing one. For bug fixes "
                "and small modifications to existing files, use edit_file "
                "instead — it preserves untouched code and avoids the "
                "whole-file-rewrite hallucination class. Never use run_bash "
                "with heredocs / echo for file creation, as quoting errors "
                "corrupt the file. Path must be inside the session workdir."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Destination path (absolute or "
                                            "relative to workdir)."},
                    "text": {"type": "string",
                             "description": "Full file contents."},
                },
                "required": ["path", "text"],
            },
            handler=lambda args: _write_file(args, allowlist=fs_allowlist),
        ),
        ToolDefinition(
            name="read_file",
            description=(
                "Read a UTF-8 text file at 'path' and return its contents. "
                "Returns ok=False with an error message if the file is "
                "missing, oversize, or outside the workdir allowlist."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "File path to read."},
                    "max_bytes": {"type": "integer",
                                  "description": "Cap; default 262144.",
                                  "default": 262144},
                },
                "required": ["path"],
            },
            handler=lambda args: _read_file(args, allowlist=read_allowlist),
        ),
        ToolDefinition(
            name="edit_file",
            description=(
                "Surgical, exact-string edit on an existing file. Replaces "
                "one occurrence of 'old_string' with 'new_string' (or all "
                "occurrences with replace_all=true). Fails if old_string "
                "isn't present, or if it appears more than once and "
                "replace_all is false — in that case extend old_string with "
                "more surrounding context to make it unique.\n\n"
                "ALWAYS prefer this over write_file when modifying existing "
                "files. A one-line bug fix is one edit_file call — not a "
                "write_file rewrite of the whole module. Whole-file "
                "rewrites silently corrupt untouched code and waste tokens."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string",
                                   "description": "Exact text to find."},
                    "new_string": {"type": "string",
                                   "description": "Replacement text."},
                    "replace_all": {"type": "boolean",
                                    "description": "Replace every occurrence.",
                                    "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=lambda args: _edit_file(args, allowlist=fs_allowlist),
        ),
        ToolDefinition(
            name="list_dir",
            description=(
                "List entries directly under 'path'. Returns name, kind "
                "(file/dir/link/other), and size for each entry."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            handler=lambda args: _list_dir(args, allowlist=read_allowlist),
        ),
        ToolDefinition(
            name="web_search",
            description=(
                "Search the open web. Use when the user asks about "
                "current/live/recent information you wouldn't know from "
                "training data: sports scores, news, today's prices, "
                "current docs, recent GitHub activity. Returns a list "
                "of {title, url, snippet}. Typical follow-up: pick the "
                "most relevant URL and call web_fetch on it for the "
                "full content. Default 6 results."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Free-text search query."},
                    "max_results": {"type": "integer",
                                    "description": "1-20, default 6.",
                                    "default": 6},
                },
                "required": ["query"],
            },
            handler=_web_search,
        ),
        ToolDefinition(
            name="web_fetch",
            description=(
                "Fetch a URL over HTTPS and return its text body. Use "
                "after web_search to read the actual page, or directly "
                "when you have a known URL. Refuses private/internal "
                "hosts (SSRF block). Body is truncated at ~8 KB; ask "
                "for max_bytes higher if you need more."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "Absolute http(s) URL."},
                    "max_bytes": {"type": "integer",
                                  "description": "Max bytes to read (>=1024).",
                                  "default": 262144},
                },
                "required": ["url"],
            },
            handler=_web_fetch,
        ),
    ]

    if memory is not None and namespace is not None:
        tools.append(ToolDefinition(
            name="memory_search",
            description=(
                "Search the session's memory for relevant prior notes. "
                "Returns up to top_k matches with relevance scores."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            handler=lambda args: _memory_search(
                args, store=memory, namespace=namespace,
            ),
        ))
        tools.append(ToolDefinition(
            name="memory_add",
            description="Save a note to the session's memory for later recall.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "default": "note"},
                },
                "required": ["text"],
            },
            handler=lambda args: _memory_add(
                args, store=memory, namespace=namespace,
            ),
        ))

    return tools
