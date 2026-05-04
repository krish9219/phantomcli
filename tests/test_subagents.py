"""Tests for the typed subagent registry."""
from __future__ import annotations

import os

import pytest

from omnicli.subagents import (
    SubagentType, Registry, DEFAULT_REGISTRY,
    get, list_types, reload_registry,
)


class TestBuiltins:
    def test_built_in_types_present(self):
        names = {t.name for t in list_types()}
        expected = {
            "general-purpose", "explore", "plan",
            "code-reviewer", "security-reviewer", "statusline-setup",
        }
        assert expected.issubset(names), f"missing: {expected - names}"

    def test_general_purpose_has_no_tool_restrictions(self):
        gp = get("general-purpose")
        assert gp is not None
        assert gp.allowed_tools == []

    def test_explore_is_read_only(self):
        e = get("explore")
        assert e is not None
        assert "write_file" not in e.allowed_tools
        assert "edit_file" not in e.allowed_tools
        assert "run_bash" not in e.allowed_tools
        assert "grep" in e.allowed_tools
        assert "read_file" in e.allowed_tools

    def test_plan_is_read_only(self):
        p = get("plan")
        assert p is not None
        assert "write_file" not in p.allowed_tools
        assert "edit_file" not in p.allowed_tools

    def test_security_reviewer_can_run_bash_but_not_write(self):
        s = get("security-reviewer")
        assert s is not None
        assert "run_bash" in s.allowed_tools
        assert "write_file" not in s.allowed_tools

    def test_case_insensitive_lookup(self):
        assert get("EXPLORE") is not None
        assert get("Explore") is not None


class TestToolAllowed:
    def test_empty_allowlist_means_all_tools(self):
        r = DEFAULT_REGISTRY
        assert r.tool_allowed("general-purpose", "run_bash") is True
        assert r.tool_allowed("general-purpose", "write_file") is True

    def test_allowlist_is_enforced(self):
        r = DEFAULT_REGISTRY
        assert r.tool_allowed("explore", "read_file") is True
        assert r.tool_allowed("explore", "write_file") is False
        assert r.tool_allowed("plan", "run_bash") is False
        assert r.tool_allowed("plan", "read_file") is True

    def test_unknown_agent_denies_all(self):
        r = DEFAULT_REGISTRY
        assert r.tool_allowed("nonexistent", "read_file") is False


class TestRegister:
    def test_can_register_custom(self):
        r = Registry()
        r.register(SubagentType(
            name="my-agent",
            description="d",
            system_prompt="p",
            allowed_tools=["read_file"],
        ))
        got = r.get("my-agent")
        assert got is not None
        assert got.allowed_tools == ["read_file"]

    def test_can_unregister(self):
        r = Registry()
        r.register(SubagentType("x", "", ""))
        assert r.unregister("x") is True
        assert r.unregister("x") is False


class TestUserAgents:
    def test_user_agent_loaded_from_md(self, tmp_path, monkeypatch):
        content = (
            "---\n"
            "name: migrate-helper\n"
            "description: Help with DB migrations\n"
            "tools: read_file, glob, grep\n"
            "timeout_s: 420\n"
            "---\n"
            "You are the migrate-helper. Always propose a rollback plan."
        )
        (tmp_path / "migrate-helper.md").write_text(content)
        monkeypatch.setenv("PHANTOM_AGENTS_DIR", str(tmp_path))
        r = reload_registry()
        got = r.get("migrate-helper")
        assert got is not None
        assert got.is_builtin is False
        assert got.description == "Help with DB migrations"
        assert set(got.allowed_tools) == {"read_file", "glob", "grep"}
        assert got.timeout_s == 420
        assert "rollback plan" in got.system_prompt

    def test_user_agent_without_frontmatter(self, tmp_path, monkeypatch):
        (tmp_path / "raw.md").write_text("Pure system prompt body.")
        monkeypatch.setenv("PHANTOM_AGENTS_DIR", str(tmp_path))
        r = reload_registry()
        got = r.get("raw")
        assert got is not None
        assert got.system_prompt == "Pure system prompt body."
        assert got.description.startswith("User subagent")

    def test_invalid_timeout_falls_back_to_default(self, tmp_path, monkeypatch):
        (tmp_path / "bad.md").write_text(
            "---\ntimeout_s: not-a-number\n---\nbody"
        )
        monkeypatch.setenv("PHANTOM_AGENTS_DIR", str(tmp_path))
        r = reload_registry()
        got = r.get("bad")
        assert got is not None
        assert got.timeout_s == 300

    def test_quoted_metadata_unquoted(self, tmp_path, monkeypatch):
        (tmp_path / "quoted.md").write_text(
            '---\ndescription: "Has quotes"\n---\nbody'
        )
        monkeypatch.setenv("PHANTOM_AGENTS_DIR", str(tmp_path))
        r = reload_registry()
        assert r.get("quoted").description == "Has quotes"


class TestListOrdering:
    def test_list_is_sorted(self):
        names = [t.name for t in list_types()]
        assert names == sorted(names)
