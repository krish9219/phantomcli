"""
PhantomCLI TUI — JARVIS-style sci-fi terminal UI
All visual primitives live here. Import and call; never print directly.
"""

import os
import sys
import time
import random
import platform
import shutil
import threading
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich import box

console = Console()


def _enable_windows_vt_mode() -> None:
    """Force Windows console (cmd.exe / conhost / PyInstaller stdio) into
    Virtual Terminal mode so escape sequences like \\033[F (cursor up + BOL)
    and \\033[J (clear to end of screen) actually work.

    Windows Terminal already enables this; legacy conhost and PyInstaller
    bundles do NOT — which is why the live agent panel was leaving stale
    headers stacked on Windows. Safe no-op on non-Windows or if the call
    fails (e.g. stdout redirected to a file)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_PROCESSED_OUTPUT             = 0x0001
        ENABLE_VIRTUAL_TERMINAL_PROCESSING  = 0x0004
        h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if h and h != ctypes.c_void_p(-1).value:
            mode = ctypes.c_uint32(0)
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(
                    h,
                    mode.value | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING,
                )
    except Exception:
        pass


_enable_windows_vt_mode()

# ── Colour palette ────────────────────────────────────────────────────────────
CY   = "cyan"           # primary
BLU  = "bright_blue"    # secondary
GRN  = "bright_green"   # success / online
AMB  = "yellow"         # warnings
RED  = "bright_red"     # errors / danger
DIM  = "bright_black"   # muted / metadata
WHT  = "bright_white"   # emphasis

FIRST_RUN_FLAG   = os.path.join(os.path.expanduser("~/.omnicli"), ".welcomed")
TERMS_FLAG       = os.path.join(os.path.expanduser("~/.omnicli"), ".terms_accepted")
GOD_MODE_FLAG    = os.path.join(os.path.expanduser("~/.omnicli"), ".godmode")

BANNER_FULL = r"""
  ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗
  ██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║
  ██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
  ██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
  ██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝"""

_WIDTH = 68  # inner HUD width


def _is_first_run() -> bool:
    return not os.path.exists(FIRST_RUN_FLAG)


def _mark_welcomed():
    os.makedirs(os.path.dirname(FIRST_RUN_FLAG), exist_ok=True)
    open(FIRST_RUN_FLAG, "w").close()


def boot_screen(version: str, licensed: bool = False):
    """Full sci-fi boot sequence. Full art first run, compact header every time."""
    from omnicli import __version__

    if _is_first_run():
        _first_run_boot(version, licensed)
        _mark_welcomed()
    else:
        _compact_header(version, licensed)


def _hud_line(text: str = "", colour: str = DIM) -> str:
    """Returns a full-width HUD inner line padded to _WIDTH."""
    padded = f"  {text}"
    pad = _WIDTH - len(text)
    return f"[{colour}]║[/{colour}]  [{colour}]{text}[/{colour}]{' ' * max(0, pad - 2)}[{colour}]║[/{colour}]"

def _hud_top(title: str = "") -> None:
    if title:
        bar = f"╔══[ {title} ]"
        bar += "═" * max(0, _WIDTH - len(bar) + 2) + "╗"
    else:
        bar = "╔" + "═" * (_WIDTH + 2) + "╗"
    console.print(f"  [{CY}]{bar}[/{CY}]")

def _hud_sep(label: str = "") -> None:
    if label:
        bar = f"╠══[ {label} ]"
        bar += "═" * max(0, _WIDTH - len(bar) + 2) + "╣"
    else:
        bar = "╠" + "═" * (_WIDTH + 2) + "╣"
    console.print(f"  [{DIM}]{bar}[/{DIM}]")

def _hud_bot() -> None:
    console.print(f"  [{CY}]╚{'═' * (_WIDTH + 2)}╝[/{CY}]")

def _hud_row(label: str, value: str, v_colour: str = CY) -> None:
    label_part = f"{label:<18}"
    value_part = value
    inner = f"{label_part}  {value_part}"
    pad = _WIDTH - len(f"  {label_part}  {value_part}") + 2
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[2m{label_part}\033[0m  "
        f"\033[{'32' if v_colour == GRN else '33' if v_colour == AMB else '31' if v_colour == RED else '36'}m{value_part}\033[0m"
        f"{' ' * max(0, pad)}\033[36m║\033[0m\n"
    )
    sys.stdout.flush()


def _typewriter(text: str, colour_code: str = "96", delay: float = 0.018) -> None:
    for ch in text:
        sys.stdout.write(f"\033[{colour_code}m{ch}\033[0m")
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _scan_bar(label: str, steps: int = 20, delay: float = 0.03) -> None:
    """Animated progress bar that fills across the terminal."""
    bar_width = 36
    for i in range(steps + 1):
        filled = int(bar_width * i / steps)
        bar = "█" * filled + "░" * (bar_width - filled)
        pct = int(100 * i / steps)
        sys.stdout.write(
            f"\r  \033[2m{label:<18}\033[0m  \033[36m[{bar}]\033[0m  \033[2m{pct:>3}%\033[0m"
        )
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()


def _glitch_flash(lines: int = 3, duration: float = 0.25) -> None:
    """Brief glitch effect — rapid noise lines."""
    import string
    chars = string.ascii_uppercase + "░▒▓█▄▀■□◆◇"
    end_t = time.time() + duration
    while time.time() < end_t:
        noise = "".join(random.choices(chars, k=random.randint(30, 65)))
        sys.stdout.write(f"\r  \033[2m{noise}\033[0m")
        sys.stdout.flush()
        time.sleep(0.04)
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()


def _first_run_boot(version: str, licensed: bool):
    """One-time full JARVIS-style boot sequence."""
    os.system("cls" if os.name == "nt" else "clear")
    time.sleep(0.1)

    # ── Glitch intro
    _glitch_flash(duration=0.3)

    # ── Banner with typewriter
    banner_lines = BANNER_FULL.strip("\n").split("\n")
    for line in banner_lines:
        sys.stdout.write(f"  \033[36m{line}\033[0m\n")
        sys.stdout.flush()
        time.sleep(0.03)

    console.print()
    _typewriter(
        f"  ◈  GOD MODE AI OS  ·  v{version}  ·  ARAVIND LABS  ·  SYSTEM BOOT",
        colour_code="36", delay=0.015,
    )
    console.print()

    # ── Scan bar
    _scan_bar("LOADING KERNEL", steps=22, delay=0.025)

    # ── HUD diagnostics
    _hud_top("PHANTOM NEURAL INTERFACE")
    time.sleep(0.08)

    modules = [
        ("KERNEL",        "PhantomOS 2.0 · Secure Kernel",         GRN),
        ("NEURAL ENGINE", "Main + Router AI loaded",                GRN),
        ("MEMORY CORE",   "Episodic · Semantic · Personal",         GRN),
        ("EXECUTOR",      "Bash · Browser · Git · Media APIs",      GRN),
        ("DEVICE LOCK",   "Fingerprint gate active",                GRN),
        ("WEB SEARCH",    "DuckDuckGo · Brave · Tavily fallback",   GRN),
        ("TELEGRAM",      "Polling thread armed",                   GRN if licensed else AMB),
        ("LICENSE",       "ACTIVE" if licensed else "ACTIVATE WITH: python run.py setup",
                          GRN if licensed else AMB),
    ]
    for label, value, col in modules:
        sys.stdout.write(
            f"  \033[36m║\033[0m  \033[2m{label:<18}\033[0m  "
            f"\033[{'32' if col == GRN else '33'}m▶  {value}\033[0m\n"
        )
        sys.stdout.flush()
        time.sleep(0.07)

    _hud_sep()

    lic_badge = "\033[32m● LICENSED\033[0m" if licensed else "\033[33m○ UNLICENSED\033[0m"
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[2mSTATUS\033[0m            "
        f"  \033[32mSYSTEM ONLINE\033[0m   \033[2m·\033[0m  {lic_badge}\n"
    )
    sys.stdout.flush()
    _hud_bot()
    console.print()
    _typewriter("  ◈  ALL SYSTEMS NOMINAL. PHANTOM IS READY.", colour_code="32", delay=0.02)
    console.print()


def _compact_header(version: str, licensed: bool):
    """Compact HUD header shown on every subsequent launch — brief cinematic intro."""
    bar_len = _WIDTH + 4

    # ── Quick scanline sweep (≈ 300 ms) — signals "system online" animation
    sys.stdout.write("\n")
    sweep_chars = "─" * bar_len
    for i in range(0, bar_len + 1, 6):
        sys.stdout.write(f"\r  \033[36m{sweep_chars[:i]}\033[0m\033[2m{sweep_chars[i:]}\033[0m")
        sys.stdout.flush()
        time.sleep(0.012)
    sys.stdout.write("\r" + " " * (bar_len + 4) + "\r")
    sys.stdout.flush()

    # ── Top border types in left-to-right
    sys.stdout.write(f"  \033[36m╔\033[0m")
    for i in range(bar_len):
        sys.stdout.write("\033[36m═\033[0m")
        if i % 10 == 0:
            sys.stdout.flush(); time.sleep(0.004)
    sys.stdout.write("\033[36m╗\033[0m\n")

    # ── Title line with pulsing licence dot
    lic_dot   = "\033[32m●\033[0m" if licensed else "\033[33m○\033[0m"
    lic_text  = "LICENSED" if licensed else "UNLICENSED"
    lic_col   = "32" if licensed else "33"
    inner     = f"  💀 PHANTOM CLI  v{version} · ARAVIND LABS  ·  {lic_text}"
    pad       = bar_len - len(inner) - 1
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[1;36m💀 PHANTOM CLI\033[0m"
        f"  \033[2mv{version} · ARAVIND LABS\033[0m"
        f"  \033[2m·\033[0m  {lic_dot} \033[{lic_col}m{lic_text}\033[0m"
        f"{' ' * max(0, pad)}\033[36m║\033[0m\n"
    )
    sys.stdout.write(f"  \033[36m╚{'═' * bar_len}╝\033[0m\n\n")
    sys.stdout.flush()
    time.sleep(0.08)


# Probe the static parts (CPU model, OS name, GPU, total RAM) once per process.
# Live parts (free RAM, free disk, cwd) refresh on every welcome.
_STATIC_SNAP = None


def _live_system_snapshot() -> dict:
    """Probe real system info live — never trust stale DB cache.

    Static parts (CPU model, OS, GPU, total RAM) are probed once per process via
    sysinfo.detect_system() which already runs the right OS commands
    (PowerShell on Windows, /proc on Linux, sysctl on macOS).
    Live parts (free RAM, disk, cwd) are re-read on every call.
    """
    global _STATIC_SNAP
    if _STATIC_SNAP is None:
        try:
            from omnicli.sysinfo import detect_system
            _STATIC_SNAP = detect_system()
        except Exception:
            _STATIC_SNAP = {}

    info = _STATIC_SNAP
    snap = {
        "os":       info.get("distro") or info.get("os") or "Unknown",
        "arch":     info.get("arch", ""),
        "cpu":      info.get("cpu_model", "CPU"),
        "cores":    info.get("cpu_cores", "?"),
        "gpu":      (info.get("gpu") or "").strip(),
        "host":     info.get("hostname") or platform.node(),
        "py":       info.get("python", platform.python_version()),
    }
    if snap["gpu"].lower() in ("none", ""):
        snap["gpu"] = ""

    # Live free RAM — uses correct probe per OS (PowerShell on Win11, /proc on Linux)
    snap["ram_free_gb"] = snap["ram_total_gb"] = snap["ram_pct"] = None
    try:
        from omnicli.sysinfo import live_ram
        free_gb, total_gb, pct = live_ram(info.get("os") or platform.system())
        snap["ram_free_gb"]  = free_gb
        snap["ram_total_gb"] = total_gb
        snap["ram_pct"]      = pct
    except Exception:
        pass
    if snap["ram_total_gb"] is None:
        snap["ram_total_gb"] = info.get("ram_gb", "?")

    # Live disk usage of the configured work dir (or home if not set)
    try:
        from omnicli.memory import get_config as _gc
        target = (_gc("work_dir", "") or "").strip() or os.path.expanduser("~")
        if not os.path.isdir(target):
            target = os.path.expanduser("~")
        du = shutil.disk_usage(target)
        snap["disk_free_gb"]  = round(du.free  / 1024**3, 1)
        snap["disk_total_gb"] = round(du.total / 1024**3, 1)
        snap["disk_pct"]      = int(du.used / du.total * 100) if du.total else 0
        snap["work_dir"]      = target
    except Exception:
        snap["disk_free_gb"] = snap["disk_total_gb"] = 0
        snap["disk_pct"] = 0
        snap["work_dir"] = os.getcwd()

    return snap


# Inner width of the welcome HUD — content area between the ║ borders.
_HUD_INNER = 70


def _hud_print_row(label: str, value: str, colour: str = "1;32") -> None:
    """Print one HUD row with strict right-border alignment.

    label is padded to 14 chars, value gets the rest of the inner width.
    Content layout: '║  LABEL[14]  ▶ VALUE[...]  ║'
    """
    label_part = f"{label:<14}"
    inner_used = 2 + 14 + 2 + 2 + len(value) + 2  # leading "  " + label + "  " + "▶ " + val + trailing "  "
    pad = _HUD_INNER - inner_used
    if pad < 0:
        # Truncate value so the right border stays put
        value = value[: max(0, len(value) + pad - 1)] + "…"
        pad = 0
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[2m{label_part}\033[0m  "
        f"\033[{colour}m▶ {value}\033[0m{' ' * pad}  "
        f"\033[36m║\033[0m\n"
    )


def _hud_print_top(title: str) -> None:
    """Top border with embedded title — title bytes are sized correctly so
    the right ╗ stays aligned with the rest of the HUD."""
    title_part = f"══[ {title} ]"
    pad = _HUD_INNER - len(title_part)
    sys.stdout.write(f"  \033[36m╔{title_part}{'═' * max(0, pad)}╗\033[0m\n")


def _hud_print_sep() -> None:
    sys.stdout.write(f"  \033[36m╠{'═' * _HUD_INNER}╣\033[0m\n")


def _hud_print_bot() -> None:
    sys.stdout.write(f"  \033[36m╚{'═' * _HUD_INNER}╝\033[0m\n")


def cinematic_welcome(owner_name: str, bot_name: str, licensed: bool, version: str, trust: int = 3) -> None:
    """Jarvis-style personalized greeting + system snapshot played every chat
    session start. ~2s, never blocks on slow probes (cached static info + a
    handful of live numbers via shutil/psutil)."""
    owner = (owner_name or "Operator").strip().split()[0] if owner_name else "Operator"
    bot   = bot_name or "PhantomCLI"
    lic   = "LICENSED" if licensed else "UNLICENSED"

    snap = _live_system_snapshot()

    sys.stdout.write("\n")
    # ── Quick HUD scanline sweep
    sweep = "─" * (_HUD_INNER + 2)
    for i in range(0, _HUD_INNER + 3, 8):
        sys.stdout.write(f"\r  \033[36m{sweep[:i]}\033[0m\033[2m{sweep[i:]}\033[0m")
        sys.stdout.flush()
        time.sleep(0.008)
    sys.stdout.write("\r" + " " * (_HUD_INNER + 6) + "\r")
    sys.stdout.flush()

    # ── HUD: identity + machine snapshot + status ─────────────────────────
    _hud_print_top(f"{bot.upper()}  v{version}  ·  ARAVIND LABS")

    # System block
    os_label = snap["os"]
    if snap.get("arch"):
        os_label = f"{os_label} ({snap['arch']})"
    _hud_print_row("HOST",      f"{snap['host']}", colour="1;36")
    _hud_print_row("OS",        os_label,                                       colour="1;36")
    cpu_str = snap["cpu"]
    if snap.get("cores") not in ("?", ""):
        cpu_str = f"{cpu_str} · {snap['cores']} cores"
    _hud_print_row("CPU",       cpu_str,                                        colour="36")
    if snap["ram_free_gb"] is not None:
        ram_line = f"{snap['ram_free_gb']} GB free / {snap['ram_total_gb']} GB total ({snap['ram_pct']}% used)"
        ram_col  = "33" if (snap['ram_pct'] or 0) > 80 else "1;32"
    else:
        ram_line = f"{snap['ram_total_gb'] or snap['ram_gb']} GB total"
        ram_col  = "1;32"
    _hud_print_row("MEMORY",    ram_line, colour=ram_col)
    if snap.get("gpu"):
        _hud_print_row("GPU",   snap["gpu"], colour="36")
    disk_line = (f"{snap['disk_free_gb']} GB free / {snap['disk_total_gb']} GB total "
                 f"({snap['disk_pct']}% used)")
    disk_col  = "33" if snap['disk_pct'] > 85 else "36"
    _hud_print_row("DISK",      disk_line, colour=disk_col)
    cwd_short = snap.get("work_dir") or os.getcwd()
    if len(cwd_short) > 50:
        cwd_short = "…" + cwd_short[-49:]
    _hud_print_row("WORK DIR",  cwd_short, colour="2;36")

    _hud_print_sep()

    # Status block
    _hud_print_row("NEURAL CORE",  "online · main + router models loaded", colour="1;32")
    _hud_print_row("MEMORY BANK",  "episodic · semantic · personal synced", colour="1;32")
    _hud_print_row("EXECUTOR",     f"trust level {trust}", colour="1;32")
    _hud_print_row("LICENSE",      lic, colour=("1;32" if licensed else "33"))

    _hud_print_bot()
    sys.stdout.flush()
    time.sleep(0.05)

    # ── Personalized greeting with typewriter
    sys.stdout.write("\n")
    _typewriter(f"  ◈  Hello, {owner}.", colour_code="36", delay=0.022)
    _typewriter(f"  ◈  {bot} is online — all systems nominal.",
                colour_code="32", delay=0.014)
    _typewriter(f"  ◈  Awaiting your directive.", colour_code="36", delay=0.018)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _print_tagline(version: str):
    sys.stdout.write(
        f"  \033[2m◈  PHANTOM CLI  v{version}  ·  GOD MODE AI OS  ·  POWERED BY ARAVIND LABS  ◈\033[0m\n"
    )
    sys.stdout.flush()


# ── Chat prompt ───────────────────────────────────────────────────────────────

# Lazy-initialised prompt_toolkit session so we pay the import cost once
# and keep history across turns.
_PT_SESSION = None
_PT_HISTORY_FILE = os.path.join(os.path.expanduser("~/.omnicli"), ".chat_history")


def _build_pt_session():
    """Build a prompt_toolkit PromptSession with slash-command autocomplete,
    Enter = submit, Shift+Enter = newline, multi-line mode, and paste
    detection (large pastes render as `[pasted +N lines]` placeholder and
    the full text is sent to the model).

    Returns None if prompt_toolkit isn't importable so callers can fall
    back to plain input().
    """
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
    except ImportError:
        return None

    try:
        from omnicli.commands import available_commands
        commands = available_commands()
    except Exception:
        commands = []

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            # Only autocomplete when the user is editing the FIRST token and
            # it starts with `/`. Avoids popup noise inside multi-line prompts.
            # We look at just the current line in multi-line mode.
            current_line = text.split("\n")[-1]
            if not current_line.startswith("/"):
                return
            if " " in current_line:
                return
            prefix = current_line.lower()
            for cmd, desc in commands:
                if cmd.lower().startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(current_line),
                        display=cmd,
                        display_meta=desc,
                    )

    os.makedirs(os.path.dirname(_PT_HISTORY_FILE), exist_ok=True)
    style = Style.from_dict({
        "completion-menu.completion":          "bg:#1a1a1a #00ffff",
        "completion-menu.completion.current":  "bg:#005f87 #ffffff bold",
        "completion-menu.meta.completion":     "bg:#1a1a1a #888888",
        "completion-menu.meta.completion.current": "bg:#005f87 #dddddd",
        "scrollbar.background":                "bg:#1a1a1a",
        "scrollbar.button":                    "bg:#00ffff",
    })

    # ── Keybindings — Enter submits, Alt+Enter = newline, paste handling ─
    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event):
        """Plain Enter submits the buffer."""
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _alt_enter(event):
        """Alt/Option+Enter → newline — works on every major terminal."""
        event.current_buffer.insert_text("\n")

    # Optional: some prompt_toolkit builds expose Keys.ShiftEnter when the
    # terminal advertises CSI-u mode.
    try:
        from prompt_toolkit.keys import Keys as _PTKeys
        shift_enter = getattr(_PTKeys, "ShiftEnter", None)
        if shift_enter is not None:
            @kb.add(shift_enter)
            def _shift_enter(event):
                event.current_buffer.insert_text("\n")
    except Exception:
        pass

    # ── Bracketed paste — Claude-Code-style collapse ────────────────────
    # Terminals wrap a paste in `\x1b[200~ ... \x1b[201~` when bracketed-
    # paste mode is on (it's on by default in Windows Terminal, iTerm2,
    # modern VS Code, etc.). prompt_toolkit surfaces that as a single
    # Keys.BracketedPaste event with `event.data = full paste text`.
    #
    # For LARGE pastes we insert a short placeholder `[paste #N: +L lines,
    # C chars — <first-line preview>]` into the visible buffer. On submit
    # the placeholder is substituted back to the full text before the
    # prompt is sent to the engine. No more 118-line line-by-line echo.
    try:
        from prompt_toolkit.keys import Keys as _PTKeys
        _bp_key = getattr(_PTKeys, "BracketedPaste", None)
    except Exception:
        _bp_key = None

    if _bp_key is not None:
        @kb.add(_bp_key)
        def _bracketed_paste(event):
            data = event.data or ""
            lines = data.splitlines()
            n_lines = len(lines)
            n_chars = len(data)
            # Small pastes behave normally — just insert them
            if n_lines < _PASTE_LINE_THRESHOLD and n_chars < _PASTE_CHAR_THRESHOLD:
                event.current_buffer.insert_text(data)
                return
            # Large paste — stash + insert placeholder
            _PASTE_COUNTER[0] += 1
            pid = _PASTE_COUNTER[0]
            _PASTE_STORE[pid] = data
            preview = (lines[0][:40] + "…") if lines and len(lines[0]) > 40 else (lines[0] if lines else "")
            placeholder = f"[paste #{pid}: +{n_lines} lines, {n_chars:,} chars — {preview!r}]"
            event.current_buffer.insert_text(placeholder)

    return PromptSession(
        completer=SlashCompleter(),
        history=FileHistory(_PT_HISTORY_FILE),
        complete_while_typing=True,
        style=style,
        key_bindings=kb,
        multiline=True,          # multi-line buffer enabled
        enable_system_prompt=False,
        mouse_support=False,
    )


# ── Large-paste detection ─────────────────────────────────────────────────────
# When the user pastes >N lines of text (common when dumping an error trace or
# a code snippet), collapse the display to `[pasted +N lines]` but keep the
# full text to send to the model. Matches Claude Code's UX.

_PASTE_LINE_THRESHOLD = 6         # any paste >= 6 lines is considered "large"
_PASTE_CHAR_THRESHOLD = 400       # or >= 400 chars

# Paste store: maps a monotonic counter ID → full pasted text. Populated
# by the bracketed-paste key handler; read by expand_pastes() on submit.
_PASTE_STORE:   dict[int, str] = {}
_PASTE_COUNTER: list[int]      = [0]


def expand_pastes(raw: str) -> str:
    """Replace `[paste #N: ...]` placeholders with the full pasted text
    and clear the corresponding entry from _PASTE_STORE. Called right
    before the directive is sent to the engine so the model sees the
    actual content, not the human-readable placeholder."""
    import re as _re
    if not raw or "[paste #" not in raw:
        return raw
    def _repl(m):
        try:
            pid = int(m.group(1))
        except ValueError:
            return m.group(0)
        full = _PASTE_STORE.pop(pid, None)
        return full if full is not None else m.group(0)
    return _re.sub(r"\[paste #(\d+):[^\]]*\]", _repl, raw)


def summarize_large_paste(raw: str) -> tuple[str, str]:
    """Return (display, full). If `raw` is large enough to be a paste, the
    display string is `[pasted +N lines, ~C chars]` — otherwise both values
    are `raw` unchanged."""
    if not raw:
        return raw, raw
    lines = raw.splitlines()
    n_lines = len(lines)
    n_chars = len(raw)
    if n_lines >= _PASTE_LINE_THRESHOLD or n_chars >= _PASTE_CHAR_THRESHOLD:
        first = (lines[0][:60] + "…") if lines and len(lines[0]) > 60 else (lines[0] if lines else "")
        display = f"[pasted +{n_lines} lines, {n_chars:,} chars — starts: {first!r}]"
        return display, raw
    return raw, raw


def chat_prompt() -> str:
    """JARVIS-style input prompt with `/` slash-command autocomplete.

    Uses prompt_toolkit when available so typing `/` opens the command menu
    and typing `/t` filters to commands starting with `t`. Falls back to
    plain input() when prompt_toolkit isn't installed.
    """
    global _PT_SESSION
    if _PT_SESSION is None:
        _PT_SESSION = _build_pt_session()  # may stay None if import fails

    sys.stdout.write(
        f"  \033[34m╔══\033[0m\033[1;36m[ DIRECTIVE ]\033[0m"
        f"\033[2m{'═' * 50}\033[0m\n"
    )
    sys.stdout.flush()

    try:
        if _PT_SESSION is not None:
            from prompt_toolkit.formatted_text import ANSI
            raw = _PT_SESSION.prompt(ANSI("\033[34m  ╚▶ \033[0m"))
        else:
            raw = input("\033[34m  ╚▶ \033[0m")
        raw = (raw or "").strip()

        # Expand `[paste #N: ...]` placeholders back to their full text.
        # The placeholder is what the user SEES in the terminal; the full
        # content is what the engine receives.
        expanded = expand_pastes(raw)

        # On terminals that DON'T support bracketed paste, fall back to
        # the old summarize-after-submit behavior so the user still sees
        # a compact placeholder instead of 118 lines of noise.
        if expanded == raw:
            display, full = summarize_large_paste(raw)
            if display != full:
                sys.stdout.write(f"\n  \033[2m{display}\033[0m\n")
            expanded = full
        sys.stdout.write("\n")
        sys.stdout.flush()
        return expanded
    except (KeyboardInterrupt, EOFError):
        return "exit"


def ai_response_header(persona: str, model: str):
    label = f"PHANTOM · {persona.upper()}"
    sys.stdout.write(
        f"  \033[34m╔══\033[0m\033[32m[ {label} ]\033[0m"
        f"  \033[2mvia {model}\033[0m\n"
        f"  \033[34m╚▶\033[0m "
    )
    sys.stdout.flush()


def ai_response_end():
    sys.stdout.write(f"\n  \033[2m{'─' * 66}\033[0m\n\n")
    sys.stdout.flush()


# ── Status / info panels ──────────────────────────────────────────────────────

def status_panel(data: dict):
    """Render JARVIS-style system status HUD."""
    rows = [
        ("MAIN ENGINE",   data.get("main_model",   "not set"), "36"),
        ("ROUTER ENGINE", data.get("router_model", "not set"), "34"),
        ("TRUST LEVEL",   str(data.get("trust", "3")),         "32"),
        ("TELEGRAM",      "ONLINE" if data.get("telegram") else "OFFLINE",
                          "32" if data.get("telegram") else "2"),
        ("LICENSE",       "ACTIVE" if data.get("licensed") else "INACTIVE",
                          "32" if data.get("licensed") else "33"),
        ("VERSION",       data.get("version", "?"),            "2"),
    ]
    sys.stdout.write(f"\n  \033[36m╔══[ 💀 PHANTOM STATUS HUD ]{'═' * 38}╗\033[0m\n")
    for label, val, code in rows:
        sys.stdout.write(
            f"  \033[36m║\033[0m  \033[2m{label:<18}\033[0m  "
            f"\033[{code}m{val}\033[0m\n"
        )
    sys.stdout.write(f"  \033[36m╚{'═' * 66}╝\033[0m\n\n")
    sys.stdout.flush()


def command_help():
    """Render JARVIS-style command reference."""
    cmds = [
        ("/help",              "Show this reference"),
        ("/status",            "System status HUD"),
        ("/model <name>",      "Switch AI model mid-chat"),
        ("/trust <1-4>",       "Change trust level"),
        ("/clear",             "Clear conversation history"),
        ("/image <prompt>",    "Generate an image"),
        ("/voice <text>",      "Text-to-speech"),
        ("/shell on|off",      "Toggle bash execution"),
        ("/memory",            "Show memory stats"),
        ("/version",           "Show version & update info"),
        ("/update",            "Check for and apply updates"),
        ("/devices",           "List registered devices"),
        ("/export",            "Export conversation to file"),
        ("/tg-trust <1-4>",    "Set Telegram-specific trust level"),
        ("/exit",              "Exit PhantomCLI"),
    ]
    sys.stdout.write(f"\n  \033[36m╔══[ 💀 PHANTOM COMMAND INTERFACE ]{'═' * 32}╗\033[0m\n")
    for cmd, desc in cmds:
        sys.stdout.write(
            f"  \033[36m║\033[0m  \033[36m{cmd:<22}\033[0m  \033[2m{desc}\033[0m\n"
        )
    sys.stdout.write(f"  \033[36m╚{'═' * 66}╝\033[0m\n\n")
    sys.stdout.flush()


def error(msg: str):
    sys.stdout.write(f"\n  \033[31m◈  SYSTEM ALERT ►  {msg}\033[0m\n\n")
    sys.stdout.flush()


def success(msg: str):
    sys.stdout.write(f"\n  \033[32m◈  {msg}\033[0m\n\n")
    sys.stdout.flush()


def warn(msg: str):
    sys.stdout.write(f"\n  \033[33m◈  {msg}\033[0m\n\n")
    sys.stdout.flush()


def info(msg: str):
    sys.stdout.write(f"  \033[2m◈  {msg}\033[0m\n")
    sys.stdout.flush()


def separator():
    sys.stdout.write(f"  \033[2m{'▰' * 4} {'─' * 56} {'▰' * 4}\033[0m\n")
    sys.stdout.flush()


# ── Terms & Conditions acceptance ─────────────────────────────────────────────

TERMS_SUMMARY = """
  ┌─────────────────────────────────────────────────────────────────┐
  │             PHANTOM CLI  ·  LICENSE AGREEMENT                   │
  │                    Aravind Labs                                 │
  ├─────────────────────────────────────────────────────────────────┤
  │  1. LICENSE  — Single user, max 3 devices. Non-transferable.    │
  │  2. USE      — Personal/commercial use permitted on licensed    │
  │               devices. No redistribution of the software.      │
  │  3. REVERSE  — Decompiling, reverse engineering, or extracting │
  │               source code is strictly prohibited.              │
  │  4. COMMANDS — You are solely responsible for all commands     │
  │               executed through PhantomCLI on your system.     │
  │  5. LIABILITY — Aravind Labs bears NO responsibility for data  │
  │               loss, system damage, or any harm caused by       │
  │               commands run via this software.                  │
  │  6. GOD MODE — Enabling Trust Level 4 grants unrestricted      │
  │               execution. You accept full responsibility.       │
  │  7. DATA     — License key validated online. No personal data  │
  │               stored on external servers beyond the key.       │
  │  8. UPDATES  — Future updates delivered through the built-in   │
  │               updater. Continued use implies acceptance.       │
  │                                                                 │
  │  Full T&C: phantom.aravindlabs.tech/terms                      │
  └─────────────────────────────────────────────────────────────────┘
"""

def show_terms_and_accept() -> bool:
    """Show T&C and ask for acceptance. Returns True if accepted."""
    if os.path.exists(TERMS_FLAG):
        return True

    # Non-interactive (nohup / background / piped): can't prompt.
    # Tell the user how to accept, then bail cleanly.
    if not sys.stdin.isatty():
        sys.stderr.write(
            "\nPhantomCLI: Terms & Conditions not yet accepted.\n"
            "Accept them once interactively, then restart the dashboard:\n"
            "  python run.py chat          # accept in terminal chat\n"
            "  python run.py setup         # accept in setup wizard\n\n"
        )
        return False

    console.print(f"\n[{CY}]{TERMS_SUMMARY}[/{CY}]")
    console.print(f"  [{AMB}]By typing YES you agree to the above terms and the full license[/{AMB}]")
    console.print(f"  [{AMB}]agreement at phantom.aravindlabs.tech/terms[/{AMB}]\n")

    try:
        answer = input("\033[96m  Do you accept? (YES / no): \033[0m").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        return False

    if answer.strip().upper() == "YES":
        os.makedirs(os.path.dirname(TERMS_FLAG), exist_ok=True)
        with open(TERMS_FLAG, "w") as f:
            import datetime
            f.write(f"accepted={datetime.datetime.utcnow().isoformat()}\n")
        success("Terms accepted. Welcome to PhantomCLI.")
        return True

    console.print(f"\n  [{RED}]Terms not accepted. PhantomCLI cannot run without agreement.[/{RED}]")
    console.print(f"  [{DIM}]Read full terms at: phantom.aravindlabs.tech/terms[/{DIM}]\n")
    return False


# ── God Mode Terminator animation ─────────────────────────────────────────────

_TERMINATOR_FRAMES = [
    # Frame 1
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║                                                               ║
  ║                    .·´¯`·.·´¯`·.                             ║
  ║                                                               ║
  ╚═══════════════════════════════════════════════════════════════╝""",
    # Frame 2
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║  SCANNING.                                                    ║
  ║         ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  12%          ║
  ╚═══════════════════════════════════════════════════════════════╝""",
    # Frame 3
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║  SCANNING..                                                   ║
  ║         ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░  38%          ║
  ╚═══════════════════════════════════════════════════════════════╝""",
    # Frame 4
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║  SCANNING...                                                  ║
  ║         ████████████████████████░░░░░░░░░░░░░░  62%          ║
  ╚═══════════════════════════════════════════════════════════════╝""",
    # Frame 5
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║  SCANNING....                                                 ║
  ║         ████████████████████████████████████░░  91%          ║
  ╚═══════════════════════════════════════════════════════════════╝""",
    # Frame 6 — eye opens
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║                                                               ║
  ║         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░               ║
  ║         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░               ║
  ║         ░░░░░░░░░  [ TARGET ACQUIRED ]  ░░░░░░░               ║
  ║         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░               ║
  ║                                                               ║
  ╚═══════════════════════════════════════════════════════════════╝""",
    # Frame 7 — Terminator eye
    """
  ╔═══════════════════════════════════════════════════════════════╗
  ║                                                               ║
  ║              ██████████████████████████████                  ║
  ║           ████                            ████               ║
  ║         ████   ████████████████████████    ████              ║
  ║        ███   ████                    ████   ███              ║
  ║        ███  ████  ████          ████  ████  ███              ║
  ║        ███  ███  ████████████████████  ███  ███              ║
  ║        ███  ████  ████          ████  ████  ███              ║
  ║        ███   ████                    ████   ███              ║
  ║         ████   ████████████████████████    ████              ║
  ║           ████                            ████               ║
  ║              ██████████████████████████████                  ║
  ║                                                               ║
  ╚═══════════════════════════════════════════════════════════════╝""",
]

_TERMINATOR_WARNING = """
  ┌─────────────────────────────────────────────────────────────────┐
  │                  ⚠  GOD MODE WARNING  ⚠                        │
  ├─────────────────────────────────────────────────────────────────┤
  │                                                                 │
  │  You are about to unlock TRUST LEVEL 4 — GOD MODE.             │
  │                                                                 │
  │  In plain English, this means:                                  │
  │                                                                 │
  │  • PhantomCLI will execute ANY command without asking you.     │
  │  • One wrong AI response could delete files, crash services,   │
  │    expose data, or permanently damage your system.             │
  │  • There is NO undo for deleted files or dropped databases.    │
  │  • Aravind Labs accepts ZERO liability for God Mode damage.    │
  │                                                                 │
  │  The 34 catastrophic patterns (rm -rf /, fork bombs, etc.)     │
  │  remain blocked. Everything else will execute immediately.     │
  │                                                                 │
  │  Only enable this on machines you fully own and can restore.   │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
"""

def god_mode_activation_sequence(phc_key_getter) -> bool:
    """
    Plays the Terminator boot animation, shows the warning,
    then asks the user to confirm with their PHC license key.
    Returns True if confirmed, False if declined/wrong key.
    """
    # Terminator scan animation
    for i, frame in enumerate(_TERMINATOR_FRAMES):
        os.system("cls" if os.name == "nt" else "clear")
        colour = RED if i >= 5 else AMB
        console.print(f"[{colour}]{frame}[/{colour}]")
        time.sleep(0.18 if i < 5 else 0.35)

    # Warning
    console.print(f"[{RED}]{_TERMINATOR_WARNING}[/{RED}]")
    time.sleep(0.5)

    # Typewriter effect for the confirm prompt
    prompt_text = "  CONFIRM GOD MODE — enter your PHC license key (or press Enter to cancel): "
    for ch in prompt_text:
        sys.stdout.write(f"\033[31m{ch}\033[0m")
        sys.stdout.flush()
        time.sleep(0.025)
    sys.stdout.write("\n\033[96m  PHC-KEY: \033[0m")
    sys.stdout.flush()

    try:
        import stdiomask
        entered = stdiomask.getpass(prompt="", mask="*").strip().upper()
    except Exception:
        entered = input("").strip().upper()

    if not entered:
        console.print(f"\n  [{AMB}]God Mode activation cancelled.[/{AMB}]\n")
        return False

    # Validate the key — match cached key or validate online if no cache yet
    cached_key = phc_key_getter()
    confirmed = False
    if cached_key:
        confirmed = entered == cached_key.upper()
    else:
        # No cached license — validate online so first-time users aren't locked out
        try:
            from omnicli.licensing import validate_key_online
            valid, _ = validate_key_online(entered)
            confirmed = valid
        except Exception:
            confirmed = False

    if confirmed:
        console.print(f"\n  [{RED}]IDENTITY CONFIRMED[/{RED}]")
        time.sleep(0.3)
        _god_mode_boot()
        # Persist god mode state
        os.makedirs(os.path.dirname(GOD_MODE_FLAG), exist_ok=True)
        open(GOD_MODE_FLAG, "w").close()
        return True
    else:
        console.print(f"\n  [{RED}]IDENTITY VERIFICATION FAILED. GOD MODE DENIED.[/{RED}]\n")
        return False


def _god_mode_boot():
    """Short boot sequence after God Mode is confirmed."""
    lines = [
        ("TRUST LEVEL 4",    "UNLOCKED",                          RED),
        ("PERMISSION GATE",  "STANDING DOWN",                     RED),
        ("EXECUTION MODE",   "UNRESTRICTED",                      RED),
        ("WARNING ACTIVE",   "34 CRITICAL PATTERNS STILL BLOCKED",AMB),
        ("RESPONSIBILITY",   "TRANSFERRED TO OPERATOR",           AMB),
        ("STATUS",           "GOD MODE ONLINE",                   RED),
    ]
    console.print()
    for module, val, colour in lines:
        console.print(f"  [{DIM}][[/{DIM}][{colour}]{module:<20}[/{colour}][{DIM}]][/{DIM}]  [{colour}]{val}[/{colour}]")
        time.sleep(0.12)
    console.print()


def danger_blocked(command: str, reason: str):
    console.print(Panel(
        f"  [{RED}]COMMAND BLOCKED[/{RED}]\n\n"
        f"  [{DIM}]Command :[/{DIM}] [{AMB}]{command}[/{AMB}]\n"
        f"  [{DIM}]Reason  :[/{DIM}] [{RED}]{reason}[/{RED}]\n\n"
        f"  [{DIM}]This operation is permanently restricted to protect the host system.[/{DIM}]",
        border_style=RED,
        title=f"[{RED}]⛔ SECURITY GATE[/{RED}]",
        padding=(0, 2),
    ))


# ══════════════════════════════════════════════════════════════════════════════
#  NEW ANIMATIONS
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Matrix Rain ────────────────────────────────────────────────────────────

_MATRIX_CHARS = (
    "ｦｧｨｩｪｫｬｭｮｯｰｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ░▒▓"
)

def matrix_rain(duration: float = 2.2, rows: int = 18):
    """
    Classic Matrix green-rain effect. Fills `rows` lines, then erases them.
    Call before a section break for dramatic effect.
    """
    import shutil
    cols      = min(shutil.get_terminal_size().columns - 4, 76)
    col_count = cols // 2
    end_t     = time.time() + duration
    line_count = 0

    while time.time() < end_t:
        segments = []
        for _ in range(col_count):
            ch = random.choice(_MATRIX_CHARS)
            # Bright head vs dim body
            if random.random() < 0.08:
                segments.append(f"\033[92;1m{ch}\033[0m")   # bright white-green head
            elif random.random() < 0.6:
                segments.append(f"\033[32m{ch}\033[0m")      # standard green
            else:
                segments.append(f"\033[2;32m{ch}\033[0m")    # dim green
        sys.stdout.write("  " + "".join(segments) + "\n")
        sys.stdout.flush()
        line_count += 1
        time.sleep(0.055)

    # Erase the rain lines
    for _ in range(line_count):
        sys.stdout.write("\033[A\033[2K")
    sys.stdout.flush()


# ── 2. Glitch Persona Transition ──────────────────────────────────────────────

def glitch_transition(from_persona: str, to_persona: str):
    """
    Glitchy shapeshift animation between two persona names.
    Shown inline before the AI response header.
    """
    import string
    noise_chars = string.ascii_uppercase + "░▒▓01█▄▀"
    target      = to_persona.upper()

    # Phase 1: scramble the old name
    for _ in range(7):
        noise = "".join(random.choices(noise_chars, k=len(from_persona) + 4))
        sys.stdout.write(
            f"\r  \033[2m◈  SHAPESHIFTING: \033[33m{noise[:20]}\033[0m   "
        )
        sys.stdout.flush()
        time.sleep(0.045)

    # Phase 2: gradually resolve into target
    revealed = list(" " * len(target))
    indices  = list(range(len(target)))
    random.shuffle(indices)
    for i in indices:
        revealed[i] = target[i]
        partial = "".join(revealed)
        sys.stdout.write(
            f"\r  \033[36m◈  SHAPESHIFTED → \033[1;32m{partial}\033[0m   "
        )
        sys.stdout.flush()
        time.sleep(0.03)

    sys.stdout.write(
        f"\r  \033[36m◈  SHAPESHIFTED → \033[1;32m{target}\033[0m{' ' * 30}\n"
    )
    sys.stdout.flush()


# ── 3. AI Typing Effect ───────────────────────────────────────────────────────

def type_out(text: str, delay: float = 0.008):
    """
    Simulate character-by-character typing for non-streamed AI output.
    Use instead of console.print when the response arrives all at once.
    Skips delay for code blocks (print them instantly).
    """
    in_code = False
    i       = 0
    while i < len(text):
        # Detect triple-backtick boundary
        if text[i:i+3] == "```":
            in_code = not in_code
            sys.stdout.write(text[i:i+3])
            sys.stdout.flush()
            i += 3
            continue
        ch = text[i]
        sys.stdout.write(ch)
        sys.stdout.flush()
        if not in_code:
            # Natural rhythm: pause slightly at sentence ends
            if ch in ".!?\n":
                time.sleep(delay * 6)
            elif ch == ",":
                time.sleep(delay * 3)
            else:
                time.sleep(delay)
        i += 1
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── 4. HUD Scan Line ──────────────────────────────────────────────────────────

def hud_scanline(label: str = "SCANNING", width: int = 60, cycles: int = 2):
    """
    A glowing scan line that sweeps left-to-right across a HUD bar.
    `cycles` controls how many full sweeps before clearing.
    """
    head  = "▓▒░"
    trail = "░"

    for _ in range(cycles):
        for pos in range(width):
            bar_chars = []
            for x in range(width):
                if x == pos:
                    bar_chars.append(f"\033[1;32m{head[0]}\033[0m")
                elif x == pos - 1:
                    bar_chars.append(f"\033[32m{head[1]}\033[0m")
                elif x == pos - 2:
                    bar_chars.append(f"\033[2;32m{head[2]}\033[0m")
                elif x < pos:
                    bar_chars.append(f"\033[2;32m{trail}\033[0m")
                else:
                    bar_chars.append(f"\033[2m·\033[0m")
            sys.stdout.write(
                f"\r  \033[36m[ {label} ]\033[0m  "
                + "".join(bar_chars)
            )
            sys.stdout.flush()
            time.sleep(0.018)

    sys.stdout.write("\r" + " " * (width + 20) + "\r")
    sys.stdout.flush()


# ── 5. Agent Spawn Panel ──────────────────────────────────────────────────────

_AGENT_STATUS_COLOUR = {
    "queued":  "\033[2m",       # dim
    "running": "\033[1;36m",    # bright cyan
    "done":    "\033[1;32m",    # bright green
    "failed":  "\033[1;31m",    # bright red
}
_AGENT_STATUS_ICON = {
    "queued":  "○",
    "running": "◉",
    "done":    "●",
    "failed":  "✗",
}
_SPINNER_FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]


def agent_spawn_intro(task: str, agents: list[dict]):
    """
    One-time display: shows the task breakdown before agents start.
    agents = [{"id": "agent_1", "name": "...", "role": "...", "files": [...]}]
    """
    sys.stdout.write("\n")
    sys.stdout.write(f"  \033[36m╔══[ 💀 PHANTOM MULTI-AGENT MODE ]{'═'*30}╗\033[0m\n")
    # Task
    task_short = task[:55] + "…" if len(task) > 55 else task
    sys.stdout.write(f"  \033[36m║\033[0m  \033[2mTASK   \033[0m  \033[1;37m{task_short}\033[0m\n")
    sys.stdout.write(f"  \033[36m╠{'═'*67}╣\033[0m\n")

    for ag in agents:
        name   = ag.get("name", "Agent")
        role   = ag.get("role", "")
        files  = ag.get("files", [])
        fshort = ", ".join(os.path.basename(f) for f in files[:3])
        if len(files) > 3:
            fshort += f" +{len(files)-3} more"
        sys.stdout.write(
            f"  \033[36m║\033[0m  \033[33m▶ {name:<22}\033[0m"
            f"  \033[2m{role:<22}\033[0m  \033[2m{fshort}\033[0m\n"
        )
        time.sleep(0.08)

    sys.stdout.write(f"  \033[36m╠{'═'*67}╣\033[0m\n")
    sys.stdout.write(f"  \033[36m║\033[0m  \033[2mSPAWNING {len(agents)} AGENTS  ·  PARALLEL EXECUTION ACTIVE\033[0m\n")
    sys.stdout.write(f"  \033[36m╚{'═'*67}╝\033[0m\n\n")
    sys.stdout.flush()
    time.sleep(0.3)


# Serialises stdout writes from the live panel so a stray log line from a
# worker thread can't slip in between erase + redraw and corrupt the count.
_PANEL_LOCK = threading.Lock()


def agent_live_panel(agents: list[dict], tick: int = 0):
    """
    Render a live-updating agent status panel (call repeatedly, then erase).
    agents = [{"id": ..., "name": ..., "status": ..., "msg": ...}]
    Returns number of lines written (for erase).

    Done agents render with strikethrough so the user can see at a glance
    what's left to do vs what's complete.
    """
    spinner = _SPINNER_FRAMES[tick % len(_SPINNER_FRAMES)]
    done_n  = sum(1 for a in agents if a.get("status") == "done")
    fail_n  = sum(1 for a in agents if a.get("status") == "failed")
    total_n = len(agents)

    header = f"  AGENTS  {done_n}/{total_n} done"
    if fail_n:
        header += f"  ·  {fail_n} failed"
    pad = 65 - len(header)

    buf = [
        f"  \033[36m┌─\033[1;36m[ {header.strip()} ]\033[0m"
        f"\033[36m{'─' * max(0, pad - 5)}┐\033[0m\n"
    ]
    for ag in agents:
        status  = ag.get("status", "queued")
        name    = ag.get("name", "Agent")[:22]
        msg     = (ag.get("msg", "") or "")[:35]
        colour  = _AGENT_STATUS_COLOUR.get(status, "")
        icon    = _AGENT_STATUS_ICON.get(status, "○")
        spin    = spinner if status == "running" else " "
        if status == "done":
            name_styled = f"\033[9;2;32m{name:<24}\033[0m"
        elif status == "failed":
            name_styled = f"\033[9;31m{name:<24}\033[0m"
        else:
            name_styled = f"{colour}{name:<24}\033[0m"
        buf.append(
            f"  \033[36m│\033[0m  {colour}{icon}\033[0m  {name_styled}"
            f"  \033[1;36m{spin}\033[0m  \033[2m{msg:<35}\033[0m"
            f"  \033[36m│\033[0m\n"
        )
    buf.append(f"  \033[36m└{'─'*65}┘\033[0m\n")

    with _PANEL_LOCK:
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
    return len(buf)


def erase_lines(n: int):
    """Erase the last n lines printed to stdout.

    Uses CSI F (cursor previous line) + J (clear from cursor to end of screen)
    so that even if extra noise (e.g. a stray log warning) was printed between
    the original render and this call, the wipe still reaches the end of the
    screen and we don't end up with a duplicated header on the next redraw.
    """
    if n <= 0:
        return
    with _PANEL_LOCK:
        # \033[<n>F = move cursor to beginning of line, n lines up
        # \033[0J  = clear from cursor to end of screen
        sys.stdout.write(f"\033[{n}F\033[0J")
        sys.stdout.flush()


def agent_spawn_panel(
    orchestrator,
    poll_interval: float = 0.35,
):
    """
    Live-updating panel that monitors orchestrator.results while agents run.
    Call this from a dedicated thread AFTER orchestrator.execute() starts.
    Blocks until all agents finish.
    """
    tick       = 0
    last_lines = 0

    while True:
        snapshot = orchestrator.status_snapshot()

        # Erase previous panel
        if last_lines:
            erase_lines(last_lines)

        # Build display rows
        rows = []
        for ag in snapshot:
            r = orchestrator.results.get(ag["id"])
            msg = ""
            if r:
                if r.status == "running":
                    msg = "working…"
                elif r.status == "done":
                    msg = f"✓ {len(r.files_written)} files"
                elif r.status == "failed":
                    msg = f"✗ {r.error[:30]}"
                else:
                    msg = "queued"
            rows.append({"id": ag["id"], "name": ag["name"], "status": ag.get("status","queued"), "msg": msg})

        last_lines = agent_live_panel(rows, tick)
        tick += 1

        # Stop when all done or failed
        all_done = all(
            orchestrator.results.get(ag["id"], type("R", (), {"status": "queued"})()).status
            in ("done", "failed")
            for ag in snapshot
        )
        if all_done:
            break

        time.sleep(poll_interval)

    # Final state
    if last_lines:
        erase_lines(last_lines)
    snapshot = orchestrator.status_snapshot()
    rows = []
    for ag in snapshot:
        r   = orchestrator.results.get(ag["id"])
        msg = f"✓ {len(r.files_written)} files · {r.elapsed}s" if r and r.status=="done" else (r.error[:30] if r else "")
        rows.append({"id": ag["id"], "name": ag["name"], "status": ag.get("status","queued"), "msg": msg})
    agent_live_panel(rows, tick=0)
