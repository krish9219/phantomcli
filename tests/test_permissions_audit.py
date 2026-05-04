"""Tests that permissions.check() appends to the audit log."""
from __future__ import annotations

import os

import pytest

from omnicli.permissions import Permissions


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    from omnicli import audit_log
    audit_log.clear()
    yield
    audit_log.clear()


class TestAuditEnabledByDefault:
    def test_allow_decision_recorded(self):
        from omnicli import audit_log
        p = Permissions(allow=["bash:git:*"])
        p.check("bash", "git status")
        rows = audit_log.tail()
        assert len(rows) == 1
        assert rows[0]["decision"] == "allow"
        assert rows[0]["subject"] == "bash"
        assert rows[0]["resource"] == "git status"

    def test_deny_decision_recorded(self):
        from omnicli import audit_log
        p = Permissions(allow=["bash:*"], deny=["bash:rm:*"])
        p.check("bash", "rm -rf /")
        rows = audit_log.tail()
        assert rows[-1]["decision"] == "deny"
        assert "rm" in rows[-1]["resource"]

    def test_ask_decision_recorded(self):
        from omnicli import audit_log
        p = Permissions()
        p.check("bash", "docker ps")
        rows = audit_log.tail()
        assert rows[-1]["decision"] == "ask"
        assert "no matching" in rows[-1]["reason"]

    def test_matched_pattern_captured(self):
        from omnicli import audit_log
        p = Permissions(allow=["write:/tmp/**"])
        p.check("write", "/tmp/file.txt")
        rec = audit_log.tail()[-1]
        assert rec["extra"]["matched_pattern"] == "write:/tmp/**"

    def test_unknown_action_still_recorded(self):
        from omnicli import audit_log
        p = Permissions()
        p.check("nuke", "everything")
        rec = audit_log.tail()[-1]
        assert rec["decision"] == "ask"


class TestAuditDisableable:
    def test_audit_false_skips_recording(self):
        from omnicli import audit_log
        p = Permissions(allow=["bash:git:*"])
        p.check("bash", "git status", audit=False)
        assert audit_log.tail() == []


class TestBatchOfDecisions:
    def test_many_checks_all_logged(self):
        from omnicli import audit_log
        p = Permissions(allow=["bash:git:*"], deny=["bash:rm:*"])
        for cmd in ["git status", "git log", "rm -rf /", "ls"]:
            p.check("bash", cmd)
        rows = audit_log.tail()
        assert len(rows) == 4
        decisions = [r["decision"] for r in rows]
        assert decisions == ["allow", "allow", "deny", "ask"]

    def test_chain_still_verifies_after_many_checks(self):
        from omnicli import audit_log
        p = Permissions(allow=["bash:git:*"])
        for i in range(10):
            p.check("bash", f"git log -n {i}")
        ok, broken = audit_log.verify_chain()
        assert ok is True
        assert broken is None


class TestAuditFailureSwallowed:
    def test_broken_audit_does_not_break_permissions(self, monkeypatch):
        """If audit_log.record raises, the permission check still returns."""
        from omnicli import audit_log
        def _boom(*a, **k): raise RuntimeError("audit dead")
        monkeypatch.setattr(audit_log, "record", _boom)
        p = Permissions(allow=["bash:*"])
        # Must not raise
        r = p.check("bash", "ls")
        assert r.decision == "allow"
