"""Tests for the append-only hash-chained audit log."""
from __future__ import annotations

import json
import os
import stat

import pytest

from omnicli import audit_log as al


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    al.clear()
    yield
    al.clear()


class TestRecord:
    def test_basic_record(self):
        r = al.record("permission", "allow",
                      subject="run_bash", resource="ls",
                      reason="matched allow pattern bash:*")
        assert r.category == "permission"
        assert r.decision == "allow"
        assert r.subject == "run_bash"
        assert r.hash
        assert r.prev_hash == ""   # first record

    def test_second_record_chains_from_first(self):
        r1 = al.record("permission", "allow")
        r2 = al.record("permission", "deny")
        assert r2.prev_hash == r1.hash
        assert r1.hash != r2.hash

    def test_extra_kwargs_captured(self):
        r = al.record("auth", "deny", url="http://example", extra_detail=42)
        assert r.extra == {"url": "http://example", "extra_detail": 42}


class TestFileFormat:
    def test_writes_jsonl(self, tmp_path):
        al.record("permission", "allow")
        al.record("permission", "deny")
        path = os.environ["PHANTOM_AUDIT_LOG"]
        with open(path) as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)   # parses cleanly

    def test_chmod_0600_unix(self):
        if os.name == "nt":
            pytest.skip("no chmod on Windows")
        al.record("permission", "allow")
        path = os.environ["PHANTOM_AUDIT_LOG"]
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600


class TestTail:
    def test_empty_returns_empty_list(self):
        assert al.tail() == []

    def test_returns_in_chronological_order(self):
        for i in range(5):
            al.record("permission", "allow", subject=f"s{i}")
        out = al.tail()
        subjects = [r["subject"] for r in out]
        assert subjects == ["s0", "s1", "s2", "s3", "s4"]

    def test_honours_n(self):
        for i in range(10):
            al.record("permission", "allow", subject=f"s{i}")
        out = al.tail(n=3)
        assert [r["subject"] for r in out] == ["s7", "s8", "s9"]


class TestChainVerification:
    def test_empty_is_valid(self):
        ok, broken = al.verify_chain()
        assert ok is True
        assert broken is None

    def test_untampered_chain_verifies(self):
        for i in range(5):
            al.record("permission", "allow", subject=f"s{i}")
        ok, broken = al.verify_chain()
        assert ok is True
        assert broken is None

    def test_tampered_line_is_detected(self):
        for i in range(3):
            al.record("permission", "allow", subject=f"s{i}")
        path = os.environ["PHANTOM_AUDIT_LOG"]
        with open(path) as f:
            lines = f.readlines()
        # Mutate line 2 (subject) without updating its hash
        rec = json.loads(lines[1])
        rec["subject"] = "tampered"
        lines[1] = json.dumps(rec) + "\n"
        with open(path, "w") as f:
            f.writelines(lines)

        ok, broken = al.verify_chain()
        assert ok is False
        assert broken == 2

    def test_deleted_middle_line_breaks_chain(self):
        for i in range(4):
            al.record("permission", "allow", subject=f"s{i}")
        path = os.environ["PHANTOM_AUDIT_LOG"]
        with open(path) as f:
            lines = f.readlines()
        # Drop line 3
        with open(path, "w") as f:
            f.writelines([lines[0], lines[1], lines[3]])

        ok, broken = al.verify_chain()
        assert ok is False

    def test_inserted_line_breaks_chain(self):
        al.record("permission", "allow", subject="a")
        al.record("permission", "allow", subject="b")
        path = os.environ["PHANTOM_AUDIT_LOG"]
        with open(path) as f:
            lines = f.readlines()
        # Insert a handcrafted record with bogus hash
        fake = json.dumps({
            "ts": 1.0, "iso": "1970-01-01T00:00:00+00:00",
            "category": "fake", "decision": "allow",
            "subject": "", "resource": "", "reason": "",
            "extra": {}, "prev_hash": "", "hash": "0" * 64,
        }) + "\n"
        with open(path, "w") as f:
            f.writelines([lines[0], fake, lines[1]])
        ok, broken = al.verify_chain()
        assert ok is False


class TestRealPermissionFlow:
    def test_records_allow_and_deny(self):
        al.record("permission", "allow", subject="run_bash",
                  resource="ls -la", reason="bash:ls matched")
        al.record("permission", "deny", subject="run_bash",
                  resource="rm -rf /", reason="bash:rm:* matched deny")
        out = al.tail()
        assert out[0]["decision"] == "allow"
        assert out[1]["decision"] == "deny"
        # Chain still verifies
        ok, _ = al.verify_chain()
        assert ok is True

    def test_auth_events(self):
        al.record("auth", "allow", subject="alice@example",
                  resource="/dashboard", reason="token ok")
        al.record("auth", "deny", subject="(anon)",
                  resource="/dashboard", reason="bad token")
        assert len(al.tail()) == 2
