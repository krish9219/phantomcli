"""Validation tests — does the PWA we generate actually parse?

These verify the generated artefacts against the W3C Web App Manifest
spec and basic JS / HTML well-formedness, so we know the browser will
accept them at deploy time.
"""

from __future__ import annotations

import html.parser
import json

import pytest

from phantom.pwa import build_manifest, build_pwa, build_service_worker


# ─── Manifest validates against W3C Web App Manifest core fields ──────────


# Full schema is large; we lock the load-bearing fields here. This
# matches what Chromium / Safari will reject if missing.
_REQUIRED_TYPES = {
    "name": str, "short_name": str, "start_url": str, "scope": str,
    "display": str, "icons": list, "theme_color": str, "background_color": str,
}
_VALID_DISPLAY = {"fullscreen", "standalone", "minimal-ui", "browser"}


class TestManifestSpec:
    def test_required_fields_present(self):
        m = build_manifest()
        for k, t in _REQUIRED_TYPES.items():
            assert k in m, f"missing required field {k!r}"
            assert isinstance(m[k], t), f"{k!r} must be {t.__name__}, got {type(m[k]).__name__}"

    def test_display_is_valid(self):
        assert build_manifest()["display"] in _VALID_DISPLAY

    def test_icons_have_required_sizes(self):
        # Chromium requires both 192x192 and 512x512 for installability.
        sizes = {i["sizes"] for i in build_manifest()["icons"]}
        assert "192x192" in sizes and "512x512" in sizes

    def test_icons_have_purpose_field(self):
        for icon in build_manifest()["icons"]:
            assert "purpose" in icon
            # Valid purposes: any, monochrome, maskable (and combinations).
            for p in icon["purpose"].split():
                assert p in {"any", "monochrome", "maskable"}

    def test_start_url_is_relative_or_absolute(self):
        u = build_manifest()["start_url"]
        assert u.startswith("/") or u.startswith("http")

    def test_scope_contains_start_url(self):
        m = build_manifest()
        assert m["start_url"].startswith(m["scope"])

    def test_theme_color_is_hex(self):
        c = build_manifest()["theme_color"]
        assert c.startswith("#") and len(c) in {4, 7}

    def test_manifest_is_compact_json_serialisable(self):
        m = build_manifest()
        s = json.dumps(m)
        # Round-trip preserves the dict.
        assert json.loads(s) == m


# ─── Service worker basic JS sanity ─────────────────────────────────────────


class TestServiceWorkerSanity:
    def test_braces_balanced(self):
        sw = build_service_worker()
        assert sw.count("{") == sw.count("}"), "unbalanced { and } in SW"
        assert sw.count("(") == sw.count(")"), "unbalanced ( and ) in SW"

    def test_lifecycle_events_registered(self):
        sw = build_service_worker()
        for evt in ("install", "activate", "fetch"):
            assert f'addEventListener("{evt}",' in sw

    def test_uses_caches_api(self):
        sw = build_service_worker()
        assert "caches.open" in sw
        assert "caches.match" in sw

    def test_skipwaiting_and_clients_claim(self):
        sw = build_service_worker()
        assert "skipWaiting" in sw
        assert "clients.claim" in sw


# ─── Generated index.html parses ────────────────────────────────────────────


class _TagCollector(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs):
        self.tags.append((tag, dict(attrs)))


class TestGeneratedHtml:
    def test_index_parses(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        html_text = (out / "index.html").read_text()
        p = _TagCollector()
        # html.parser doesn't raise on bad HTML, but it must produce a
        # non-empty tag list — verifies the generated HTML at least
        # parses to something.
        p.feed(html_text)
        tag_names = {t[0] for t in p.tags}
        assert "html" in tag_names
        assert "head" in tag_names
        assert "body" in tag_names

    def test_index_links_manifest_with_rel(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        p = _TagCollector()
        p.feed((out / "index.html").read_text())
        links = [attrs for tag, attrs in p.tags if tag == "link"]
        manifest_links = [a for a in links if a.get("rel") == "manifest"]
        assert manifest_links, "no <link rel='manifest'> in index.html"
        assert manifest_links[0]["href"].endswith("manifest.webmanifest")

    def test_index_registers_service_worker(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        text = (out / "index.html").read_text()
        assert "navigator.serviceWorker.register" in text
        assert "service-worker.js" in text


# ─── End-to-end build is repeatable + clean ─────────────────────────────────


class TestBuildIntegrity:
    def test_no_extraneous_files(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        names = {p.name for p in out.iterdir()}
        expected = {
            "index.html", "manifest.webmanifest", "service-worker.js",
            "main.js", "main.css", "icon-192.png", "icon-512.png",
            "README.md",
        }
        assert names == expected

    def test_pngs_have_iend_chunk(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        for icon in ("icon-192.png", "icon-512.png"):
            data = (out / icon).read_bytes()
            # Last 8 bytes should be the IEND chunk.
            assert data.endswith(b"IEND\xaeB`\x82")

    def test_generated_files_are_text_not_binary(self, tmp_path):
        out = build_pwa(tmp_path / "dist")
        for name in ("index.html", "manifest.webmanifest",
                     "service-worker.js", "main.js", "main.css", "README.md"):
            (out / name).read_text()  # raises on undecodable bytes
