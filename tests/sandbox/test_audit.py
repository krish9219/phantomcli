"""Tests for :mod:`phantom.sandbox.audit`.

Coverage target: 100% line, 100% branch on `phantom.sandbox.audit`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from phantom._version import __version__
from phantom.sandbox.audit import (
    AuditRecord,
    AuditWriter,
    default_audit_path,
    make_record,
)
from phantom.sandbox.policy import ResourceLimits, SandboxPolicy

# POSIX file-mode bits aren't enforceable on Windows: ``os.chmod`` ignores
# permission bits, and ``Path.stat().st_mode`` reflects the FAT/NTFS-style
# defaults (0o666 for files, 0o777 for dirs). Tests that *only* assert
# file-mode behaviour skip on Windows.
_skip_on_win32 = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file-mode bits are not enforceable on Windows.",
)


# Platform-portable absolute paths. ``/tmp/job`` is absolute on POSIX but
# not on Windows (no drive letter), so the SandboxPolicy ``isabs`` check
# rejects it. ``os.path.abspath`` returns ``/tmp/job`` unchanged on POSIX
# and ``C:\tmp\job`` on Windows — both legal absolute paths for the
# validator. The tests below don't assert exact path values; they just
# need a valid SandboxPolicy instance.
_J = os.path.abspath("/tmp/j")
_JOB = os.path.abspath("/tmp/job")
_A = os.path.abspath("/a")
_B = os.path.abspath("/b")


# ─── default_audit_path ───────────────────────────────────────────────────────


class TestDefaultAuditPath:
    def test_uses_phantom_home_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / "ph"))
        p = default_audit_path()
        assert p == tmp_path / "ph" / "sandbox-audit.log"

    @_skip_on_win32
    def test_creates_parent_dir_with_mode_0700(self, tmp_path, monkeypatch):
        target = tmp_path / "ph"
        monkeypatch.setenv("PHANTOM_HOME", str(target))
        default_audit_path()
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o700

    def test_idempotent_when_dir_exists(self, tmp_path, monkeypatch):
        target = tmp_path / "ph"
        target.mkdir(mode=0o750)
        monkeypatch.setenv("PHANTOM_HOME", str(target))
        # Should not raise even though mode != 0700.
        p = default_audit_path()
        assert p.parent == target

    def test_falls_back_to_home_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PHANTOM_HOME", raising=False)
        # Path.home() consults HOME on POSIX and USERPROFILE on Windows;
        # set both so the test works on every host.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        p = default_audit_path()
        assert p == tmp_path / ".phantom" / "sandbox-audit.log"


# ─── AuditRecord.to_json_line ─────────────────────────────────────────────────


class TestAuditRecord:
    def test_to_json_line_is_newline_terminated(self):
        rec = _sample_record()
        line = rec.to_json_line()
        assert line.endswith("\n")
        # Exactly one newline.
        assert line.count("\n") == 1

    def test_json_round_trip(self):
        rec = _sample_record()
        line = rec.to_json_line()
        parsed = json.loads(line)
        assert parsed["code"] == "ok"
        assert parsed["tier"] == "unshare"
        assert parsed["argv_len"] == 2
        assert parsed["exit_code"] == 0
        assert parsed["truncated"] is False
        assert parsed["phantom_ver"] == __version__

    def test_compact_json_format(self):
        # Records should be compact (no spaces) so logrotate sees one
        # short line per record.
        rec = _sample_record()
        line = rec.to_json_line()
        assert ", " not in line  # no JSON pretty-print spacing
        assert ": " not in line


# ─── AuditWriter ──────────────────────────────────────────────────────────────


class TestAuditWriter:
    def test_writes_json_line(self, tmp_path):
        path = tmp_path / "audit.log"
        w = AuditWriter(path)
        rec = _sample_record()
        n = w.write(rec)
        assert n > 0
        contents = path.read_text()
        assert contents.endswith("\n")
        parsed = json.loads(contents)
        assert parsed["code"] == "ok"

    def test_appends_not_overwrites(self, tmp_path):
        path = tmp_path / "audit.log"
        w = AuditWriter(path)
        w.write(_sample_record())
        w.write(_sample_record())
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert json.loads(line)["code"] == "ok"

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "audit.log"
        w = AuditWriter(path)
        w.write(_sample_record())
        assert path.exists()

    @_skip_on_win32
    def test_file_mode_is_0600(self, tmp_path):
        path = tmp_path / "audit.log"
        w = AuditWriter(path)
        w.write(_sample_record())
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    @_skip_on_win32
    def test_file_mode_repaired_on_subsequent_write(self, tmp_path):
        path = tmp_path / "audit.log"
        w = AuditWriter(path)
        w.write(_sample_record())
        # Operator widens permissions accidentally.
        os.chmod(path, 0o644)
        w.write(_sample_record())
        # Should be back to 0o600 after the second write.
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_path_property(self, tmp_path):
        path = tmp_path / "audit.log"
        w = AuditWriter(path)
        assert w.path == path

    def test_concurrent_writers_do_not_corrupt(self, tmp_path):
        # Two AuditWriter instances on the same path; each does an O_APPEND
        # write per record. The kernel guarantees per-write atomicity for
        # writes ≤ PIPE_BUF; our records are well under that.
        path = tmp_path / "audit.log"
        w1 = AuditWriter(path)
        w2 = AuditWriter(path)
        for _ in range(5):
            w1.write(_sample_record())
            w2.write(_sample_record())
        lines = path.read_text().splitlines()
        assert len(lines) == 10
        for line in lines:
            assert json.loads(line)["code"] == "ok"


# ─── make_record ──────────────────────────────────────────────────────────────


class TestMakeRecord:
    def test_basic_record(self):
        policy = SandboxPolicy(
            workdir=_JOB,
            writable_paths=(_JOB,),
        )
        rec = make_record(
            code="ok",
            tier="unshare",
            argv=["echo", "hi"],
            policy=policy,
            duration_s=0.0123,
            exit_code=0,
            truncated=False,
            pid_actual=42,
        )
        assert rec.code == "ok"
        assert rec.tier == "unshare"
        assert rec.argv_len == 2
        assert rec.exit_code == 0
        assert rec.truncated is False
        assert rec.pid_actual == 42
        assert rec.duration_s == 0.0123  # rounded to 4dp

    def test_argv_hash_is_sha256_hex(self):
        policy = SandboxPolicy(workdir=_J, writable_paths=(_J,))
        rec = make_record(
            code="ok",
            tier="unshare",
            argv=["echo", "hi"],
            policy=policy,
            duration_s=0.01,
            exit_code=0,
            truncated=False,
            pid_actual=42,
        )
        assert len(rec.cmd_sha256) == 64
        assert all(c in "0123456789abcdef" for c in rec.cmd_sha256)

    def test_same_argv_same_hash(self):
        policy = SandboxPolicy(workdir=_J, writable_paths=(_J,))
        a = make_record(code="ok", tier="t", argv=["a", "b"], policy=policy,
                        duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        b = make_record(code="ok", tier="t", argv=["a", "b"], policy=policy,
                        duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        assert a.cmd_sha256 == b.cmd_sha256

    def test_different_argv_different_hash(self):
        policy = SandboxPolicy(workdir=_J, writable_paths=(_J,))
        a = make_record(code="ok", tier="t", argv=["a", "b"], policy=policy,
                        duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        b = make_record(code="ok", tier="t", argv=["a", "c"], policy=policy,
                        duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        assert a.cmd_sha256 != b.cmd_sha256

    def test_policy_hash_changes_with_network_flag(self):
        p1 = SandboxPolicy(workdir=_J, writable_paths=(_J,), network=False)
        p2 = SandboxPolicy(workdir=_J, writable_paths=(_J,), network=True)
        r1 = make_record(code="ok", tier="t", argv=["a"], policy=p1,
                         duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        r2 = make_record(code="ok", tier="t", argv=["a"], policy=p2,
                         duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        assert r1.policy_hash != r2.policy_hash

    def test_policy_hash_stable_under_path_reorder(self):
        # Reordering writable_paths must NOT change the policy hash —
        # the security envelope is the same.
        p1 = SandboxPolicy(workdir=_A, writable_paths=(_A, _B))
        p2 = SandboxPolicy(workdir=_A, writable_paths=(_B, _A))
        r1 = make_record(code="ok", tier="t", argv=["x"], policy=p1,
                         duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        r2 = make_record(code="ok", tier="t", argv=["x"], policy=p2,
                         duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        assert r1.policy_hash == r2.policy_hash

    def test_timestamp_format(self):
        policy = SandboxPolicy(workdir=_J, writable_paths=(_J,))
        rec = make_record(code="ok", tier="t", argv=["x"], policy=policy,
                          duration_s=0, exit_code=0, truncated=False, pid_actual=None)
        # ISO-8601 with microsecond precision and Z suffix.
        assert rec.ts.endswith("Z")
        assert "T" in rec.ts


def _sample_record() -> AuditRecord:
    """Return a deterministic AuditRecord for tests."""
    policy = SandboxPolicy(workdir=_J, writable_paths=(_J,))
    return make_record(
        code="ok",
        tier="unshare",
        argv=["echo", "hi"],
        policy=policy,
        duration_s=0.0123,
        exit_code=0,
        truncated=False,
        pid_actual=42,
    )
