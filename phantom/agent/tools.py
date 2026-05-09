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

    tools: list[ToolDefinition] = [
        ToolDefinition(
            name="run_bash",
            description=(
                "Execute a shell command in a sandbox. The command runs with "
                "no network by default; the working directory is writable but "
                "the host filesystem is read-only with secret paths blocked. "
                "Default timeout is 60 seconds (max 600). "
                "DO NOT run long-running servers in the foreground — they "
                "will block until the timeout fires. Background them: on "
                "Windows use `start /b python app.py`, on POSIX use "
                "`nohup python app.py >server.log 2>&1 &`. After starting "
                "a server, stop calling tools and tell the user the URL."
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
            handler=lambda args: _read_file(args, allowlist=fs_allowlist),
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
            handler=lambda args: _list_dir(args, allowlist=fs_allowlist),
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
