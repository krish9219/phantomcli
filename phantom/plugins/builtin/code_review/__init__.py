"""code-review — pure-Python static review of a unified diff.

Designed to be cheap, deterministic, and zero-dep. The agent feeds a
diff (e.g. from ``git diff HEAD`` or from the swarm runner) and
gets a list of findings categorised by severity. Useful as a first
pass before paying an LLM to do a deeper review.

Payload schema::

    {"diff": "<unified diff text>"}

Returns::

    {"ok": true,
     "findings": [
       {"severity": "high", "rule": "hardcoded_secret",
        "file": "src/x.py", "line": 42, "msg": "..."}
     ],
     "stats": {"files": 3, "added": 120, "removed": 45}}
"""

from __future__ import annotations

import re
from typing import Any

from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["CodeReviewPlugin", "review_diff"]


_SECRET_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|secret|token|password)\s*=\s*["\'][^"\']{12,}["\']'),
     "hardcoded_secret"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "aws_access_key"),
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), "openai_key_like"),
    (re.compile(r'ghp_[A-Za-z0-9]{20,}'), "github_token"),
    (re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}'), "slack_token"),
]

_DANGEROUS_CALLS = {
    "eval(":          ("high", "unsafe_eval", "eval() executes arbitrary code"),
    "exec(":          ("high", "unsafe_exec", "exec() executes arbitrary code"),
    "shell=True":     ("med",  "shell_true",  "subprocess shell=True is a shell-injection footgun"),
    "pickle.loads":   ("high", "unsafe_pickle", "pickle.loads on untrusted input is RCE"),
    "yaml.load(":     ("med",  "unsafe_yaml", "yaml.load without SafeLoader can execute arbitrary objects"),
    "verify=False":   ("med",  "tls_disabled", "TLS verification disabled"),
}

_RAW_SQL_RE = re.compile(r'(?i)\b(SELECT|INSERT|UPDATE|DELETE)\b.*[\'"%]')

_BLOB_THRESHOLD_LINES = 500


def _walk_added_lines(diff: str):
    current_file = ""
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            current_file = raw[4:].strip()
            if current_file.startswith("b/"):
                current_file = current_file[2:]
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line_no = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            line_no += 1
            yield current_file, line_no, raw[1:]
        elif raw.startswith("-"):
            continue
        else:
            line_no += 1


def review_diff(diff: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    files: set[str] = set()
    added = 0
    removed = 0
    for raw in diff.splitlines():
        if raw.startswith("+") and not raw.startswith("+++"):
            added += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            removed += 1

    file_added_counts: dict[str, int] = {}

    for path, line, text in _walk_added_lines(diff):
        if path:
            files.add(path)
            file_added_counts[path] = file_added_counts.get(path, 0) + 1
        for pattern, rule in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({
                    "severity": "high",
                    "rule": rule,
                    "file": path,
                    "line": line,
                    "msg": f"line matches {rule} pattern",
                })
                break
        for needle, (sev, rule, msg) in _DANGEROUS_CALLS.items():
            if needle in text:
                findings.append({
                    "severity": sev,
                    "rule": rule,
                    "file": path,
                    "line": line,
                    "msg": msg,
                })
        if _RAW_SQL_RE.search(text):
            findings.append({
                "severity": "med",
                "rule": "raw_sql",
                "file": path,
                "line": line,
                "msg": "raw SQL with string interpolation — verify parameterised query",
            })

    for path, count in file_added_counts.items():
        if count >= _BLOB_THRESHOLD_LINES:
            findings.append({
                "severity": "low",
                "rule": "large_blob",
                "file": path,
                "line": 0,
                "msg": f"{count} added lines — consider splitting the change",
            })

    has_test_file = any("test" in f.lower() for f in files)
    has_non_test_change = any("test" not in f.lower() for f in files)
    if has_non_test_change and not has_test_file and added > 30:
        findings.append({
            "severity": "low",
            "rule": "no_tests_added",
            "file": "",
            "line": 0,
            "msg": "non-trivial change with no test file modified",
        })

    return {
        "ok": True,
        "findings": findings,
        "stats": {
            "files": len(files),
            "added": added,
            "removed": removed,
        },
    }


class CodeReviewPlugin(Plugin):
    """Pure-Python static review of a unified diff."""

    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        diff = payload.get("diff")
        if not isinstance(diff, str):
            return {"ok": False, "error": "missing 'diff' (string)"}
        return review_diff(diff)
