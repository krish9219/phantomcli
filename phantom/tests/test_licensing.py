"""Tests for phantom/licensing/ — state machine, cache integrity, gates."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Run with PHANTOM_HOME pointed at a fresh tmp dir; reload the module."""
    home = tmp_path / "phantom_home"
    monkeypatch.setenv("PHANTOM_HOME", str(home))
    # Bypass any network call by default.
    monkeypatch.setattr("urllib.request.urlopen", _raise_network)

    import importlib

    import phantom.licensing as licensing
    importlib.reload(licensing)
    return licensing, home


def _raise_network(*args, **kwargs):
    raise OSError("network disabled in test")


def test_key_pattern_matches_only_hex():
    from phantom.licensing import KEY_PATTERN
    assert KEY_PATTERN.match("PHC-DEADBEEF-CAFE0123-FACE4567")
    assert KEY_PATTERN.match("phc-deadbeef-cafe0123-face4567")  # case-insensitive
    assert not KEY_PATTERN.match("PHC-TESTAAAA-BBBBBBBB-CCCCCCCC")  # 'T','S' not hex
    assert not KEY_PATTERN.match("PHX-DEADBEEF-CAFE0123-FACE4567")  # wrong prefix
    assert not KEY_PATTERN.match("PHC-DEADBEEF-CAFE0123")           # too few segments


def test_first_run_starts_trial(isolated_home):
    licensing, home = isolated_home
    s = licensing.license_status()
    assert s.tier == "trial"
    assert s.days_remaining == licensing.TRIAL_DAYS
    # Subsequent calls should not reset the timer.
    s2 = licensing.license_status()
    assert s2.days_remaining == licensing.TRIAL_DAYS


def test_trial_expires_after_14_days(isolated_home):
    licensing, home = isolated_home
    licensing.license_status()  # initialise
    state = licensing._load_state()
    state["trial_start"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    licensing._save_state(state)
    s = licensing.license_status()
    assert s.tier == "free"
    assert s.reason == "trial_expired"


def test_trial_remaining_counts_down(isolated_home):
    licensing, home = isolated_home
    licensing.license_status()
    state = licensing._load_state()
    state["trial_start"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    licensing._save_state(state)
    s = licensing.license_status()
    assert s.tier == "trial"
    assert s.days_remaining == 4


def test_grandfather_detection_pre_gate_install(isolated_home):
    """Files older than 1 hour in ~/.phantom => pre-gate user, grandfather as Pro."""
    licensing, home = isolated_home
    home.mkdir(parents=True, exist_ok=True)
    old_file = home / "sessions" / "old.log"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text("legacy")
    old_ts = time.time() - 7200  # 2 hours ago
    os.utime(old_file, (old_ts, old_ts))

    s = licensing.license_status()
    assert s.tier == "pro"
    assert s.reason == "grandfathered"

    # Persists across calls.
    s2 = licensing.license_status()
    assert s2.tier == "pro"
    assert s2.reason == "grandfathered"


def test_fresh_install_does_not_grandfather(isolated_home):
    licensing, home = isolated_home
    home.mkdir(parents=True, exist_ok=True)
    fresh = home / "fresh.txt"
    fresh.write_text("just made")  # mtime = now → not pre-gate

    s = licensing.license_status()
    assert s.tier == "trial"
    assert s.reason == "trial_started"


def test_corrupt_cache_falls_back_cleanly(isolated_home):
    licensing, home = isolated_home
    home.mkdir(parents=True, exist_ok=True)
    (home / ".license").write_bytes(b"this is not valid fernet ciphertext")
    # Should not raise — corrupt file is treated as empty.
    s = licensing.license_status()
    assert s.tier in {"trial", "pro"}


def test_cache_machine_bound(isolated_home, tmp_path):
    """Fernet key derives from machine_key seed XOR hardware fingerprint.
    Copying the .license file alone (without the seed) yields decrypt failure."""
    licensing, home = isolated_home
    licensing.license_status()
    state = licensing._load_state()
    state["key"] = "PHC-DEADBEEF-CAFE0123-FACE4567"
    state["validated_at"] = datetime.now(timezone.utc).isoformat()
    licensing._save_state(state)

    enc_blob = (home / ".license").read_bytes()
    other_home = tmp_path / "other_home"
    other_home.mkdir()
    (other_home / ".license").write_bytes(enc_blob)
    # Without the seed, the other home will derive a different key from the same
    # hardware fingerprint, so decryption fails (as expected).
    import os as _os
    saved = _os.environ.get("PHANTOM_HOME")
    _os.environ["PHANTOM_HOME"] = str(other_home)
    try:
        import importlib
        import phantom.licensing as relic
        importlib.reload(relic)
        # The seed in the new home is freshly generated, so the cipher
        # written under the original seed cannot decrypt — _load_state
        # should return {} not raise.
        loaded = relic._load_state()
        assert loaded == {}
    finally:
        if saved is not None:
            _os.environ["PHANTOM_HOME"] = saved


def test_require_pro_passes_when_pro(isolated_home):
    licensing, home = isolated_home
    home.mkdir(parents=True, exist_ok=True)
    state = licensing._load_state()
    state["grandfathered"] = True
    state["validated_at"] = datetime.now(timezone.utc).isoformat()
    licensing._save_state(state)

    s = licensing.require_pro("test-feature")
    assert s.tier == "pro"


def test_require_pro_exits_when_free(isolated_home):
    licensing, home = isolated_home
    licensing.license_status()
    state = licensing._load_state()
    state["trial_start"] = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    licensing._save_state(state)

    with pytest.raises(SystemExit) as ei:
        licensing.require_pro("daemon")
    assert ei.value.code == 1


def test_activate_rejects_invalid_format(isolated_home):
    licensing, home = isolated_home
    with pytest.raises(ValueError, match="invalid key format"):
        licensing.activate("NOT-A-KEY")


def test_device_id_is_stable_per_machine(isolated_home):
    licensing, _ = isolated_home
    a = licensing.device_id()
    b = licensing.device_id()
    assert a == b
    assert len(a) == 32
    assert all(c in "0123456789abcdef" for c in a)
