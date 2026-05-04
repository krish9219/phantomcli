"""Tests for the PWA finalization: push, manifest, service worker, API."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest

from phantom.pwa.manifest import build_manifest, build_service_worker
from phantom.pwa.push import (
    PushSubscription,
    SubscriptionStore,
    default_subscription_path,
    generate_vapid_keys,
)


# ─── manifest ──────────────────────────────────────────────────────────────


def test_manifest_required_fields():
    m = build_manifest()
    for field in ("name", "short_name", "start_url", "scope", "display",
                  "icons", "theme_color", "background_color"):
        assert field in m


def test_manifest_icons_include_512():
    icons = build_manifest()["icons"]
    sizes = {i["sizes"] for i in icons}
    assert "192x192" in sizes
    assert "512x512" in sizes


def test_manifest_supports_maskable_icons():
    icons = build_manifest()["icons"]
    assert any("maskable" in i.get("purpose", "") for i in icons)


# ─── service worker ────────────────────────────────────────────────────────


def test_service_worker_includes_app_shell_precache():
    sw = build_service_worker()
    for asset in ("/app/", "/app/index.html", "/app/main.js", "/app/main.css"):
        assert asset in sw


def test_service_worker_handles_push_event():
    sw = build_service_worker()
    assert 'addEventListener("push"' in sw
    assert "showNotification" in sw


def test_service_worker_handles_notificationclick():
    sw = build_service_worker()
    assert 'addEventListener("notificationclick"' in sw
    assert "openWindow" in sw


def test_service_worker_implements_background_sync():
    sw = build_service_worker()
    assert 'addEventListener("sync"' in sw
    assert "phantom-flush-outbox" in sw
    assert "indexedDB" in sw


def test_service_worker_offline_queue_for_post():
    sw = build_service_worker()
    # POST goes through networkOrEnqueue
    assert "networkOrEnqueue" in sw
    assert 'request.method === "POST"' in sw


def test_service_worker_cache_versioning():
    sw_v1 = build_service_worker(cache_version="v1")
    sw_v2 = build_service_worker(cache_version="v2")
    assert "phantom-app-shell-v1" in sw_v1
    assert "phantom-app-shell-v2" in sw_v2


# ─── push subscription store ───────────────────────────────────────────────


@pytest.fixture
def store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path / "pwa" / "subscriptions.json"


def _sub(endpoint: str = "https://push.example/abc") -> PushSubscription:
    return PushSubscription(endpoint=endpoint, p256dh="p256dh-x", auth="auth-y",
                            user_agent="UA/1.0")


def test_subscription_id_is_endpoint():
    s = _sub("https://push.example/123")
    assert s.id == "https://push.example/123"


def test_subscription_to_pywebpush_shape():
    s = _sub()
    body = s.to_pywebpush()
    assert body["endpoint"] == s.endpoint
    assert body["keys"] == {"p256dh": s.p256dh, "auth": s.auth}


def test_store_add_and_count(store_path: Path):
    s = SubscriptionStore(path=store_path)
    assert len(s) == 0
    new = s.add(_sub("https://push/1"))
    assert new is True
    assert len(s) == 1
    new2 = s.add(_sub("https://push/1"))  # same endpoint
    assert new2 is False
    assert len(s) == 1


def test_store_remove(store_path: Path):
    s = SubscriptionStore(path=store_path)
    s.add(_sub("https://push/1"))
    assert s.remove("https://push/1") is True
    assert s.remove("https://push/1") is False
    assert len(s) == 0


def test_store_persists_across_instances(store_path: Path):
    s1 = SubscriptionStore(path=store_path)
    s1.add(_sub("https://push/persisted"))
    s2 = SubscriptionStore(path=store_path)
    assert len(s2) == 1
    assert s2.all()[0].endpoint == "https://push/persisted"


def test_store_file_perms_owner_only(store_path: Path):
    s = SubscriptionStore(path=store_path)
    s.add(_sub())
    mode = os.stat(store_path).st_mode & 0o777
    assert mode == 0o600


def test_store_handles_garbage_file(tmp_path: Path):
    p = tmp_path / "subs.json"
    p.write_text("not json")
    s = SubscriptionStore(path=p)
    assert len(s) == 0


def test_default_subscription_path_creates_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    p = default_subscription_path()
    assert p.parent.exists()
    assert p.name == "subscriptions.json"


# ─── VAPID key generation ─────────────────────────────────────────────────


def test_vapid_keys_have_correct_lengths():
    k = generate_vapid_keys()
    # Decode base64url
    pad = lambda s: s + "=" * (-len(s) % 4)
    priv = base64.urlsafe_b64decode(pad(k.private_key_b64url))
    pub = base64.urlsafe_b64decode(pad(k.public_key_b64url))
    assert len(priv) == 32
    assert len(pub) == 65
    assert pub[0:1] == b"\x04"  # uncompressed point marker


def test_vapid_keys_are_unique_per_call():
    a = generate_vapid_keys()
    b = generate_vapid_keys()
    assert a.private_key_b64url != b.private_key_b64url


def test_vapid_application_server_key_matches_public_key():
    k = generate_vapid_keys()
    assert k.application_server_key_b64url == k.public_key_b64url


# ─── PWA FastAPI router (in-process) ──────────────────────────────────────


@pytest.fixture
def pwa_router(store_path: Path):
    from phantom.pwa.api import build_pwa_router
    store = SubscriptionStore(path=store_path)
    return build_pwa_router(store=store, vapid_public_key_b64url="PUBKEY")


def _client(router):
    pytest.importorskip("starlette")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_router_serves_manifest(pwa_router):
    c = _client(pwa_router)
    r = c.get("/pwa/manifest.webmanifest")
    assert r.status_code == 200
    assert "name" in r.json()


def test_router_serves_service_worker_with_correct_headers(pwa_router):
    c = _client(pwa_router)
    r = c.get("/pwa/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers.get("service-worker-allowed") == "/"


def test_router_serves_vapid_public_key(pwa_router):
    c = _client(pwa_router)
    r = c.get("/pwa/vapid-public-key")
    assert r.status_code == 200
    assert r.json() == {"public_key": "PUBKEY"}


def test_router_503_when_no_vapid_configured(store_path: Path):
    from phantom.pwa.api import build_pwa_router
    router = build_pwa_router(store=SubscriptionStore(path=store_path))
    c = _client(router)
    r = c.get("/pwa/vapid-public-key")
    assert r.status_code == 503


def test_router_subscribe_accepts_valid_subscription(pwa_router):
    c = _client(pwa_router)
    body = {
        "endpoint": "https://push.example/abc",
        "keys": {"p256dh": "p256", "auth": "auth"},
    }
    r = c.post("/pwa/subscribe", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["new"] is True
    assert j["total"] == 1


def test_router_subscribe_rejects_non_https_endpoint(pwa_router):
    c = _client(pwa_router)
    r = c.post("/pwa/subscribe", json={
        "endpoint": "http://insecure", "keys": {"p256dh": "p", "auth": "a"},
    })
    assert r.status_code == 400


def test_router_subscribe_rejects_missing_keys(pwa_router):
    c = _client(pwa_router)
    r = c.post("/pwa/subscribe", json={"endpoint": "https://x", "keys": {}})
    assert r.status_code == 400


def test_router_unsubscribe(pwa_router):
    c = _client(pwa_router)
    body = {"endpoint": "https://push.example/abc",
            "keys": {"p256dh": "p", "auth": "a"}}
    c.post("/pwa/subscribe", json=body)
    r = c.request("DELETE", "/pwa/subscribe", json={"endpoint": body["endpoint"]})
    assert r.status_code == 200
    j = r.json()
    assert j["removed"] is True
    assert j["total"] == 0


def test_router_healthz_reports_subscription_count(pwa_router):
    c = _client(pwa_router)
    r = c.get("/pwa/healthz")
    assert r.status_code == 200
    assert r.json()["push_enabled"] is True
