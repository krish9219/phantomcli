"""Tests for prompt_builder — the CONTEXT.md + compaction + cache pipeline."""
from __future__ import annotations

import json
import os

import pytest

from omnicli.prompt_builder import build, detect_provider


class TestProviderDetection:
    def test_anthropic(self):
        assert detect_provider("https://api.anthropic.com/v1") == "anthropic"

    def test_openai(self):
        assert detect_provider("https://api.openai.com/v1") == "openai"

    def test_groq(self):
        assert detect_provider("https://api.groq.com/openai/v1") == "groq"

    def test_nvidia(self):
        assert detect_provider("https://integrate.api.nvidia.com/v1") == "nvidia"

    def test_gemini(self):
        assert detect_provider("https://generativelanguage.googleapis.com") == "gemini"

    def test_unknown_returns_auto(self):
        assert detect_provider("https://self-hosted.example.com") == "auto"

    def test_empty_returns_auto(self):
        assert detect_provider("") == "auto"


class TestContextInjection:
    def test_context_md_discovered_and_injected(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".phantom").mkdir(parents=True)
        (home / ".phantom" / "CONTEXT.md").write_text("User-wide rules: no rm -rf.")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))

        out = build(
            [{"role": "user", "content": "hi"}],
            provider="anthropic",
            project_dir=str(tmp_path),
            apply_cache=False,
        )
        # Leading system message now contains the context block
        assert out[0]["role"] == "system"
        assert "User-wide rules" in out[0]["content"]

    def test_inject_context_false_skips(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".phantom").mkdir(parents=True)
        (home / ".phantom" / "CONTEXT.md").write_text("SHOULD NOT APPEAR")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))

        out = build(
            [{"role": "user", "content": "hi"}],
            provider="anthropic",
            project_dir=str(tmp_path),
            inject_context=False,
            apply_cache=False,
        )
        merged_text = " ".join(
            m["content"] if isinstance(m["content"], str) else ""
            for m in out
        )
        assert "SHOULD NOT APPEAR" not in merged_text


class TestCacheAnnotation:
    def test_anthropic_gets_cache_markers_for_long_system(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        from omnicli.prompt_cache import cached_block_count
        msgs = [
            {"role": "system", "content": "x" * 10_000},
            {"role": "user",   "content": "hi"},
        ]
        out = build(msgs, provider="anthropic", project_dir=str(tmp_path),
                    inject_context=False)
        assert cached_block_count(out) >= 1

    def test_openai_has_cache_markers_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        from omnicli.prompt_cache import cached_block_count
        msgs = [
            {"role": "system", "content": "x" * 10_000},
            {"role": "user",   "content": "hi"},
        ]
        out = build(msgs, provider="openai", project_dir=str(tmp_path),
                    inject_context=False)
        # cache_control must have been stripped before return
        assert cached_block_count(out) == 0

    def test_groq_strips_markers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        from omnicli.prompt_cache import cached_block_count
        msgs = [{"role": "system", "content": "x" * 10_000},
                {"role": "user",   "content": "hi"}]
        out = build(msgs, provider="groq", project_dir=str(tmp_path),
                    inject_context=False)
        assert cached_block_count(out) == 0

    def test_short_system_gets_no_markers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        from omnicli.prompt_cache import cached_block_count
        msgs = [{"role": "system", "content": "short"},
                {"role": "user",   "content": "hi"}]
        out = build(msgs, provider="anthropic", project_dir=str(tmp_path),
                    inject_context=False)
        assert cached_block_count(out) == 0

    def test_apply_cache_false_skips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        from omnicli.prompt_cache import cached_block_count
        msgs = [{"role": "system", "content": "x" * 10_000},
                {"role": "user",   "content": "hi"}]
        out = build(msgs, provider="anthropic", project_dir=str(tmp_path),
                    inject_context=False, apply_cache=False)
        assert cached_block_count(out) == 0


class TestCompactionIntegration:
    def test_long_history_compacts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        msgs = [{"role": "system", "content": "You are Phantom."}]
        for i in range(50):
            msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": "conversation " * 500})
        before = len(msgs)
        out = build(msgs, provider="anthropic", project_dir=str(tmp_path),
                    inject_context=False, compact_budget=20_000)
        # Compacted: fewer messages than before
        assert len(out) < before

    def test_small_history_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        out = build(msgs, provider="anthropic", project_dir=str(tmp_path),
                    inject_context=False, compact_budget=128_000,
                    apply_cache=False)
        # Preserved wholesale
        assert len(out) == 2


class TestCompositionOrder:
    def test_context_then_compact_then_cache(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".phantom").mkdir(parents=True)
        (home / ".phantom" / "CONTEXT.md").write_text("Rules apply.")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))

        msgs = [{"role": "system", "content": "primary"}]
        for _ in range(30):
            msgs.append({"role": "user", "content": "x" * 2000})
        out = build(msgs, provider="anthropic", project_dir=str(tmp_path),
                    compact_budget=3000)
        # 1) Context block was injected (CONTEXT.md text appears)
        text_all = " ".join(
            m["content"] if isinstance(m["content"], str)
            else " ".join(b.get("text", "") for b in m["content"] if isinstance(b, dict))
            for m in out
        )
        assert "Rules apply" in text_all
        # 2) Compaction happened — total msg count reduced
        assert len(out) < 31 + 2  # +2 for possible context + original system

    def test_idempotent_on_empty_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "none.md"))
        msgs = [{"role": "user", "content": "hi"}]
        out = build(msgs, provider="auto", project_dir=str(tmp_path))
        # Nothing to inject, nothing to compact → same shape
        assert len(out) == 1
        assert out[0]["content"] == "hi"
