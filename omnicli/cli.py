"""
PhantomCLI v4.0.10 — God Mode AI OS
Powered by Aravind Labs
"""

import os
import sys
import time
import shutil
import zipfile
import tempfile
import logging
from typing import Optional

# ─── DEPENDENCY PREFLIGHT ────────────────────────────────────────────────────
# Import-time check: if a required third-party package is missing, print a
# friendly install hint instead of letting Python raise a cryptic
# ImportError deep in some transitive module. Must run BEFORE we start
# pulling in those libs (rich, typer, etc.).

def _preflight_dependencies() -> None:
    """
    Ensure every third-party package PhantomCLI needs is importable.

    If something is missing, attempt an in-place `pip install` against the
    current interpreter so the user never sees a raw ImportError. The
    installer scripts already handle this for fresh installs; this path
    covers edge cases where someone runs PhantomCLI outside the managed venv
    or a transient pip failure left deps half-installed.

    Set PHANTOM_NO_AUTOINSTALL=1 to disable the auto-heal and get the old
    fail-fast behaviour (useful for CI and minimal containers).
    """
    required = {
        "typer":        "typer==0.9.0",
        "click":        "click==8.1.7",
        "rich":         "rich",
        "stdiomask":    "stdiomask",
        "openai":       "openai",
        "fastapi":      "fastapi",
        "uvicorn":      "uvicorn[standard]",
        "cryptography": "cryptography",
        "requests":     "requests",
        "packaging":    "packaging",
        "ddgs":         "ddgs",
        "prompt_toolkit": "prompt_toolkit>=3.0",
    }
    import importlib
    missing = []
    for mod, pip_name in required.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append((mod, pip_name))

    if not missing:
        return

    auto_off = os.environ.get("PHANTOM_NO_AUTOINSTALL") == "1"
    pkgs = [pip_name for _, pip_name in missing]

    if not auto_off:
        sys.stderr.write(
            "\n⚡ PhantomCLI — installing missing dependencies: "
            + ", ".join(m for m, _ in missing) + "\n"
        )
        import subprocess
        # Prefer installing into the current interpreter. If that fails with
        # PEP 668 (externally managed), retry with --user which works even on
        # system Python on modern Debian/Ubuntu.
        cmd_base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
        try:
            subprocess.check_call(cmd_base + pkgs)
        except subprocess.CalledProcessError:
            try:
                subprocess.check_call(cmd_base + ["--user"] + pkgs)
            except subprocess.CalledProcessError:
                pass  # fall through to the error banner below
        # Re-check after the attempt.
        still_missing = []
        for mod, pip_name in missing:
            try:
                importlib.import_module(mod)
            except ImportError:
                still_missing.append((mod, pip_name))
        if not still_missing:
            sys.stderr.write("✓ dependencies installed\n\n")
            return
        missing = still_missing

    lines = [
        "",
        "⚡ PhantomCLI — missing Python dependencies",
        "",
        "The following packages are required but not installed:",
        "",
    ]
    for mod, pip_name in missing:
        lines.append(f"  • {mod}  (pip install {pip_name})")
    lines += [
        "",
        "Install everything at once with:",
        "    pip install -r requirements.txt",
        "",
        "Or re-run the installer:",
        "    curl -fsSL https://phantom.aravindlabs.tech/install.sh | bash",
        "",
    ]
    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(1)


_preflight_dependencies()

from pathlib import Path
from rich.prompt import Prompt
from omnicli.logging_setup import configure_logging
# Configure logging as early as possible so every subsequent import that
# grabs a logger inherits our handlers/level.
configure_logging()
log = logging.getLogger("omnicli.cli")


def _clear_god_mode_on_boot() -> None:
    """
    Wipe any lingering God Mode activation timestamp at CLI start. A user who
    restarts the process must explicitly re-confirm Trust 4 — no silent
    inheritance across sessions.
    """
    try:
        from omnicli.memory import save_config
        save_config("god_mode_activated_at", "")
    except Exception:
        pass


_clear_god_mode_on_boot()

from omnicli.auth import save_api_key, get_api_key
from omnicli.memory import init_db, save_message, get_recent_history, save_config, get_config
from omnicli.visuals import PhantomSpinner
from omnicli import __version__, settings as S
from omnicli.tui import (
    console, boot_screen, chat_prompt, ai_response_header, ai_response_end,
    status_panel, command_help, error, success, warn, info, separator,
    CY, GRN, AMB, RED, DIM, BLU, WHT
)

import typer
import stdiomask

app = typer.Typer(
    help="PhantomCLI v4.0.10 — God Mode AI OS · Aravind Labs",
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
)

VERSION_URL  = "https://phantom.aravindlabs.tech/api/phantomcli/version"

# In-process persona state — avoids writing session state to the shared SQLite DB
# (which caused cross-session/cross-instance pollution when CLI + dashboard run together)
_active_persona: str = ""
_prev_persona:   str = ""
UPDATE_ZIP   = "https://phantom.aravindlabs.tech/phantomcli/downloads/phantomcli-source.zip"
INSTALL_DIR  = Path(__file__).resolve().parent.parent

_MAX_INPUT_LEN = 32_000


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    try:
        from omnicli.telegram_bot import notify
        return notify(message)
    except Exception:
        return False


def test_connection(api_key: str, base_url: str, model: str) -> tuple[bool, str]:
    from openai import OpenAI
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return True, "OK"
    except Exception as e:
        return False, str(e)


def _check_for_update() -> dict | None:
    try:
        import requests
        from packaging.version import Version
        r    = requests.get(VERSION_URL, timeout=5)
        data = r.json()
        if Version(data.get("version", "0.0.0")) > Version(__version__):
            return data
    except Exception:
        pass
    return None


SKIP_DIRS = {'venv', '.venv', 'env', '.env', 'build', 'dist', '__pycache__', '.git', '.pytest_cache', '.mypy_cache', 'node_modules'}
SKIP_EXTS = {'.pyc', '.pyo', '.so', '.dylib', '.dll', '.exe'}


def _format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != 'B' else f"{size_bytes}B"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def _should_skip_file(filepath: str) -> bool:
    parts = Path(filepath).parts
    for part in parts:
        if part in SKIP_DIRS:
            return True
    return any(filepath.endswith(ext) for ext in SKIP_EXTS)


def _do_update(key: str = "") -> bool:
    import requests
    try:
        session = requests.Session()
        r = session.get(UPDATE_ZIP, timeout=(30, 30), stream=True)
        r.raise_for_status()

        total     = int(r.headers.get("content-length", 0))
        total_str = _format_size(total) if total else "Unknown"

        if total > 500 * 1024 * 1024:
            warn(f"Large download detected ({total_str}). This may take 15-30 minutes.")

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            downloaded          = 0
            last_progress_time  = time.time()
            last_update_time    = 0.0
            PROGRESS_TIMEOUT    = 60
            UPDATE_INTERVAL     = 0.3
            start_time          = time.time()

            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    if time.time() - last_progress_time > PROGRESS_TIMEOUT:
                        error("Download stalled — no data received for 60s.")
                        return False
                    continue

                tmp.write(chunk)
                downloaded        += len(chunk)
                last_progress_time = time.time()

                now = time.time()
                if now - last_update_time >= UPDATE_INTERVAL:
                    last_update_time = now
                    elapsed = now - start_time

                    if total:
                        pct      = min(100, int(downloaded / total * 100))
                        filled   = int(30 * pct / 100)
                        bar      = "█" * filled + "░" * (30 - filled)
                        rate     = downloaded / elapsed if elapsed > 0 else 1
                        remaining = (total - downloaded) / rate
                        eta      = f"{int(remaining//60)}m{int(remaining%60):02d}s"
                        line     = f"\r  [{CY}]{bar}[/{CY}] {pct:3d}% | {_format_size(downloaded)}/{total_str} | ETA: {eta}"
                    else:
                        line = f"\r  [{CY}]⠋[/{CY}] Downloaded: {_format_size(downloaded)}"

                    sys.stdout.write(line + " " * 10)
                    sys.stdout.flush()

            tmp_path = tmp.name

        print()
        info("Download complete. Extracting…")

        extracted = skipped = 0
        with zipfile.ZipFile(tmp_path, "r") as z:
            namelist    = z.namelist()
            total_files = len(namelist)
            for i, member in enumerate(namelist):
                if _should_skip_file(member):
                    skipped += 1
                    continue
                try:
                    z.extract(member, INSTALL_DIR)
                    extracted += 1
                except Exception as e:
                    warn(f"Could not extract {member}: {e}")
                if i % 50 == 0:
                    pct = int((i + 1) / total_files * 100)
                    sys.stdout.write(f"\r  [{BLU}]Extracting…[/{BLU}] {pct}% ({i+1}/{total_files} files)")
                    sys.stdout.flush()

        print()
        info(f"Extracted {extracted} files, skipped {skipped} build artifacts.")
        os.unlink(tmp_path)

        # Wipe stale __pycache__ trees so Python can't shadow the new .py
        # files with previously-compiled bytecode. Without this step, an
        # updated cli.py whose extracted mtime predates an existing .pyc
        # gets ignored — users see "old menus" until something else
        # triggers recompilation. We also touch each extracted .py file
        # forward to current mtime as a belt-and-braces measure.
        try:
            pycache_count = 0
            touched = 0
            now = time.time()
            for root, dirs, files in os.walk(INSTALL_DIR):
                # Walk a copy so we can mutate dirs in place to skip subtrees.
                for d in list(dirs):
                    if d == "__pycache__":
                        target = Path(root) / d
                        try:
                            shutil.rmtree(target, ignore_errors=True)
                            pycache_count += 1
                        except Exception:
                            pass
                        dirs.remove(d)
                for f in files:
                    if f.endswith(".py"):
                        try:
                            os.utime(Path(root) / f, (now, now))
                            touched += 1
                        except Exception:
                            pass
            info(f"Cleared {pycache_count} stale __pycache__ trees · "
                 f"refreshed mtime on {touched} .py files.")
        except Exception as exc:
            warn(f"__pycache__ cleanup hit a non-fatal error: {exc}. "
                 "If menus still show old options, run a clean reinstall.")

        # Re-sync Python deps so new requirements land in the venv without a reinstall.
        # Prefer the venv python inside INSTALL_DIR; fall back to sys.executable.
        req_file = INSTALL_DIR / "requirements.txt"
        if req_file.exists():
            import subprocess as _sp
            venv_py_candidates = [
                INSTALL_DIR / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python"),
                INSTALL_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python"),
            ]
            py_exec = next((str(p) for p in venv_py_candidates if p.exists()), sys.executable)
            info("Syncing Python dependencies…")
            try:
                _sp.run(
                    [py_exec, "-m", "pip", "install", "--quiet", "--upgrade", "-r", str(req_file)],
                    check=False, timeout=180,
                )
            except Exception as e:
                warn(f"Dependency sync skipped: {e}")

        try:
            import requests as _r
            ver = _r.get(VERSION_URL, timeout=5).json().get("version", __version__)
            (INSTALL_DIR / ".version").write_text(ver)
        except Exception:
            pass

        return True

    except requests.exceptions.Timeout:
        error("Update failed: connection timed out.")
        return False
    except requests.exceptions.ConnectionError as e:
        error(f"Update failed: connection error — {e}")
        return False
    except Exception as e:
        error(f"Update failed: {e}")
        return False


# ─── SETUP SECTIONS ───────────────────────────────────────────────────────────

def _badge(key: str) -> str:
    val = get_api_key() if key == "__main_key__" else get_config(key, "")
    return f"[{GRN}]●[/{GRN}]" if val else f"[{DIM}]○[/{DIM}]"


def _setup_section(category: str, title: str, intro: str = ""):
    items = S.get_category(category)
    console.print(f"\n[{CY}]── {title} ──[/{CY}]")
    if intro:
        console.print(f"[{DIM}]{intro}[/{DIM}]\n")
    for key, default, label, desc, _, secret in items:
        existing = get_api_key() if key == "main_api_key" else get_config(key, default)
        console.print(f"[{DIM}]{desc}[/{DIM}]")
        if secret:
            console.print(f"[{AMB}]{label}[/{AMB}]" + (" (blank = keep)" if existing else "") + ": ", end="")
            val = stdiomask.getpass(prompt="", mask="*").strip() or existing
        else:
            val = Prompt.ask(f"[{AMB}]{label}[/{AMB}]", default=existing or default).strip()
        if val:
            if key == "main_api_key":
                save_api_key(val)
            else:
                save_config(key, val)


def _setup_main_engine() -> bool:
    console.print(f"\n[{CY}]── Main Engine (Heavy Model) ──[/{CY}]")
    console.print(f"[{DIM}]The primary model that answers your questions.[/{DIM}]\n")
    existing_key = get_api_key()
    console.print(f"[{AMB}]API Key[/{AMB}]" + (" (blank = keep): " if existing_key else ": "), end="")
    key = stdiomask.getpass(prompt="", mask="*").strip() or existing_key
    if not key:
        error("API Key cannot be empty.")
        return False
    url   = Prompt.ask(f"[{AMB}]Base URL[/{AMB}]",  default=get_config("main_url",   "https://api.anthropic.com/v1")).strip()
    model = Prompt.ask(f"[{AMB}]Model[/{AMB}]",      default=get_config("main_model", "claude-opus-4-5")).strip()
    sp = PhantomSpinner(); sp.start(phase="thinking")
    ok, err = test_connection(key, url, model)
    sp.stop()
    if not ok:
        error(f"Connection failed: {err}")
        return False
    save_api_key(key); save_config("main_url", url); save_config("main_model", model)
    success(f"Main Engine saved ({model})")
    return True


def _setup_router_engine() -> bool:
    console.print(f"\n[{CY}]── Router Engine (Fast Classifier) ──[/{CY}]")
    console.print(f"[{DIM}]Lightweight model that routes prompts to the right expert.[/{DIM}]\n")
    existing = get_config("router_api_key")
    console.print(f"[{AMB}]API Key[/{AMB}]" + (" (blank = keep): " if existing else ": "), end="")
    key = stdiomask.getpass(prompt="", mask="*").strip() or existing
    if not key:
        error("API Key cannot be empty.")
        return False
    url   = Prompt.ask(f"[{AMB}]Base URL[/{AMB}]",  default=get_config("router_url",   "https://api.groq.com/openai/v1")).strip()
    model = Prompt.ask(f"[{AMB}]Model[/{AMB}]",      default=get_config("router_model", "llama3-8b-8192")).strip()
    sp = PhantomSpinner(); sp.start(phase="routing")
    ok, err = test_connection(key, url, model)
    sp.stop()
    if not ok:
        error(f"Connection failed: {err}")
        return False
    save_config("router_api_key", key); save_config("router_url", url); save_config("router_model", model)
    success(f"Router Engine saved ({model})")
    return True


def _setup_telegram():
    console.print(f"\n[{CY}]── Telegram Bot ──[/{CY}]")
    console.print(
        f"[{DIM}]Two-way chat with PhantomCLI from your phone.\n"
        f"  1. Open Telegram → @BotFather → /newbot → copy token\n"
        f"  2. Message your bot, then visit https://api.telegram.org/bot<TOKEN>/getUpdates[/{DIM}]\n"
    )
    token = Prompt.ask(f"[{AMB}]Bot Token[/{AMB}]",  default=get_config("telegram_token", "skip")).strip()
    if token.lower() == "skip":
        info("Telegram skipped.")
        return
    chat_id = Prompt.ask(f"[{AMB}]Chat ID[/{AMB}]", default=get_config("telegram_chat_id", "")).strip()
    if not chat_id:
        info("No Chat ID — skipped.")
        return
    tg_trust = Prompt.ask(
        f"[{AMB}]Telegram Trust (1-4)[/{AMB}]",
        default=get_config("telegram_trust", "2"),
        choices=["1", "2", "3", "4"],
    ).strip()
    sp = PhantomSpinner(); sp.start(phase="routing")
    try:
        import requests as _req
        r = _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"⚡ PhantomCLI connected · Aravind Labs\nTrust level: {tg_trust}"},
            timeout=8,
        )
        sp.stop()
        if r.ok:
            save_config("telegram_token", token)
            save_config("telegram_chat_id", chat_id)
            save_config("telegram_trust", tg_trust)
            success("Telegram connected! Check your chat.")
        else:
            error(f"Telegram error: {r.json().get('description', 'unknown')}")
    except Exception as e:
        sp.stop(); error(f"Failed: {e}")


def _get_cached_phc_key() -> str:
    try:
        from omnicli.licensing import _load_cached
        data = _load_cached()
        return data.get("key", "") if data else ""
    except Exception:
        return ""


def _activate_license() -> bool:
    """
    Prompt user to enter their PHC license key, validate it online,
    and cache it on success. Returns True if activated.
    """
    from omnicli.licensing import validate_key_online, KEY_PATTERN
    console.print(f"\n  [{CY}]Enter your PhantomCLI license key[/{CY}]")
    console.print(f"  [{DIM}]Format: PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX[/{DIM}]")
    console.print(f"  [{DIM}]Buy at: phantom.aravindlabs.tech/buy[/{DIM}]\n")
    try:
        key = input("\033[96m  PHC-KEY: \033[0m").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return False
    if not key:
        return False
    if not KEY_PATTERN.match(key):
        error("Invalid key format. Expected: PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX")
        return False
    sp = PhantomSpinner()
    sp.start(phase="thinking")
    valid, msg = validate_key_online(key)
    sp.stop()
    if valid:
        success(f"License activated! ✓  ({msg})")
        return True
    else:
        error(f"Activation failed: {msg}")
        return False


def _require_license() -> bool:
    """
    Gate function — ensures a valid license is cached before proceeding.
    If unlicensed, prompts the user to activate. Returns True to proceed.
    """
    from omnicli.licensing import is_licensed
    if is_licensed():
        return True

    from rich.panel import Panel as _Panel
    console.print(_Panel(
        f"[{AMB}]⚠  LICENSE REQUIRED[/{AMB}]\n\n"
        f"PhantomCLI requires a valid license to run.\n"
        f"Purchase at: [{CY}]phantom.aravindlabs.tech/buy[/{CY}]\n\n"
        f"If you already purchased, enter your key below.",
        border_style=AMB, padding=(1, 4),
    ))
    activated = _activate_license()
    if not activated:
        error("No valid license. Exiting.")
    return activated


def _setup_trust():
    console.print(f"\n[{CY}]── Trust Level ──[/{CY}]")
    console.print(
        f"  [{CY}]1[/{CY}]  Paranoid  — confirm every command\n"
        f"  [{CY}]2[/{CY}]  Standard  — allow safe read-only\n"
        f"  [{CY}]3[/{CY}]  Developer — allow most, warn on dangerous\n"
        f"  [{CY}]4[/{CY}]  [{RED}]God Mode  — unrestricted execution (requires PHC key)[/{RED}]\n"
    )
    choice = Prompt.ask(
        f"[{AMB}]Default trust[/{AMB}]",
        choices=["1", "2", "3", "4"],
        default=get_config("default_trust", "3"),
    )
    if choice == "4":
        from omnicli.tui import god_mode_activation_sequence
        if not god_mode_activation_sequence(_get_cached_phc_key):
            info("Keeping previous trust level.")
            return
    save_config("default_trust", choice)
    labels = {"1": "Paranoid", "2": "Standard", "3": "Developer", "4": "God Mode 💀"}
    success(f"Trust set to {choice} — {labels[choice]}")


def _setup_media(category: str, title: str, intro: str):
    items = S.get_category(category)
    console.print(f"\n[{CY}]── {title} ──[/{CY}]")
    console.print(f"[{DIM}]{intro}[/{DIM}]\n")
    for key, default, label, desc, _, secret in items:
        existing = get_config(key, default)
        console.print(f"  [{DIM}]{desc}[/{DIM}]")
        if secret:
            console.print(f"  [{AMB}]{label}[/{AMB}] (blank = keep/skip): ", end="")
            val = stdiomask.getpass(prompt="", mask="*").strip()
            if val:
                save_config(key, val)
                success(f"{label} saved.")
        else:
            val = Prompt.ask(f"  [{AMB}]{label}[/{AMB}]", default=existing).strip()
            if val:
                save_config(key, val)


def _manage_devices():
    from omnicli.licensing import get_license_info, list_devices, deactivate_device, _load_cached, get_device_id, MAX_DEVICES
    data = _load_cached()
    if not data:
        error("No active license found.")
        return
    key = data.get("key", "")
    console.print(f"\n[{CY}]── License Devices  [/{CY}][{DIM}]Max {MAX_DEVICES} per license[/{DIM}]\n")
    sp = PhantomSpinner(); sp.start(phase="routing")
    ok, devices = list_devices(key)
    sp.stop()
    if not ok or not devices:
        info("No devices registered.")
        return
    this = get_device_id()
    for i, d in enumerate(devices, 1):
        marker = f" [{CY}]← this device[/{CY}]" if d.get("device_id") == this else ""
        last   = d.get("last_seen", "")[:10]
        console.print(f"  [{WHT}]{i}.[/{WHT}] {d.get('device_name','?')} [{DIM}]({d.get('platform','?')} · {last})[/{DIM}]{marker}")
    console.print(f"\n  [{DIM}]{len(devices)}/{MAX_DEVICES} slots used · {MAX_DEVICES - len(devices)} remaining[/{DIM}]\n")
    if not devices:
        return
    action = Prompt.ask(f"[{WHT}]Deactivate device # (0 = back)[/{WHT}]", default="0").strip()
    if action == "0":
        return
    try:
        idx = int(action) - 1
        if idx < 0 or idx >= len(devices):
            error("Invalid selection.")
            return
    except ValueError:
        error("Invalid input.")
        return
    target = devices[idx]
    if not typer.confirm(f"Deactivate '{target.get('device_name','?')}'?", default=False):
        info("Cancelled.")
        return
    sp2 = PhantomSpinner(); sp2.start(phase="routing")
    ok2, msg = deactivate_device(key, target.get("device_id", ""))
    sp2.stop()
    if ok2:
        success(f"'{target.get('device_name','?')}' removed from license.")
        if target.get("device_id") == this:
            from omnicli.licensing import revoke_local_license
            revoke_local_license()
            warn("Local license cache cleared. Re-activate to use PhantomCLI.")
    else:
        error(f"Failed: {msg}")


def _load_phantom_env() -> int:
    """Read ``~/.phantom/.env`` and apply each ``KEY=VALUE`` line to
    ``os.environ`` (without overwriting values already present).

    Idempotent. Called at the top of ``_run_setup`` and before every
    OAuth flow invocation so values persisted by the setup walkthroughs
    survive restarts and ``phantom update``.

    Returns the number of keys loaded — useful in tests.
    """
    from pathlib import Path as _Path
    env_path = _Path.home() / ".phantom" / ".env"
    if not env_path.is_file():
        return 0
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    loaded = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip surrounding quotes if any (KEY="value" form).
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not key:
            continue
        # Don't overwrite a value already in os.environ — operator's shell
        # export wins over the dotfile. Standard dotenv semantics.
        if key in os.environ:
            continue
        os.environ[key] = val
        loaded += 1
    return loaded


def _run_setup() -> bool:
    """Menu-driven setup wizard."""
    from rich.panel import Panel as _Panel

    # Persisted OAuth client_ids and other tokens live in ~/.phantom/.env.
    # Load them BEFORE rendering the menu so badges / dots are accurate
    # and child processes inherit the right env.
    _load_phantom_env()

    while True:
        console.print()
        console.print(_Panel(
            f"[{CY}]⚡ PHANTOM CLI SETUP[/{CY}]  [{DIM}]v{__version__} · Aravind Labs[/{DIM}]\n"
            f"[{DIM}]Pick a section to configure.[/{DIM}]",
            border_style=BLU, padding=(0, 4),
        ))

        def row(n, key, label, hint=""):
            badge = _badge(key)
            h = f" [{DIM}]{hint}[/{DIM}]" if hint else ""
            console.print(f"  [{CY}]{n}[/{CY}]  {badge}  {label}{h}")

        row("1", "__main_key__",   "Main Engine      ", "API key, model, base URL")
        row("2", "router_api_key", "Router Engine    ", "Fast routing model")
        row("3", "telegram_token", "Telegram         ", "Two-way bot + notifications")
        row("4", "default_trust",  "Trust Level      ", "Command permission mode")
        row("5", "fal_api_key",    "Image APIs       ", "FAL, DALL-E, Stability AI, Replicate")
        row("6", "runway_key",     "Video APIs       ", "RunwayML, Kling, Pika, Luma")
        row("7", "elevenlabs_key", "Voice APIs       ", "ElevenLabs, OpenAI TTS, PlayHT, Deepgram")
        row("8", "dashboard_port", "Dashboard Port   ", f"currently {get_config('dashboard_port','8080')}")

        from omnicli.licensing import get_license_info
        lic     = get_license_info()
        lic_dot = f"[{GRN}]●[/{GRN}]" if lic.get("licensed") else f"[{DIM}]○[/{DIM}]"
        console.print(f"  [{CY}]9[/{CY}]  {lic_dot}  Manage Devices   [{DIM}]View / deactivate registered PCs[/{DIM}]")
        lic_status = f"[{GRN}]Active[/{GRN}]" if lic.get("licensed") else f"[{AMB}]Not activated[/{AMB}]"
        console.print(f"  [{CY}]L[/{CY}]  {lic_dot}  License          [{DIM}]Activate / status — {lic_status}[/{DIM}]")
        v4_dot = f"[{GRN}]●[/{GRN}]"
        # Login dot reflects whether a GitHub OAuth token is already saved.
        login_dot = _login_status_dot()
        console.print(f"  [{CY}]W[/{CY}]  {login_dot}  Login with Account [{DIM}]Free GPT-4o + Claude via GitHub Models · no API key needed[/{DIM}]")
        console.print(f"  [{CY}]V[/{CY}]  {v4_dot}  v4 Features      [{DIM}]Sandbox · Plugins · Channels · MCP · KeyPool · i18n · OTel[/{DIM}]")
        console.print(f"  [{CY}]A[/{CY}]     Full Setup      [{DIM}]Configure everything (1-8)[/{DIM}]")
        console.print(f"  [{CY}]0[/{CY}]     Done\n")

        choice = Prompt.ask(
            f"[{WHT}]Select[/{WHT}]",
            choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "a", "L", "l", "V", "v", "W", "w"],
            default="0",
        ).upper()

        if choice == "0":
            break
        elif choice == "1":
            _setup_main_engine()
        elif choice == "2":
            _setup_router_engine()
        elif choice == "3":
            _setup_telegram()
        elif choice == "4":
            _setup_trust()
        elif choice == "5":
            _setup_media("image", "Image Generation APIs",
                "Generate images using AI. Configure your preferred provider.")
        elif choice == "6":
            _setup_media("video", "Video Generation APIs",
                "Generate videos from text or images.")
        elif choice == "7":
            _setup_media("voice", "Voice APIs (TTS + STT)",
                "Text-to-speech and speech-to-text providers.")
        elif choice == "8":
            port = Prompt.ask(f"[{AMB}]Dashboard port[/{AMB}]", default=get_config("dashboard_port", "8080")).strip()
            save_config("dashboard_port", port)
            success(f"Dashboard port set to {port}")
        elif choice == "9":
            _manage_devices()
        elif choice == "L":
            if lic.get("licensed"):
                info(f"License is active. Key: {lic.get('key', '—')}")
            else:
                _activate_license()
        elif choice == "A":
            if not _setup_main_engine():
                return False
            if not _setup_router_engine():
                return False
            _setup_telegram(); _setup_trust()
            _setup_media("image", "Image APIs", "")
            _setup_media("video", "Video APIs", "")
            _setup_media("voice", "Voice APIs", "")
        elif choice == "V":
            _setup_v4_features()
        elif choice == "W":
            _setup_login_with_account()
        console.print()

    success("Setup complete.")
    return True


def _login_status_dot(provider: str = "github") -> str:
    """Return ●/○ depending on whether *provider* is configured.

    Considers two paths as "configured":

    1. An OAuth token exists in TokenStore and is not expired.
    2. The engine config (``main_url``) points at this provider's
       OpenAI-compatible endpoint — covers the AI Studio API key path
       for Google Gemini, where there is no OAuth token but the user
       has pasted a working API key.
    """
    # Path 1: OAuth token present.
    try:
        from phantom.agent.oauth_provider import TokenStore
        store = TokenStore.default()
        tokens = store.load(provider)
        if tokens is not None and not tokens.expired():
            return f"[{GRN}]●[/{GRN}]"
    except Exception:
        pass
    # Path 2: engine is wired to the provider's OpenAI-compat endpoint.
    try:
        url = (get_config("main_url", "") or "").lower()
    except Exception:
        url = ""
    if provider == "github" and "models.github.ai" in url:
        return f"[{GRN}]●[/{GRN}]"
    if provider == "google" and "generativelanguage.googleapis.com" in url:
        return f"[{GRN}]●[/{GRN}]"
    return f"[{DIM}]○[/{DIM}]"


def _setup_login_with_account():
    """Walk the user through GitHub OAuth login → free GPT-4o + Claude.

    This delegates to ``phantom auth login --provider github``, which
    drives the device-code flow, saves the token, and auto-wires the
    engine config so ``phantom chat`` works immediately afterwards
    with no further setup.
    """
    from rich.panel import Panel as _Panel
    import subprocess as _subprocess
    import sys as _sys
    import os as _os

    while True:
        console.print()
        console.print(_Panel(
            f"[{CY}]⚡ LOGIN WITH ACCOUNT[/{CY}]  [{DIM}]Free LLM access — no API key required[/{DIM}]\n"
            f"[{DIM}]Sign in with your existing developer account; Phantom uses\n"
            f"the provider's OAuth grant to call models on your behalf.[/{DIM}]",
            border_style=GRN, padding=(0, 4),
        ))

        gh_cid = _os.environ.get("PHANTOM_OAUTH_GITHUB_CLIENT_ID", "")
        gh_msg = (
            f"[{GRN}]configured[/{GRN}]" if gh_cid
            else f"[{AMB}]not set — see option G first[/{AMB}]"
        )
        # Pull the active model so the user knows what's currently in effect.
        active_model = get_config("main_model", "(unset)")
        rows = [
            ("1", "GitHub Models",
             f"Free GPT-4o + Claude 3.5 Sonnet + Llama 3.3 + Phi 4. {_login_status_dot('github')}",
             True),
            ("2", "Google Gemini",
             f"Free Gemini via AI Studio API key. {_login_status_dot('google')}",
             True),
            ("3", "Anthropic Console",
             f"[{AMB}]EXPERIMENTAL[/{AMB}] — Anthropic doesn't currently expose API access via OAuth.",
             True),
            ("4", "OpenAI / ChatGPT",
             f"[{AMB}]EXPERIMENTAL[/{AMB}] — ChatGPT subscription is NOT the API. Won't grant API access.",
             True),
            ("M", "Change model",  f"Pick a model from the active provider's catalog (active: {active_model})", True),
            ("S", "Status",        "Show which providers are currently logged in.",        True),
            ("L", "Logout",        "Forget tokens for a provider.",                        True),
            ("G", "GitHub setup",  f"Configure GitHub OAuth client_id  [{gh_msg}]",        True),
            ("0", "Back",          "Return to main setup menu.",                           True),
        ]

        for n, label, desc, _enabled in rows:
            console.print(f"  [{CY}]{n}[/{CY}]  {label:<22} [{DIM}]{desc}[/{DIM}]")

        console.print()
        choice = Prompt.ask(
            f"[{WHT}]Select[/{WHT}]",
            choices=["0", "1", "2", "3", "4", "M", "m", "S", "s", "L", "l", "G", "g"],
            default="0",
        ).upper()

        if choice == "0":
            break
        elif choice == "1":
            _login_run_phantom_auth("github")
        elif choice == "2":
            _login_google_submenu()
        elif choice == "3":
            warn("Anthropic doesn't expose API access via OAuth. Use option 4 of "
                 "the main menu (Trust Level) and a real ANTHROPIC_API_KEY for now.")
        elif choice == "4":
            warn("OpenAI's ChatGPT login does not grant API access. A ChatGPT Plus "
                 "subscription is a separate product from the OpenAI API. Use the "
                 "main-menu option 1 with an OpenAI API key.")
        elif choice == "M":
            _login_change_model()
        elif choice == "S":
            _login_run_phantom_auth_subcmd(["status"])
        elif choice == "L":
            provider = Prompt.ask(
                f"[{AMB}]Provider to log out[/{AMB}]",
                choices=["github", "google", "anthropic", "openai", "skip"],
                default="skip",
            )
            if provider != "skip":
                _login_run_phantom_auth_subcmd(["logout", "--provider", provider])
        elif choice == "G":
            _login_setup_github_client_id()
        console.print()


# Curated model catalogs per provider. Free-form input is also accepted so
# users can reach models we haven't enumerated. Keep these short and ordered
# by likely usefulness.
_GITHUB_MODELS = [
    ("gpt-4o",                       "OpenAI · default · fast + smart, ~10 RPD on free tier"),
    ("gpt-4o-mini",                  "OpenAI · cheap + fast, much higher daily limit"),
    ("o1-mini",                      "OpenAI · reasoning model"),
    ("Llama-3.3-70B-Instruct",       "Meta · open-weights, top-tier for cost"),
    ("Llama-3.2-90B-Vision-Instruct","Meta · multimodal"),
    ("Phi-4",                        "Microsoft · small + efficient"),
    ("Mistral-Large-2411",           "Mistral · long context"),
    ("Codestral-2501",               "Mistral · code-focused"),
    ("DeepSeek-V3",                  "DeepSeek · solid open model"),
    ("Cohere-command-r-plus-08-2024","Cohere · RAG-optimised"),
]
_GEMINI_MODELS = [
    ("gemini-2.0-flash",             "default · fast, 15 RPM / 1500 RPD on free tier"),
    ("gemini-2.0-flash-thinking-exp","reasoning variant"),
    ("gemini-1.5-pro",               "more capable, lower limits (60 RPD)"),
    ("gemini-1.5-flash",             "older fast model"),
    ("gemini-1.5-flash-8b",          "smallest + cheapest"),
]


def _login_change_model():
    """Let the user pick a new ``main_model`` for the active provider.

    Detects which provider is active by looking at ``main_url``, then
    shows the curated catalog for that provider plus a free-form
    "Other" option. Persists to the engine config.
    """
    from omnicli.memory import save_config
    url = (get_config("main_url", "") or "").lower()
    if "models.github.ai" in url:
        provider_label = "GitHub Models"
        catalog = _GITHUB_MODELS
    elif "generativelanguage.googleapis.com" in url:
        provider_label = "Google Gemini"
        catalog = _GEMINI_MODELS
    else:
        warn("No login provider detected. Log in via option 1 (GitHub) or 2 (Google) first, "
             "or use main-menu option 1 (Main Engine) to set a custom provider + model directly.")
        return

    current = get_config("main_model", "(unset)")
    console.print(
        f"\n[{CY}]── Change model · {provider_label} ──[/{CY}]\n"
        f"[{DIM}]Current model: {current}[/{DIM}]\n"
    )
    for i, (name, desc) in enumerate(catalog, 1):
        marker = f"[{GRN}]●[/{GRN}]" if name == current else f"[{DIM}]○[/{DIM}]"
        console.print(f"  [{CY}]{i}[/{CY}]  {marker}  {name:<32} [{DIM}]{desc}[/{DIM}]")
    console.print(f"  [{CY}]o[/{CY}]      Other (type model name)")
    console.print(f"  [{CY}]0[/{CY}]      Back\n")

    choices = [str(i) for i in range(1, len(catalog) + 1)] + ["0", "o", "O"]
    raw = Prompt.ask(
        f"[{WHT}]Select[/{WHT}]", choices=choices, default="0",
    )
    raw = raw.upper() if raw.isalpha() else raw
    if raw == "0":
        return
    if raw == "O":
        new_model = Prompt.ask(
            f"[{AMB}]Model name (free-form, e.g. 'mistral-medium-3' or 'gemini-2.5-flash')[/{AMB}]",
            default=current,
        ).strip()
        if not new_model:
            info("Empty input. Aborting.")
            return
    else:
        idx = int(raw) - 1
        new_model = catalog[idx][0]

    try:
        save_config("main_model", new_model)
    except Exception as exc:
        error(f"Could not save model: {exc}")
        return
    success(f"Model changed: {current} → {new_model}. Run `phantom chat` to use it.")


def _login_google_submenu():
    """Present the Google login paths.

    OAuth device flow is *not* offered — Google's OAuth server rejects
    the ``generative-language`` scope on the device-flow endpoint with
    400 ``invalid_scope``, and the OpenAI-compat shim at
    ``v1beta/openai`` wants an API key (``AIza…``), not a bearer
    (``ya29…``). AI Studio key paste is the only working path today.
    Option (b) opens an explanation panel.
    """
    console.print(
        f"\n[{CY}]── Google Gemini login ──[/{CY}]\n\n"
        f"  [{CY}]a[/{CY}]  AI Studio API key  [{DIM}]Recommended: paste a key from https://aistudio.google.com/app/apikey[/{DIM}]\n"
        f"  [{CY}]b[/{CY}]  Why no OAuth?       [{DIM}]Read the technical reason device flow can't reach Gemini[/{DIM}]\n"
        f"  [{CY}]0[/{CY}]  Back\n"
    )
    sub = Prompt.ask(
        f"[{WHT}]Select[/{WHT}]",
        choices=["0", "a", "A", "b", "B"],
        default="a",
    ).upper()
    if sub == "0":
        return
    if sub == "A":
        _login_paste_google_ai_studio_key()
    elif sub == "B":
        _login_explain_google_oauth_limit()


def _login_explain_google_oauth_limit():
    """Lay out why Phantom doesn't ship Google OAuth login for Gemini."""
    from rich.panel import Panel as _Panel
    body = (
        f"OAuth device flow [{AMB}]cannot[/{AMB}] reach the Gemini API today, for two stacked\n"
        f"reasons that aren't fixable on the client side:\n\n"
        f"[{CY}]1.  Google blocks the scope.[/{CY}]\n"
        f"    POST oauth2.googleapis.com/device/code with scope\n"
        f"    [{DIM}]https://www.googleapis.com/auth/generative-language[/{DIM}] returns:\n\n"
        f"        [{AMB}]400 invalid_scope: 'Invalid device flow scope'[/{AMB}]\n\n"
        f"    Google's device-flow allowlist only covers email/profile/openid +\n"
        f"    specific YouTube/Drive scopes. AI scopes are excluded by design.\n\n"
        f"[{CY}]2.  The Gemini OpenAI-compat shim wants API keys, not bearers.[/{CY}]\n"
        f"    Even if the scope worked, [{DIM}]generativelanguage.googleapis.com/v1beta/openai/[/{DIM}]\n"
        f"    validates against the AI Studio key system (keys starting with [{AMB}]AIza[/{AMB}]).\n"
        f"    OAuth access tokens (starting with [{AMB}]ya29[/{AMB}]) are rejected there even with\n"
        f"    the right scope.\n\n"
        f"[{GRN}]Workable today:[/{GRN}] paste an AI Studio key (option a). Same free tier\n"
        f"(15 req/min, 1500 req/day on Gemini 2.0 Flash). 30-second setup. No GCP project.\n\n"
        f"[{DIM}]A proper Gemini OAuth flow would need: Desktop OAuth client + loopback\n"
        f"redirect + PKCE + a Gemini-native HTTP client (not the OpenAI-compat shim).\n"
        f"~250 LoC of new code; can ship if there's demand.[/{DIM}]"
    )
    console.print(_Panel(body, title=f"[{AMB}]Google OAuth — why it doesn't work for Gemini[/{AMB}]",
                         border_style=AMB, padding=(1, 3)))


def _login_paste_google_ai_studio_key():
    """Walk the user through getting a Google AI Studio API key + saving it.

    This is the easy path: no GCP project, no OAuth, no consent screen.
    The key just goes straight into the engine config so phantom chat
    talks to Gemini via the OpenAI-compat endpoint.
    """
    import webbrowser as _wb
    from omnicli.auth import save_api_key
    from omnicli.memory import save_config

    console.print(
        f"\n[{CY}]── Google AI Studio API key ──[/{CY}]\n"
        f"[{DIM}]Free tier covers Gemini 2.0 Flash (15 RPM, 1500 RPD) + Gemini 1.5 Pro (60 RPD).[/{DIM}]\n\n"
        f"  1. We'll open [{CY}]https://aistudio.google.com/app/apikey[/{CY}] in your browser.\n"
        f"  2. Click [{AMB}]Create API key[/{AMB}] (use an existing GCP project or let it create one — the\n"
        f"     'free tier' project does NOT need billing enabled, unlike full Cloud Console).\n"
        f"  3. Copy the key (starts with [{AMB}]AIza[/{AMB}]…).\n"
    )
    open_browser = Prompt.ask(
        f"[{AMB}]Open the API key page in your browser now? [Y/n][/{AMB}]",
        choices=["y", "Y", "n", "N", ""],
        default="y",
    ).lower()
    if open_browser != "n":
        try:
            _wb.open("https://aistudio.google.com/app/apikey")
        except Exception as exc:
            warn(f"Could not auto-open browser: {exc}. Visit the URL manually.")

    key = stdiomask.getpass(
        prompt=f"\nPaste the API key (input hidden, blank to cancel): ",
        mask="*",
    ).strip()
    if not key:
        info("No key entered. Aborting.")
        return
    if not key.startswith("AIza"):
        warn("That doesn't look like a Google AI Studio key (expected to start with 'AIza'). "
             "Saving anyway in case the format changed.")

    try:
        save_api_key(key)
        save_config("main_url", "https://generativelanguage.googleapis.com/v1beta/openai")
        save_config("main_model", "gemini-2.0-flash")
    except Exception as exc:
        error(f"Could not save engine config: {exc}")
        return
    success(
        "Saved. Engine wired to https://generativelanguage.googleapis.com/v1beta/openai "
        "with default model gemini-2.0-flash. Run `phantom chat` to start."
    )


def _login_setup_google_client_id():
    """Walk the user through registering a Google OAuth client + saving the
    client_id locally so the device flow actually works.

    Google's device flow requires an OAuth client registered as
    'TVs and Limited Input devices'. Desktop / Web client types do NOT
    accept the device-code grant.
    """
    from pathlib import Path as _Path
    import os as _os
    import webbrowser as _wb

    console.print(
        f"\n[{CY}]── Google OAuth client_id setup ──[/{CY}]\n"
        f"[{DIM}]Skip this if you only want Gemini — option 2 → a (paste API key) is\n"
        f"the easy path. Use this only if you want true OAuth login.[/{DIM}]\n\n"
        f"  1. Go to [{CY}]https://console.cloud.google.com/apis/credentials[/{CY}]\n"
        f"  2. Create / select a project.\n"
        f"  3. Enable the [{AMB}]Generative Language API[/{AMB}]:\n"
        f"     [{CY}]https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com[/{CY}]\n"
        f"  4. Configure [{AMB}]OAuth consent screen[/{AMB}] (External, fill app name + email,\n"
        f"     add scope [{AMB}].../auth/generative-language[/{AMB}]).\n"
        f"  5. Credentials → [{AMB}]Create Credentials → OAuth client ID[/{AMB}] →\n"
        f"     application type: [{GRN}]TVs and Limited Input devices[/{GRN}] (REQUIRED).\n"
        f"  6. Copy the [{AMB}]Client ID[/{AMB}] (ends with .apps.googleusercontent.com).\n"
    )
    open_browser = Prompt.ask(
        f"[{AMB}]Open the credentials page now? [Y/n][/{AMB}]",
        choices=["y", "Y", "n", "N", ""],
        default="y",
    ).lower()
    if open_browser != "n":
        try:
            _wb.open("https://console.cloud.google.com/apis/credentials")
        except Exception:
            pass

    cid = Prompt.ask(
        f"\n[{AMB}]Paste Google OAuth Client ID (blank to skip)[/{AMB}]",
        default="",
    ).strip()
    if not cid:
        info("No client_id entered. You can re-run this option later.")
        return

    env_path = _Path.home() / ".phantom" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    new_lines = [
        line for line in existing_lines
        if not line.strip().startswith("PHANTOM_OAUTH_GOOGLE_CLIENT_ID=")
    ]
    new_lines.append(f"PHANTOM_OAUTH_GOOGLE_CLIENT_ID={cid}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    _os.environ["PHANTOM_OAUTH_GOOGLE_CLIENT_ID"] = cid
    success(f"Saved client_id to {env_path}. Now choose option 2 → b to log in.")


def _phantom_install_dir() -> str:
    """Return the directory that contains the omnicli/ and phantom/ packages.

    Derived from this file's location: omnicli/cli.py → omnicli/ → install dir.
    Required for spawning ``python -m phantom.cli`` subprocesses, because
    with ``-m`` Python adds the *cwd* to sys.path — and the user's shell
    cwd (typically their home dir) doesn't contain the phantom package.
    """
    import os as _os
    return _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))


def _phantom_subprocess_env() -> dict:
    """Return an environment dict with PYTHONPATH set so a child Python
    process can import both ``phantom`` and ``omnicli`` regardless of cwd.

    Also re-applies ``~/.phantom/.env`` so the child sees OAuth client_ids
    and similar tokens even if the parent was launched without them.
    """
    import os as _os
    _load_phantom_env()  # idempotent; updates _os.environ in this process
    env = _os.environ.copy()
    install_dir = _phantom_install_dir()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        install_dir + _os.pathsep + existing if existing else install_dir
    )
    return env


def _login_run_phantom_auth(provider: str):
    """Drive the device-code flow for *provider* via the phantom CLI."""
    import subprocess as _subprocess, sys as _sys
    install_dir = _phantom_install_dir()
    env = _phantom_subprocess_env()
    cmd = [_sys.executable, "-m", "phantom.cli", "auth", "login",
           "--provider", provider]
    console.print(f"\n[{DIM}]Running: phantom auth login --provider {provider}[/{DIM}]\n")
    try:
        # NOT capture_output — the user MUST see the device code + verification URL
        # and we want to forward stdin in case the flow ever prompts.
        rc = _subprocess.call(cmd, cwd=install_dir, env=env)
        if rc != 0:
            warn(f"Login failed (exit {rc}). If this is a new install, you may "
                 f"need to register a {provider} OAuth app and set "
                 f"PHANTOM_OAUTH_{provider.upper()}_CLIENT_ID first.")
        else:
            success(f"Login flow complete for {provider}.")
    except Exception as exc:
        error(f"Could not invoke phantom auth: {exc}")


def _login_run_phantom_auth_subcmd(args: list):
    import subprocess as _subprocess, sys as _sys
    install_dir = _phantom_install_dir()
    env = _phantom_subprocess_env()
    cmd = [_sys.executable, "-m", "phantom.cli", "auth"] + list(args)
    try:
        rc = _subprocess.call(cmd, cwd=install_dir, env=env)
        if rc != 0:
            warn(f"Command exited with status {rc}.")
    except Exception as exc:
        error(f"Could not run phantom auth: {exc}")


def _login_setup_github_client_id():
    """Walk the user through registering a GitHub OAuth App + storing the
    client_id locally so the next login attempt actually works.

    GitHub's device flow requires a registered OAuth App with Device
    Flow enabled. Takes about 30 seconds to set up.
    """
    from pathlib import Path as _Path
    import os as _os

    console.print(
        f"\n[{CY}]── GitHub OAuth client_id setup ──[/{CY}]\n"
        f"[{DIM}]GitHub's device flow needs a registered OAuth App.[/{DIM}]\n\n"
        f"  1. Open: [{CY}]https://github.com/settings/developers[/{CY}]\n"
        f"  2. Click [{AMB}]New OAuth App[/{AMB}].\n"
        f"  3. Application name : [{AMB}]Phantom CLI[/{AMB}] (anything you like)\n"
        f"     Homepage URL     : [{AMB}]https://phantom.aravindlabs.tech[/{AMB}]\n"
        f"     Callback URL     : [{AMB}]http://localhost[/{AMB}] (unused but required)\n"
        f"  4. After creating: tick [{GRN}]Enable Device Flow[/{GRN}], then save.\n"
        f"  5. Copy the [{AMB}]Client ID[/{AMB}] shown at the top.\n"
    )
    cid = Prompt.ask(
        f"[{AMB}]Paste GitHub OAuth Client ID (blank to skip)[/{AMB}]",
        default="",
    ).strip()
    if not cid:
        info("No client_id entered. You can re-run this option later.")
        return

    env_path = _Path.home() / ".phantom" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    # Idempotent: if a previous client_id is in the file, replace it.
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    new_lines = [
        line for line in existing_lines
        if not line.strip().startswith("PHANTOM_OAUTH_GITHUB_CLIENT_ID=")
    ]
    new_lines.append(f"PHANTOM_OAUTH_GITHUB_CLIENT_ID={cid}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Also export into the current process so the next subprocess we spawn
    # picks it up without a shell restart.
    _os.environ["PHANTOM_OAUTH_GITHUB_CLIENT_ID"] = cid

    success(f"Saved client_id to {env_path}. You can now choose option 1 to log in.")


def _setup_v4_features():
    """Show the v4 capability sheet and offer drill-downs for each subsystem."""
    from rich.panel import Panel as _Panel
    from rich.table import Table as _Table

    while True:
        console.print()
        console.print(_Panel(
            f"[{CY}]⚡ PHANTOM v4 FEATURES[/{CY}]  [{DIM}]v{__version__} · 2026-04-26[/{DIM}]\n"
            f"[{DIM}]Eight pillars added in v4. Configure or inspect any of them below.[/{DIM}]",
            border_style=GRN, padding=(0, 4),
        ))

        rows = [
            ("1", "Sandbox",        "Tiered: bubblewrap → firejail → unshare → docker. Append-only audit log."),
            ("2", "Plugins",        "Ed25519-signed bundles. clock, weather, gh-search, code-search, todo."),
            ("3", "Channels",       "WebChat (trust 3) + Telegram, Discord, Slack (trust 2). Per-channel size caps."),
            ("4", "MCP + ACP",      "JSON-RPC 2.0 client/server. ACP child-agent runtime with topological waves."),
            ("5", "Skills + Memory","SKILL.md bundles. SQLite + FTS5 + TF-IDF reranker, namespaced."),
            ("6", "Voice/Canvas/PWA","VAD-driven STT/TTS, typed Canvas tree, web app manifest + service worker."),
            ("7", "i18n",           "Locales: en, hi, te, es, zh. Locale-parity enforced by test."),
            ("8", "KeyPool + OTel", "Round-robin API key rotation with cooldown. OTel-export-shape metrics."),
            ("9", "Onboarding",     "Pure-data state machine wizard for first-time setup."),
            ("D", "Doctor",         "Run host capability report (sandbox backend, GPU, deps)."),
            ("P", "Plugin manager", "List / enable / disable plugins."),
            ("0", "Back",           "Return to main setup menu."),
        ]

        for n, label, desc in rows:
            console.print(f"  [{CY}]{n}[/{CY}]  [{GRN}]●[/{GRN}]  {label:<18} [{DIM}]{desc}[/{DIM}]")

        console.print()
        choice = Prompt.ask(
            f"[{WHT}]Select v4 feature[/{WHT}]",
            choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "D", "d", "P", "p"],
            default="0",
        ).upper()

        if choice == "0":
            break
        elif choice == "1":
            _v4_sandbox_status()
        elif choice == "2":
            _v4_plugin_overview()
        elif choice == "3":
            _v4_channels_overview()
        elif choice == "4":
            _v4_mcp_overview()
        elif choice == "5":
            _v4_memory_overview()
        elif choice == "6":
            _v4_voice_canvas_overview()
        elif choice == "7":
            _v4_i18n_overview()
        elif choice == "8":
            _v4_keypool_otel_overview()
        elif choice == "9":
            _v4_onboarding_overview()
        elif choice == "D":
            _v4_run_doctor()
        elif choice == "P":
            _v4_plugin_manager()
        console.print()


def _v4_panel(title: str, body: str, hint: str = ""):
    from rich.panel import Panel as _Panel
    suffix = f"\n\n[{DIM}]{hint}[/{DIM}]" if hint else ""
    console.print(_Panel(body + suffix, title=f"[{GRN}]{title}[/{GRN}]", border_style=GRN, padding=(1, 3)))


def _v4_sandbox_status():
    try:
        from phantom.sandbox import detect_backend  # type: ignore
        backend = detect_backend()
        body = f"Active backend: [{CY}]{backend}[/{CY}]\n\nFallback chain: bubblewrap → firejail → unshare → docker.\nEvery shell call routes through phantom.sandbox.run.\nAudit log: ~/.phantom/sandbox-audit.log (mode 0600, append-only)."
    except Exception as e:
        body = f"Could not query sandbox backend: {e}\n\nOn Windows the unshare/bwrap/firejail backends are unavailable; use the docker backend for full isolation, or run in default mode for v3-compatible execution."
    _v4_panel("Sandbox", body, "Run `phantom doctor` for the full host capability report.")


def _v4_plugin_overview():
    body = (
        "Bundled reference plugins:\n"
        f"  [{CY}]clock[/{CY}]        — no caps\n"
        f"  [{CY}]weather[/{CY}]      — network\n"
        f"  [{CY}]gh-search[/{CY}]    — network + executor\n"
        f"  [{CY}]code-search[/{CY}]  — executor + filesystem\n"
        f"  [{CY}]todo[/{CY}]         — memory\n\n"
        "Manage with `phantom plugin {list,enable,disable}`.\n"
        "User plugins go in ~/.phantom/plugins/<name>/manifest.json with Ed25519 signature."
    )
    _v4_panel("Plugin SDK", body)


def _v4_channels_overview():
    body = (
        "Channel adapters (trust caps shown):\n"
        f"  [{GRN}]WebChat[/{GRN}]   trust 3 (local user, embedded WebSocket)\n"
        f"  [{GRN}]Telegram[/{GRN}]  trust 2 (configured at menu item 3)\n"
        f"  [{GRN}]Discord[/{GRN}]   trust 2 (set DISCORD_BOT_TOKEN in ~/.phantom/.env)\n"
        f"  [{GRN}]Slack[/{GRN}]     trust 2 (set SLACK_BOT_TOKEN + SLACK_APP_TOKEN)\n\n"
        "All four share the same agent loop and trust-cap-enforced router.\n"
        "Per-channel size capping with truncation marker."
    )
    _v4_panel("Multi-Channel Adapters", body, "Discord/Slack tokens go in ~/.phantom/.env — restart phantom to pick up.")


def _v4_mcp_overview():
    body = (
        "MCP (Model Context Protocol) — JSON-RPC 2.0 client AND server.\n"
        "  Client config:  ~/.phantom/mcp.json\n"
        "  Server start:   phantom mcp_serve\n"
        "  Discoverable:   tools/list, tools/call, resources/list\n\n"
        "ACP runtime (child-agent orchestration):\n"
        "  Topological dependency waves\n"
        "  Per-wave error isolation\n"
        "  Mass-spawn cap: 1024 lifetime, configurable per-wave"
    )
    _v4_panel("MCP + ACP", body)


def _v4_memory_overview():
    body = (
        "Memory v2:\n"
        "  Backend:   SQLite + FTS5 full-text index\n"
        "  Reranker:  hashing-trick TF-IDF cosine\n"
        "  Namespace: (user, project, session) — no cross-leak\n"
        f"  Storage:   ~/.phantom/memory.db\n\n"
        "Skills system:\n"
        "  SKILL.md bundles with trigger-based activation\n"
        f"  Bundled: [{CY}]git_workflow[/{CY}]\n"
        "  User skills: ~/.phantom/skills/<name>/SKILL.md"
    )
    _v4_panel("Skills + Memory v2", body)


def _v4_voice_canvas_overview():
    body = (
        "Realtime voice:\n"
        "  VoiceLoop — VAD-driven STT flush + TTS playback queue + barge-in cancellation\n"
        "  Configure provider via the existing menu item 7 (Voice APIs)\n\n"
        "Canvas UI tree:\n"
        "  Typed nodes: text, code, table, chart, button, form, container\n"
        "  Per-kind validation, JSON-serialisable\n\n"
        "PWA:\n"
        "  Web app manifest + service worker\n"
        "  stale-while-revalidate cache strategy\n"
        "  network-first for /app/api/, skipWaiting on activate"
    )
    _v4_panel("Voice + Canvas + PWA", body)


def _v4_i18n_overview():
    body = (
        "Available locales:\n"
        f"  [{CY}]en[/{CY}]  English (default)\n"
        f"  [{CY}]hi[/{CY}]  Hindi\n"
        f"  [{CY}]te[/{CY}]  Telugu\n"
        f"  [{CY}]es[/{CY}]  Spanish\n"
        f"  [{CY}]zh[/{CY}]  Chinese\n\n"
        "Set via PHANTOM_LOCALE env var or ~/.phantom/config.json key 'locale'.\n"
        "Locale parity enforced by test — every string in every locale or CI fails."
    )
    _v4_panel("i18n", body)


def _v4_keypool_otel_overview():
    body = (
        "KeyPool (auth rotation):\n"
        "  Round-robin across N API keys per provider, with per-key cooldown on 429/5xx\n"
        "  stats() exposes only the last 4 chars of each key — never the full key\n"
        "  Configure: ~/.phantom/.env with NVIDIA_API_KEY, NVIDIA_API_KEY_2, NVIDIA_API_KEY_3 etc.\n\n"
        "Observability:\n"
        "  Dependency-free Counter / Histogram / Registry primitives\n"
        "  OTel-export-compatible shape — point OTEL_EXPORTER_OTLP_ENDPOINT at your collector\n"
        "  Built-in metrics: model.calls, tool.dispatch, hook.fire, sandbox.run"
    )
    _v4_panel("KeyPool + Observability", body)


def _v4_onboarding_overview():
    body = (
        "First-time setup wizard. Pure-data state machine — no TTY required.\n\n"
        "States: license → providers → telegram → trust → channels → done.\n"
        "Current run state stored at ~/.phantom/onboarding.json.\n\n"
        "Re-run anytime: `phantom onboarding` (resumes if mid-run, restarts if complete)."
    )
    _v4_panel("Onboarding Wizard", body)


def _v4_run_doctor():
    import subprocess, sys as _sys
    install_dir = _phantom_install_dir()
    env = _phantom_subprocess_env()
    console.print(f"\n[{DIM}]Running `phantom doctor`…[/{DIM}]\n")
    try:
        result = subprocess.run(
            [_sys.executable, "-m", "phantom.cli", "doctor"],
            capture_output=True, text=True, timeout=30,
            cwd=install_dir, env=env,
        )
        if result.stdout:
            console.print(result.stdout)
        if result.returncode != 0 and result.stderr:
            console.print(f"[{AMB}]{result.stderr}[/{AMB}]")
    except Exception as e:
        console.print(f"[{AMB}]Doctor unavailable: {e}[/{AMB}]")


def _v4_plugin_manager():
    import subprocess, sys as _sys
    install_dir = _phantom_install_dir()
    env = _phantom_subprocess_env()
    console.print(f"\n[{DIM}]Running `phantom plugin list`…[/{DIM}]\n")
    try:
        result = subprocess.run(
            [_sys.executable, "-m", "phantom.cli", "plugin", "list"],
            capture_output=True, text=True, timeout=15,
            cwd=install_dir, env=env,
        )
        if result.stdout:
            console.print(result.stdout)
        if result.returncode != 0 and result.stderr:
            console.print(f"[{AMB}]{result.stderr}[/{AMB}]")
    except Exception as e:
        console.print(f"[{AMB}]Plugin manager unavailable: {e}[/{AMB}]")
    console.print(f"[{DIM}]To enable / disable: phantom plugin enable <name>  ·  phantom plugin disable <name>[/{DIM}]")


def _launch_dashboard(port: int):
    import uvicorn
    init_db()

    if not _require_license():
        raise SystemExit(0)

    from omnicli.tui import show_terms_and_accept
    if not show_terms_and_accept():
        raise SystemExit(0)

    if not get_api_key() or not get_config("router_api_key"):
        from rich.panel import Panel as _Panel
        console.print(_Panel(
            f"[{CY}]⚡ PHANTOM CLI v{__version__}[/{CY}]  [{DIM}]Aravind Labs[/{DIM}]\n\n"
            f"[{AMB}]First-time setup required.[/{AMB}]\n\n"
            f"Run: [{CY}]python run.py setup[/{CY}]",
            border_style=BLU, padding=(1, 4),
        ))
        return

    from omnicli.licensing import is_licensed as _is_licensed
    licensed = _is_licensed()
    boot_screen(__version__, licensed)

    from omnicli import telegram_bot
    if telegram_bot.start():
        info("📱 Telegram bot active")

    import threading
    def _bg():
        upd = _check_for_update()
        if upd:
            warn(f"Update available: v{upd['version']} — run `python run.py update`")
    threading.Thread(target=_bg, daemon=True).start()

    effective_port = int(get_config("dashboard_port", str(port)))
    # Dashboard binds to loopback by default — prevents anyone on the same LAN/WiFi
    # from reaching the unauthenticated WebSocket. Override by setting
    # config `dashboard_host` (e.g. 0.0.0.0) only when you truly need LAN access.
    effective_host = get_config("dashboard_host", "127.0.0.1") or "127.0.0.1"
    info(f"Dashboard → http://localhost:{effective_port}")
    console.print()

    uvicorn.run("omnicli.dashboard:app", host=effective_host, port=effective_port, log_level="warning")


# ─── CLI COMMANDS ─────────────────────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def default(ctx: typer.Context, port: int = typer.Option(8080, help="Dashboard port")):
    """⚡ PhantomCLI v4.0.10 — God Mode AI OS · Aravind Labs"""
    if ctx.invoked_subcommand is None:
        _launch_dashboard(port)


@app.command()
def dashboard(port: int = typer.Option(8080, help="Port to run dashboard on")):
    """Launch the PhantomCLI web dashboard."""
    _launch_dashboard(port)


@app.command()
def setup():
    """Interactive setup wizard — configure AI, Telegram, Media APIs and more."""
    init_db()
    ok = _run_setup()
    if not ok:
        raise typer.Exit(1)


# ─── FIRST-TIME ONBOARDING WIZARD ─────────────────────────────────────────────

def _onboarding_wizard():
    """
    Premium first-time setup experience.
    Detects system → collects owner profile → saves to DB.
    Better than Claude's intro: animated, contextual, personal.
    """
    import shutil
    from omnicli.sysinfo import detect_system, format_system_card
    from omnicli.memory import save_owner_profile, save_system_info
    from omnicli.tui import (
        matrix_rain, hud_scanline, _hud_top, _hud_bot, _hud_sep,
        _typewriter, _glitch_flash, _scan_bar, GRN, CY, AMB, DIM, RED, WHT
    )

    os.system("cls" if os.name == "nt" else "clear")
    time.sleep(0.1)

    # ── Matrix rain opening ──────────────────────────────────────────────────
    matrix_rain(duration=2.0)

    # ── Glitch flash ─────────────────────────────────────────────────────────
    _glitch_flash(duration=0.3)

    # ── Banner ───────────────────────────────────────────────────────────────
    from omnicli.tui import BANNER_FULL
    for line in BANNER_FULL.strip("\n").split("\n"):
        sys.stdout.write(f"  \033[36m{line}\033[0m\n")
        sys.stdout.flush()
        time.sleep(0.025)

    console.print()
    _typewriter(
        f"  ◈  FIRST BOOT DETECTED  ·  INITIALISING PHANTOM NEURAL INTERFACE  ◈",
        colour_code="36", delay=0.012,
    )
    console.print()
    time.sleep(0.3)

    # ── System detection ─────────────────────────────────────────────────────
    hud_scanline("DETECTING HARDWARE", width=55, cycles=1)
    _scan_bar("SYSTEM SCAN", steps=24, delay=0.02)

    sys_info = detect_system()
    save_system_info(sys_info)

    _hud_top("SYSTEM PROFILE DETECTED")
    for label, value, colour in format_system_card(sys_info):
        sys.stdout.write(
            f"  \033[36m║\033[0m  \033[2m{label:<18}\033[0m  "
            f"\033[{'32' if colour == GRN else '36'}m{value}\033[0m\n"
        )
        sys.stdout.flush()
        time.sleep(0.06)
    _hud_bot()

    console.print()
    _typewriter(
        f"  ◈  {sys_info['max_agents']} parallel agents configured based on your hardware.",
        colour_code="32", delay=0.013,
    )
    console.print()
    time.sleep(0.5)

    # ── Onboarding form ───────────────────────────────────────────────────────
    _hud_top("OPERATOR IDENTIFICATION  ·  STEP 1 / 4")
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[2mLet's personalise your Phantom. Press Enter to skip optional fields.\033[0m\n"
    )
    _hud_bot()
    console.print()

    profile = {}

    def _ask(label: str, hint: str = "", required: bool = False, default: str = "") -> str:
        hint_str = f"  \033[2m{hint}\033[0m" if hint else ""
        if hint_str:
            sys.stdout.write(f"{hint_str}\n")
        prompt_colour = "\033[96m" if required else "\033[36m"
        req_badge     = " \033[33m(required)\033[0m" if required else " \033[2m(optional)\033[0m"
        sys.stdout.write(f"  {prompt_colour}{label}\033[0m{req_badge}: ")
        sys.stdout.flush()
        try:
            val = input("").strip()
        except (EOFError, KeyboardInterrupt):
            val = ""
        return val or default

    # Step 1: Identity
    first_name = _ask("Your first name",   "How should Phantom address you?",      required=True)
    while not first_name:
        sys.stdout.write("  \033[33m  ↳ First name is required.\033[0m\n")
        first_name = _ask("Your first name", required=True)

    full_name  = _ask("Full name",         "Used in file headers and reports")
    profile["owner_first_name"] = first_name
    profile["owner_name"]       = full_name or first_name

    console.print()
    _typewriter(f"  ◈  Great to meet you, {first_name}! 👋", colour_code="32", delay=0.015)
    console.print()
    time.sleep(0.2)

    # Step 2: Bot persona
    _hud_top("BOT CONFIGURATION  ·  STEP 2 / 4")
    _hud_bot()
    console.print()

    bot_name = _ask(
        "What should I be called?",
        f'Name your AI (default: PHANTOM)',
        default="PHANTOM",
    )
    profile["bot_name"] = bot_name.upper()

    bot_personality = _ask(
        "Personality style",
        "professional / casual / technical / creative  (default: professional)",
        default="professional",
    )
    profile["bot_personality"] = bot_personality or "professional"

    console.print()
    _typewriter(
        f"  ◈  I am {profile['bot_name']} — {profile['bot_personality']} mode activated.",
        colour_code="36", delay=0.015,
    )
    console.print()
    time.sleep(0.2)

    # Step 3: Work context
    _hud_top("WORK CONTEXT  ·  STEP 3 / 4")
    _hud_bot()
    console.print()

    role   = _ask("Your role",        "e.g. Software Engineer, Data Scientist, Student")
    domain = _ask("Primary domain",   "e.g. Web Dev, ML/AI, DevOps, Finance, Research")
    company= _ask("Company / org",    "Optional — used for personalised suggestions")
    lang   = _ask("Preferred language", "English, Tamil, Hindi, etc. (default: English)", default="English")

    profile["owner_role"]     = role
    profile["owner_domain"]   = domain
    profile["owner_company"]  = company
    profile["owner_language"] = lang or "English"

    # Working directory — where Phantom creates and runs projects.
    # If user skips, we default to ~/PhantomProjects. Once set it sticks until
    # they change it via /workdir or in the settings dashboard.
    console.print()
    _hud_top("WORKING DIRECTORY  ·  STEP 4 / 4")
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[2mPhantom will create and run projects here. cd happens automatically.\033[0m\n"
    )
    _hud_bot()
    console.print()
    default_wd = os.path.join(os.path.expanduser("~"), "PhantomProjects")
    wd = _ask(
        "Working directory",
        f"Path where projects live (default: {default_wd})",
        default=default_wd,
    )
    wd = os.path.expanduser(wd or default_wd).rstrip("/\\")
    try:
        os.makedirs(wd, exist_ok=True)
    except OSError as e:
        sys.stdout.write(f"  \033[33m  ↳ Could not create {wd} ({e}); falling back to {default_wd}\033[0m\n")
        wd = default_wd
        os.makedirs(wd, exist_ok=True)
    profile["work_dir"] = wd
    save_config("work_dir", wd)
    console.print()
    _typewriter(f"  ◈  Work directory locked: {wd}", colour_code="32", delay=0.013)
    console.print()

    # Voice mode
    console.print()
    sys.stdout.write(
        "  \033[36mEnable voice mode?\033[0m  \033[2m(Phantom speaks responses aloud — requires speakers)\033[0m\n"
        "  \033[2mType 'yes' to enable, or press Enter to skip:\033[0m "
    )
    sys.stdout.flush()
    try:
        voice_ans = input("").strip().lower()
    except (EOFError, KeyboardInterrupt):
        voice_ans = ""
    profile["voice_mode"] = "on" if voice_ans in ("yes", "y", "1") else "off"

    # ── Save profile ──────────────────────────────────────────────────────────
    save_owner_profile(profile)
    save_config("max_agents", str(sys_info["max_agents"]))

    # ── Personalised finalise screen ─────────────────────────────────────────
    console.print()
    hud_scanline("CONFIGURING PHANTOM", width=55, cycles=1)
    _scan_bar("FINALISING", steps=20, delay=0.03)

    bot  = profile["bot_name"]
    name = first_name
    os.system("cls" if os.name == "nt" else "clear")
    matrix_rain(duration=1.2)

    _hud_top(f"💀 {bot} IS READY")
    rows = [
        ("OPERATOR",    profile["owner_name"],                    GRN),
        ("BOT NAME",    bot,                                       CY),
        ("PERSONALITY", profile["bot_personality"].upper(),        CY),
        ("ROLE",        profile.get("owner_role", "—"),            CY),
        ("DOMAIN",      profile.get("owner_domain", "—"),          CY),
        ("COMPANY",     profile.get("owner_company", "—"),         DIM),
        ("LANGUAGE",    profile.get("owner_language", "English"),  DIM),
        ("VOICE MODE",  profile["voice_mode"].upper(),             GRN if profile["voice_mode"]=="on" else DIM),
        ("OS",          f"{sys_info.get('distro', sys_info['os'])} ({sys_info['arch']})", DIM),
        ("MAX AGENTS",  f"{sys_info['max_agents']} parallel agents", GRN),
    ]
    for label, val, colour in rows:
        sys.stdout.write(
            f"  \033[36m║\033[0m  \033[2m{label:<18}\033[0m  "
            f"\033[{'32' if colour==GRN else '2' if colour==DIM else '36'}m{val}\033[0m\n"
        )
        sys.stdout.flush()
        time.sleep(0.055)
    _hud_sep()
    sys.stdout.write(
        f"  \033[36m║\033[0m  \033[32m{bot} is online and ready for your directives, {name}.\033[0m\n"
    )
    _hud_bot()
    console.print()
    _typewriter(f"  ◈  BOOT COMPLETE. TYPE YOUR FIRST DIRECTIVE BELOW.", colour_code="32", delay=0.018)
    console.print()
    time.sleep(0.4)


@app.command()
def chat(trust: int = typer.Option(3, help="Trust level 1-4")):
    """Chat with PhantomCLI in the sci-fi terminal."""
    init_db()

    if not _require_license():
        raise SystemExit(0)

    if not get_api_key() or not get_config("router_api_key"):
        error("Not configured. Run: python run.py setup")
        raise typer.Exit()

    # ── First-time onboarding wizard ─────────────────────────────────────────
    from omnicli.memory import is_first_run
    if is_first_run():
        _onboarding_wizard()

    # ── Anchor cwd to the configured work_dir (set during onboarding) ───────
    _wd = (get_config("work_dir", "") or "").strip()
    if _wd and os.path.isdir(_wd):
        try:
            os.chdir(_wd)
        except OSError:
            pass
    elif not _wd:
        # Existing installs predating the work_dir prompt — pick a sane default
        # so HUD never shows C:\WINDOWS\system32.
        _default = os.path.join(os.path.expanduser("~"), "PhantomProjects")
        try:
            os.makedirs(_default, exist_ok=True)
            save_config("work_dir", _default)
            os.chdir(_default)
        except OSError:
            pass

    from omnicli.licensing import is_licensed
    licensed  = is_licensed()
    bot_name  = get_config("bot_name", "PhantomCLI")
    boot_screen(__version__, licensed)

    from omnicli import telegram_bot
    if telegram_bot.start():
        info("📱 Telegram bot active")

    owner_name = get_config("owner_first_name", "") or get_config("owner_name", "")
    voice_on   = get_config("voice_mode", "off") == "on"

    # ── Cinematic Jarvis-style greeting on every session start ──────────────
    from omnicli.tui import cinematic_welcome
    cinematic_welcome(owner_name, bot_name, licensed, __version__, trust)
    info("Type / to see commands  ·  /voice on|off  ·  exit to quit")
    separator()

    _voice_fail_streak = 0
    while True:
        try:
            # ── Input: voice or text ─────────────────────────────────────────
            voice_on = get_config("voice_mode", "off") == "on"
            if voice_on:
                try:
                    from omnicli.voice import listen as _listen
                    spoken = _listen()
                    if spoken:
                        _voice_fail_streak = 0
                        sys.stdout.write(f"  \033[2m🎤 {spoken}\033[0m\n\n")
                        sys.stdout.flush()
                        text = spoken
                    else:
                        _voice_fail_streak += 1
                        if _voice_fail_streak >= 3:
                            from omnicli.memory import set_config
                            set_config("voice_mode", "off")
                            warn("Voice mode disabled after 3 failed captures. Check your mic and re-enable with /voice on.")
                            _voice_fail_streak = 0
                        text = chat_prompt()
                except Exception:
                    _voice_fail_streak += 1
                    text = chat_prompt()
            else:
                text = chat_prompt()

            if not text:
                continue
            if text.lower() in ("exit", "quit", "/exit", "/quit"):
                bot = get_config("bot_name", "PhantomCLI")
                success(f"{bot} signing off. Goodbye.")
                break

            # ── /voice toggle ────────────────────────────────────────────────
            if text.lower().startswith("/voice"):
                parts = text.split()
                if len(parts) > 1 and parts[1].lower() in ("on", "off"):
                    from omnicli.voice import toggle_voice
                    toggle_voice(parts[1].lower() == "on")
                else:
                    state = get_config("voice_mode", "off")
                    info(f"Voice mode is currently: {state.upper()}")
                separator()
                continue

            if len(text) > _MAX_INPUT_LEN:
                warn(f"Input too long ({len(text):,} chars). Max {_MAX_INPUT_LEN:,}.")
                continue

            # ── Slash command intercept ──────────────────────────────────────
            from omnicli.commands import handle as _cmd
            result = _cmd(text, trust_level=trust, context="terminal")
            if result.handled:
                if result.reply:
                    console.print(f"\n  {result.reply}\n")
                    separator()
                if result.fatal:
                    break
                continue

            # ── /raw and /agent — escape-hatch slash commands (4.0.9) ────────
            # `/raw <prompt>` and `/agent <prompt>` skip BOTH the fix-request
            # router and the multi-agent orchestrator. The prompt is sent
            # straight to the single-agent path so the user's role assignment
            # and instructions are honoured verbatim. Use this when a prompt
            # keeps getting hijacked by the orchestrator's heuristics.
            stripped = text.lstrip()
            raw_mode = False
            for prefix in ("/raw ", "/agent "):
                if stripped.lower().startswith(prefix):
                    raw_mode = True
                    text = stripped[len(prefix):].strip()
                    if not text:
                        warn(f"Usage: {prefix.strip()} <your prompt> "
                             "— bypasses orchestrator and persona shapeshift.")
                        text = ""
                        break
                    info(f"raw mode: skipping orchestrator and persona "
                         "shapeshift for this turn.")
                    break
            if raw_mode and not text:
                separator()
                continue

            # ── Agent spawn decision ─────────────────────────────────────────
            from omnicli.agents import AgentOrchestrator
            if not raw_mode and _looks_like_fix_request(text) and _active_project_dir():
                _run_focused_fix(text, trust)
                separator()
                continue
            if not raw_mode and AgentOrchestrator.should_spawn(text):
                # Claude-Code-style project memory: check if a related project
                # already exists. If so, offer to extend it instead of creating
                # a fresh project_xxxxxxxx every time the user rephrases.
                chosen_project = _maybe_reuse_related_project(text)
                _run_multi_agent(text, trust, project_dir_override=chosen_project)
                separator()
                continue

            # ── Single-agent AI response ─────────────────────────────────────
            history = get_recent_history(limit=20)
            save_message("user", text)

            sp = PhantomSpinner()
            sp.start(phase="routing")
            sp.set_phase("thinking")

            from omnicli.engine import generate_response, get_dynamic_persona
            from omnicli.tasks import TaskTracker, status_icon

            model   = get_config("main_model", "?")
            _streamed: list[str] = []

            def _on_chunk(chunk: str):
                if not _streamed:
                    sp.stop()
                    from omnicli.tui import glitch_transition
                    if _prev_persona and _prev_persona != _active_persona:
                        glitch_transition(_prev_persona, _active_persona)
                    ai_response_header(_active_persona or "PHANTOM", model)
                _streamed.append(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()

            # ── Live task tracker: print progress lines as tools run ────────
            _task_last_status: dict[str, str] = {}
            _task_header_shown = {"v": False}

            def _on_task(tracker: TaskTracker):
                # Print task transitions as dedicated lines. Completed tasks
                # render with strikethrough so the user can see what's left.
                try:
                    sys.stdout.write('\r' + ' ' * 100 + '\r')
                    sys.stdout.flush()
                    tasks = tracker.snapshot()
                    new_ones = [t for t in tasks if t.id not in _task_last_status]
                    if new_ones and all(t.status == "pending" for t in new_ones) and not _task_header_shown["v"]:
                        total = len(tasks)
                        console.print(f"[bold cyan]Plan:[/bold cyan] [dim]0/{total} done[/dim]")
                        _task_header_shown["v"] = True
                    done_count = sum(1 for t in tasks if t.status == "done")
                    for t in tasks:
                        prev = _task_last_status.get(t.id)
                        if prev == t.status:
                            continue
                        _task_last_status[t.id] = t.status
                        icon = status_icon(t.status)
                        if t.status == "pending":
                            console.print(f"  [dim]{icon} {t.name}[/dim]")
                        elif t.status == "running":
                            console.print(f"  [yellow]{icon}[/yellow] [bold]{t.name}[/bold]")
                        elif t.status == "done":
                            dur = t.duration()
                            suffix = f" [dim]({dur:.1f}s)[/dim]" if dur is not None else ""
                            console.print(
                                f"  [green]{icon}[/green] "
                                f"[strike dim]{t.name}[/strike dim]{suffix}  "
                                f"[dim cyan]({done_count}/{len(tasks)})[/dim cyan]"
                            )
                        elif t.status == "failed":
                            detail = f" [dim]— {t.detail[:60]}[/dim]" if t.detail else ""
                            console.print(f"  [red]{icon}[/red] [strike]{t.name}[/strike]{detail}")
                except Exception:
                    pass

            tracker = TaskTracker(on_change=_on_task)

            # Resolve persona before calling engine so _on_chunk can reference it
            global _active_persona, _prev_persona
            from omnicli.engine import get_dynamic_persona as _gdp
            _prev_persona   = _active_persona
            _active_persona = _gdp(text)

            # ── Info-query nudge ─────────────────────────────────────────
            # When the user is asking for CURRENT / LATEST information, prepend
            # a system-level hint so the agent uses web_search + browse_url
            # instead of (for example) running python against the active
            # project's stale sample_data.json. Fixes the 'shapeshifted to
            # GAME DEVELOPER, ran local python, returned fake data' flow.
            effective_text = text
            if _wants_fresh_info(text):
                effective_text = (
                    "⚠ IMPORTANT ROUTING HINT (system-inserted):\n"
                    "The user is asking for CURRENT / LIVE / LATEST information. You MUST:\n"
                    "  1. Use the `web_search` tool with a specific query to find relevant URLs.\n"
                    "  2. Use the `browse_url` tool on the 2-3 top results to scrape real page content.\n"
                    "  3. Synthesise the answer FROM THE SCRAPED CONTENT — cite source URLs.\n"
                    "Do NOT read existing project files, do NOT run python scripts against cached\n"
                    "sample_data.json, do NOT fabricate answers from training data. If the web\n"
                    "tools fail for every query you try, say so honestly — don't invent.\n\n"
                    "USER REQUEST:\n" + text
                )

            response, _ = generate_response(effective_text, history, trust, on_chunk=_on_chunk, tracker=tracker, persona=_active_persona)

            if not _streamed:
                tokens = int((len(text.split()) + len(response.split())) * 1.3)
                sp.stop(tokens=tokens)
                ai_response_header(_active_persona or "PHANTOM", model)
                # Use typing effect for non-streamed responses
                from omnicli.tui import type_out
                type_out(response, delay=0.006)
            else:
                pass  # already streamed char-by-char

            ai_response_end()
            save_message("assistant", response)

            # ── Voice readback ───────────────────────────────────────────────
            if get_config("voice_mode", "off") == "on":
                from omnicli.voice import speak
                speak(response)

            if len(response.split()) > 50:
                send_telegram(
                    f"⚡ *{bot_name} done*\n\n"
                    f"*Q:* {text[:100]}\n\n"
                    f"*A:* {response[:300]}"
                )

        except KeyboardInterrupt:
            console.print()
            break


# Python stdlib top-level module names. Used to strip hallucinated stdlib
# entries from requirements.txt before pip install runs (the LLM occasionally
# writes `pprint`, `json`, `os`, `datetime` into the file, breaking the whole
# `pip install -r` because pip rejects the unknown 'distribution').
_PY_STDLIB = frozenset({
    "abc","aifc","argparse","array","ast","asynchat","asyncio","asyncore","atexit","audioop",
    "base64","bdb","binascii","bisect","builtins","bz2","calendar","cgi","cgitb","chunk",
    "cmath","cmd","code","codecs","codeop","collections","colorsys","compileall","concurrent",
    "configparser","contextlib","contextvars","copy","copyreg","cProfile","crypt","csv",
    "ctypes","curses","dataclasses","datetime","dbm","decimal","difflib","dis","distutils",
    "doctest","email","encodings","ensurepip","enum","errno","faulthandler","fcntl","filecmp",
    "fileinput","fnmatch","fractions","ftplib","functools","gc","getopt","getpass","gettext",
    "glob","graphlib","grp","gzip","hashlib","heapq","hmac","html","http","idlelib","imaplib",
    "imghdr","imp","importlib","inspect","io","ipaddress","itertools","json","keyword",
    "lib2to3","linecache","locale","logging","lzma","mailbox","mailcap","marshal","math",
    "mimetypes","mmap","modulefinder","msilib","msvcrt","multiprocessing","netrc","nis",
    "nntplib","numbers","operator","optparse","os","ossaudiodev","parser","pathlib","pdb",
    "pickle","pickletools","pipes","pkgutil","platform","plistlib","poplib","posix","posixpath",
    "pprint","profile","pstats","pty","pwd","py_compile","pyclbr","pydoc","queue","quopri",
    "random","re","readline","reprlib","resource","rlcompleter","runpy","sched","secrets",
    "select","selectors","shelve","shlex","shutil","signal","site","smtpd","smtplib","sndhdr",
    "socket","socketserver","spwd","sqlite3","ssl","stat","statistics","string","stringprep",
    "struct","subprocess","sunau","symbol","symtable","sys","sysconfig","syslog","tabnanny",
    "tarfile","telnetlib","tempfile","termios","test","textwrap","threading","time","timeit",
    "tkinter","token","tokenize","tomllib","trace","traceback","tracemalloc","tty","turtle",
    "turtledemo","types","typing","unicodedata","unittest","urllib","uu","uuid","venv","warnings",
    "wave","weakref","webbrowser","winreg","winsound","wsgiref","xdrlib","xml","xmlrpc",
    "zipapp","zipfile","zipimport","zlib","zoneinfo","__future__",
})


def _sanitize_requirements(req_path: str) -> None:
    """Strip Python stdlib modules and obvious garbage from requirements.txt
    before pip install. Modifies the file in place. The model occasionally
    writes `pprint`, `json`, `os`, etc. as deps — pip rejects those, which
    fails the entire install (pip 23+ aborts on first bad line)."""
    import re as _re
    try:
        with open(req_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    kept, dropped = [], []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            kept.append(raw)
            continue
        # Extract the bare package name (strip extras, version pins, env markers)
        # e.g. "Flask[async]>=2.0; python_version>='3.8'" -> "Flask"
        m = _re.match(r"\s*([A-Za-z0-9_.\-]+)", line)
        if not m:
            kept.append(raw); continue
        pkg = m.group(1)
        # Normalize for stdlib comparison: PyPI is case-insensitive; underscores
        # and hyphens are interchangeable. stdlib names are lowercase ASCII.
        if pkg.lower().replace("-", "_") in _PY_STDLIB:
            dropped.append(pkg)
            continue
        kept.append(raw)
    if dropped:
        try:
            with open(req_path, "w", encoding="utf-8") as f:
                f.writelines(kept)
            warn(f"requirements.txt: dropped stdlib modules ({', '.join(dropped)}) — pip can't install those.")
        except OSError:
            pass


def _read_log_tail(log_path: str, max_chars: int = 4000) -> str:
    """Read the last ~max_chars of a log file. Used to feed the self-heal
    engine call with the exact traceback from a crashed app.

    Implementation: read the whole file (log files are small — megabyte-scale
    at most) and slice to the tail. An earlier seek/readline version dropped
    the entire tail when a single log line was longer than max_chars."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        if len(data) <= max_chars:
            return data
        return data[-max_chars:]
    except OSError:
        return ""


def _smoke_test_url(url: str, timeout: float = 8.0) -> tuple[bool, int, str, str]:
    """HTTP-GET the URL and decide if it's a real working page.

    Returns (ok, status_code, body_snippet, reason).
    ok is True iff: status is 2xx, body is non-trivially long, and body
    does NOT contain a Python traceback or a Flask/werkzeug 500 marker.
    """
    import urllib.request as _ur
    import urllib.error as _ue

    try:
        req = _ur.Request(url, headers={"User-Agent": "PhantomCLI-SmokeTest/1.0"})
        with _ur.urlopen(req, timeout=timeout) as r:
            status = r.getcode() or 0
            raw = r.read(32768)  # 32KB is plenty to detect trouble
            body = raw.decode("utf-8", errors="replace")
    except _ue.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:32768]
        except Exception:
            body = ""
        return (False, e.code, body[:1500], f"HTTP {e.code} from server")
    except _ue.URLError as e:
        return (False, 0, "", f"connection failed: {e.reason}")
    except Exception as e:
        return (False, 0, "", f"smoke-test error: {e}")

    snippet = body[:1500]
    if status < 200 or status >= 300:
        return (False, status, snippet, f"non-2xx status {status}")
    if len(body.strip()) < 120:
        return (False, status, snippet, f"response body is only {len(body.strip())} chars")
    markers = (
        "traceback (most recent call last)",
        "werkzeug.exceptions",
        "jinja2.exceptions",
        "internal server error",
        "500 internal server",
        "unhandledpromiserejection",
    )
    low = body.lower()
    for m in markers:
        if m in low:
            return (False, status, snippet, f"body contains error marker: {m!r}")
    return (True, status, snippet, "ok")


def _self_heal_app(project_dir: str, url: str, log_path: str,
                   failure_reason: str, body_snippet: str, trust: int) -> bool:
    """Ask the engine to read the crash and patch the code, then indicate
    whether a relaunch is warranted. Returns True iff the engine wrote edits
    (caller should kill + relaunch). Bounded to one healing round."""
    try:
        from omnicli.engine import generate_response
    except Exception:
        return False
    log_tail = _read_log_tail(log_path, max_chars=4000)
    heal_prompt = (
        f"The app you just built at `{project_dir}` is running but failing smoke test.\n\n"
        f"URL: {url}\n"
        f"Smoke-test verdict: {failure_reason}\n\n"
        f"RESPONSE BODY (first 1500 chars):\n"
        f"```\n{body_snippet}\n```\n\n"
        f"APP LOG TAIL ({log_path}):\n"
        f"```\n{log_tail or '(log empty)'}\n```\n\n"
        "Fix the ACTUAL ROOT CAUSE in the source files. Common culprits:\n"
        "  • Template references a field the backend doesn't emit → align names.\n"
        "  • Backend loads sample_data.json with wrong keys → fix the loader.\n"
        "  • Missing static asset the template links → create it.\n"
        "  • Fetcher raises on missing API key instead of falling back → wrap\n"
        "    in try/except, always return data.\n"
        "  • Template uses `{{ variable }}` where variable is None → guard with\n"
        "    `{{ variable or 'fallback' }}` or pre-populate in the route.\n\n"
        "Use write_file / edit_file to patch the files. Do NOT create new files "
        "unless a linked static asset is genuinely missing. Do NOT rewrite the "
        "whole app — minimum edits to make the page render with real content. "
        "After your edits, output one line: `HEAL_DONE: <one-sentence summary>`."
    )
    try:
        response, _ = generate_response(heal_prompt, [], trust)
        return bool(response and "HEAL_DONE" in response.upper() or "write_file" in (response or "").lower())
    except Exception:
        return False


def _auto_launch_app(project_dir: str, trust: int, _heal_attempted: bool = False) -> str | None:
    """Install requirements (if any) and run the project's runner script,
    surfacing the public URL when one is printed.

    Returns the URL if found, else None. Never blocks longer than ~45s.
    The launched process is left running in the background so the user can
    keep using it after this function returns.

    If the app responds with a 5xx or a crash page, one self-heal pass is
    attempted (engine reads log+body, patches files, we kill + relaunch).
    `_heal_attempted` is the internal guard that prevents infinite recursion.
    """
    import re as _re
    import shutil as _sh
    import subprocess as _sp
    import time as _t

    if not os.path.isdir(project_dir):
        return None

    # Pick the runner: OS-native first, then python entry as fallback.
    is_win = sys.platform == "win32"
    candidates = (["run.bat"] if is_win else ["run.sh"]) + ["app.py", "main.py", "server.js", "run.py"]
    runner = next(
        (os.path.join(project_dir, c) for c in candidates
         if os.path.isfile(os.path.join(project_dir, c))),
        None,
    )
    if not runner:
        warn("No runner found (run.bat / run.sh / app.py). Skipping auto-launch.")
        return None

    # Install pip deps if requirements.txt is present (≤90s timeout).
    req = os.path.join(project_dir, "requirements.txt")
    if os.path.isfile(req):
        # Sanitize: strip Python stdlib modules that the LLM sometimes hallucinates
        # into requirements.txt (pprint, json, os, datetime, ...). pip cannot
        # install stdlib and the whole `-r requirements.txt` install fails.
        _sanitize_requirements(req)
        info(f"📦 Installing dependencies from requirements.txt …")
        try:
            r = _sp.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", req],
                cwd=project_dir, capture_output=True, text=True, timeout=90,
            )
            if r.returncode != 0:
                warn(f"pip install exited {r.returncode} — continuing anyway.")
                if r.stderr:
                    console.print(f"[dim]{r.stderr.strip()[:400]}[/dim]")
        except _sp.TimeoutExpired:
            warn("pip install timed out after 90s — continuing.")
        except Exception as e:
            warn(f"pip install error: {e}")

    # Build the launch command.
    if runner.endswith(".bat"):
        cmd = ["cmd.exe", "/c", runner]
    elif runner.endswith(".sh"):
        cmd = ["bash", runner]
    elif runner.endswith(".js"):
        if not _sh.which("node"):
            warn("node not found on PATH; cannot launch JS app.")
            return None
        cmd = ["node", runner]
    else:
        cmd = [sys.executable, runner]

    # Stream the app's stdout+stderr to a log file inside the project dir.
    # This gives us two things:
    #   1. A durable record the model can read later via run_bash when the
    #      user reports "the link isn't working" / "500 error".
    #   2. A drain for the app's pipe so it doesn't block on a full buffer
    #      after our 30s URL-detection window closes.
    log_path = os.path.join(project_dir, ".phantom_app.log")
    try:
        log_fh = open(log_path, "w", buffering=1)
    except OSError as e:
        warn(f"Could not open {log_path}: {e} — app output will not be captured.")
        log_fh = None

    info(f"🚀 Launching: {' '.join(cmd)}")
    # Force unbuffered Python so request errors / tracebacks reach the log file
    # immediately. Without this, FastAPI/Flask 500s never make it into
    # .phantom_app.log and the model can't diagnose them.
    launch_env = dict(os.environ)
    launch_env["PYTHONUNBUFFERED"] = "1"
    launch_env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = _sp.Popen(
            cmd, cwd=project_dir,
            stdout=(log_fh if log_fh else _sp.PIPE),
            stderr=_sp.STDOUT,
            text=True, bufsize=1,
            env=launch_env,
        )
    except Exception as e:
        error(f"Failed to launch: {e}")
        if log_fh: log_fh.close()
        return None

    # Watch first 30s of stdout for a URL. Common patterns:
    #   - "Uvicorn running on http://127.0.0.1:8000"
    #   - "Forwarding   https://abcd-1234.ngrok-free.app -> http://localhost:8000"
    #   - "Your tunnel has started! https://...trycloudflare.com"
    url_pat = _re.compile(r"https?://[^\s'\"<>]+")

    # Reject patterns we never want to surface as the "live URL":
    #   - ngrok/cloudflare error-doc pages (e.g. ngrok.com/docs/errors/err_ngrok_103)
    #   - help/login pages, GitHub repos, raw API documentation hosts
    BAD_HOSTS  = ("ngrok.com", "cloudflare.com", "ngrok.io/docs",
                  "github.com", "fastapi.tiangolo.com", "starlette.io")
    BAD_PATHS  = ("/docs/errors", "/errors/", "err_ngrok", "/help/",
                  "/signup", "/login", "/dashboard")
    TUNNEL_HOSTS = ("ngrok-free.app", ".ngrok.app", "trycloudflare.com",
                    "loca.lt", "serveo.net", "tunnelmole.com", "tuns.sh")

    def _bad_url(u: str) -> bool:
        ul = u.lower()
        return any(h in ul for h in BAD_HOSTS) or any(p in ul for p in BAD_PATHS)

    def _is_tunnel(u: str) -> bool:
        ul = u.lower()
        return any(h in ul for h in TUNNEL_HOSTS)

    def _is_local(u: str) -> bool:
        ul = u.lower()
        return ("://localhost" in ul or "://127.0.0.1" in ul or "://0.0.0.0" in ul)

    local_url: str | None = None
    tunnel_url: str | None = None
    captured: list[str] = []

    def _process_line(line: str) -> None:
        nonlocal local_url, tunnel_url
        line = line.rstrip()
        captured.append(line)
        for m in url_pat.finditer(line):
            u = m.group(0).rstrip(".,)]'\"")
            # Tunnel check FIRST — `cloudflare.com` is a substring of
            # `trycloudflare.com`, so we'd otherwise reject real tunnel URLs.
            if _is_tunnel(u):
                if not tunnel_url:
                    tunnel_url = u
                continue
            if _bad_url(u):
                continue
            if _is_local(u) and not local_url:
                local_url = u.replace("://0.0.0.0", "://localhost")

    deadline = _t.time() + 30
    if log_fh:
        # Tail the log file we just opened (proc writes to it directly).
        last_pos = 0
        local_seen_at: float | None = None
        while _t.time() < deadline:
            try:
                cur_size = os.path.getsize(log_path)
            except OSError:
                cur_size = 0
            if cur_size > last_pos:
                with open(log_path, "r", errors="replace") as r:
                    r.seek(last_pos)
                    for line in r:
                        _process_line(line)
                    last_pos = r.tell()
            if tunnel_url and local_url:
                break
            if local_url:
                if local_seen_at is None:
                    local_seen_at = _t.time()
                elif _t.time() - local_seen_at > 5 and not tunnel_url:
                    # 5s grace for a tunnel after localhost — if none, ship localhost.
                    break
            if proc.poll() is not None:
                break
            _t.sleep(0.25)
    else:
        # No log file — fall back to draining the pipe directly.
        while _t.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                _t.sleep(0.1)
                continue
            _process_line(line)
            if tunnel_url and local_url:
                break

    # Prefer a real tunnel only if it actually looks like one — otherwise localhost.
    final_url = tunnel_url or local_url

    # Persist app metadata so the engine can inject debug context next turn.
    # When the user says "the link doesn't work" the model has the PID,
    # log file path, and URL — it can read the log + curl the URL instead
    # of guessing at random files.
    try:
        save_config("last_app_pid",       str(proc.pid))
        save_config("last_app_log",       log_path)
        save_config("last_app_url",       final_url or "")
        save_config("last_app_started_at", str(int(_t.time())))
    except Exception:
        pass

    # Drop a self-contained diagnostic script in the project dir. The model
    # invokes this single script (instead of inline PowerShell/curl that
    # breaks across OSes) to get: process-alive check + HTTP status + body
    # + log tail — with proper error handling for connection failures.
    try:
        diag_url = (final_url or "http://localhost:8000").replace("'", "''")
        diag_log = log_path.replace("'", "''")
        diag_pid = str(proc.pid)
        ps1 = (
            f"$PID_TARGET = {diag_pid}\n"
            f"$URL = '{diag_url}'\n"
            f"$LOG = '{diag_log}'\n"
            "Write-Output \"=== PROCESS CHECK (PID $PID_TARGET) ===\"\n"
            "$p = Get-Process -Id $PID_TARGET -ErrorAction SilentlyContinue\n"
            "if ($p) { Write-Output \"ALIVE: $($p.ProcessName)\" }\n"
            "else { Write-Output 'DEAD: process exited or never started — app crashed. See log tail below for the traceback.' }\n"
            "Write-Output ''\n"
            "Write-Output \"=== HTTP CHECK ($URL) ===\"\n"
            "try {\n"
            "  $r = Invoke-WebRequest -Uri $URL -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop\n"
            "  Write-Output \"STATUS: $($r.StatusCode) $($r.StatusDescription)\"\n"
            "  Write-Output 'BODY (first 1500 chars):'\n"
            "  $b = $r.Content; if ($b.Length -gt 1500) { $b = $b.Substring(0,1500) }\n"
            "  Write-Output $b\n"
            "} catch {\n"
            "  $resp = $_.Exception.Response\n"
            "  if ($resp) {\n"
            "    try { $sr = New-Object IO.StreamReader($resp.GetResponseStream()); $b = $sr.ReadToEnd() } catch { $b = '(could not read response body)' }\n"
            "    Write-Output \"STATUS: $([int]$resp.StatusCode) (HTTP error)\"\n"
            "    Write-Output 'BODY (first 1500 chars):'\n"
            "    if ($b.Length -gt 1500) { $b = $b.Substring(0,1500) }\n"
            "    Write-Output $b\n"
            "  } else {\n"
            "    Write-Output \"CONNECTION_ERROR: $($_.Exception.Message)\"\n"
            "    Write-Output '(App is not listening on this URL — most likely it crashed. Read the log tail below for the traceback, then fix THAT.)'\n"
            "  }\n"
            "}\n"
            "Write-Output ''\n"
            "Write-Output \"=== LOG TAIL ($LOG, last 100 lines) ===\"\n"
            "if (Test-Path $LOG) { Get-Content -Tail 100 $LOG } else { Write-Output '(Log file not found)' }\n"
        )
        sh = (
            "#!/usr/bin/env bash\n"
            f"PID_TARGET='{diag_pid}'\n"
            f"URL='{diag_url}'\n"
            f"LOG='{diag_log}'\n"
            'echo "=== PROCESS CHECK (PID $PID_TARGET) ==="\n'
            'if kill -0 "$PID_TARGET" 2>/dev/null; then\n'
            '  echo "ALIVE: $(ps -p $PID_TARGET -o comm= 2>/dev/null | tr -d \" \")"\n'
            "else\n"
            '  echo "DEAD: process exited or never started — app crashed. See log tail below for the traceback."\n'
            "fi\n"
            'echo ""\n'
            'echo "=== HTTP CHECK ($URL) ==="\n'
            'if command -v curl >/dev/null 2>&1; then\n'
            '  body=$(curl -sS --max-time 8 -w "\\n__PH_STATUS__=%{http_code}" "$URL" 2>&1)\n'
            '  status=$(printf \'%s\' "$body" | grep -o \'__PH_STATUS__=[0-9]*\' | tail -1 | cut -d= -f2)\n'
            '  body_only=$(printf \'%s\' "$body" | sed \'s/__PH_STATUS__=.*//\')\n'
            '  echo "STATUS: ${status:-CONNECTION_ERROR}"\n'
            '  echo "BODY (first 1500 chars):"\n'
            '  printf \'%s\' "$body_only" | head -c 1500\n'
            '  echo ""\n'
            "else\n"
            '  echo "(curl not installed)"\n'
            "fi\n"
            'echo ""\n'
            'echo "=== LOG TAIL ($LOG, last 100 lines) ==="\n'
            'if [ -f "$LOG" ]; then tail -n 100 "$LOG"; else echo "(Log file not found)"; fi\n'
        )
        diag_ps1 = os.path.join(project_dir, "phantom_diag.ps1")
        diag_sh  = os.path.join(project_dir, "phantom_diag.sh")
        with open(diag_ps1, "w", encoding="utf-8") as f: f.write(ps1)
        with open(diag_sh,  "w", encoding="utf-8") as f: f.write(sh)
        try: os.chmod(diag_sh, 0o755)
        except OSError: pass
        save_config("last_app_diag_ps1", diag_ps1)
        save_config("last_app_diag_sh",  diag_sh)
    except Exception:
        pass

    if final_url:
        # ── Smoke test: actually HTTP-GET the URL before we claim it's live.
        # The URL-in-stdout heuristic only proves the server bound a port; it
        # does NOT prove `/` returns 200 or that templates render without a
        # Jinja / schema-mismatch crash. Without this block, users would see
        # "🌐 App is live" and click through to a 500 page.
        smoke_url = final_url
        # For tunnel URLs, prefer hitting localhost directly — tunnels can
        # take extra seconds to propagate and a local check is a fair proxy
        # for "did the app come up".
        if tunnel_url and local_url:
            smoke_url = local_url
        # Give the framework a moment to finish startup after binding.
        _t.sleep(1.2)
        ok, status, body_snippet, reason = _smoke_test_url(smoke_url, timeout=8.0)

        if ok:
            console.print()
            kind = "Public tunnel" if (tunnel_url and final_url == tunnel_url) else "Local URL"
            console.print(
                f"[bold green]🌐 App is live at:[/bold green] [bold cyan]{final_url}[/bold cyan]  "
                f"[dim]({kind} · HTTP {status} · verified)[/dim]"
            )
            if not tunnel_url:
                console.print(
                    "[dim]   Open that link in your browser. "
                    "To share it publicly, run `ngrok http 8000` (after `ngrok authtoken <token>`) in another terminal.[/dim]"
                )
            console.print(f"[dim]   Process running in background (PID {proc.pid}). "
                          f"Use Ctrl+C in your terminal to stop the app once you're done.[/dim]")
            console.print()
            return final_url

        # Smoke test failed. Either a 5xx, an empty body, or a traceback
        # in the rendered HTML. One self-heal pass: engine reads the log +
        # response body, patches the source, we relaunch.
        console.print()
        warn(f"App answered but smoke-test failed — {reason}. Triggering self-heal…")
        if body_snippet:
            console.print(f"[dim]   response preview: {body_snippet[:300].replace(chr(10), ' ⏎ ')}[/dim]")

        if not _heal_attempted and trust >= 2:
            healed = _self_heal_app(
                project_dir=project_dir, url=smoke_url, log_path=log_path,
                failure_reason=reason, body_snippet=body_snippet, trust=trust,
            )
            if healed:
                info("🔧 Self-heal wrote edits — restarting the app…")
                try:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except _sp.TimeoutExpired: proc.kill()
                except Exception:
                    pass
                try:
                    if log_fh: log_fh.close()
                except Exception:
                    pass
                return _auto_launch_app(project_dir, trust, _heal_attempted=True)

        # Heal disabled, already tried, or produced no edits — be honest.
        console.print()
        console.print(
            f"[bold yellow]⚠ App is listening at[/bold yellow] [cyan]{final_url}[/cyan] "
            f"[dim]but not healthy (reason: {reason}).[/dim]"
        )
        console.print(f"[dim]   Log: {log_path}  ·  PID {proc.pid}[/dim]")
        console.print(f"[dim]   Ask Phantom to 'fix the crash' — it has the log path and URL in memory.[/dim]")
        console.print()
    else:
        console.print()
        warn("App started but no URL detected within 30s. Last log lines:")
        for ln in captured[-12:]:
            console.print(f"  [dim]{ln}[/dim]")
        console.print(f"[dim]   PID {proc.pid} — check the project dir or rerun manually.[/dim]")
        console.print()
    return final_url


# ─── Smart routing: fix-the-error vs rebuild ─────────────────────────────────

_FIX_PATTERNS = (
    "traceback", "stack trace", "error", "exception", "exit code",
    "failed", "doesn't work", "not working", "broken", "crashes",
    "bug", "fix", "debug", "diagnose", "500", "404", "segfault",
    "undefined", "typeerror", "valueerror", "importerror", "syntaxerror",
    "keyerror", "attributeerror", "runtimeerror",
)

_ROLE_STARTS = (
    "you are a", "you are an", "you're a", "you're an",
    "act as a", "act as an", "you act as",
    "your job is", "your role is", "your task is",
    "as a senior", "as an expert", "as a professional",
    "imagine you are", "imagine you're", "pretend you are",
)

_PHASE_MARKERS = (
    "phase 1", "phase 2", "phase 3",
    "step 1", "step 2", "step 3",
    "stage 1", "stage 2", "stage 3",
)


def _has_explicit_role(text: str) -> bool:
    """True if the prompt opens with an explicit role assignment.

    Prompts like ``"You are a senior data scientist…"`` carry a
    deliberate system role and must not be hijacked by the fix-request
    or multi-agent orchestrator paths. Only the first ~300 characters
    are inspected so a passing mention of "your role" deep in the body
    doesn't trigger the guard.
    """
    if not text:
        return False
    head = text.lstrip()[:300].lower()
    return any(head.startswith(r) for r in _ROLE_STARTS)


def _has_structured_phases(text: str) -> bool:
    """True if the prompt is a multi-phase plan (Phase 1 / Phase 2 / …).

    A prompt that walks through 2+ explicit phases is a structured
    instruction, not an error report — even if individual phases
    mention words like "error" or "fix" inside them.
    """
    if not text:
        return False
    t = text.lower()
    return sum(1 for m in _PHASE_MARKERS if m in t) >= 2


def _looks_like_fix_request(text: str) -> bool:
    """True if the user is pasting an error or asking for a fix.
    Routes to focused-edit mode instead of multi-agent rebuild.

    Guards (added 4.0.9): a structured prompt (explicit role or 2+
    phase markers) is never a fix request, even when it contains
    fix-pattern words inside instructional text. A real traceback
    block still wins regardless. Long prompts (>1500 chars) without
    a traceback are presumed structured and skipped.
    """
    if not text:
        return False
    t = text.lower()
    # Strong signal: a traceback block is present — overrides everything.
    if "traceback (most recent call last)" in t:
        return True
    # New guards — structured prompts are not fix requests.
    if _has_explicit_role(text) or _has_structured_phases(text):
        return False
    if len(text) > 1500:
        return False
    # Multiple fix-cues in the same short, unstructured message.
    hits = sum(1 for p in _FIX_PATTERNS if p in t)
    return hits >= 2


def _active_project_dir() -> Optional[str]:
    """Return the last-built project dir if it still exists on disk."""
    try:
        from omnicli.memory import get_config as _gc
        p = (_gc("last_project_dir", "") or "").strip()
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return None


def _run_focused_fix(text: str, trust: int):
    """Surgical fix mode — reads the active project's files, asks the model
    to identify the broken file and make minimal edits via edit_file, then
    re-launches with smoke test. Does NOT spawn a multi-agent rebuild."""
    project_dir = _active_project_dir()
    if not project_dir:
        warn("No active project to fix — falling back to regular mode.")
        return
    log_path = ""
    last_url = ""
    try:
        from omnicli.memory import get_config as _gc
        log_path = (_gc("last_app_log", "") or "").strip()
        last_url = (_gc("last_app_url", "") or "").strip()
    except Exception:
        pass

    # Give the model a CONDENSED snapshot: directory listing + file sizes,
    # the error text the user pasted, and the log tail if we have it.
    tree_lines: list[str] = []
    try:
        for root, dirs, files in os.walk(project_dir):
            # Don't descend into venv / build / __pycache__
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "venv", ".venv", "build", "dist", "node_modules")]
            rel = os.path.relpath(root, project_dir)
            indent = "" if rel == "." else "  " * (rel.count(os.sep) + 1)
            if rel != ".":
                tree_lines.append(f"{indent}{os.path.basename(root)}/")
            for f in sorted(files):
                fp = os.path.join(root, f)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    sz = 0
                tree_lines.append(f"{indent}  {f}  ({sz:,} bytes)")
            if sum(len(l) for l in tree_lines) > 3000:
                tree_lines.append("  …(truncated)")
                break
    except Exception as e:
        tree_lines.append(f"(cannot scan: {e})")

    log_tail = ""
    if log_path and os.path.isfile(log_path):
        try:
            log_tail = _read_log_tail(log_path, max_chars=3000)
        except Exception:
            pass

    info(f"🔧 Focused-fix mode — editing {project_dir} (no rebuild).")

    fix_prompt = (
        f"You are fixing a bug in an EXISTING project at `{project_dir}`. "
        f"DO NOT rebuild the project. DO NOT call plan_tasks. Make minimal "
        f"targeted edits to the already-written files.\n\n"
        f"=== USER REPORT ===\n{text}\n\n"
        f"=== ACTIVE PROJECT LAYOUT ===\n" + "\n".join(tree_lines) + "\n\n"
        + (f"=== APP LOG TAIL ({log_path}) ===\n{log_tail}\n\n" if log_tail else "")
        + (f"=== LAST KNOWN URL ===\n{last_url}\n\n" if last_url else "")
        + "Diagnostic protocol:\n"
        "  1. Use read_file on the file the traceback points at (check the "
        "File lines in the traceback).\n"
        "  2. Make the SMALLEST possible edit_file call that fixes the bug.\n"
        "     Common causes: generator passed where a list was expected (wrap "
        "in list()), missing key in a dict, wrong arg order, typo. Do NOT "
        "rewrite the whole file.\n"
        "  3. If multiple files share the bug, fix all of them but keep each "
        "edit minimal.\n"
        "  4. After edits, output a 2-line summary: files changed + what "
        "you fixed. DO NOT relaunch the app yourself — I'll do it next turn."
    )

    try:
        from omnicli.engine import generate_response
        from omnicli.memory import save_message
        save_message("user", text)
        resp, _ = generate_response(fix_prompt, [], trust)
        save_message("assistant", resp or "")
    except Exception as e:
        error(f"focused-fix call failed: {e}")
        return

    # Offer to relaunch automatically — saves the user having to ask "run it"
    try:
        console.print()
        console.print("[cyan]Fix applied. Relaunching app to smoke-test…[/cyan]")
        _auto_launch_app(project_dir, trust)
    except Exception as e:
        warn(f"relaunch skipped: {e}")


def _maybe_reuse_related_project(directive: str) -> Optional[str]:
    """If an existing project's summary closely matches `directive`, ask the
    user whether to extend it. Returns the project_dir to reuse, or None
    to create a fresh one. Silent when there are no matches."""
    try:
        from omnicli.project_memory import find_related_projects, format_related_prompt
    except ImportError:
        return None

    # ── Tier 1: active project + refinement phrasing → silent reuse ──
    # When the user says "ui is bad", "also add X", "not working", "blank",
    # they're refining the CURRENT project — don't spawn a new one.
    active = _active_project_dir()
    if active and _looks_like_refinement(directive):
        info(f"↩  Continuing in active project: {os.path.basename(active)}")
        try:
            from omnicli.project_memory import append_run
            append_run(active, "refinement_applied",
                       note=f"directive: {directive[:120]}")
        except Exception:
            pass
        return active

    # ── Tier 2: summary-based similarity scan ──
    rows = find_related_projects(directive, min_score=0.20, top_k=3)
    # If the active project exists but didn't make the cut, include it anyway
    # so the user can still choose to reuse it.
    if active and not any(r.project_dir == active for r in rows):
        try:
            from omnicli.project_memory import _parse_existing, SUMMARY_FILENAME
            summary_path = os.path.join(active, SUMMARY_FILENAME)
            if os.path.isfile(summary_path):
                active_row = _parse_existing(summary_path)
                active_row.relatedness = 0.15   # low but non-zero
                rows.insert(0, active_row)
        except Exception:
            pass

    if not rows:
        return None
    console.print()
    console.print("[bold cyan]◈  Related project(s) found in memory:[/bold cyan]")
    console.print(f"[dim]{format_related_prompt(rows)}[/dim]")
    console.print()
    try:
        answer = input(
            f"  Extend an existing project? [1-{len(rows)}] / [n]ew / [Enter=new]: "
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return None
    if answer in ("", "n", "new", "no"):
        return None
    try:
        idx = int(answer) - 1
        if 0 <= idx < len(rows):
            chosen = rows[idx].project_dir
            info(f"✓ Extending existing project: {chosen}")
            try:
                from omnicli.project_memory import append_run
                append_run(chosen, "extend_requested",
                           note=f"new directive: {directive[:120]}")
            except Exception:
                pass
            return chosen
    except ValueError:
        pass
    return None


# Refinement-phrase detector — used by _maybe_reuse_related_project to
# decide whether a directive is a "keep working on this" refinement vs
# a brand-new build request.
_REFINEMENT_CUES = (
    "also", "but", "instead", "actually", "not working", "doesn't work",
    "broken", "ui is not", "it is showing", "change the", "update the",
    "improve", "fix the", "make the", "add a", "add the", "remove",
    "looks bad", "looks wrong", "not accurate", "wrong data", "outdated",
    "stale", "blank", "inaccurate", "should be", "should have", "please",
    "the ui", "the data", "the page", "it's still", "still showing",
)


def _looks_like_refinement(text: str) -> bool:
    """True when the directive is refining an existing project rather than
    requesting something brand new. Conservative — needs an explicit cue."""
    if not text:
        return False
    t = text.lower()
    return any(c in t for c in _REFINEMENT_CUES)


# Phrases that indicate the user wants CURRENT / LIVE / LATEST data — the
# single-agent then gets a hint to use web_search + browse_url rather than
# reading stale project files. Matches Claude Code's behaviour on "what's
# the latest ..." type queries.
_FRESH_INFO_CUES = (
    "latest", "current", "today's", "todays", "today",
    "right now", "as of today", "live",
    "search the internet", "search the web", "from the internet",
    "from the web", "look up", "look it up",
    "get me the", "get the latest",
    "what's happening", "whats happening",
    "news", "headlines",
    "score", "scores", "match", "result",
    "price of", "stock price", "crypto price",
    "weather in", "forecast for",
    "recent", "just happened", "breaking",
)


def _wants_fresh_info(text: str) -> bool:
    """True when the user is asking for fresh, current, internet-backed
    information. Triggers the info-query nudge so the single-agent uses
    web_search + browse_url instead of reading existing project files."""
    if not text:
        return False
    t = text.lower()
    return any(c in t for c in _FRESH_INFO_CUES)


def _run_multi_agent(prompt: str, trust: int,
                    project_dir_override: Optional[str] = None):
    """Orchestrate a multi-agent job with live terminal panel.

    If `project_dir_override` is supplied, the orchestrator builds INTO
    that existing directory (project memory's "extend" flow) instead of
    creating a new project_xxxxxxxx."""
    import threading
    from omnicli.agents import AgentOrchestrator
    from omnicli.tui import (
        agent_spawn_intro, agent_spawn_panel, matrix_rain,
        hud_scanline, _typewriter,
    )

    bot_name = get_config("bot_name", "PhantomCLI")

    # ── Plan ─────────────────────────────────────────────────────────────────
    sp = PhantomSpinner()
    sp.start(phase="thinking")

    orch = AgentOrchestrator(prompt, trust_level=trust,
                              project_dir=project_dir_override)
    # Step 1: rewrite the user's brief into a clearer spec so the planner
    # and per-agent tasks are grounded in detail.
    improved = orch.improve_prompt()
    sp.stop()
    if getattr(orch, "improved_prompt", "") and improved != orch.original_prompt:
        console.print()
        console.print("[bold cyan]◈  Refined directive:[/bold cyan]")
        console.print(f"[dim]{improved}[/dim]")
        console.print()

    # ── Step 1.5 (v3.0.6): research phase ─────────────────────────────────
    # Before the multi-agent build, scrape live web data relevant to the
    # directive's domain so agents seed their app with facts, not LLM
    # hallucination. Silent no-op if the directive has no detectable
    # research domain (todo apps, calculators, games, etc.).
    try:
        from omnicli.research_phase import detect_domain as _detect_domain
        if _detect_domain(prompt):
            console.print("[bold cyan]◈  Research phase — scraping live data…[/bold cyan]")
            sp.start(phase="routing")
            result = orch.research(on_status=lambda m: console.print(f"  [dim]{m}[/dim]"))
            sp.stop()
            if result and result.ok:
                console.print(
                    f"  [green]✓[/green] Research captured "
                    f"({len(result.sources)} sources) → "
                    f"{os.path.join(orch.project_dir, 'research.json')}"
                )
            elif result:
                console.print(
                    "  [yellow]⚠[/yellow] Research phase couldn't reach any sources — "
                    "agents will use LLM-seeded fallback with a demo banner."
                )
            console.print()
    except Exception as _rp_err:
        logging.getLogger("omnicli").debug("research phase error: %s", _rp_err)

    sp.start(phase="thinking")
    tasks = orch.plan()
    sp.stop()

    if not tasks:
        # Planning failed — fall through to single agent
        from omnicli.engine import generate_response
        history = get_recent_history(limit=5)
        save_message("user", prompt)
        response, _ = generate_response(prompt, history, trust)
        save_message("assistant", response)
        console.print(response)
        return

    # ── Intro panel ──────────────────────────────────────────────────────────
    hud_scanline("AGENT DEPLOYMENT", width=50, cycles=1)
    agent_spawn_intro(
        prompt,
        [{"name": t.name, "role": t.role, "files": t.assigned_files} for t in tasks],
    )

    # ── Execute agents + live panel in separate threads ───────────────────────
    exec_done = threading.Event()

    def _exec():
        orch.execute()
        exec_done.set()

    exec_thread = threading.Thread(target=_exec, daemon=True)
    exec_thread.start()

    # Run live panel in main thread until agents complete.
    # Mute the omnicli console log handler so a stray WARNING from a worker
    # thread can't slip in between an erase and a redraw and corrupt the
    # panel (the duplicated "AGENTS X/Y done" header users were seeing).
    import time as _t
    import logging as _lg
    tick = 0
    from omnicli.tui import agent_live_panel, erase_lines
    last_lines = 0

    _root_log = _lg.getLogger("omnicli")
    _muted_handlers = []
    for _h in _root_log.handlers:
        if isinstance(_h, _lg.StreamHandler) and getattr(_h, "stream", None) in (sys.stderr, sys.stdout):
            _muted_handlers.append((_h, _h.level))
            _h.setLevel(_lg.CRITICAL + 1)

    while not exec_done.is_set():
        snapshot = orch.status_snapshot()
        if last_lines: erase_lines(last_lines)
        from omnicli.tui import _AGENT_STATUS_COLOUR, _AGENT_STATUS_ICON, _SPINNER_FRAMES
        rows = []
        for ag in snapshot:
            r = orch.results.get(ag["id"])
            msg = ""
            if r:
                if r.status == "running":
                    # Richer progress: show files the agent has produced SO
                    # FAR + the current target file. Gives the user real
                    # feedback instead of an opaque "working…" for 90 seconds.
                    elapsed = int(_t.time() - r.start_time) if r.start_time else 0
                    done_count = len(r.files_written)
                    total_assigned = len(ag.get("files", []))
                    # Next pending assigned file (the one not yet written)
                    pending = [os.path.basename(f) for f in ag.get("files", [])
                               if f not in r.files_written]
                    current = pending[0] if pending else "finalising"
                    msg = f"{done_count}/{total_assigned} files · now: {current} · {elapsed}s"
                elif r.status == "done":
                    msg = f"✓ {len(r.files_written)}/{len(ag.get('files', []))} files · {r.elapsed}s"
                elif r.status == "failed":
                    msg = f"✗ {(r.error or '')[:30]}"
                else:
                    msg = "queued"
            rows.append({"id": ag["id"], "name": ag["name"],
                         "status": r.status if r else "queued", "msg": msg})
        last_lines = agent_live_panel(rows, tick)
        tick += 1
        _t.sleep(0.35)

    exec_thread.join()

    # Final panel
    if last_lines: erase_lines(last_lines)
    snapshot = orch.status_snapshot()
    rows = []
    for ag in snapshot:
        r = orch.results.get(ag["id"])
        msg = (f"✓ {len(r.files_written)} files · {r.elapsed}s"
               if r and r.status == "done"
               else (r.error or "")[:30] if r else "")
        rows.append({"id": ag["id"], "name": ag["name"],
                     "status": r.status if r else "queued", "msg": msg})
    agent_live_panel(rows, tick=0)

    # Restore log handler levels now that the panel is done.
    for _h, _lvl in _muted_handlers:
        _h.setLevel(_lvl)

    # ── Output ────────────────────────────────────────────────────────────────
    final = orch._build_output()
    console.print()
    console.print(final)

    save_message("user",      prompt)
    save_message("assistant", final)

    # ── Persist active project + write phantom_summary.md for future reuse ──
    succeeded = sum(1 for r in orch.results.values() if r.status == "done")
    if succeeded > 0:
        # Write the project's summary file so a future "same directive"
        # can find + extend this project instead of creating a new one.
        try:
            from omnicli.project_memory import write_summary
            file_rows = []
            for t in orch.tasks:
                r = orch.results.get(t.agent_id)
                if r:
                    for fp in r.files_written:
                        try: sz = os.path.getsize(fp)
                        except OSError: sz = 0
                        file_rows.append({"path": fp, "size": sz,
                                          "purpose": f"{t.name} — {t.role}"})
            agent_rows = []
            for t in orch.tasks:
                r = orch.results.get(t.agent_id)
                agent_rows.append({
                    "name": t.name, "role": t.role,
                    "status": (r.status if r else "missing"),
                    "elapsed_s": (r.elapsed if r else 0),
                })
            write_summary(
                project_dir=orch.project_dir,
                directive=getattr(orch, "original_prompt", prompt),
                refined=getattr(orch, "improved_prompt", ""),
                files=file_rows,
                agents=agent_rows,
                extra_runs=[{
                    "ts":     time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "action": "initial_build",
                    "note":   f"{succeeded}/{len(orch.tasks)} agents succeeded",
                }],
            )
        except Exception as _sm_err:
            logging.getLogger("omnicli").debug("summary write failed: %s", _sm_err)
        try:
            from omnicli.memory import save_config as _sc
            _sc("last_project_dir", orch.project_dir)
            # Detect entry file for the next-turn hint
            all_files = [f for r in orch.results.values() for f in r.files_written]
            entry = next(
                (f for name in ("app.py", "main.py", "index.js", "server.js", "manage.py", "run.py")
                 for f in all_files if f.endswith(name)),
                "",
            )
            _sc("last_project_entry", entry)
            _sc("last_project_prompt", prompt[:200])
        except Exception:
            pass
        info(f"📌 Active project set: {orch.project_dir}  (next 'run the app' uses this dir)")

        # ── Auto-launch the app if the user asked us to ─────────────────────
        # Triggered when prompt mentions run/launch/start/share, trust ≥ 2,
        # and the build produced a runner. Captures stdout for ~30s looking
        # for an http(s) URL (FastAPI default, or ngrok/cloudflared tunnel).
        import re as _re
        if trust >= 2 and _re.search(
            r"\b(run|launch|start|share|deploy|spin\s*up)\b",
            (prompt or "").lower(),
        ):
            url = _auto_launch_app(orch.project_dir, trust)
            if url:
                save_config("last_app_url", url)

    if get_config("voice_mode", "off") == "on":
        from omnicli.voice import speak
        speak(f"Multi-agent task complete. {sum(1 for r in orch.results.values() if r.status=='done')} agents succeeded.")


@app.command()
def telegram_cmd():
    """Start the Telegram bot in standalone mode."""
    init_db()
    from omnicli.auth import get_api_key as _gak
    from omnicli import telegram_bot
    if not get_config("telegram_token") or not get_config("telegram_chat_id"):
        error("Telegram not configured. Run: python run.py setup → option 3")
        raise typer.Exit(1)
    if not _gak():
        error("AI engine not configured. Run: python run.py setup → option 1")
        raise typer.Exit(1)

    from rich.panel import Panel as _Panel
    console.print(_Panel(
        f"[{CY}]⚡ PHANTOM CLI TELEGRAM BOT[/{CY}]  [{DIM}]v{__version__} · Aravind Labs[/{DIM}]\n"
        f"[{DIM}]Listening for messages. Press Ctrl+C to stop.[/{DIM}]",
        border_style=BLU, padding=(1, 4),
    ))

    if telegram_bot.start():
        success("Bot online! Send /start from Telegram.")
        send_telegram(f"⚡ *PhantomCLI v{__version__}* is online · Aravind Labs\nSend /help to see commands.")
        try:
            while telegram_bot.is_running():
                time.sleep(1)
        except KeyboardInterrupt:
            telegram_bot.stop()
            info("Bot stopped.")
    else:
        error("Failed to start bot.")
        raise typer.Exit(1)


@app.command(name="telegram")
def telegram_standalone():
    """Alias: Start the Telegram bot."""
    telegram_cmd()


@app.command()
def update(
    force: bool = typer.Option(False, "--force", "-f", help="Force update"),
    yes:   bool = typer.Option(False, "--yes",   "-y", help="Skip confirmation prompt"),
):
    """Check for updates and upgrade PhantomCLI."""
    from rich.panel import Panel as _Panel
    console.print(_Panel(
        f"[{CY}]⚡ PHANTOM UPDATER[/{CY}]  [{DIM}]current: v{__version__}[/{DIM}]",
        border_style=BLU, padding=(0, 4),
    ))

    is_non_interactive = not sys.stdin.isatty() or not sys.stdout.isatty()

    sp  = PhantomSpinner(); sp.start(phase="routing")
    upd = _check_for_update()
    sp.stop()

    if not upd and not force:
        success(f"Already on the latest version (v{__version__})")
        return
    if upd:
        console.print(f"\n  [{AMB}]New version: v{upd['version']}[/{AMB}]")
        for item in upd.get("changelog", [])[:4]:
            info(f"  • {item}")
        console.print()

    if not yes and not force:
        if is_non_interactive:
            info("Non-interactive mode detected. Use --yes or -y to skip confirmation.")
            return
        try:
            if not typer.confirm("Proceed with update?", default=True):
                info("Cancelled.")
                return
        except Exception:
            warn("Could not get confirmation. Use --yes to force update.")
            return

    ok = _do_update()
    if ok:
        # Verify what's actually on disk by reading the freshly-extracted
        # __init__.py rather than the in-memory __version__ (which is
        # whatever was loaded at process start — not what just landed).
        on_disk = _read_on_disk_omnicli_version() or "?"
        target = upd.get("version", "latest") if upd else "latest"
        if on_disk == target:
            success(f"Updated to v{target}! On-disk version verified: v{on_disk}. "
                    f"Shutting down so the new code takes effect — run `phantom chat` to continue.")
        else:
            warn(f"Update reported success but on-disk version reads as v{on_disk} "
                 f"(expected v{target}). If menus still show old options, run a "
                 f"clean reinstall — see https://phantom.aravindlabs.tech/docs.")
    else:
        raise typer.Exit(1)


def _read_on_disk_omnicli_version() -> str:
    """Read ``omnicli/__init__.py`` directly from disk and parse out
    ``__version__`` without importing the module (which would return the
    cached in-memory value from this very process)."""
    init_file = INSTALL_DIR / "omnicli" / "__init__.py"
    try:
        text = init_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    import re as _re
    m = _re.search(r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text)
    return m.group(1) if m else ""
