"""Tests for context_memory — CLAUDE.md-style hierarchical loading."""
from __future__ import annotations

import os

import pytest

from omnicli.context_memory import (
    discover, load, inject_into_messages, MergedContext, LoadedFile,
    MAX_PER_FILE, DEFAULT_MAX_CHARS,
)


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    """Build tmp_path/{home, proj/.phantom, proj/sub/sub2}/. Return helper."""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    (proj / ".phantom").mkdir(parents=True)
    (proj / "sub" / "sub2").mkdir(parents=True)
    home.mkdir()
    (home / ".phantom").mkdir()

    # Point user path under the fake home.
    monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))
    # Fake $HOME so _local_file's loop stops at our fake home, not the real one.
    monkeypatch.setenv("HOME", str(home))

    def _write(where: str, content: str):
        mapping = {
            "user":         home / ".phantom" / "CONTEXT.md",
            "project_root": proj / ".phantom" / "CONTEXT.md",
            "local":        proj / "sub" / "CONTEXT.md",
            "deep_local":   proj / "sub" / "sub2" / "CONTEXT.md",
        }
        mapping[where].write_text(content)

    return {
        "home":  home,
        "proj":  proj,
        "deep":  proj / "sub" / "sub2",
        "write": _write,
    }


class TestDiscovery:
    def test_no_files_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "nope.md"))
        monkeypatch.setenv("HOME", str(tmp_path))
        assert discover(str(tmp_path)) == []

    def test_user_only(self, fake_tree):
        fake_tree["write"]("user", "U")
        items = discover(str(fake_tree["proj"]))
        assert len(items) == 1
        assert items[0].scope == "user"
        assert items[0].content == "U"

    def test_project_root_discovered_from_subdir(self, fake_tree):
        fake_tree["write"]("project_root", "P")
        items = discover(str(fake_tree["deep"]))
        scopes = [i.scope for i in items]
        assert "project_root" in scopes

    def test_local_beats_project_root_in_scope_precedence(self, fake_tree):
        fake_tree["write"]("project_root", "P")
        fake_tree["write"]("deep_local", "L")
        items = discover(str(fake_tree["deep"]))
        # Expect: project_root, then local (deep_local)
        scopes = [i.scope for i in items]
        assert scopes == ["project_root", "local"]

    def test_all_three_scopes_ordered(self, fake_tree):
        fake_tree["write"]("user", "U")
        fake_tree["write"]("project_root", "P")
        fake_tree["write"]("deep_local", "L")
        items = discover(str(fake_tree["deep"]))
        scopes = [i.scope for i in items]
        assert scopes == ["user", "project_root", "local"]

    def test_project_root_file_not_duplicated_as_local(self, fake_tree):
        """When the project root IS the starting directory, the root's
        CONTEXT.md must not also appear under the `local` scope."""
        fake_tree["write"]("project_root", "P")
        items = discover(str(fake_tree["proj"]))
        scopes = [i.scope for i in items]
        assert scopes.count("local") == 0
        assert "project_root" in scopes


class TestLoad:
    def test_merged_text_has_scope_headers(self, fake_tree):
        fake_tree["write"]("user", "user-guidance")
        fake_tree["write"]("project_root", "project-rules")
        merged = load(str(fake_tree["proj"]))
        assert "[user]" in merged.text
        assert "[project_root]" in merged.text
        assert "user-guidance" in merged.text
        assert "project-rules" in merged.text

    def test_empty_when_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(tmp_path / "nope.md"))
        monkeypatch.setenv("HOME", str(tmp_path))
        merged = load(str(tmp_path))
        assert merged.empty is True

    def test_per_file_tail_truncation(self, fake_tree):
        big = "x" * (MAX_PER_FILE + 5_000)
        fake_tree["write"]("user", big)
        merged = load(str(fake_tree["proj"]))
        user_file = next(f for f in merged.files if f.scope == "user")
        assert user_file.truncated is True
        assert len(user_file.content) == MAX_PER_FILE

    def test_overall_budget_trimmed(self, fake_tree):
        # 3 files each ~8KB = 24KB; cap at 10KB forces overall trim.
        fake_tree["write"]("user", "u" * 8_000)
        fake_tree["write"]("project_root", "p" * 8_000)
        fake_tree["write"]("deep_local", "l" * 8_000)
        merged = load(str(fake_tree["deep"]), max_chars=10_000)
        assert merged.total_chars <= 10_000 + 200  # small slack for banner
        assert "context truncated" in merged.text

    def test_below_budget_untrimmed(self, fake_tree):
        fake_tree["write"]("user", "short")
        merged = load(str(fake_tree["proj"]))
        assert "context truncated" not in merged.text

    def test_header_banner_present(self, fake_tree):
        fake_tree["write"]("user", "U")
        merged = load(str(fake_tree["proj"]))
        assert "PHANTOM CONTEXT" in merged.text
        assert "most-specific scope" in merged.text


class TestInjection:
    def test_inject_into_empty_messages(self, fake_tree):
        fake_tree["write"]("user", "hi")
        merged = load(str(fake_tree["proj"]))
        out = inject_into_messages([], merged)
        assert len(out) == 1
        assert out[0]["role"] == "system"
        assert "hi" in out[0]["content"]

    def test_inject_after_existing_system(self, fake_tree):
        fake_tree["write"]("user", "hi")
        merged = load(str(fake_tree["proj"]))
        msgs = [
            {"role": "system", "content": "sys1"},
            {"role": "user",   "content": "hello"},
        ]
        out = inject_into_messages(msgs, merged)
        assert out[0]["role"] == "system"
        assert out[0]["content"] == "sys1"
        assert out[1]["role"] == "system"
        assert "hi" in out[1]["content"]
        assert out[2]["role"] == "user"

    def test_inject_prepend_when_no_system(self, fake_tree):
        fake_tree["write"]("user", "hi")
        merged = load(str(fake_tree["proj"]))
        msgs = [{"role": "user", "content": "hello"}]
        out = inject_into_messages(msgs, merged)
        assert out[0]["role"] == "system"
        assert out[1]["role"] == "user"

    def test_empty_merged_is_noop(self):
        merged = MergedContext()
        msgs = [{"role": "user", "content": "x"}]
        out = inject_into_messages(msgs, merged)
        assert out == msgs
        assert out is not msgs  # returns a copy


class TestReadRobustness:
    def test_broken_file_content_becomes_empty(self, tmp_path, monkeypatch):
        # Directly invoke discover with a file that can't be read — simulate
        # by making it a directory. discover should skip it gracefully.
        home = tmp_path / "home"
        (home / ".phantom").mkdir(parents=True)
        # Make the CONTEXT.md a directory, not a file
        (home / ".phantom" / "CONTEXT.md").mkdir()
        monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))
        monkeypatch.setenv("HOME", str(home))
        # The os.path.isfile check returns False for a directory — should be skipped
        items = discover(str(home))
        assert items == []
