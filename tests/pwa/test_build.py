"""Tests for :func:`phantom.pwa.build_pwa`."""

from __future__ import annotations

import json

from phantom.pwa import build_pwa


class TestBuildPwa:
    def test_emits_full_tree(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        for name in (
            "index.html",
            "manifest.webmanifest",
            "service-worker.js",
            "main.js",
            "main.css",
            "icon-192.png",
            "icon-512.png",
            "README.md",
        ):
            assert (out / name).exists(), f"missing {name}"

    def test_manifest_is_valid_json(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        m = json.loads((out / "manifest.webmanifest").read_text())
        assert m["display"] == "standalone"
        assert m["name"] == "Phantom"

    def test_index_links_manifest_and_sw(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        index = (out / "index.html").read_text()
        assert 'manifest.webmanifest' in index
        assert 'service-worker.js' in index

    def test_cache_version_propagates_to_sw(self, tmp_path):
        out = build_pwa(tmp_path / "dist", cache_version="v7")
        sw = (out / "service-worker.js").read_text()
        assert "phantom-app-shell-v7" in sw

    def test_icons_are_real_pngs(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        for name in ("icon-192.png", "icon-512.png"):
            data = (out / name).read_bytes()
            # PNG magic
            assert data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_idempotent_rebuild(self, tmp_path):
        d = tmp_path / "dist"
        build_pwa(d)
        # Second build into the same dir overwrites cleanly.
        build_pwa(d, cache_version="v2")
        sw = (d / "service-worker.js").read_text()
        assert "phantom-app-shell-v2" in sw

    def test_custom_site_name(self, tmp_path):
        out = build_pwa(tmp_path / "dist", site_name="MyAgent", short_name="MA")
        m = json.loads((out / "manifest.webmanifest").read_text())
        assert m["name"] == "MyAgent"
        assert m["short_name"] == "MA"
