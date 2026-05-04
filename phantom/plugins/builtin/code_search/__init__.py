"""Code search plugin — wraps ripgrep inside the sandbox.

Capabilities: ``executor`` (shell out to ``rg``), ``filesystem``
(operator-listed search roots are bind-mounted read-only). No network.

Payload schema::

    {"pattern": "<regex>", "path": "/abs/path", "max_results": 100}

Result schema::

    {"matches": [{"file": str, "line": int, "text": str}, ...]}
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from phantom.engine import ExecuteBashRequest, execute_bash
from phantom.errors import PluginError
from phantom.plugins.capability import Capability
from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["CodeSearchPlugin"]


class CodeSearchPlugin(Plugin):
    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        if Capability.EXECUTOR not in ctx.capabilities:
            raise PluginError("code-search plugin requires the 'executor' capability")

        pattern = payload.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            raise PluginError("code-search payload requires a non-empty 'pattern'")
        path = payload.get("path", "")
        if not isinstance(path, str) or not os.path.isabs(path):
            raise PluginError("code-search 'path' must be an absolute path")
        if not os.path.exists(path):
            raise PluginError(f"code-search path does not exist: {path}")
        max_results = int(payload.get("max_results", 100))
        if not 1 <= max_results <= 1000:
            raise PluginError("code-search 'max_results' must be 1..1000")

        rg = shutil.which("rg")
        if rg is None:
            raise PluginError("ripgrep (rg) is not installed on the host")

        # rg --json emits one event per line; we parse and filter to matches only.
        safe = pattern.replace("'", "'\\''")
        cmd = f"rg --json --max-count {max_results} '{safe}' {path}"

        req = ExecuteBashRequest(
            command=cmd,
            workdir=str(ctx.workdir),
            writable_paths=(str(ctx.workdir),),
            network=False,
            original_argv=("rg", "--json", "--max-count", str(max_results), pattern, path),
        )
        result = execute_bash(req)
        # rg exits 1 when no matches were found — that's fine.
        if result.exit_code not in (0, 1):
            raise PluginError(
                f"rg exited {result.exit_code}: {result.stderr.strip()}"
            )

        matches: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") != "match":
                continue
            data = evt.get("data") or {}
            text = (data.get("lines") or {}).get("text", "")
            file = (data.get("path") or {}).get("text", "")
            line_no = data.get("line_number")
            matches.append({"file": file, "line": line_no, "text": text.rstrip("\n")})
            if len(matches) >= max_results:
                break

        return {"matches": matches}
