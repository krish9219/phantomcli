"""Cross-platform terminal-init helpers.

The big one: forcing Windows ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` on
so ANSI escape sequences render as colours instead of literal
``^[[36m`` text. PowerShell 5.x doesn't enable VT mode by default
(PowerShell 7+ and Windows Terminal do); without this, every coloured
prompt and tool icon comes out as garbage.

The v1.1.28 attempt used ctypes alone and silently failed on at least
one user's host. v1.1.29 stacks four strategies and falls back to
ANSI-stripping if every one fails, so the output is at least readable.

Order of preference:
1. ``os.system("")`` — calls cmd.exe which initialises the console
   as a side effect. Reliable on Windows 10+ and the cheapest path.
2. Native Win32 ``SetConsoleMode`` via ctypes with explicit type
   bindings (the v1.1.28 version skipped argtypes/restype which made
   GetStdHandle return values unreliable).
3. Colorama's ``just_fix_windows_console`` — handles edge cases the
   first two miss (e.g., redirected stdout to a pipe).
4. Last resort: install a stdout/stderr wrapper that strips ANSI
   escape sequences. Output looks plain but is at least not garbage.
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
    prompts. Defaults to True on POSIX, depends on init result on
    Windows.
    """
    return _ANSI_OK


def _try_os_system_trick() -> bool:
    """`os.system("")` triggers the Windows console host to initialise
    its VT processing mode as a side effect. Cheapest fix; works on
    Win10 1607+ for both PowerShell and cmd.exe."""
    try:
        os.system("")
        return True
    except Exception:
        return False


def _try_setconsolemode() -> bool:
    """Native Win32 path with explicit argtypes/restype so ctypes
    interprets the int returns correctly. v1.1.28 omitted these."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        # CRITICAL: explicit type bindings. Without these, GetStdHandle
        # may return Python int that doesn't compare correctly with
        # INVALID_HANDLE_VALUE (-1).
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

        any_success = False
        for std_handle_id in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            handle = kernel32.GetStdHandle(std_handle_id)
            if not handle or handle == INVALID_HANDLE_VALUE:
                continue
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if kernel32.SetConsoleMode(handle, new_mode):
                any_success = True
        return any_success
    except Exception:
        return False


def _try_colorama() -> bool:
    """Colorama wraps stdout/stderr and translates ANSI escapes into
    Win32 console calls. Most reliable fallback when the above two fail."""
    try:
        import colorama
        if hasattr(colorama, "just_fix_windows_console"):
            colorama.just_fix_windows_console()
        else:
            colorama.init(autoreset=False, convert=True, strip=False)
        return True
    except Exception:
        return False


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


def enable_ansi() -> bool:
    """Enable ANSI escape-code rendering. Returns True if colours are
    expected to render natively, False if we fell back to stripping.

    Strategies attempted in order:
      1. ``os.system("")`` — cheapest Windows VT init.
      2. ``SetConsoleMode`` via ctypes (with proper type bindings).
      3. Colorama wrapper.
      4. ANSI-stripping stdout/stderr wrappers.

    Idempotent.
    """
    global _INITIALIZED, _ANSI_OK
    if _INITIALIZED:
        return _ANSI_OK

    if os.name != "nt":
        _INITIALIZED = True
        _ANSI_OK = True
        return True

    # Try native paths in order. Each one is a no-op-on-failure so
    # stacking them is safe.
    success = False
    if _try_os_system_trick():
        success = True
    # Even if (1) reports ok, run (2) too — belt and braces.
    if _try_setconsolemode():
        success = True
    if not success:
        if _try_colorama():
            success = True
    if not success:
        # Final fallback: strip ANSI. Output is monochrome but readable.
        _install_strip_wrapper()
        success = False  # we fell back; caller may want to know

    _INITIALIZED = True
    _ANSI_OK = success
    return success
