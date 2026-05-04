"""Browser tool implementation.

The user-facing :class:`Browser` is a thin orchestrator. Real work
lives in a :class:`BrowserBackend` — the production backend wraps
Playwright; the test backend is a deterministic stub. Swapping
backends keeps the agent's tool API stable.
"""

from __future__ import annotations

import base64
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "Browser",
    "BrowserBackend",
    "BrowserError",
    "BrowserResult",
    "PlaywrightBackend",
    "StubBackend",
]

log = logging.getLogger("phantom.browser")


class BrowserError(RuntimeError):
    """Any backend-level failure (selector miss, navigation fail, JS error)."""


@dataclass(frozen=True, slots=True)
class BrowserResult:
    op: str                       # "navigate" | "click" | …
    ok: bool
    # Op-specific payload. Documented per-method in :class:`Browser`.
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str = ""


# ─── backend ABC ─────────────────────────────────────────────────────────────


class BrowserBackend(ABC):
    """Backend interface. Subclasses must implement every method."""

    @abstractmethod
    def navigate(self, url: str, *, wait_until: str, timeout_ms: int) -> dict[str, Any]: ...

    @abstractmethod
    def snapshot(self, *, max_chars: int) -> dict[str, Any]: ...

    @abstractmethod
    def click(self, selector: str, *, timeout_ms: int) -> dict[str, Any]: ...

    @abstractmethod
    def type_text(self, selector: str, text: str, *, timeout_ms: int, submit: bool) -> dict[str, Any]: ...

    @abstractmethod
    def wait_for(self, selector: str, *, timeout_ms: int) -> dict[str, Any]: ...

    @abstractmethod
    def screenshot(self, *, full_page: bool) -> dict[str, Any]: ...

    @abstractmethod
    def eval_js(self, script: str) -> dict[str, Any]: ...

    @abstractmethod
    def scroll(self, delta_y: int) -> dict[str, Any]: ...

    @abstractmethod
    def close(self) -> None: ...


# ─── stub backend (deterministic, no network) ────────────────────────────────


class StubBackend(BrowserBackend):
    """In-memory backend for tests and CI.

    Records every call in ``self.calls`` so tests can assert call
    sequences without spinning a real browser.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._url: str = ""
        self._title: str = ""
        self._dom: str = "<html><body></body></html>"
        self._closed = False

    def _record(self, op: str, *args, **kw) -> None:
        if self._closed:
            raise BrowserError("backend already closed")
        self.calls.append((op, args, kw))

    def navigate(self, url, *, wait_until, timeout_ms):
        self._record("navigate", url, wait_until=wait_until, timeout_ms=timeout_ms)
        self._url = url
        # Auto-derive a deterministic title for tests.
        self._title = url.rsplit("/", 1)[-1] or url
        return {"url": url, "title": self._title, "status": 200}

    def snapshot(self, *, max_chars):
        self._record("snapshot", max_chars=max_chars)
        return {"url": self._url, "title": self._title,
                "html": self._dom[:max_chars], "truncated": False}

    def click(self, selector, *, timeout_ms):
        self._record("click", selector, timeout_ms=timeout_ms)
        return {"selector": selector, "found": True}

    def type_text(self, selector, text, *, timeout_ms, submit):
        self._record("type_text", selector, text, timeout_ms=timeout_ms, submit=submit)
        return {"selector": selector, "chars": len(text), "submit": submit}

    def wait_for(self, selector, *, timeout_ms):
        self._record("wait_for", selector, timeout_ms=timeout_ms)
        return {"selector": selector, "appeared": True, "waited_ms": 0}

    def screenshot(self, *, full_page):
        self._record("screenshot", full_page=full_page)
        # 1×1 transparent PNG
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGAYZcL5wAAAABJRU5ErkJggg=="
        )
        return {"image_b64": base64.b64encode(png).decode("ascii"),
                "width": 1280, "height": 800, "full_page": full_page}

    def eval_js(self, script):
        self._record("eval_js", script)
        return {"result": None, "type": "null"}

    def scroll(self, delta_y):
        self._record("scroll", delta_y)
        return {"delta_y": delta_y}

    def close(self) -> None:
        self._record("close")
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed


# ─── playwright backend (production) ─────────────────────────────────────────


class PlaywrightBackend(BrowserBackend):
    """Real browser via playwright.sync_api.

    Lazy-initialises Chromium on first use so importing this module
    doesn't pay the Playwright startup cost. Each :class:`Browser`
    instance owns one browser context + one page.
    """

    def __init__(self, *, headless: bool = True, viewport_width: int = 1280,
                 viewport_height: int = 800) -> None:
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._closed = False

    def _ensure(self) -> None:
        if self._page is not None:
            return
        if self._closed:
            raise BrowserError("backend already closed")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserError(
                "playwright not installed (pip install playwright && playwright install chromium)"
            ) from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
        )
        self._page = self._context.new_page()

    def navigate(self, url, *, wait_until, timeout_ms):
        self._ensure()
        try:
            resp = self._page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return {
                "url": self._page.url,
                "title": self._page.title() or "",
                "status": resp.status if resp else 0,
            }
        except Exception as e:
            raise BrowserError(f"navigate failed: {e}") from e

    def snapshot(self, *, max_chars):
        self._ensure()
        html = self._page.content()
        truncated = len(html) > max_chars
        return {
            "url": self._page.url,
            "title": self._page.title() or "",
            "html": html[:max_chars],
            "truncated": truncated,
        }

    def click(self, selector, *, timeout_ms):
        self._ensure()
        try:
            self._page.click(selector, timeout=timeout_ms)
            return {"selector": selector, "found": True}
        except Exception as e:
            raise BrowserError(f"click failed: {e}") from e

    def type_text(self, selector, text, *, timeout_ms, submit):
        self._ensure()
        try:
            self._page.fill(selector, text, timeout=timeout_ms)
            if submit:
                self._page.press(selector, "Enter")
            return {"selector": selector, "chars": len(text), "submit": submit}
        except Exception as e:
            raise BrowserError(f"type_text failed: {e}") from e

    def wait_for(self, selector, *, timeout_ms):
        self._ensure()
        try:
            t0 = time.perf_counter()
            self._page.wait_for_selector(selector, timeout=timeout_ms)
            return {
                "selector": selector,
                "appeared": True,
                "waited_ms": int((time.perf_counter() - t0) * 1000),
            }
        except Exception as e:
            raise BrowserError(f"wait_for failed: {e}") from e

    def screenshot(self, *, full_page):
        self._ensure()
        try:
            png = self._page.screenshot(full_page=full_page)
            return {
                "image_b64": base64.b64encode(png).decode("ascii"),
                "width": self.viewport_width,
                "height": self.viewport_height,
                "full_page": full_page,
            }
        except Exception as e:
            raise BrowserError(f"screenshot failed: {e}") from e

    def eval_js(self, script):
        self._ensure()
        try:
            result = self._page.evaluate(script)
            return {"result": result, "type": type(result).__name__}
        except Exception as e:
            raise BrowserError(f"eval_js failed: {e}") from e

    def scroll(self, delta_y):
        self._ensure()
        try:
            self._page.evaluate(f"window.scrollBy(0, {int(delta_y)})")
            return {"delta_y": int(delta_y)}
        except Exception as e:
            raise BrowserError(f"scroll failed: {e}") from e

    def close(self) -> None:
        self._closed = True
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass


# ─── orchestrator ────────────────────────────────────────────────────────────


class Browser:
    """Agent-facing browser tool.

    Each method returns a :class:`BrowserResult` so the agent loop can
    branch on ``ok`` without try/except plumbing.
    """

    def __init__(self, backend: Optional[BrowserBackend] = None) -> None:
        self.backend = backend or PlaywrightBackend()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ── primitives ───────────────────────────────────────────────────

    def navigate(self, url: str, *, wait_until: str = "load", timeout_ms: int = 15000) -> BrowserResult:
        return self._call("navigate", lambda: self.backend.navigate(
            url, wait_until=wait_until, timeout_ms=timeout_ms,
        ))

    def snapshot(self, *, max_chars: int = 50_000) -> BrowserResult:
        return self._call("snapshot", lambda: self.backend.snapshot(max_chars=max_chars))

    def click(self, selector: str, *, timeout_ms: int = 5000) -> BrowserResult:
        return self._call("click", lambda: self.backend.click(selector, timeout_ms=timeout_ms))

    def type_text(self, selector: str, text: str, *, timeout_ms: int = 5000,
                  submit: bool = False) -> BrowserResult:
        return self._call("type_text", lambda: self.backend.type_text(
            selector, text, timeout_ms=timeout_ms, submit=submit,
        ))

    def wait_for(self, selector: str, *, timeout_ms: int = 15000) -> BrowserResult:
        return self._call("wait_for", lambda: self.backend.wait_for(selector, timeout_ms=timeout_ms))

    def screenshot(self, *, full_page: bool = False) -> BrowserResult:
        return self._call("screenshot", lambda: self.backend.screenshot(full_page=full_page))

    def eval_js(self, script: str) -> BrowserResult:
        return self._call("eval_js", lambda: self.backend.eval_js(script))

    def scroll(self, delta_y: int) -> BrowserResult:
        return self._call("scroll", lambda: self.backend.scroll(delta_y))

    def close(self) -> None:
        try:
            self.backend.close()
        except Exception:
            pass

    # ── private ──────────────────────────────────────────────────────

    @staticmethod
    def _call(op: str, fn) -> BrowserResult:
        t0 = time.perf_counter()
        try:
            data = fn()
            return BrowserResult(
                op=op, ok=True, data=data,
                duration_ms=round((time.perf_counter() - t0) * 1000.0, 3),
            )
        except BrowserError as e:
            return BrowserResult(
                op=op, ok=False, data={}, error=str(e),
                duration_ms=round((time.perf_counter() - t0) * 1000.0, 3),
            )
