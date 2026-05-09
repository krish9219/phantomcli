"""JARVIS-style boot animation + onboarding for ``phantom chat``.

The banner runs the first time chat starts in a session — it identifies
the host, lists key system resources, and (on a clean install) prompts
the user for an assistant name + their name + a workspace path. The
profile is persisted at ``$PHANTOM_HOME/profile.json`` so subsequent
boots skip the questions.

Animation is intentionally cheap: a few sequenced ANSI lines with short
delays. Auto-disabled when stdout isn't a TTY (CI, piped input).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable

from phantom._version import __version__
from phantom.cli.sysinfo import SystemInfo, collect_system_info
from phantom.profile import (
    Profile,
    default_workspace_hint,
    load_profile,
    save_profile,
)

__all__ = ["onboard_if_needed", "render_boot_banner"]


# ANSI helpers — same palette as the spinner and chat prompts.
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_LOGO_LINES = (
    "  ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗",
    "  ██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║",
    "  ██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║",
    "  ██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║",
    "  ██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║",
    "  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝",
)


def _is_tty(write_target) -> bool:
    """We animate only when the underlying stream is a TTY. Tests and CI use
    a list/buffer ``write_target`` and want zero-delay output."""
    target = write_target
    if hasattr(target, "__self__"):
        # bound method like sys.stdout.write — get the stream
        target = target.__self__
    return getattr(target, "isatty", lambda: False)()


def render_boot_banner(
    *,
    write: Callable[[str], None],
    profile: Profile,
    system: SystemInfo,
    animate: bool = True,
) -> None:
    """Print the cyan logo + system snapshot + greeting.

    On a TTY each line draws after a short pause (~30ms) so it feels
    like a system coming online. On a non-TTY we skip the delays.
    """

    delay = 0.03 if animate else 0.0

    write("\n")
    for line in _LOGO_LINES:
        write(f"{_CYAN}{line}{_RESET}\n")
        if delay:
            time.sleep(delay)

    name = profile.assistant_name or "Phantom"
    write(f"{_DIM}  {name} v{__version__}  ·  Aravind Labs  ·  https://phantom.aravindlabs.tech{_RESET}\n\n")
    if delay:
        time.sleep(delay * 4)

    # ─── system snapshot ──────────────────────────────────────────────
    bar = f"{_DIM}  ─────────────────────────────────────────────{_RESET}"
    write(bar + "\n")
    write(f"  {_DIM}host       :{_RESET} {system.hostname}\n")
    write(f"  {_DIM}os         :{_RESET} {system.os_name} {system.os_release}\n")
    write(f"  {_DIM}cpu        :{_RESET} {system.cpu} {_DIM}({system.cpu_count} cores){_RESET}\n")
    if system.ram_total_gb > 0:
        write(
            f"  {_DIM}ram        :{_RESET} {system.ram_free_gb:.1f} GB free "
            f"{_DIM}of {system.ram_total_gb:.1f} GB{_RESET}\n"
        )
    if system.disk_total_gb > 0:
        write(
            f"  {_DIM}disk       :{_RESET} {system.disk_free_gb:.1f} GB free "
            f"{_DIM}of {system.disk_total_gb:.1f} GB{_RESET}\n"
        )
    if profile.workspace_path:
        write(f"  {_DIM}workspace  :{_RESET} {profile.workspace_path}\n")
    write(bar + "\n\n")

    # ─── greeting ─────────────────────────────────────────────────────
    if profile.user_name:
        greet = (
            f"  {_GREEN}●{_RESET} {_BOLD}Welcome back, {profile.user_name}.{_RESET} "
            f"{_DIM}{name} is online and ready.{_RESET}\n"
        )
    else:
        greet = (
            f"  {_GREEN}●{_RESET} {_BOLD}{name} is online and ready.{_RESET}\n"
        )
    write(greet)
    if profile.god_mode:
        write(f"  {_YELLOW}⚡ god-mode active{_RESET}\n")
    write(f"  {_DIM}Type {_RESET}/help{_DIM} for slash commands, {_RESET}/exit{_DIM} to quit.{_RESET}\n\n")


def onboard_if_needed(
    *,
    write: Callable[[str], None],
    read_line: Callable[[str], str],
) -> Profile:
    """If profile.json is missing or incomplete, ask the user for the
    onboarding fields. Returns the (possibly newly-saved) profile.

    Idempotent: a profile that already has all three fields is returned
    untouched. Cancelling (EOF, Ctrl+C) saves a partial profile so the
    user isn't asked the same question twice in a row — but the loader
    will re-prompt on next chat for any still-empty field.
    """
    profile = load_profile()
    if profile.is_complete():
        return profile

    write(f"\n  {_CYAN}First-run setup{_RESET}\n")
    write(f"  {_DIM}Phantom will ask three quick questions, then it's ready.{_RESET}\n\n")

    if not profile.assistant_name or profile.assistant_name == "Phantom":
        try:
            answer = read_line(
                f"  what should I call myself? {_DIM}[Phantom]{_RESET} "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        profile.assistant_name = answer or "Phantom"

    if not profile.user_name:
        try:
            answer = read_line("  and what should I call you? ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        profile.user_name = answer

    if not profile.workspace_path:
        suggested = default_workspace_hint()
        try:
            answer = read_line(
                f"  where should I create projects? {_DIM}[{suggested}]{_RESET} "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        chosen = answer or suggested
        # Expand ~, normalise, create if missing.
        chosen = os.path.abspath(os.path.expanduser(chosen))
        try:
            Path(chosen).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        profile.workspace_path = chosen

    save_profile(profile)
    write(f"\n  {_GREEN}✓{_RESET} saved to {_DIM}{Path(os.environ.get('PHANTOM_HOME') or '~/.phantom') / 'profile.json'}{_RESET}\n")
    return profile
