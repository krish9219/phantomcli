"""GitHub search plugin — wraps the ``gh`` CLI inside the sandbox.

Capabilities: ``network`` (gh talks to api.github.com), ``executor``
(we shell out to ``gh search``). The plugin runs inside the Stage-1
sandbox; gh's auth token comes from the host's ``GH_TOKEN`` env var,
which the loader passes through if the operator opts in.

Payload schema::

    {"query": "<search>", "type": "repos" | "issues" | "code", "limit": 10}

Result schema::

    {"items": [<gh json output, parsed>], "raw_command": "<gh ... --json ...>"}
"""

from __future__ import annotations

import json
from typing import Any

from phantom.engine import ExecuteBashRequest, execute_bash
from phantom.errors import PluginError
from phantom.plugins.capability import Capability
from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["GhSearchPlugin"]

_VALID_TYPES = {"repos", "issues", "code", "prs"}


class GhSearchPlugin(Plugin):
    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        for needed in (Capability.NETWORK, Capability.EXECUTOR):
            if needed not in ctx.capabilities:
                raise PluginError(
                    f"gh-search plugin requires the {needed.value!r} capability"
                )

        query = payload.get("query", "")
        if not isinstance(query, str) or not query.strip():
            raise PluginError("gh-search payload requires a non-empty 'query'")

        kind = payload.get("type", "repos")
        if kind not in _VALID_TYPES:
            raise PluginError(
                f"gh-search 'type' must be one of {sorted(_VALID_TYPES)}, got {kind!r}"
            )

        limit = int(payload.get("limit", 10))
        if not 1 <= limit <= 100:
            raise PluginError("gh-search 'limit' must be 1..100")

        # Build a defence-in-depth shell command. The query is shell-quoted
        # via the ExecuteBashRequest's permanent blocklist, but we add an
        # extra quoting layer here against query injection.
        safe_query = query.replace("'", "'\\''")
        cmd = f"gh search {kind} --json fullName,description,url --limit {limit} '{safe_query}'"

        req = ExecuteBashRequest(
            command=cmd,
            workdir=str(ctx.workdir),
            writable_paths=(str(ctx.workdir),),
            network=True,
            original_argv=("gh", "search", kind, "--limit", str(limit), query),
        )
        result = execute_bash(req)
        if result.exit_code != 0:
            raise PluginError(
                f"gh-search exited {result.exit_code}: {result.stderr.strip() or result.stdout.strip()}"
            )

        try:
            items = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as exc:
            raise PluginError(f"gh-search returned non-JSON output: {exc}") from exc

        return {"items": items, "raw_command": cmd}
