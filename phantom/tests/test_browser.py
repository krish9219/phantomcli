"""Tests for the browser tool — exercised against StubBackend."""

from __future__ import annotations

import base64

import pytest

from phantom.browser import (
    Browser,
    BrowserError,
    BrowserResult,
    PlaywrightBackend,
    StubBackend,
)


# ─── BrowserResult shape ────────────────────────────────────────────────────


def test_browser_result_dataclass_immutable():
    r = BrowserResult(op="navigate", ok=True, data={"url": "https://x"})
    with pytest.raises(Exception):
        r.ok = False  # frozen


# ─── Browser orchestration via stub ─────────────────────────────────────────


def _new_browser():
    return Browser(backend=StubBackend())


def test_navigate_records_call_and_returns_ok():
    b = _new_browser()
    r = b.navigate("https://example.com")
    assert r.ok and r.op == "navigate"
    assert r.data["url"] == "https://example.com"
    assert r.data["status"] == 200
    assert r.duration_ms >= 0


def test_snapshot_returns_dom():
    b = _new_browser()
    b.navigate("https://example.com")
    r = b.snapshot()
    assert r.ok
    assert "html" in r.data
    assert "truncated" in r.data


def test_snapshot_respects_max_chars():
    b = Browser(backend=StubBackend())
    r = b.snapshot(max_chars=5)
    assert r.ok
    assert len(r.data["html"]) <= 5


def test_click_records_selector():
    b = _new_browser()
    r = b.click("button.submit")
    assert r.ok
    assert r.data["selector"] == "button.submit"


def test_type_text_records_chars_and_submit():
    b = _new_browser()
    r = b.type_text("input[name=email]", "user@example.com", submit=True)
    assert r.ok
    assert r.data["chars"] == len("user@example.com")
    assert r.data["submit"] is True


def test_wait_for_records_selector():
    b = _new_browser()
    r = b.wait_for(".result")
    assert r.ok
    assert r.data["appeared"] is True


def test_screenshot_returns_b64_png():
    b = _new_browser()
    r = b.screenshot()
    assert r.ok
    img = r.data["image_b64"]
    assert isinstance(img, str)
    decoded = base64.b64decode(img)
    assert decoded.startswith(b"\x89PNG")


def test_screenshot_full_page_flag_passed():
    b = _new_browser()
    r = b.screenshot(full_page=True)
    assert r.data["full_page"] is True


def test_eval_js_returns_typed_result():
    b = _new_browser()
    r = b.eval_js("return 42")
    assert r.ok
    assert "result" in r.data
    assert "type" in r.data


def test_scroll_returns_delta():
    b = _new_browser()
    r = b.scroll(500)
    assert r.ok
    assert r.data["delta_y"] == 500


def test_close_is_idempotent():
    b = _new_browser()
    b.close()
    b.close()  # must not raise


def test_call_after_close_returns_error_result():
    b = _new_browser()
    b.close()
    r = b.navigate("https://x")
    assert not r.ok
    assert "closed" in r.error.lower()


def test_context_manager_closes_backend():
    backend = StubBackend()
    with Browser(backend=backend):
        pass
    assert backend.is_closed


def test_call_sequence_recorded_on_backend():
    backend = StubBackend()
    b = Browser(backend=backend)
    b.navigate("https://x")
    b.click(".btn")
    b.type_text("#name", "phantom", submit=False)
    b.close()
    ops = [c[0] for c in backend.calls]
    assert ops == ["navigate", "click", "type_text", "close"]


def test_failure_returns_result_not_raise():
    """A backend that raises must produce ok=False, never propagate."""

    class BadBackend(StubBackend):
        def navigate(self, *a, **kw):
            raise BrowserError("nope")

    b = Browser(backend=BadBackend())
    r = b.navigate("https://x")
    assert not r.ok
    assert r.error == "nope"
    assert r.duration_ms >= 0


# ─── PlaywrightBackend (without playwright installed) ───────────────────────


def test_playwright_backend_raises_clean_error_when_missing(monkeypatch: pytest.MonkeyPatch):
    """If playwright isn't importable, the error message guides install."""
    import builtins
    real_import = builtins.__import__

    def block_pw(name, *a, **kw):
        if name == "playwright.sync_api" or name.startswith("playwright."):
            raise ImportError("no playwright in this env")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", block_pw)
    pw = PlaywrightBackend()
    with pytest.raises(BrowserError, match="playwright not installed"):
        pw.navigate("https://x", wait_until="load", timeout_ms=15000)


def test_playwright_backend_close_is_safe_when_never_initialized():
    pw = PlaywrightBackend()
    pw.close()  # no-op, no exception
