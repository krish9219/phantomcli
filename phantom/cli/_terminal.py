"""Cross-platform terminal-init helpers.

The big one: forcing Windows ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` on
so ANSI escape sequences render as colours instead of literal
``^[[36m`` text. PowerShell 5.x doesn't enable VT mode by default
(PowerShell 7+ and Windows Terminal do); without this, every coloured
prompt and tool icon comes out as garbage.

History:

* v1.1.28: ctypes-only path with no ``argtypes``/``restype`` bindings.
  Silently failed on at least one user host.
* v1.1.29: stacked four strategies (``os.system("")`` → SetConsoleMode
  with proper bindings → colorama → strip wrapper). Treated each call
  as a success signal even though ``os.system("")`` always returns 0
  whether or not it actually enabled VT. So the wrong terminals still
  saw ``^[[36m`` because we never reached the strip fallback.
* v1.1.30: each Windows strategy is now followed by a **read-back
  verification** via ``GetConsoleMode`` — if the VT bit isn't actually
  on after the call, we fall through to the next strategy. Plus we
  honour ``NO_COLOR``/``PHANTOM_NO_COLOR`` and detect non-TTY stdout
  (piped/redirected) and install the strip wrapper unconditionally
  in those cases.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

__all__ = ["ansi_supported", "enable_ansi"]


_INITIALIZED = False
_ANSI_OK = False


def ansi_supported() -> bool:
    """Returns whether the previous ``enable_ansi()`` call succeeded.

    The chat REPL can use this to decide whether to emit coloured
    prompts. Defaults to True on POSIX with a TTY, depends on init
    result on Windows.
    """
    return _ANSI_OK


# ─── Windows VT verification ──────────────────────────────────────────────

def _vt_actually_enabled() -> bool:
    """Read back ``GetConsoleMode`` and check the VT flag is actually
    set on the stdout handle.

    This is the only honest signal that VT mode took effect — every
    "enable" call below can succeed without the change sticking
    (process token issues, redirected output, PowerShell ISE host,
    legacy console host, etc.). v1.1.29 trusted ``os.system("")`` as
    a success signal, which always reports 0 whether or not VT is on.
    v1.1.30 verifies via read-back instead.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL

        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if not handle or handle == INVALID_HANDLE_VALUE:
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        return False


# ─── Strategies (each one is fire-and-forget; the verifier decides) ──────

def _try_os_system_trick() -> None:
    """``os.system("")`` triggers the Windows console host to initialise
    its VT processing mode as a side effect. Cheapest fix; works on
    Win10 1607+ for both PowerShell and cmd.exe. Returns nothing —
    success is decided by the read-back verifier."""
    try:
        os.system("")
    except Exception:
        pass


def _try_setconsolemode() -> None:
    """Native Win32 path with explicit argtypes/restype so ctypes
    interprets the int returns correctly."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        for std_handle_id in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            handle = kernel32.GetStdHandle(std_handle_id)
            if not handle or handle == INVALID_HANDLE_VALUE:
                continue
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


def _try_colorama() -> bool:
    """Colorama wraps stdout/stderr and translates ANSI escapes into
    Win32 console calls. Reliable fallback when read-back keeps reporting
    VT off (e.g., PowerShell ISE, legacy console host).

    Returns True if colorama installed its wrappers — colorama is
    self-verifying since it replaces ``sys.stdout`` directly, so we
    trust the import."""
    try:
        import colorama
        if hasattr(colorama, "just_fix_windows_console"):
            colorama.just_fix_windows_console()
        else:
            colorama.init(autoreset=False, convert=True, strip=False)
        return True
    except Exception:
        return False


# ─── Last-resort ANSI strip wrapper ───────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class _AnsiStrippingStream:
    """Last-resort stdout wrapper that strips ANSI escape sequences.
    Output loses colour but is at least readable instead of garbage."""

    def __init__(self, underlying):
        self._underlying = underlying

    def write(self, s):
        if isinstance(s, str):
            s = _ANSI_RE.sub("", s)
        return self._underlying.write(s)

    def flush(self):
        return self._underlying.flush()

    def isatty(self):
        return getattr(self._underlying, "isatty", lambda: False)()

    def __getattr__(self, name):
        return getattr(self._underlying, name)


def _install_strip_wrapper() -> bool:
    """Install ANSI-stripping wrappers around sys.stdout and sys.stderr.

    Last resort when the host can't render ANSI at all. The output
    won't have colours but it won't have ``^[[36m`` garbage either.
    """
    try:
        if not isinstance(sys.stdout, _AnsiStrippingStream):
            sys.stdout = _AnsiStrippingStream(sys.stdout)
        if not isinstance(sys.stderr, _AnsiStrippingStream):
            sys.stderr = _AnsiStrippingStream(sys.stderr)
        return True
    except Exception:
        return False


# ─── Pre-flight: detect environments where ANSI definitely won't render ──

def _no_color_requested() -> bool:
    """Honour NO_COLOR (https://no-color.org) and PHANTOM_NO_COLOR.

    NO_COLOR being set to *any* value (including "0") disables colour
    by the spec. PHANTOM_NO_COLOR is an app-specific override for users
    who want colour elsewhere but not from phantom."""
    return bool(os.environ.get("NO_COLOR") or os.environ.get("PHANTOM_NO_COLOR"))


def _stdout_is_redirected() -> bool:
    """Stdout is going somewhere that won't render escapes (file, pipe,
    captured by a parent process). In that case, the user will see
    literal ``^[[36m`` if we don't strip."""
    try:
        return not sys.stdout.isatty()
    except Exception:
        return False


def _is_dumb_terminal() -> bool:
    """``TERM=dumb`` is the universal "no escape codes" signal."""
    return os.environ.get("TERM", "").lower() == "dumb"


# ─── Main entry point ─────────────────────────────────────────────────────

def enable_ansi() -> bool:
    """Enable ANSI escape-code rendering, or install a strip wrapper if
    the host can't render them. Idempotent.

    Returns True if colours are expected to render, False if we
    installed the strip wrapper.

    Order:
      1. If user opted out via ``NO_COLOR`` / ``PHANTOM_NO_COLOR``,
         or stdout isn't a TTY, or ``TERM=dumb`` — strip.
      2. POSIX with a TTY: assumed to support ANSI.
      3. Windows: try strategies in order, **verifying after each one**
         via ``GetConsoleMode`` read-back. First one that sticks wins.
      4. If every Windows strategy fails verification: try colorama,
         which wraps stdout to translate escapes itself.
      5. If colorama also unavailable: install strip wrapper.
    """
    global _INITIALIZED, _ANSI_OK
    if _INITIALIZED:
        return _ANSI_OK

    # Pre-flight: explicit opt-out, redirected stdout, or dumb terminal.
    if _no_color_requested() or _stdout_is_redirected() or _is_dumb_terminal():
        _install_strip_wrapper()
        _INITIALIZED = True
        _ANSI_OK = False
        return False

    # POSIX TTY: assumed VT-capable. (xterm/iTerm/Terminal.app/Linux
    # console all support ANSI by default.)
    if os.name != "nt":
        _INITIALIZED = True
        _ANSI_OK = True
        return True

    # Windows: try, then VERIFY. If verification fails, fall through.
    _try_os_system_trick()
    if _vt_actually_enabled():
        _INITIALIZED = True
        _ANSI_OK = True
        return True

    _try_setconsolemode()
    if _vt_actually_enabled():
        _INITIALIZED = True
        _ANSI_OK = True
        return True

    # SetConsoleMode didn't stick (PowerShell ISE, legacy host, …).
    # Try colorama — it wraps stdout to translate escapes in-process,
    # which works even when the console itself can't render VT.
    if _try_colorama():
        _INITIALIZED = True
        _ANSI_OK = True
        return True

    # Final fallback: strip ANSI. Output is monochrome but readable.
    _install_strip_wrapper()
    _INITIALIZED = True
    _ANSI_OK = False
    return False
