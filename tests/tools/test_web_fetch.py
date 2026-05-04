"""Tests for :mod:`phantom.tools.web_fetch`."""

from __future__ import annotations

import httpx
import pytest

from phantom.errors import PhantomError
from phantom.tools.web_fetch import WebFetchResult, is_private_host, web_fetch


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# ─── private-host detection (SSRF block) ────────────────────────────────────


class TestIsPrivateHost:
    @pytest.mark.parametrize("host", [
        "127.0.0.1", "192.168.1.1", "10.0.0.1", "172.16.0.1",
        "169.254.1.1",  # link-local
        "::1",  # IPv6 loopback
    ])
    def test_private_blocked(self, host):
        assert is_private_host(host) is True

    @pytest.mark.parametrize("host", [
        "1.1.1.1", "8.8.8.8", "93.184.216.34",
    ])
    def test_public_ip_allowed(self, host):
        assert is_private_host(host) is False

    def test_empty_blocked(self):
        assert is_private_host("") is True

    def test_unresolvable_blocked(self):
        # A bogus TLD that won't resolve. Failing closed.
        assert is_private_host("never-resolves.invalid") is True


# ─── happy-path fetch ───────────────────────────────────────────────────────


class TestWebFetchHappyPath:
    def test_text_round_trip(self):
        def handler(req):
            return httpx.Response(
                200, text="hello world",
                headers={"content-type": "text/plain"},
            )
        client = _client(handler)
        result = web_fetch(url="https://1.1.1.1/page", client=client)
        client.close()
        assert result.ok
        assert result.status == 200
        assert result.text == "hello world"
        assert result.content_type == "text/plain"
        assert not result.truncated

    def test_truncates_at_max_bytes(self):
        big = "x" * 10_000
        def handler(req):
            return httpx.Response(200, text=big)
        client = _client(handler)
        result = web_fetch(url="https://1.1.1.1/x", max_bytes=4096, client=client)
        client.close()
        assert result.truncated
        assert "[phantom: response truncated]" in result.text

    def test_4xx_returns_ok_false(self):
        def handler(req):
            return httpx.Response(404, text="missing")
        client = _client(handler)
        result = web_fetch(url="https://1.1.1.1/x", client=client)
        client.close()
        assert result.ok is False
        assert result.status == 404


# ─── validation errors ──────────────────────────────────────────────────────


class TestValidation:
    def test_bad_scheme_rejected(self):
        result = web_fetch(url="file:///etc/passwd")
        assert not result.ok
        assert "http" in result.error

    def test_no_host_rejected(self):
        result = web_fetch(url="https://")
        assert not result.ok

    def test_private_host_rejected(self):
        result = web_fetch(url="http://192.168.1.1/admin")
        assert not result.ok
        assert "private" in result.error

    def test_loopback_rejected(self):
        result = web_fetch(url="http://127.0.0.1/")
        assert not result.ok
        assert "private" in result.error

    def test_max_bytes_floor(self):
        with pytest.raises(PhantomError, match="max_bytes"):
            web_fetch(url="https://1.1.1.1", max_bytes=100)

    def test_timeout_must_be_positive(self):
        with pytest.raises(PhantomError, match="timeout_s"):
            web_fetch(url="https://1.1.1.1", timeout_s=0)


# ─── network failures wrapped (no exception leaks) ──────────────────────────


class TestNetworkFailures:
    def test_connection_error_returns_failure(self):
        def boom(req):
            raise httpx.ConnectError("dns")
        client = _client(boom)
        result = web_fetch(url="https://1.1.1.1/x", client=client)
        client.close()
        assert not result.ok
        assert "ConnectError" in result.error

    def test_timeout_wrapped(self):
        def slow(req):
            raise httpx.ReadTimeout("slow")
        client = _client(slow)
        result = web_fetch(url="https://1.1.1.1/x", client=client)
        client.close()
        assert not result.ok
        assert "Timeout" in result.error


# ─── result helpers ─────────────────────────────────────────────────────────


class TestResult:
    def test_to_dict_round_trip(self):
        r = WebFetchResult(ok=True, status=200, text="x", url="https://x")
        d = r.to_dict()
        assert d["ok"] is True and d["text"] == "x"
