"""Smoke tests for the PyInstaller-built `phantom` binary.

These tests skip cleanly if the binary hasn't been built. CI builds the
binary in a separate job; locally, run ``pyinstaller --clean
--noconfirm phantomcli.spec`` once and the tests light up.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# PyInstaller appends ``.exe`` on Windows; the smoke tests need to look
# for whichever form actually got built.
_BIN_NAME = "phantom.exe" if sys.platform == "win32" else "phantom"
BIN = REPO / "dist" / _BIN_NAME


pytestmark = pytest.mark.skipif(
    not BIN.exists(),
    reason="binary not built (run: pyinstaller --clean --noconfirm phantomcli.spec)",
)


def _run(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BIN), *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_binary_is_executable():
    if sys.platform == "win32":
        # Windows uses file extensions (.exe) to determine executability,
        # not POSIX mode bits. The presence of the .exe suffix and the
        # fact that BIN exists is the executability check on Windows.
        assert BIN.suffix.lower() == ".exe"
        return
    st = os.stat(BIN)
    assert st.st_mode & 0o111, "binary is not executable"


def test_binary_version_matches_package():
    from phantom._version import __version__
    res = _run(["version"])
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == __version__


def test_binary_help_lists_v1_commands():
    res = _run(["--help"])
    assert res.returncode == 0, res.stderr
    output = res.stdout
    for cmd in ("version", "doctor", "bench", "swarm", "self-dev",
                "serve", "connect", "dictate", "memory", "config",
                "plugin", "mcp"):
        assert cmd in output, f"binary --help missing command: {cmd}"


def test_binary_doctor_runs():
    res = _run(["doctor", "--json"])
    # exit 0 (sandbox available) or 1 (no backend) both fine — we only
    # assert the command parses and produces JSON.
    assert res.stdout.strip().startswith("{") or res.stdout.strip().startswith("[")


def test_binary_bench_returns_v1():
    res = _run(["bench", "--turns", "10", "--json"], timeout=60)
    assert res.returncode == 0, res.stderr
    import json as _json
    payload = _json.loads(res.stdout)
    assert payload["version"] == "1.0.0"
    assert payload["cold_start_ms"] > 0
    assert payload["daemon_start_ms"] >= 0


def test_binary_cold_start_under_2s():
    """The binary's `version` round-trip must beat 2 s — far above our
    real target (the daemon path) but a sanity guard against PyInstaller
    regressions like UPX being re-enabled."""
    t0 = time.perf_counter()
    res = _run(["version"])
    elapsed = time.perf_counter() - t0
    assert res.returncode == 0
    assert elapsed < 2.0, f"binary cold start took {elapsed:.2f}s (regression?)"


def test_binary_unknown_subcommand_exits_nonzero():
    res = _run(["this-command-does-not-exist"])
    assert res.returncode != 0


def test_binary_size_reasonable():
    """Sanity guard: binary should be 20-300 MB. <20 MB means data
    deps were dropped; >300 MB means we accidentally bundled torch."""
    size_mb = BIN.stat().st_size / (1024 * 1024)
    assert 20 < size_mb < 300, f"binary is {size_mb:.0f} MB — outside [20, 300]"
