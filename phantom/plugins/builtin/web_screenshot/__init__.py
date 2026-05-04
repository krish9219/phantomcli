"""web-screenshot — headless-browser PNG capture.

Renders the URL in Playwright Chromium and returns base64 PNG bytes
plus viewport + load metadata. Useful for "show me what
https://example.com looks like right now" without leaving the agent.

Payload schema::

    {"url": "https://example.com",
     "width": 1280,           # optional, default 1280
     "height": 800,           # optional, default 800
     "full_page": false,      # optional, default false
     "wait_until": "networkidle",  # one of: load, domcontentloaded, networkidle
     "timeout_ms": 15000}     # optional, default 15000

Returns::

    {"ok": true,
     "image_b64": "<base64 png>",
     "width": 1280, "height": 800,
     "url": "https://example.com",
     "title": "Example Domain",
     "load_ms": 412}

If Playwright isn't installed, falls back to a no-op error response so
the agent can degrade gracefully (e.g. by calling the web_fetch tool).
"""

from __future__ import annotations

import base64
import importlib
import time
from typing import Any

from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["WebScreenshotPlugin"]


class WebScreenshotPlugin(Plugin):
    """Headless screenshot via Playwright."""

    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "missing or invalid 'url' (must be http(s)://...)"}

        try:
            sync_api = importlib.import_module("playwright.sync_api")
        except ImportError:
            return {
                "ok": False,
                "error": "playwright not installed (run: pip install playwright && playwright install chromium)",
            }

        width = int(payload.get("width", 1280))
        height = int(payload.get("height", 800))
        full_page = bool(payload.get("full_page", False))
        wait_until = str(payload.get("wait_until", "networkidle"))
        if wait_until not in ("load", "domcontentloaded", "networkidle"):
            wait_until = "networkidle"
        timeout_ms = int(payload.get("timeout_ms", 15000))

        t0 = time.perf_counter()
        try:
            with sync_api.sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                    title = page.title() or ""
                    png = page.screenshot(full_page=full_page)
                finally:
                    browser.close()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        load_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "image_b64": base64.b64encode(png).decode("ascii"),
            "width": width,
            "height": height,
            "url": url,
            "title": title,
            "load_ms": load_ms,
        }
