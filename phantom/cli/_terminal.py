"""Cross-platform terminal-init helpers.

The big one: forcing Windows ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` on
so ANSI escape sequences render as colours instead of literal
``^[[36m`` text. PowerShell 5.x doesn't enable VT mode by default
(PowerShell 7+ and Windows Terminal do); without this, every coloured
prompt and tool icon comes out as garbage.

Order of preference:
1. Native Win32 ``SetConsoleMode`` via ctypes — zero deps, fastest.
2. Colorama's ``just_fix_windows_console`` — fallback if (1) fails.
3. Silent no-op on POSIX or older Pythons.
"""

from __future__ import annotations

import os
import sys

__all__ = ["enable_ansi"]


_INITIALIZED = False


def enable_ansi() -> bool:
    """Enable ANSI escape-code rendering on the current process's stdout.

    Returns True when colours are now expected to render, False on
    failure (the caller can decide to skip styling).

    Idempotent — calling more than once is safe.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return True

    if os.name != "nt":
        # POSIX terminals already handle ANSI by default.
        _INITIALIZED = True
        return True

    # Try native Win32 first — no extra dependency.
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        for std_handle_id in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            handle = kernel32.GetStdHandle(std_handle_id)
            if handle in (0, ctypes.c_void_p(-1).value):
                continue
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if not kernel32.SetConsoleMode(handle, new_mode):
                continue
        _INITIALIZED = True
        return True
    except Exception:
        pass

    # Fallback: colorama. May or may not be installed.
    try:
        import colorama
        if hasattr(colorama, "just_fix_windows_console"):
            colorama.just_fix_windows_console()
        else:
            colorama.init(autoreset=False, convert=True, strip=False)
        _INITIALIZED = True
        return True
    except Exception:
        return False
