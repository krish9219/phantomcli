"""github-pr — GitHub pull-request inspector via the gh CLI.

Operations
----------

* ``list``      — list open PRs in a repo (or current cwd if it's a clone).
* ``view <n>``  — show one PR's metadata + body.
* ``files <n>`` — list files changed in a PR.
* ``review <n>`` — fetch the review comment thread.

We shell out to ``gh`` rather than re-implementing the GraphQL client.
The plugin asks for ``executor`` + ``network`` capabilities; the loader
materialises those into a SandboxPolicy that allows shelling out and
making the gh API call.

Payload schema::

    {"op": "list" | "view" | "files" | "review",
     "repo": "owner/name",        # optional for view/files/review
     "number": 123,                # required for view/files/review
     "limit": 30}                  # optional for list (default 30)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["GithubPRPlugin"]


class GithubPRPlugin(Plugin):
    """Read-only GitHub PR inspector."""

    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        op = str(payload.get("op", "")).lower()
        if not op:
            return {"ok": False, "error": "missing 'op' (list|view|files|review)"}

        if not shutil.which("gh"):
            return {"ok": False, "error": "gh CLI not on PATH (install: https://cli.github.com/)"}

        if op == "list":
            return self._list(payload)
        if op in ("view", "files", "review"):
            return self._view_like(op, payload)
        return {"ok": False, "error": f"unknown op: {op}"}

    @staticmethod
    def _list(payload: dict[str, Any]) -> dict[str, Any]:
        limit = int(payload.get("limit", 30))
        repo = payload.get("repo")
        argv = ["gh", "pr", "list", "--state", "open", "--limit", str(limit), "--json",
                "number,title,author,createdAt,url,labels"]
        if repo:
            argv += ["--repo", str(repo)]
        res = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return {"ok": False, "error": res.stderr.strip()}
        try:
            return {"ok": True, "prs": json.loads(res.stdout)}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"bad gh JSON: {e}"}

    @staticmethod
    def _view_like(op: str, payload: dict[str, Any]) -> dict[str, Any]:
        number = payload.get("number")
        if not isinstance(number, int):
            return {"ok": False, "error": "missing 'number' (int)"}
        repo = payload.get("repo")
        if op == "view":
            argv = ["gh", "pr", "view", str(number), "--json",
                    "number,title,body,state,url,author,createdAt,labels,reviewDecision"]
        elif op == "files":
            argv = ["gh", "pr", "diff", str(number), "--name-only"]
        else:  # review
            argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{number}/comments"]
            # gh fills the {{owner}}/{{repo}} placeholder via --repo OR cwd.
        if repo:
            argv += ["--repo", str(repo)]
        res = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return {"ok": False, "error": res.stderr.strip()}
        if op == "view":
            try:
                return {"ok": True, "pr": json.loads(res.stdout)}
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"bad gh JSON: {e}"}
        if op == "files":
            files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return {"ok": True, "files": files}
        # review
        try:
            return {"ok": True, "comments": json.loads(res.stdout)}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"bad gh JSON: {e}"}
