"""Tests for :mod:`phantom.agent.oauth_provider`."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from phantom.agent.oauth_provider import (
    AnthropicOAuthFlow,
    GoogleOAuthFlow,
    OAuthSubscriptionProvider,
    OpenAIOAuthFlow,
    TokenSet,
    TokenStore,
    _DeviceCodeFlow,
)
from phantom.errors import LicenseError, PhantomError


# ─── TokenSet ────────────────────────────────────────────────────────────────


class TestTokenSet:
    def test_expired_when_in_the_past(self):
        t = TokenSet(access_token="x", expires_at=time.time() - 100)
        assert t.expired()

    def test_not_expired_in_the_future(self):
        t = TokenSet(access_token="x", expires_at=time.time() + 1000)
        assert not t.expired()

    def test_zero_expiry_treated_as_no_expiry(self):
        t = TokenSet(access_token="x", expires_at=0)
        assert not t.expired()

    def test_slack_window(self):
        t = TokenSet(access_token="x", expires_at=time.time() + 30)
        # Default slack is 60s; 30s in the future is "expired".
        assert t.expired()
        assert not t.expired(slack_s=10)

    def test_round_trip_dict(self):
        t = TokenSet(access_token="a", refresh_token="r", expires_at=99.5)
        assert TokenSet.from_dict(t.to_dict()) == t


# ─── TokenStore ──────────────────────────────────────────────────────────────


class TestTokenStore:
    def test_save_load_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        store = TokenStore.default()
        t = TokenSet(access_token="abc", refresh_token="def", expires_at=12345)
        store.save("anthropic", t)
        loaded = store.load("anthropic")
        assert loaded == t

    def test_load_missing_returns_none(self, tmp_path):
        store = TokenStore(base=tmp_path / "auth")
        assert store.load("anthropic") is None

    def test_file_mode_0600(self, tmp_path):
        import stat
        store = TokenStore(base=tmp_path / "auth")
        store.save("x", TokenSet(access_token="a"))
        path = (tmp_path / "auth" / "x.token")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_invalid_provider_name_rejected(self, tmp_path):
        store = TokenStore(base=tmp_path / "auth")
        for bad in ("../etc/passwd", "x/y", ""):
            with pytest.raises(PhantomError, match="invalid provider"):
                store.save(bad, TokenSet(access_token="x"))

    def test_corrupted_file_raises_clear_error(self, tmp_path):
        store = TokenStore(base=tmp_path / "auth")
        (tmp_path / "auth").mkdir()
        (tmp_path / "auth" / "x.token").write_bytes(b"not encrypted")
        with pytest.raises(LicenseError, match="decrypt"):
            store.load("x")

    def test_delete_idempotent(self, tmp_path):
        store = TokenStore(base=tmp_path / "auth")
        store.delete("never-saved")  # must not raise
        store.save("x", TokenSet(access_token="a"))
        store.delete("x")
        assert store.load("x") is None


# ─── _DeviceCodeFlow.begin ───────────────────────────────────────────────────


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestDeviceCodeBegin:
    def test_returns_state(self):
        client = _client(lambda r: httpx.Response(200, json={
            "device_code": "DC", "user_code": "USR-123",
            "verification_uri": "https://login.example.com/dev",
            "interval": 5, "expires_in": 600,
        }))
        flow = _DeviceCodeFlow(
            name="example", client_id="cid",
            device_endpoint="https://x/dev",
            token_endpoint="https://x/tok",
            scope="read", client=client,
        )
        state = flow.begin()
        assert state["device_code"] == "DC"
        assert state["user_code"] == "USR-123"

    def test_missing_required_field_raises(self):
        client = _client(lambda r: httpx.Response(200, json={
            "device_code": "DC", "user_code": "USR",
            # no verification_uri
            "interval": 5, "expires_in": 600,
        }))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        with pytest.raises(LicenseError, match="verification_uri"):
            flow.begin()

    def test_endpoint_5xx_wrapped(self):
        client = _client(lambda r: httpx.Response(500, text="internal"))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        with pytest.raises(LicenseError, match="500"):
            flow.begin()


# ─── _DeviceCodeFlow.poll ────────────────────────────────────────────────────


class TestDeviceCodePoll:
    def test_authorization_pending_returns_none(self):
        client = _client(lambda r: httpx.Response(
            400, json={"error": "authorization_pending"},
        ))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        assert flow.poll({"device_code": "DC"}) is None

    def test_slow_down_returns_none(self):
        client = _client(lambda r: httpx.Response(
            400, json={"error": "slow_down"},
        ))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        assert flow.poll({"device_code": "DC"}) is None

    def test_success_returns_token_set(self):
        client = _client(lambda r: httpx.Response(200, json={
            "access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
        }))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        ts = flow.poll({"device_code": "DC"})
        assert ts.access_token == "AT"
        assert ts.refresh_token == "RT"
        assert ts.expires_at > time.time()

    def test_user_denied_raises(self):
        client = _client(lambda r: httpx.Response(
            400, json={"error": "access_denied"},
        ))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        with pytest.raises(LicenseError, match="access_denied"):
            flow.poll({"device_code": "DC"})


# ─── _DeviceCodeFlow.refresh ─────────────────────────────────────────────────


class TestDeviceCodeRefresh:
    def test_round_trip(self):
        client = _client(lambda r: httpx.Response(200, json={
            "access_token": "NEW", "expires_in": 3600,
        }))
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=client)
        ts = flow.refresh("OLD_REFRESH")
        assert ts.access_token == "NEW"
        # Refresh token preserved when server doesn't return a new one.
        assert ts.refresh_token == "OLD_REFRESH"

    def test_empty_refresh_token_rejected(self):
        flow = _DeviceCodeFlow(name="x", client_id="c",
                                device_endpoint="https://x/dev",
                                token_endpoint="https://x/tok",
                                scope="r", client=httpx.Client())
        with pytest.raises(LicenseError, match="cannot refresh"):
            flow.refresh("")


# ─── Concrete provider flows expose the right defaults ──────────────────────


class TestConcreteFlows:
    def test_google_endpoints(self):
        f = GoogleOAuthFlow(client_id="x")
        assert "googleapis.com" in f.token_endpoint

    def test_openai_endpoints(self):
        f = OpenAIOAuthFlow(client_id="x")
        assert "openai.com" in f.token_endpoint
        assert "offline_access" in f.scope

    def test_anthropic_endpoints(self):
        f = AnthropicOAuthFlow(client_id="x")
        assert "anthropic.com" in f.token_endpoint


# ─── OAuthSubscriptionProvider end-to-end ───────────────────────────────────


class _FakeFlow:
    name = "fake"
    refreshed_with: list[str] = []

    def __init__(self):
        self.refreshed_with = []
        self.refresh_outputs = []

    def begin(self):
        return {"device_code": "DC", "user_code": "USR",
                "verification_uri": "https://example/dev",
                "interval": 1, "expires_in": 60}

    def poll(self, state):
        return TokenSet(access_token="AT", refresh_token="RT",
                        expires_at=time.time() + 3600)

    def refresh(self, refresh_token):
        self.refreshed_with.append(refresh_token)
        return self.refresh_outputs.pop(0)


class TestOAuthSubscriptionProvider:
    def test_complete_login_persists_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        # Fast poll: the fake flow returns tokens immediately, so even
        # with default polling we exit on first iteration.
        flow = _FakeFlow()
        # Make sleep instant so the test is fast.
        monkeypatch.setattr("time.sleep", lambda s: None)
        store = TokenStore.default()
        provider = OAuthSubscriptionProvider(
            flow=flow, store=store,
            base_url="https://api.example.com/v1", model="m",
        )
        state = provider.begin_login()
        assert state["user_code"] == "USR"
        provider.complete_login(state)
        loaded = store.load("fake")
        assert loaded.access_token == "AT"

    def test_complete_call_uses_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        store = TokenStore.default()
        store.save("fake", TokenSet(
            access_token="AT", refresh_token="RT",
            expires_at=time.time() + 3600,
        ))

        captured = []
        def handler(req):
            captured.append(req.headers.get("authorization"))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "hi"},
                             "finish_reason": "stop"}],
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        flow = _FakeFlow()
        provider = OAuthSubscriptionProvider(
            flow=flow, store=store,
            base_url="https://api.example.com/v1", model="m",
            client=client,
        )
        from phantom.agent.provider import ProviderMessage
        out = provider.complete(
            [ProviderMessage(role="user", content="hi")],
            tools=[],
        )
        assert out.text == "hi"
        assert captured[0] == "Bearer AT"

    def test_expired_token_triggers_refresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        store = TokenStore.default()
        store.save("fake", TokenSet(
            access_token="OLD", refresh_token="RT",
            expires_at=time.time() - 100,
        ))
        flow = _FakeFlow()
        flow.refresh_outputs = [
            TokenSet(access_token="NEW", refresh_token="RT",
                     expires_at=time.time() + 3600),
        ]
        captured = []
        def handler(req):
            captured.append(req.headers.get("authorization"))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}],
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = OAuthSubscriptionProvider(
            flow=flow, store=store,
            base_url="https://api.example.com/v1", model="m",
            client=client,
        )
        from phantom.agent.provider import ProviderMessage
        provider.complete(
            [ProviderMessage(role="user", content="hi")],
            tools=[],
        )
        # Refresh was invoked; the new bearer was used.
        assert flow.refreshed_with == ["RT"]
        assert captured[0] == "Bearer NEW"

    def test_no_token_raises_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        store = TokenStore.default()
        flow = _FakeFlow()
        provider = OAuthSubscriptionProvider(
            flow=flow, store=store,
            base_url="https://api.example.com/v1", model="m",
        )
        from phantom.agent.provider import ProviderMessage
        with pytest.raises(LicenseError, match="no token"):
            provider.complete(
                [ProviderMessage(role="user", content="hi")], tools=[],
            )

    def test_expired_no_refresh_token_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        store = TokenStore.default()
        store.save("fake", TokenSet(
            access_token="OLD", refresh_token="",
            expires_at=time.time() - 100,
        ))
        flow = _FakeFlow()
        provider = OAuthSubscriptionProvider(
            flow=flow, store=store,
            base_url="https://api.example.com/v1", model="m",
        )
        from phantom.agent.provider import ProviderMessage
        with pytest.raises(LicenseError, match="re-run"):
            provider.complete(
                [ProviderMessage(role="user", content="hi")], tools=[],
            )

    def test_logout_removes_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
        store = TokenStore.default()
        store.save("fake", TokenSet(access_token="AT"))
        flow = _FakeFlow()
        provider = OAuthSubscriptionProvider(
            flow=flow, store=store,
            base_url="https://api.example.com/v1", model="m",
        )
        provider.logout()
        assert store.load("fake") is None
