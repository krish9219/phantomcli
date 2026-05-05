"""Tests for the three v1.0 first-party plugins.

These tests use the plugins directly (no full loader instantiation) so
they remain pure-Python and have no Playwright / gh CLI / network deps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from phantom.plugins.builtin.code_review import CodeReviewPlugin, review_diff
from phantom.plugins.builtin.github_pr import GithubPRPlugin
from phantom.plugins.builtin.web_screenshot import WebScreenshotPlugin
from phantom.plugins.capability import Capability
from phantom.plugins.manifest import PluginManifest
from phantom.plugins.plugin import PluginContext
from phantom.sandbox import SandboxPolicy

BUILTIN = Path(__file__).resolve().parent.parent / "plugins" / "builtin"


# ─── manifests are valid + present ───────────────────────────────────────────


def test_each_v1_plugin_ships_a_manifest():
    for name in ("github_pr", "web_screenshot", "code_review"):
        m = BUILTIN / name / "manifest.json"
        assert m.exists(), f"missing manifest: {m}"
        body = json.loads(m.read_text())
        assert body["version"] == "1.0.0"
        assert "entry_point" in body


def _ctx(tmp_path: Path, manifest_path: Path) -> PluginContext:
    body = json.loads(manifest_path.read_text())
    pm = PluginManifest(
        name=body["name"],
        version=body["version"],
        description=body.get("description", ""),
        entry_point=body["entry_point"],
        capabilities=frozenset(Capability(c) for c in body.get("capabilities", [])),
        homepage=body.get("homepage", ""),
        author=body.get("author", ""),
        license=body.get("license", ""),
        signature=None,
        extras=body.get("extras", {}),
    )
    return PluginContext(
        workdir=tmp_path,
        sandbox_policy=SandboxPolicy(workdir=str(tmp_path)),
        capabilities=pm.capabilities,
        manifest=pm,
        extras={},
    )


# ─── code-review (pure python) ───────────────────────────────────────────────


def test_code_review_flags_eval_call():
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n+++ b/x.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+result = eval(user_input)\n"
    )
    out = review_diff(diff)
    assert out["ok"] is True
    rules = {f["rule"] for f in out["findings"]}
    assert "unsafe_eval" in rules


def test_code_review_flags_aws_key():
    diff = (
        "+++ b/cfg.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+KEY = 'AKIAABCDEFGHIJKLMNOP'\n"
    )
    out = review_diff(diff)
    rules = {f["rule"] for f in out["findings"]}
    assert "aws_access_key" in rules


def test_code_review_flags_shell_true():
    diff = (
        "+++ b/run.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+subprocess.run(cmd, shell=True)\n"
    )
    out = review_diff(diff)
    assert any(f["rule"] == "shell_true" for f in out["findings"])


def test_code_review_clean_diff_returns_no_findings():
    diff = "+++ b/clean.py\n@@ -0,0 +1,1 @@\n+x = 1\n"
    out = review_diff(diff)
    assert out["findings"] == []


def test_code_review_stats_count_added_removed():
    diff = (
        "+++ b/x.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
        "+++ b/y.py\n@@ -1,1 +1,0 @@\n-z\n"
    )
    out = review_diff(diff)
    assert out["stats"]["added"] == 2
    assert out["stats"]["removed"] == 1


def test_code_review_plugin_call_rejects_missing_diff(tmp_path: Path):
    plugin = CodeReviewPlugin(manifest=None)  # type: ignore[arg-type]
    out = plugin.call(None, {})  # type: ignore[arg-type]
    assert out["ok"] is False


# ─── github-pr (no gh CLI required for shape test) ───────────────────────────


def test_github_pr_unknown_op_rejected(tmp_path: Path):
    plugin = GithubPRPlugin(manifest=None)  # type: ignore[arg-type]
    out = plugin.call(None, {"op": "explode"})  # type: ignore[arg-type]
    assert out["ok"] is False
    # could be "gh CLI not on PATH" or "unknown op" depending on env
    assert isinstance(out.get("error"), str) and out["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="gh CLI isn't installed on the GitHub Windows runner")
def test_github_pr_view_requires_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Pretend gh exists so we exercise the validation path
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + (Path("/usr/bin")).as_posix())
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho '{}'\n")
    fake.chmod(0o755)
    plugin = GithubPRPlugin(manifest=None)  # type: ignore[arg-type]
    out = plugin.call(None, {"op": "view"})  # type: ignore[arg-type]
    assert out["ok"] is False
    assert "number" in out["error"].lower()


# ─── web-screenshot (playwright optional) ────────────────────────────────────


def test_web_screenshot_requires_url():
    plugin = WebScreenshotPlugin(manifest=None)  # type: ignore[arg-type]
    out = plugin.call(None, {})  # type: ignore[arg-type]
    assert out["ok"] is False
    assert "url" in out["error"].lower()


def test_web_screenshot_rejects_non_http_url():
    plugin = WebScreenshotPlugin(manifest=None)  # type: ignore[arg-type]
    out = plugin.call(None, {"url": "ftp://example.com"})  # type: ignore[arg-type]
    assert out["ok"] is False


def test_web_screenshot_handles_missing_playwright(monkeypatch: pytest.MonkeyPatch):
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "playwright.sync_api":
            raise ImportError("no playwright")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    plugin = WebScreenshotPlugin(manifest=None)  # type: ignore[arg-type]
    out = plugin.call(None, {"url": "https://example.com"})  # type: ignore[arg-type]
    assert out["ok"] is False
    assert "playwright" in out["error"].lower()
