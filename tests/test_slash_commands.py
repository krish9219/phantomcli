"""Tests for the slash command registry + built-in handlers."""
from __future__ import annotations

import os

import pytest

from omnicli.slash_commands import (
    parse, dispatch, Registry, SlashCommand, SlashResult, reload_registry,
)


class TestParser:
    def test_plain_text_returns_none(self):
        assert parse("hello world") is None

    def test_command_without_args(self):
        assert parse("/help") == ("help", "")

    def test_command_with_args(self):
        assert parse("/model gpt-4") == ("model", "gpt-4")

    def test_whitespace_tolerated(self):
        assert parse("   /clear   ") == ("clear", "")

    def test_hyphenated_names_parse(self):
        assert parse("/my-command foo") == ("my-command", "foo")

    def test_underscored_names_parse(self):
        assert parse("/my_command foo") == ("my_command", "foo")

    def test_multiline_args_preserved(self):
        assert parse("/write\nhello\nworld") == ("write", "hello\nworld")

    def test_empty_string_returns_none(self):
        assert parse("") is None

    def test_non_slash_prefix_returns_none(self):
        assert parse("help") is None
        assert parse("./help") is None


class TestRegistry:
    def test_register_and_get(self):
        r = Registry()
        r.register(SlashCommand("foo", "desc", lambda a, c: "ok"))
        assert r.get("foo") is not None
        assert r.get("FOO") is not None  # case-insensitive

    def test_unregister(self):
        r = Registry()
        r.register(SlashCommand("foo", "desc", lambda a, c: "ok"))
        assert r.unregister("foo") is True
        assert r.unregister("foo") is False
        assert r.get("foo") is None

    def test_list_sorted(self):
        r = Registry()
        r.register(SlashCommand("zulu", "", lambda a, c: ""))
        r.register(SlashCommand("alpha", "", lambda a, c: ""))
        r.register(SlashCommand("mike", "", lambda a, c: ""))
        names = [c.name for c in r.list()]
        assert names == ["alpha", "mike", "zulu"]


class TestDispatchUnknown:
    def test_unknown_command_returns_error(self):
        result = dispatch("/nope")
        assert result.error is True
        assert "Unknown" in result.text

    def test_non_slash_returns_non_error(self):
        result = dispatch("just chatting")
        assert result.error is False


class TestBuiltins:
    def test_help_lists_builtins(self):
        r = dispatch("/help")
        assert not r.error
        assert "/help" in r.text
        assert "/clear" in r.text
        assert "/model" in r.text
        assert "/perm" in r.text

    def test_help_specific_command(self):
        r = dispatch("/help model")
        assert not r.error
        assert "model" in r.text.lower()

    def test_help_unknown_specific(self):
        r = dispatch("/help nonexistent")
        assert r.error is True

    def test_clear_signals_clear(self):
        r = dispatch("/clear")
        assert r.clear is True

    def test_exit_signals_exit(self):
        r = dispatch("/exit")
        assert r.exit is True

    def test_quit_is_alias_of_exit(self):
        r = dispatch("/quit")
        assert r.exit is True

    def test_model_get_shows_current(self):
        r = dispatch("/model")
        assert "model" in r.text.lower()

    def test_model_set_updates_config(self):
        from omnicli.memory import get_config
        r = dispatch("/model gpt-5-turbo")
        assert not r.error
        assert get_config("main_model", "") == "gpt-5-turbo"

    def test_memory_shows_keys(self):
        r = dispatch("/memory")
        assert "main_model" in r.text

    def test_cost_reports_zero_for_empty_context(self):
        r = dispatch("/cost", ctx={"messages": []})
        assert "0" in r.text


class TestPermCommand:
    def test_perm_list_empty(self):
        r = dispatch("/perm list")
        assert not r.error
        assert "Allow" in r.text

    def test_perm_allow_adds(self):
        from omnicli.memory import get_config
        r = dispatch("/perm allow bash:git:*")
        assert not r.error
        assert "bash:git:*" in (get_config("permissions_allow", "") or "")

    def test_perm_deny_adds(self):
        from omnicli.memory import get_config
        r = dispatch("/perm deny bash:rm:*")
        assert "bash:rm:*" in (get_config("permissions_deny", "") or "")

    def test_perm_remove(self):
        from omnicli.memory import get_config
        dispatch("/perm allow bash:ls")
        assert "bash:ls" in (get_config("permissions_allow", "") or "")
        r = dispatch("/perm remove bash:ls")
        assert not r.error
        assert "bash:ls" not in (get_config("permissions_allow", "") or "")

    def test_perm_missing_pattern_errors(self):
        r = dispatch("/perm allow")
        assert r.error is True


class TestCompactCommand:
    def test_compact_on_empty(self):
        r = dispatch("/compact", ctx={"messages": []})
        assert "No conversation" in r.text

    def test_compact_reduces(self):
        msgs = [{"role": "system", "content": "S"}]
        for i in range(30):
            msgs.append({"role": "user", "content": "x" * 2000})
        ctx = {"messages": msgs, "budget": 2000}
        r = dispatch("/compact", ctx=ctx)
        assert "Compacted" in r.text
        # ctx should be updated in place
        assert len(ctx["messages"]) < 31


class TestUserCommands:
    def test_user_command_loaded_and_rewrites(self, tmp_path, monkeypatch):
        # Point the user commands dir to a tmp dir
        (tmp_path / "greet.md").write_text(
            "---\ndescription: Greet someone\n---\n"
            "Please greet {args} warmly and professionally."
        )
        monkeypatch.setenv("PHANTOM_COMMANDS_DIR", str(tmp_path))
        r = reload_registry()
        cmd = r.get("greet")
        assert cmd is not None
        assert cmd.is_builtin is False
        assert "Greet someone" in cmd.description
        result = r.dispatch("/greet Alice")
        assert result.rewrite == "Please greet Alice warmly and professionally."

    def test_user_command_without_frontmatter(self, tmp_path, monkeypatch):
        (tmp_path / "raw.md").write_text("Just a raw template for {args}")
        monkeypatch.setenv("PHANTOM_COMMANDS_DIR", str(tmp_path))
        r = reload_registry()
        result = r.dispatch("/raw Bob")
        assert result.rewrite == "Just a raw template for Bob"


class TestHandlerExceptionsAreCaught:
    def test_broken_handler_does_not_crash_registry(self):
        r = Registry()

        def boom(_a, _c):
            raise RuntimeError("oh no")

        r.register(SlashCommand("boom", "x", boom))
        result = r.dispatch("/boom")
        assert result.error is True
        assert "oh no" in result.text
