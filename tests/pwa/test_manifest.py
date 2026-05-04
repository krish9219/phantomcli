"""Tests for :mod:`phantom.pwa.manifest`."""

from __future__ import annotations

import json

from phantom.pwa import build_manifest, build_service_worker


class TestBuildManifest:
    def test_default_shape(self):
        m = build_manifest()
        assert m["name"] == "Phantom"
        assert m["display"] == "standalone"
        assert m["start_url"] == "/app/"
        # icons present at both required sizes.
        sizes = {i["sizes"] for i in m["icons"]}
        assert sizes == {"192x192", "512x512"}
        # JSON-serialisable.
        json.dumps(m)

    def test_overrides(self):
        m = build_manifest(name="MyAgent", short_name="MA", theme_color="#fff")
        assert m["name"] == "MyAgent"
        assert m["short_name"] == "MA"
        assert m["theme_color"] == "#fff"


class TestServiceWorker:
    def test_includes_app_shell(self):
        sw = build_service_worker()
        assert "/app/" in sw
        assert "/app/manifest.webmanifest" in sw

    def test_versioned_cache_name(self):
        sw_v1 = build_service_worker(cache_version="v1")
        sw_v2 = build_service_worker(cache_version="v2")
        assert "phantom-app-shell-v1" in sw_v1
        assert "phantom-app-shell-v2" in sw_v2
        assert sw_v1 != sw_v2

    def test_emits_skip_waiting(self):
        sw = build_service_worker()
        assert "skipWaiting" in sw

    def test_implements_network_first_for_api(self):
        sw = build_service_worker()
        assert "/app/api/" in sw
