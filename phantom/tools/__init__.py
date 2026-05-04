"""Phantom built-in tools (v4.1).

Lightweight tools registered next to the agent loop for common tasks
that don't need a full plugin or a sandbox round-trip:

* :mod:`phantom.tools.web_fetch` — single-page HTTP GET with caps.
* :mod:`phantom.tools.fs`        — read/write/list with allowlist.
"""

from __future__ import annotations

from phantom.tools.fs import edit_file, list_dir, read_file, write_file
from phantom.tools.web_fetch import WebFetchResult, web_fetch

__all__ = [
    "WebFetchResult",
    "edit_file",
    "list_dir",
    "read_file",
    "web_fetch",
    "write_file",
]
