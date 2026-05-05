"""Tests for permissions.Permissions pattern matching."""
from __future__ import annotations

import os
import sys

import pytest

from omnicli.permissions import Permissions, load


class TestBashMatching:
    def test_colon_form_matches_program(self):
        p = Permissions(allow=["bash:git:*"])
        assert p.check("bash", "git status").decision == "allow"
        assert p.check("bash", "git log -n 5").decision == "allow"
        assert p.check("bash", "rm -rf /").decision == "ask"

    def test_colon_form_with_subcommand(self):
        p = Permissions(allow=["bash:git:log *"])
        assert p.check("bash", "git log -n 5").decision == "allow"
        assert p.check("bash", "git status").decision == "ask"

    def test_program_only_pattern_matches_any_invocation(self):
        """`bash:ls` (no colon after the program) matches `ls` with any args
        — mirrors Claude Code's `Bash(ls)` semantics where the pattern is a
        program allowlist, not an exact-string match."""
        p = Permissions(allow=["bash:ls"])
        assert p.check("bash", "ls").decision == "allow"
        assert p.check("bash", "ls -la").decision == "allow"
        # But a different program must not match.
        assert p.check("bash", "cat /etc/passwd").decision == "ask"

    def test_env_prefix_stripped(self):
        p = Permissions(allow=["bash:git:*"])
        assert p.check("bash", "FOO=bar GIT_TRACE=1 git status").decision == "allow"

    def test_wildcard_program(self):
        p = Permissions(allow=["bash:*"])
        assert p.check("bash", "literally anything goes").decision == "allow"


class TestPathMatching:
    @pytest.mark.skipif(sys.platform == "win32", reason="glob patterns use POSIX path separators; Windows path matching needs separate test coverage")
    def test_double_star_path(self, tmp_path):
        # Pattern must be an absolute form for safety — expand home manually.
        p = Permissions(allow=[f"write:{tmp_path}/**"])
        target = tmp_path / "sub" / "deep" / "file.txt"
        assert p.check("write", str(target)).decision == "allow"

    def test_deny_outside_scope(self, tmp_path):
        p = Permissions(allow=[f"write:{tmp_path}/**"])
        assert p.check("write", "/etc/passwd").decision == "ask"

    @pytest.mark.skipif(sys.platform == "win32", reason="glob patterns use POSIX path separators; Windows path matching needs separate test coverage")
    def test_home_tilde_expansion(self):
        p = Permissions(allow=["read:~/projects/**"])
        home = os.path.expanduser("~")
        assert p.check("read", f"{home}/projects/foo.py").decision == "allow"
        assert p.check("read", f"{home}/secrets/key").decision == "ask"

    def test_relative_pattern_matches_basename(self):
        p = Permissions(allow=["read:*.log"])
        assert p.check("read", "/var/log/app.log").decision == "allow"
        assert p.check("read", "/var/log/app.txt").decision == "ask"


class TestPrecedence:
    def test_deny_beats_allow(self):
        p = Permissions(
            allow=["bash:*"],
            deny=["bash:rm:*"],
        )
        assert p.check("bash", "rm -rf /").decision == "deny"
        assert p.check("bash", "ls").decision == "allow"

    def test_allow_beats_ask(self):
        p = Permissions(
            allow=["bash:git:*"],
            ask=["bash:*"],
        )
        assert p.check("bash", "git status").decision == "allow"
        assert p.check("bash", "docker ps").decision == "ask"

    def test_empty_config_returns_ask(self):
        p = Permissions(allow=[], deny=[], ask=[])
        r = p.check("bash", "anything")
        assert r.decision == "ask"
        assert "no matching" in r.reason

    def test_has_config_false_when_empty(self):
        assert Permissions(allow=[], deny=[], ask=[]).has_config() is False

    def test_has_config_true_when_any_list_present(self):
        assert Permissions(allow=["bash:*"]).has_config() is True


class TestUrlMatching:
    def test_browse_domain_match(self):
        p = Permissions(allow=["browse:https://github.com/*"])
        assert p.check("browse", "https://github.com/foo/bar").decision == "allow"
        assert p.check("browse", "https://evil.example.com").decision == "ask"


class TestConfigLoading:
    def test_load_from_config(self, monkeypatch):
        from omnicli import memory
        memory.save_config("permissions_allow", "bash:git:*\nwrite:~/x/**\n# a comment\n\n")
        memory.save_config("permissions_deny", "bash:rm:*")
        p = load()
        assert "bash:git:*" in p.allow
        assert "write:~/x/**" in p.allow
        assert "bash:rm:*" in p.deny
        # Comments and blanks stripped
        assert "" not in p.allow
        assert "# a comment" not in p.allow

    def test_unknown_action_returns_ask(self):
        p = Permissions(allow=["nuke:*"])
        r = p.check("nuke", "everything")
        assert r.decision == "ask"
        assert "unknown" in r.reason.lower()
