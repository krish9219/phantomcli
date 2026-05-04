"""Default tool set for the agent loop.

Each tool is a :class:`ToolDefinition`: a JSON schema (the model sees
this) plus a Python callable (the executor invokes this). Adding a
new tool is one new entry in :func:`default_tools`.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from phantom.engine import ExecuteBashRequest, execute_bash
from phantom.errors import PhantomError
from phantom.memory import MemoryStore
from phantom.tools.fs import edit_file, list_dir, read_file, write_file

__all__ = ["default_tools"]


def _run_bash(args: dict[str, Any], *, workdir: str) -> str:
    cmd = args.get("command", "")
    if not isinstance(cmd, str) or not cmd.strip():
        raise PhantomError("run_bash: 'command' must be a non-empty string")
    req = ExecuteBashRequest(
        command=cmd,
        workdir=workdir,
        writable_paths=(workdir,),
        network=bool(args.get("network", False)),
    )
    result = execute_bash(req)
    summary = {
        "exit_code": result.exit_code,
        "stdout": result.stdout[-4096:],
        "stderr": result.stderr[-4096:],
        "tier": result.tier,
        "wall_s": round(result.wall_s, 4),
        "truncated": result.truncated,
    }
    return json.dumps(summary)


def _write_file(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        raise PhantomError("write_file: 'path' must be a non-empty string")
    text = args.get("text") or args.get("content") or ""
    if not isinstance(text, str):
        raise PhantomError("write_file: 'text' must be a string")
    result = write_file(path=path, text=text, allowlist=allowlist)
    return json.dumps(result)


def _read_file(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        raise PhantomError("read_file: 'path' must be a non-empty string")
    max_bytes = int(args.get("max_bytes", 256 * 1024))
    if max_bytes < 1024:
        max_bytes = 1024
    result = read_file(path=path, allowlist=allowlist, max_bytes=max_bytes)
    return json.dumps(result)


def _list_dir(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        raise PhantomError("list_dir: 'path' must be a non-empty string")
    result = list_dir(path=path, allowlist=allowlist)
    return json.dumps(result)


def _edit_file(args: dict[str, Any], *, allowlist: tuple[str, ...]) -> str:
    path = args.get("path", "")
    if not isinstance(path, str) or not path.strip():
        raise PhantomError("edit_file: 'path' must be a non-empty string")
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
                "the host filesystem is read-only with secret paths blocked."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "network": {"type": "boolean",
                                "description": "Allow network egress.",
                                "default": False},
                },
                "required": ["command"],
            },
            handler=lambda args: _run_bash(args, workdir=workdir),
        ),
        ToolDefinition(
            name="write_file",
            description=(
                "Create or overwrite a UTF-8 text file at 'path' with 'text'. "
                "Parent directories are created as needed. Use this for "
                "writing source code, configs, or data files — DO NOT use "
                "run_bash with heredocs / echo for file creation, as quoting "
                "errors corrupt the file. Path must be inside the session "
                "workdir."
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
                "Replace exactly one occurrence of 'old_string' with "
                "'new_string' in a file. Fails if old_string is missing or "
                "non-unique unless replace_all=true. Prefer this over "
                "write_file for small in-place changes — it sends only the "
                "diff so the model is less likely to hallucinate."
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
