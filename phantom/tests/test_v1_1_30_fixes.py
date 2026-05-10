"""Tests for v1.1.30 — the Windows ANSI rendering issue v1.1.29 didn't
actually fix.

Root cause: ``_try_os_system_trick()`` always returns truthy because
``os.system("")`` runs successfully whether or not VT mode got enabled.
v1.1.29 treated that as a success signal, set ``_ANSI_OK = True``, and
never reached the strip fallback. Users on PowerShell 5.x / legacy
console hosts kept seeing literal ``^[[36m``.

v1.1.30:
  * Each Windows strategy is followed by a ``GetConsoleMode`` read-back
    that checks the VT flag is actually on. If not, fall through.
  * Honour ``NO_COLOR`` / ``PHANTOM_NO_COLOR`` env vars and ``TERM=dumb``.
  * Detect non-TTY stdout (piped/redirected output) and install the
    strip wrapper so escape codes don't leak into log files.
"""

from __future__ import annotations

import sys

import pytest


def _isolate(monkeypatch):
    """Reset terminal-init state so each test drives the function from
    a clean slate."""
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    monkeypatch.setattr("phantom.cli._terminal._ANSI_OK", False)


def _no_preflight(monkeypatch):
    """Disable the pre-flight checks so the test drives the Windows
    strategy path without env-var or TTY interference."""
    monkeypatch.setattr("phantom.cli._terminal._no_color_requested", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._stdout_is_redirected", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._is_dumb_terminal", lambda: False)


# ─── Pre-flight: NO_COLOR / non-TTY / dumb terminal ───────────────────────

def test_no_color_env_var_installs_strip_wrapper(monkeypatch):
    """https://no-color.org — any value of NO_COLOR (including empty
    string per the spec, but we accept truthy here) means 'no colour'.
    We install the strip wrapper so any escapes phantom emits get
    sanitised before reaching the user's terminal."""
    _isolate(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                        lambda: installed.append(True) or True)
    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is False
    assert installed == [True]


def test_phantom_no_color_env_var_installs_strip_wrapper(monkeypatch):
    """PHANTOM_NO_COLOR is an app-specific override — works the same
    way as NO_COLOR but affects only phantom."""
    _isolate(monkeypatch)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("PHANTOM_NO_COLOR", "1")
    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                        lambda: installed.append(True) or True)
    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is False
    assert installed == [True]


def test_redirected_stdout_installs_strip_wrapper(monkeypatch):
    """When stdout is going to a file/pipe (not a TTY), escape codes
    leak as literal text. Strip them so logs are readable."""
    _isolate(monkeypatch)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PHANTOM_NO_COLOR", raising=False)
    # Force isatty() False on stdout.
    class _FakeStdout:
        def isatty(self): return False
        def write(self, s): pass
        def flush(self): pass
    monkeypatch.setattr("sys.stdout", _FakeStdout())
    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                        lambda: installed.append(True) or True)
    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is False
    assert installed == [True]


def test_dumb_terminal_installs_strip_wrapper(monkeypatch):
    """TERM=dumb is the universal "no escape codes" signal."""
    _isolate(monkeypatch)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PHANTOM_NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                        lambda: installed.append(True) or True)
    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is False
    assert installed == [True]


# ─── VT verification: read-back via GetConsoleMode ────────────────────────

def test_vt_actually_enabled_returns_true_on_posix(monkeypatch):
    """The verifier short-circuits on POSIX since there's nothing to
    verify — the assumption (TTY-attached) was already checked by
    the pre-flight."""
    monkeypatch.setattr("os.name", "posix")
    from phantom.cli._terminal import _vt_actually_enabled
    assert _vt_actually_enabled() is True


def test_setconsolemode_path_used_when_os_system_doesnt_enable_vt(monkeypatch):
    """v1.1.29's bug: os.system("") returns 0 even when it doesn't
    enable VT, so the function set _ANSI_OK = True and stopped there.
    v1.1.30 verifies via GetConsoleMode read-back — first call returns
    False (os.system trick didn't work), so the function continues to
    SetConsoleMode."""
    _isolate(monkeypatch)
    _no_preflight(monkeypatch)
    monkeypatch.setattr("os.name", "nt")

    verify_results = [False, True]  # os.system fails verify, SetConsoleMode succeeds
    def fake_verify():
        return verify_results.pop(0) if verify_results else True
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", fake_verify)

    setcm_called = []
    monkeypatch.setattr("phantom.cli._terminal._try_setconsolemode",
                        lambda: setcm_called.append(True))

    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is True
    assert setcm_called == [True]  # SetConsoleMode WAS reached because os.system didn't verify


def test_strip_fallback_when_verification_keeps_failing(monkeypatch):
    """Repro of the user-reported v1.1.29 bug: every Windows attempt
    "succeeds" (no exception) but VT mode never actually engages.
    v1.1.30 verifies, sees False, and installs the strip wrapper.

    This is the exact failure path the v1.1.29 user hit — PowerShell
    5.x with redirected console buffers where SetConsoleMode silently
    has no effect on the calling process."""
    _isolate(monkeypatch)
    _no_preflight(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    # Verifier always says "VT is off" — simulating the user's
    # PowerShell 5.x environment where SetConsoleMode doesn't stick.
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", lambda: False)

    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                        lambda: installed.append(True) or True)

    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is False
    assert installed == [True], (
        "v1.1.29 regression — when VT can't actually be enabled, "
        "the strip wrapper MUST install or the user sees ^[[36m"
    )


def test_colorama_used_when_setconsolemode_doesnt_stick(monkeypatch):
    """If GetConsoleMode read-back keeps reporting False, colorama is
    the next line of defence (it wraps stdout to translate escapes
    in-process, working even when the console can't render VT)."""
    _isolate(monkeypatch)
    _no_preflight(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)
    colorama_called = []
    monkeypatch.setattr("phantom.cli._terminal._try_colorama",
                        lambda: colorama_called.append(True) or True)

    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is True
    assert colorama_called == [True]


# ─── Strategy functions return None now (no spurious success signal) ──────

def test_try_os_system_trick_returns_none():
    """v1.1.29 returned True from os.system success; v1.1.30 returns
    None — the verifier is the only honest signal."""
    from phantom.cli._terminal import _try_os_system_trick
    assert _try_os_system_trick() is None


def test_try_setconsolemode_returns_none():
    """Same as above — fire-and-forget, verifier decides success."""
    from phantom.cli._terminal import _try_setconsolemode
    assert _try_setconsolemode() is None


# ─── Idempotency still works after the rewrite ────────────────────────────

def test_enable_ansi_is_idempotent_after_strip_install(monkeypatch):
    """Once we've installed the strip wrapper, subsequent calls must
    be a no-op (don't double-wrap stdout, don't re-run strategies)."""
    _isolate(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    _no_preflight(monkeypatch)
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", lambda: False)

    install_count = {"n": 0}
    def fake_install():
        install_count["n"] += 1
        return True
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper", fake_install)

    from phantom.cli._terminal import enable_ansi
    rc1 = enable_ansi()
    rc2 = enable_ansi()
    assert rc1 == rc2 == False
    # Strip wrapper installed exactly once across two calls.
    assert install_count["n"] == 1
