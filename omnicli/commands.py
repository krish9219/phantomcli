"""
PhantomCLI In-Chat Command Processor
Intercepts /commands before they reach the AI engine.
Works in both terminal chat and Telegram.
"""

import re
import os
from omnicli.memory import get_config, save_config, init_db
from omnicli import settings as S

# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

class CommandResult:
    def __init__(self, handled: bool, reply: str = "", fatal: bool = False):
        self.handled = handled
        self.reply   = reply
        self.fatal   = fatal

NOT_A_COMMAND = CommandResult(False)

# Model name allowlist — alphanumeric, dots, dashes, colons, slashes (for org/model)
_MODEL_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._\-:/]{1,100}$')


# Single source of truth for slash commands — used by:
#   - handle() for dispatch
#   - chat_prompt() for autocompletion in the REPL
#   - /help text
COMMAND_REGISTRY: list[tuple[str, str]] = [
    ("/help",       "Show command reference"),
    ("/status",     "System status HUD"),
    ("/model",      "Switch AI model: /model <name>"),
    ("/models",     "List known-good models"),
    ("/trust",      "Set trust level 1-4"),
    ("/tg-trust",   "Set Telegram trust level (max 3)"),
    ("/clear",      "Clear conversation history"),
    ("/memory",     "Memory bank stats"),
    ("/read",       "Read file into context: /read <path>"),
    ("/recall",     "Search long-term memory: /recall <query>"),
    ("/undo",       "Undo last write_file"),
    ("/version",    "Version & update info"),
    ("/update",     "Check for updates"),
    ("/devices",    "List registered devices"),
    ("/export",     "Export conversation to file"),
    ("/shell",      "Toggle bash execution: /shell on|off"),
    ("/image",      "Generate image: /image <prompt>"),
    ("/voice",      "Voice mode: /voice on|off  or  /voice <text>"),
    ("/timeout",    "Set bash timeout: /timeout <seconds>"),
    ("/clean",      "Clean ~/phantom_projects: /clean [days|all]"),
    ("/project",    "Show or clear the active project: /project [clear]"),
    ("/workdir",    "Show or change PhantomCLI working directory: /workdir [path]"),
    ("/logs",       "Tail the running app's log (auto-launched build): /logs [N]"),
    ("/keys",       "API key pool: /keys [add|remove ...]"),
    ("/web",        "Web search + scrape + summarise: /web <query>"),
    ("/sources",    "Show sources from the last /web call"),
    ("/exit",       "Exit PhantomCLI"),
    ("/quit",       "Exit PhantomCLI"),
]


def available_commands() -> list[tuple[str, str]]:
    """Public accessor for the command registry (for autocomplete UIs)."""
    return list(COMMAND_REGISTRY)


# ─── DISPATCHER ───────────────────────────────────────────────────────────────

def handle(text: str, trust_level: int = 3, context: str = "terminal") -> CommandResult:
    """
    Parse and execute an in-chat slash command.
    context = 'terminal' | 'telegram'
    Returns CommandResult(handled=False) if not a command.
    """
    t = text.strip()
    if not t.startswith("/"):
        return NOT_A_COMMAND

    parts = t.split(None, 1)
    cmd   = parts[0].lower()
    args  = parts[1].strip() if len(parts) > 1 else ""

    dispatch = {
        "/help":      lambda: _help(),
        "/h":         lambda: _help(),
        "/status":    lambda: _status(),
        "/model":     lambda: _model(args),
        "/models":    lambda: _models(),
        "/trust":     lambda: _trust(args, context),
        "/tg-trust":  lambda: _tg_trust(args),
        "/clear":     lambda: _clear(),
        "/memory":    lambda: _memory(),
        "/version":   lambda: _version(),
        "/update":    lambda: _update(),
        "/devices":   lambda: _devices(),
        "/export":    lambda: _export(),
        "/shell":     lambda: _shell(args),
        "/image":     lambda: _image(args, trust_level),
        "/voice":     lambda: _voice(args),
        "/keys":      lambda: _keys(args),
        "/read":      lambda: _read(args),
        "/undo":      lambda: _undo(),
        "/recall":    lambda: _recall(args),
        "/timeout":   lambda: _timeout_cmd(args),
        "/clean":     lambda: _clean(args),
        "/project":   lambda: _project(args),
        "/workdir":   lambda: _workdir(args),
        "/logs":      lambda: _logs(args),
        "/web":       lambda: _web(args),
        "/sources":   lambda: _sources(args),
        "/exit":      lambda: CommandResult(True, "Shutting down PhantomCLI. Goodbye.", fatal=True),
        "/quit":      lambda: CommandResult(True, "Shutting down PhantomCLI. Goodbye.", fatal=True),
    }

    fn = dispatch.get(cmd)
    if fn:
        return fn()

    return CommandResult(True, f"Unknown command: `{cmd}`\nType /help to see available commands.")


# ─── COMMAND IMPLEMENTATIONS ──────────────────────────────────────────────────

def _help() -> CommandResult:
    lines = [
        "⚡ *PHANTOM CLI COMMANDS*",
        "",
        "`/help`                   Show this reference",
        "`/status`                 System status HUD",
        "`/model <name>`           Switch AI model",
        "`/models`                 List known-good models",
        "`/trust <1-4>`            Change trust level",
        "`/tg-trust <1-3>`         Set Telegram-only trust level (max 3)",
        "`/clear`                  Clear conversation history",
        "`/memory`                 Memory bank stats",
        "`/read <path>`            Read a file into conversation context",
        "`/recall <query>`         Search long-term memory and inject results",
        "`/undo`                   Undo the last write_file operation",
        "`/version`                Version & update info",
        "`/update`                 Check for updates",
        "`/devices`                List registered devices",
        "`/export`                 Export conversation to file",
        "`/shell on|off`           Toggle bash execution",
        "`/image <prompt>`         Generate an image",
        "`/voice <text>`           Text-to-speech",
        "`/timeout <seconds>`      Set bash execution timeout (default: 300s for trust 3+)",
        "`/clean [days]`           Clean up old ~/phantom_projects/ runs (default: 7d)",
        "`/workdir [path]`         Show or change PhantomCLI working directory",
        "`/keys`                   Show API key pool status",
        "`/keys add <key>`         Add key to pool (auto-assigns slot 2-4)",
        "`/keys add <key> <slot>`  Add key to specific slot (1-4)",
        "`/keys remove <slot>`     Remove key from slot 2-4",
        "`/web <query>`            Web search + scrape + summarise (info-only, no project)",
        "`/sources`                Show URLs from the most recent /web call",
        "`/exit`                   Exit PhantomCLI",
    ]
    return CommandResult(True, "\n".join(lines))


def _status() -> CommandResult:
    from omnicli.auth import get_api_key
    from omnicli.licensing import is_licensed, get_device_name
    from omnicli import __version__

    main_model   = get_config("main_model",   "not configured")
    router_model = get_config("router_model", "not configured")
    trust        = get_config("default_trust", "3")
    tg_trust     = get_config("telegram_trust", "2")
    tg_on        = bool(get_config("telegram_token"))
    shell_on     = get_config("shell_enabled", "true")
    bash_timeout = get_config("bash_timeout", "default (300s/60s)")
    licensed     = is_licensed()
    image_prov   = get_config("image_provider", "not set")
    voice_prov   = get_config("voice_tts_provider", "not set")

    from omnicli.auth import get_api_key_pool
    pool_status = get_api_key_pool().status()

    ok  = "✅"
    off = "❌"
    lines = [
        f"⚡ *PHANTOM CLI v{__version__}*  ·  Aravind Labs",
        "",
        f"Main Engine   : {ok if get_api_key() else off} `{main_model}`",
        f"Router Engine : {ok if get_config('router_api_key') else off} `{router_model}`",
        f"Telegram      : {ok if tg_on else off}",
        f"License       : {ok if licensed else off}",
        f"Shell Exec    : {'🟢 ON' if shell_on == 'true' else '🔴 OFF'}  (timeout: {bash_timeout}s)",
        f"Trust Level   : `{trust}` (local)  `{tg_trust}` (telegram)",
        f"Image API     : `{image_prov}`",
        f"Voice API     : `{voice_prov}`",
        f"Device        : `{get_device_name()[:40]}`",
        "",
        f"API Key Pool  : {len(pool_status)} key(s) configured",
    ]
    for row in pool_status:
        icon = "🟢" if "ready" in row["status"] else "🟡"
        lines.append(f"  Slot {row['slot']}  {icon} `{row['preview']}`  {row['status']}")
    return CommandResult(True, "\n".join(lines))


def _model(args: str) -> CommandResult:
    if not args:
        current = get_config("main_model", "not set")
        return CommandResult(True, f"Current model: `{current}`\nUsage: `/model <model-name>`")
    if not _MODEL_PATTERN.match(args):
        return CommandResult(True,
            "❌ Invalid model name. Use only letters, numbers, dots, dashes, colons, or slashes.\n"
            "Example: `/model llama-3.3-70b-versatile`")
    save_config("main_model", args)
    return CommandResult(True, f"✅ Model switched to `{args}`\nTakes effect on your next message.")


def _trust(args: str, context: str = "terminal") -> CommandResult:
    if not args or args not in ("1", "2", "3", "4"):
        current = get_config("default_trust", "3")
        labels  = {"1": "Paranoid", "2": "Standard", "3": "Developer", "4": "God Mode"}
        return CommandResult(True,
            f"Current trust: `{current}` ({labels.get(current, '?')})\n"
            "Usage: `/trust <1-4>`\n"
            "1=Paranoid  2=Standard  3=Developer  4=God Mode ⚠️")

    if args == "4" and context == "telegram":
        return CommandResult(True,
            "⛔ God Mode (Trust Level 4) cannot be activated remotely via Telegram.\n"
            "Activate locally at the terminal where PhantomCLI is running.")

    if args == "4" and context == "terminal":
        from omnicli.tui import god_mode_activation_sequence
        from omnicli.licensing import _load_cached
        def _get_key():
            d = _load_cached()
            return d.get("key", "") if d else ""
        if not god_mode_activation_sequence(_get_key):
            return CommandResult(True, "God Mode activation cancelled.")
        # Stamp activation so check_trust_gate can TTL-expire it later.
        from omnicli.executor import mark_god_mode_activated, _god_mode_ttl_s
        mark_god_mode_activated()
        ttl_min = _god_mode_ttl_s() // 60

    save_config("default_trust", args)
    labels = {"1": "Paranoid 🔒", "2": "Standard 🛡", "3": "Developer 🛠", "4": "God Mode 💀"}
    if args == "4":
        return CommandResult(True,
            f"✅ Trust level set to `{args}` — {labels[args]}\n"
            f"⏳ God Mode will auto-expire after {ttl_min} minutes of idle time. "
            "Re-run `/trust 4` to extend.")
    return CommandResult(True, f"✅ Trust level set to `{args}` — {labels[args]}")


def _tg_trust(args: str) -> CommandResult:
    if not args or args not in ("1", "2", "3", "4"):
        current = get_config("telegram_trust", "2")
        return CommandResult(True,
            f"Current Telegram trust: `{current}`\n"
            "Usage: `/tg-trust <1-3>` (max 3 — God Mode blocked on Telegram)")
    if args == "4":
        return CommandResult(True,
            "⛔ God Mode (Trust Level 4) cannot be set on Telegram.\n"
            "Maximum Telegram trust is 3 (Developer).")
    save_config("telegram_trust", args)
    labels = {"1": "Paranoid 🔒", "2": "Standard 🛡", "3": "Developer 🛠"}
    return CommandResult(True, f"✅ Telegram trust set to `{args}` — {labels[args]}")


def _clear() -> CommandResult:
    from omnicli.memory import clear_history
    init_db()
    clear_history()
    return CommandResult(True, "🧹 Conversation history cleared.")


def _memory() -> CommandResult:
    import sqlite3
    from omnicli.memory import DB_PATH
    if not os.path.exists(DB_PATH):
        return CommandResult(True, "No memory database found yet.")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM episodic_logs")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM episodic_logs WHERE role='user'")
        user  = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM episodic_logs WHERE role='assistant'")
        ai    = cur.fetchone()[0]
        try:
            cur.execute("SELECT COUNT(*) FROM rag_memory")
            rag = cur.fetchone()[0]
        except Exception:
            rag = 0
    size = os.path.getsize(DB_PATH) // 1024
    lines = [
        "🧠 *MEMORY BANK*",
        f"Episodic logs : `{total}` entries ({user} user · {ai} AI)",
        f"RAG memory    : `{rag}` chunks",
        f"Database size : `{size} KB`",
    ]
    return CommandResult(True, "\n".join(lines))


def _version() -> CommandResult:
    import requests
    from omnicli import __version__
    lines = [f"⚡ PhantomCLI v{__version__}  ·  Aravind Labs"]
    try:
        r    = requests.get("https://phantom.aravindlabs.tech/api/phantomcli/version", timeout=5)
        data = r.json()
        latest = data.get("version", "?")
        from packaging.version import Version
        if Version(latest) > Version(__version__):
            lines.append(f"\n🆕 Update available: v{latest}")
            lines.append("Run `/update` to upgrade.")
        else:
            lines.append("✅ You are on the latest version.")
        if data.get("changelog"):
            lines.append("\n*Latest changelog:*")
            for item in data["changelog"][:3]:
                lines.append(f"  • {item}")
    except Exception:
        lines.append("(Could not reach update server)")
    return CommandResult(True, "\n".join(lines))


def _update() -> CommandResult:
    try:
        from omnicli.cli import _check_for_update, _do_update
        info = _check_for_update()
        if not info:
            from omnicli import __version__
            return CommandResult(True, f"✅ Already on the latest version (v{__version__})")
        ok = _do_update()
        if ok:
            # fatal=True → REPL breaks cleanly. Python already cached the old
            # module code in sys.modules; staying in-session would silently run
            # stale code. Force relaunch so the user always runs fresh bytes.
            msg = (
                f"✅ Updated to v{info['version']}!\n"
                f"   Shutting down so the new code takes effect — run `phantom chat` to continue."
            )
            return CommandResult(True, msg, fatal=True)
        return CommandResult(True, "❌ Update failed. Try `python run.py update` manually.")
    except Exception as e:
        return CommandResult(True, f"❌ Update error: {e}")


def _devices() -> CommandResult:
    from omnicli.licensing import get_license_info, list_devices, _load_cached, get_device_id, MAX_DEVICES
    data = _load_cached()
    if not data:
        return CommandResult(True, "❌ No active license found.")
    key  = data.get("key", "")
    ok, devices = list_devices(key)
    if not ok:
        return CommandResult(True, "❌ Could not reach license server.")
    this  = get_device_id()
    lines = [f"💻 *REGISTERED DEVICES*  ({len(devices)}/{MAX_DEVICES} slots used)"]
    for i, d in enumerate(devices, 1):
        marker = " ← this device" if d.get("device_id") == this else ""
        lines.append(f"  {i}. {d.get('device_name','?')}{marker}")
    if len(devices) < MAX_DEVICES:
        lines.append(f"\n  {MAX_DEVICES - len(devices)} slot(s) remaining.")
    return CommandResult(True, "\n".join(lines))


def _export() -> CommandResult:
    import sqlite3
    import datetime
    from omnicli.memory import DB_PATH
    if not os.path.exists(DB_PATH):
        return CommandResult(True, "No conversation history to export.")
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT role, content, timestamp FROM episodic_logs ORDER BY id ASC")
        rows = cur.fetchall()
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.expanduser("~"), f"phantom_export_{ts}.txt")
    with open(path, "w", encoding="utf-8") as f:
        for role, content, stamp in rows:
            f.write(f"[{stamp}] {role.upper()}\n{content}\n\n{'─'*60}\n\n")
    return CommandResult(True, f"✅ Conversation exported to:\n`{path}`")


def _shell(args: str) -> CommandResult:
    if args.lower() in ("on", "true", "1"):
        save_config("shell_enabled", "true")
        return CommandResult(True, "🟢 Shell execution *enabled*.")
    if args.lower() in ("off", "false", "0"):
        save_config("shell_enabled", "false")
        return CommandResult(True, "🔴 Shell execution *disabled*.")
    current = get_config("shell_enabled", "true")
    return CommandResult(True,
        f"Shell execution: `{'ON' if current == 'true' else 'OFF'}`\n"
        "Usage: `/shell on` or `/shell off`")


def _image(prompt: str, trust_level: int) -> CommandResult:
    if not prompt:
        provider = get_config("image_provider", "not configured")
        return CommandResult(True,
            f"Usage: `/image <your prompt>`\n"
            f"Current provider: `{provider}`\n"
            "Configure in `python run.py setup` → Image APIs")

    provider = get_config("image_provider", "")
    if not provider:
        return CommandResult(True,
            "❌ No image provider configured.\n"
            "Run `python run.py setup` → Image APIs")

    from omnicli.media import generate_image
    from omnicli.tui import console as _con
    _con.print(f"  [dim]🎨 Generating image via {provider}…[/dim]")

    ok, result = generate_image(prompt)
    if ok:
        return CommandResult(True,
            f"✅ *Image generated!*\n"
            f"Provider : `{provider}`\n"
            f"Saved to : `{result}`\n"
            f"_(Auto-opened with your system viewer)_")
    return CommandResult(True, f"❌ Image generation failed:\n{result}")


def _voice(text: str) -> CommandResult:
    if not text:
        provider = get_config("voice_tts_provider", "not configured")
        return CommandResult(True,
            f"Usage: `/voice <text to speak>`\n"
            f"Current TTS provider: `{provider}`\n"
            "Configure in `python run.py setup` → Voice APIs")

    provider = get_config("voice_tts_provider", "")
    if not provider:
        return CommandResult(True,
            "❌ No TTS provider configured.\n"
            "Run `python run.py setup` → Voice APIs")

    from omnicli.media import generate_tts
    from omnicli.tui import console as _con
    _con.print(f"  [dim]🔊 Synthesising speech via {provider}…[/dim]")

    ok, result = generate_tts(text)
    if ok:
        return CommandResult(True,
            f"✅ *Speech synthesised!*\n"
            f"Provider : `{provider}`\n"
            f"Saved to : `{result}`\n"
            f"_(Auto-playing with your system player)_")
    return CommandResult(True, f"❌ TTS failed:\n{result}")


def _keys(args: str) -> CommandResult:
    """
    /keys                     — show pool status
    /keys add <apikey>        — add key to next free slot
    /keys add <apikey> <slot> — add key to specific slot (1-4)
    /keys remove <slot>       — remove key from slot 2-4
    """
    from omnicli.auth import get_api_key_pool, add_pool_key
    from omnicli.memory import save_config

    pool = get_api_key_pool()

    if not args or args.strip().lower() == "list":
        rows = pool.status()
        if not rows:
            return CommandResult(True,
                "No API keys configured.\n"
                "Add keys with: `/keys add <your-nvidia-api-key>`\n\n"
                "💡 Get free NVIDIA API keys at: build.nvidia.com\n"
                "   You can add up to 4 keys for automatic rotation.")
        lines = ["⚡ *API KEY POOL STATUS*", ""]
        for row in rows:
            icon = "🟢" if "ready" in row["status"] else "🟡"
            lines.append(f"  Slot {row['slot']}  {icon}  `{row['preview']}`  —  {row['status']}")
        lines += [
            "",
            f"*Total keys:* {len(rows)}  |  "
            f"*Ready:* {sum(1 for r in rows if 'ready' in r['status'])}",
            "",
            "Add more with: `/keys add <key>` (up to 4 total)",
            "Remove with:   `/keys remove <slot>`",
        ]
        return CommandResult(True, "\n".join(lines))

    parts = args.strip().split()
    sub   = parts[0].lower()

    # ── /keys add <key> [slot] ─────────────────────────────────────────────────
    if sub == "add":
        if len(parts) < 2:
            return CommandResult(True,
                "Usage: `/keys add <api-key> [slot]`\n"
                "Slot is optional (1-4). Omit to auto-assign the next free slot.")

        new_key = parts[1]
        if len(new_key) < 20:
            return CommandResult(True,
                "❌ That doesn't look like a valid API key (too short).\n"
                "NVIDIA keys typically start with `nvapi-`.")

        # Determine slot
        if len(parts) >= 3:
            try:
                slot = int(parts[2])
                if slot < 1 or slot > 4:
                    return CommandResult(True, "❌ Slot must be 1, 2, 3, or 4.")
            except ValueError:
                return CommandResult(True, "❌ Slot must be a number (1-4).")
        else:
            # Auto-assign: find first slot not in use
            existing = {r["preview"] for r in pool.status()}
            slot = None
            for s in range(1, 5):
                test_key = _get_slot_key(s)
                if not test_key:
                    slot = s
                    break
            if slot is None:
                return CommandResult(True,
                    "❌ All 4 slots are full. Remove one first with `/keys remove <slot>`.")

        ok = add_pool_key(new_key, slot)
        if ok:
            preview = f"{new_key[:8]}…{new_key[-4:]}"
            return CommandResult(True,
                f"✅ Key `{preview}` saved to slot {slot}.\n"
                f"Pool now has {len(pool.status())} key(s) — "
                f"automatic rotation active on rate limits.")
        return CommandResult(True, "❌ Failed to save key.")

    # ── /keys remove <slot> ────────────────────────────────────────────────────
    if sub == "remove":
        if len(parts) < 2:
            return CommandResult(True, "Usage: `/keys remove <slot>` (slot 2-4 only — slot 1 is primary)")
        try:
            slot = int(parts[1])
        except ValueError:
            return CommandResult(True, "❌ Slot must be a number.")
        if slot == 1:
            return CommandResult(True,
                "⚠️ Cannot remove slot 1 (primary key) via this command.\n"
                "Re-run `python run.py setup` to change your primary key.")
        if slot < 2 or slot > 4:
            return CommandResult(True, "❌ Slot must be 2, 3, or 4.")
        db_key = f"router_api_key_{slot}"
        save_config(db_key, "")
        return CommandResult(True, f"✅ Key in slot {slot} removed.")

    return CommandResult(True,
        "Unknown /keys sub-command.\n"
        "Usage:\n"
        "  `/keys`               — show pool status\n"
        "  `/keys add <key>`     — add a key (auto-assigns slot)\n"
        "  `/keys add <key> <slot>` — add to specific slot (1-4)\n"
        "  `/keys remove <slot>` — remove a key (slots 2-4 only)")


def _get_slot_key(slot: int) -> str:
    """Return the key stored in a given slot (1-4), or empty string."""
    if slot == 1:
        from omnicli.auth import get_api_key
        return get_api_key() or ""
    from omnicli.memory import get_config
    return get_config(f"router_api_key_{slot}", "") or ""


# ─── KNOWN-GOOD MODELS ────────────────────────────────────────────────────────

_KNOWN_MODELS = {
    "NVIDIA (free tier — build.nvidia.com)": [
        ("meta/llama-3.3-70b-instruct",  "Llama 3.3 70B   · best free-tier model ✓"),
        ("meta/llama-3.1-8b-instruct",   "Llama 3.1 8B    · fastest free model"),
        ("meta/llama-3.1-70b-instruct",  "Llama 3.1 70B   · solid general purpose"),
        ("mistralai/mistral-7b-instruct","Mistral 7B       · light & fast"),
        ("google/gemma-7b",              "Gemma 7B         · Google open model"),
        ("microsoft/phi-3-mini-128k-instruct", "Phi-3 Mini  · compact, 128K context"),
    ],
    "Groq (fast inference — console.groq.com)": [
        ("llama3-70b-8192",              "Llama 3 70B      · very fast"),
        ("llama3-8b-8192",               "Llama 3 8B       · ultra-fast router"),
        ("mixtral-8x7b-32768",           "Mixtral 8x7B     · long context"),
        ("gemma-7b-it",                  "Gemma 7B IT      · instruction-tuned"),
    ],
    "OpenAI (api.openai.com)": [
        ("gpt-4o",                       "GPT-4o           · flagship model"),
        ("gpt-4o-mini",                  "GPT-4o Mini      · fast & cheap"),
        ("gpt-4-turbo",                  "GPT-4 Turbo      · 128K context"),
    ],
    "Anthropic (api.anthropic.com/v1)": [
        ("claude-opus-4-7",              "Claude Opus 4.7  · most capable"),
        ("claude-sonnet-4-6",            "Claude Sonnet 4.6 · balanced"),
        ("claude-haiku-4-5-20251001",    "Claude Haiku 4.5 · fastest"),
    ],
}


def _models() -> CommandResult:
    current = get_config("main_model", "not set")
    lines   = [f"⚡ *KNOWN-GOOD MODELS*  (current: `{current}`)", ""]
    for provider, models in _KNOWN_MODELS.items():
        lines.append(f"*{provider}*")
        for model_id, desc in models:
            marker = " ← active" if model_id == current else ""
            lines.append(f"  `{model_id}`\n    {desc}{marker}")
        lines.append("")
    lines.append("Switch with: `/model <model-id>`")
    lines.append("Set base URL in `python run.py setup` → Main Engine")
    return CommandResult(True, "\n".join(lines))


def _read(path: str) -> CommandResult:
    """
    /read <path>  — read a file and inject its contents into the conversation.
    The assistant will see the file in the next message's context.
    """
    if not path:
        return CommandResult(True,
            "Usage: `/read <file-path>`\n"
            "Reads a file from disk and makes its content available to the AI.\n"
            "Example: `/read ~/projects/app.py`")

    import os as _os
    import stat as _stat
    from pathlib import Path as _Path
    # Chars the model actually sees. Tuned for ~50KB-ish source files; real
    # limit is the context window of the configured model.
    _MAX       = 200_000
    _MAX_BYTES = 10 * 1024 * 1024   # 10 MiB hard cap on what we'll even open

    p = _Path(path.replace("~", _os.path.expanduser("~")))
    if not p.exists():
        return CommandResult(True, f"❌ File not found: `{p}`")
    if p.is_dir():
        entries = sorted(p.iterdir())[:100]
        body = "\n".join(
            f"  {'📁' if e.is_dir() else '📄'} {e.name}"
            + (f"  ({e.stat().st_size:,}B)" if e.is_file() else "")
            for e in entries
        )
        return CommandResult(True, f"📁 *Directory:* `{p}`\n\n{body}")
    if not p.is_file():
        return CommandResult(True, f"❌ Not a readable file: `{p}`")

    try:
        st = p.stat()
        # Block character/block devices and FIFOs — reading /dev/urandom or a
        # named pipe would block forever.
        mode = st.st_mode
        if _stat.S_ISCHR(mode) or _stat.S_ISBLK(mode) or _stat.S_ISFIFO(mode) or _stat.S_ISSOCK(mode):
            return CommandResult(True,
                f"❌ Refusing to read `{p}` — not a regular file "
                "(looks like a device, pipe, or socket)."
            )
        size = st.st_size
        if size > _MAX_BYTES:
            return CommandResult(True,
                f"❌ File `{p}` is {size:,} bytes (> {_MAX_BYTES:,} limit).\n"
                "Refusing to read files larger than 10 MiB. Copy a smaller slice "
                "first (e.g. `head -c 32000 {path} > snippet.txt`) and `/read` that."
            )

        # Read with a hard byte cap so even an unknown-size pseudo-file can't hang us.
        with open(p, "rb") as fh:
            raw = fh.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return CommandResult(True,
                f"❌ File `{p}` exceeded the 10 MiB read cap while streaming. Aborted."
            )
        content = raw.decode("utf-8", errors="replace")
        lines   = len(content.splitlines())
        truncated = len(content) > _MAX
        snippet   = content[:_MAX]
        note = f"\n\n_(showing first {_MAX:,} of {len(content):,} chars)_" if truncated else ""
        return CommandResult(True,
            f"📄 *File:* `{p}`  ({lines} lines · {size:,} bytes)\n\n"
            f"```\n{snippet}\n```{note}\n\n"
            f"_(File content is now in context — ask the AI to work with it)_"
        )
    except Exception as e:
        return CommandResult(True, f"❌ Error reading file: {e}")


def _timeout_cmd(args: str) -> CommandResult:
    """
    /timeout           — show current bash execution timeout
    /timeout <seconds> — set a new timeout (10–3600s)
    """
    current = get_config("bash_timeout", "")
    if not args:
        default_display = "(default: 300s for trust 3+, 60s otherwise)"
        val = current or default_display
        return CommandResult(True,
            f"Bash execution timeout: `{val}`\n"
            "Usage: `/timeout <seconds>` (10–3600)")
    try:
        secs = int(args.strip())
        if secs < 10 or secs > 3600:
            return CommandResult(True, "❌ Timeout must be between 10 and 3600 seconds.")
        save_config("bash_timeout", str(secs))
        return CommandResult(True, f"✅ Bash timeout set to `{secs}s`.")
    except ValueError:
        return CommandResult(True, "❌ Please provide a number of seconds (e.g. `/timeout 300`)")


def _recall(query: str) -> CommandResult:
    """
    /recall <query>  — search long-term RAG memory and display matching facts.
    The results are shown in-context so the AI can use them in the next message.
    """
    if not query:
        return CommandResult(True,
            "Usage: `/recall <search query>`\n"
            "Searches your long-term memory bank and injects matching facts.\n"
            "Example: `/recall Python scraper project`")

    from omnicli.memory import search_rag_memory, DB_PATH
    import os as _os

    if not _os.path.exists(DB_PATH):
        return CommandResult(True, "No memory database found yet. Start chatting to build memory.")

    hits = search_rag_memory(query, limit=5)
    if not hits:
        return CommandResult(True,
            f"No memories found matching: `{query}`\n"
            "Use `/memory` to see memory stats.")

    lines = [f"🧠 *MEMORY RECALL* — `{query}`", ""]
    for i, hit in enumerate(hits, 1):
        lines.append(f"**{i}.** {hit[:500]}")
        if len(hit) > 500:
            lines.append(f"   _(truncated — {len(hit):,} chars total)_")
        lines.append("")
    lines.append("_(These facts are now in context for the AI)_")
    return CommandResult(True, "\n".join(lines))


def _clean(args: str) -> CommandResult:
    """
    /clean            — delete ~/phantom_projects/* entries older than 7 days
    /clean <days>     — use a custom age threshold (0 = wipe everything)
    /clean all        — alias for /clean 0
    """
    import shutil
    import time as _time
    from pathlib import Path as _Path

    base = _Path.home() / "phantom_projects"
    if not base.exists():
        return CommandResult(True, "Nothing to clean — `~/phantom_projects/` doesn't exist yet.")

    arg = args.strip().lower()
    if arg in ("", "default"):
        days = 7
    elif arg == "all":
        days = 0
    else:
        try:
            days = int(arg)
            if days < 0:
                return CommandResult(True, "❌ Days must be a non-negative integer.")
        except ValueError:
            return CommandResult(True,
                "Usage: `/clean [days|all]`  (default 7 days)")

    cutoff = _time.time() - (days * 86400)
    removed: list[tuple[str, int]] = []
    errors:  list[str] = []
    freed_bytes = 0

    for entry in base.iterdir():
        try:
            if days > 0 and entry.stat().st_mtime >= cutoff:
                continue
            # Sum sizes before delete
            size = 0
            if entry.is_dir():
                for p in entry.rglob("*"):
                    try:
                        if p.is_file():
                            size += p.stat().st_size
                    except OSError:
                        pass
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                entry.unlink(missing_ok=True)
            removed.append((entry.name, size))
            freed_bytes += size
        except Exception as e:
            errors.append(f"{entry.name}: {e}")

    if not removed and not errors:
        return CommandResult(True,
            f"✨ Nothing to clean — no entries older than {days} day(s) in `~/phantom_projects/`.")

    def _fmt(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    lines = [
        f"🧹 *CLEANED `~/phantom_projects/`*",
        f"Threshold : older than {days} day(s)" if days > 0 else "Threshold : all entries",
        f"Removed   : {len(removed)} entries ({_fmt(freed_bytes)} freed)",
    ]
    for name, size in removed[:10]:
        lines.append(f"  • `{name}`  ({_fmt(size)})")
    if len(removed) > 10:
        lines.append(f"  …and {len(removed) - 10} more")
    if errors:
        lines.append("")
        lines.append(f"⚠ {len(errors)} errors:")
        for msg in errors[:5]:
            lines.append(f"  • {msg}")
    return CommandResult(True, "\n".join(lines))


def _project(args: str) -> CommandResult:
    """Show, set, or clear the active multi-agent project directory."""
    arg = (args or "").strip()
    if arg.lower() in ("clear", "reset", "off"):
        save_config("last_project_dir",    "")
        save_config("last_project_entry",  "")
        save_config("last_project_prompt", "")
        return CommandResult(True, "✅ Active project cleared.")

    if arg:
        # /project <path> — set explicitly
        target = os.path.expanduser(arg)
        if not os.path.isdir(target):
            return CommandResult(True, f"❌ Not a directory: `{target}`")
        save_config("last_project_dir", target)
        # Re-detect entry file
        entry = ""
        for name in ("app.py", "main.py", "server.js", "index.js", "manage.py", "run.py"):
            cand = os.path.join(target, name)
            if os.path.isfile(cand):
                entry = cand
                break
        save_config("last_project_entry", entry)
        return CommandResult(True,
            f"✅ Active project set: `{target}`"
            + (f"\nEntry file: `{entry}`" if entry else ""))

    proj   = get_config("last_project_dir", "")
    entry  = get_config("last_project_entry", "")
    prompt = get_config("last_project_prompt", "")
    if not proj:
        return CommandResult(True,
            "No active project.\n"
            "Set one with `/project <path>` or run a multi-agent task.")
    exists = "✅" if os.path.isdir(proj) else "❌ (missing)"
    lines = [
        "📌 *ACTIVE PROJECT*",
        f"Dir    : `{proj}`  {exists}",
    ]
    if entry:  lines.append(f"Entry  : `{entry}`")
    if prompt: lines.append(f"Prompt : {prompt}")
    lines.append("")
    lines.append("Use `/project clear` to forget it.")
    return CommandResult(True, "\n".join(lines))


def _logs(args: str) -> CommandResult:
    """Tail the log of the most recently auto-launched app."""
    log_path = (get_config("last_app_log", "") or "").strip()
    pid      = (get_config("last_app_pid", "") or "").strip()
    url      = (get_config("last_app_url", "") or "").strip()
    if not log_path or not os.path.isfile(log_path):
        return CommandResult(True,
            "No app log found. `/logs` shows output from apps that Phantom "
            "auto-launched after a multi-agent build.")
    try:
        n = max(1, int((args or "60").strip()))
    except ValueError:
        n = 60
    try:
        with open(log_path, "r", errors="replace") as f:
            tail = f.readlines()[-n:]
    except OSError as e:
        return CommandResult(True, f"❌ Could not read {log_path}: {e}")
    head_lines = [
        f"📜 *APP LOG*  ({len(tail)} lines)",
        f"PID : `{pid or 'unknown'}`",
        f"URL : `{url or 'unknown'}`",
        f"FILE: `{log_path}`",
        "",
        "```",
    ]
    return CommandResult(True, "\n".join(head_lines + [l.rstrip() for l in tail] + ["```"]))


# Session-local cache of the sources from the most recent /web call.
# Shown on demand via /sources so the primary /web output stays clean.
_LAST_WEB_SOURCES: list[tuple[str, str]] = []
_LAST_WEB_QUERY: str = ""


def _sources(args: str) -> CommandResult:
    """/sources — show the URLs scraped by the most recent /web call.

    Kept separate from /web output so the answer itself stays uncluttered.
    Type /sources any time after a /web to see the full list with a short
    excerpt from each page."""
    if not _LAST_WEB_SOURCES:
        return CommandResult(True,
            "No /web call in this session yet. Run `/web <query>` first, "
            "then `/sources` will list the URLs it scraped.")
    lines = [
        f"📚 *Sources from /web:* `{_LAST_WEB_QUERY}`",
        f"({len(_LAST_WEB_SOURCES)} pages scraped live)",
        "",
    ]
    for i, (u, t) in enumerate(_LAST_WEB_SOURCES, 1):
        first_line = (t.splitlines() or [""])[0][:100]
        lines.append(f"  [{i}] {u}")
        if first_line.strip():
            lines.append(f"      _{first_line}_")
    return CommandResult(True, "\n".join(lines))


def _web(args: str) -> CommandResult:
    """/web <query> — ITERATIVE web research (Claude-Code-style).

    Does multi-step search + scrape + follow-up rather than one shallow
    pass. Flow:
      Round 1: search user's query → scrape top 5 URLs
      Refine:  router model inspects what was found, proposes 2 specific
               follow-up queries targeted at the gaps
      Round 2: for each follow-up, search + scrape top 2 URLs
      Synth:   all ~9 scraped pages → comprehensive answer with
               inline [Source N] citations

    Info-only. No project creation. No file writes. ~30-60s wall time.
    """
    query = (args or "").strip()
    if not query:
        return CommandResult(True,
            "Usage: `/web <query>`\n\nExamples:\n"
            "  `/web latest IPL 2026 match results`\n"
            "  `/web bitcoin price now`\n"
            "  `/web top tech news today`\n\n"
            "How it works (iterative):\n"
            "  • Round 1: DuckDuckGo search → scrape top 5 pages (Playwright/Jina/requests)\n"
            "  • Refine:  router model proposes 2 specific follow-up queries based on gaps\n"
            "  • Round 2: scrape top 2 pages per follow-up\n"
            "  • Synth:   ~9 pages → comprehensive, cited answer\n"
            "  No project, no file writes. 30-60s wall time.",
        )

    import re as _re
    import logging as _lg
    import json as _json
    log = _lg.getLogger("omnicli.commands._web")

    def _extract_urls(raw: str) -> list[str]:
        urls = []
        for u in _re.findall(r'https?://[^\s\]\)\'"<>]+', raw or ""):
            u = u.rstrip(".,;:!?)")
            if any(bad in u.lower() for bad in (
                "google.com/url", "duckduckgo.com/y.js", "bing.com/ck",
            )):
                continue
            if u not in urls:
                urls.append(u)
        return urls

    def _search(q: str, n: int = 6) -> list[str]:
        try:
            from omnicli.engine import _web_search
            return _extract_urls(_web_search(q, max_results=n))
        except Exception as e:
            log.debug("search error %r: %s", q, e)
            return []

    def _scrape_many(urls: list[str], cap: int,
                     exclude: set[str]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        try:
            from omnicli.browser import run_browser
        except Exception:
            return out
        for u in urls:
            if u in exclude:
                continue
            if len(out) >= cap:
                break
            try:
                text = run_browser(u) or ""
            except Exception as e:
                log.debug("browse error %s: %s", u, e)
                continue
            if text and "Could not fetch" not in text and len(text) > 300:
                out.append((u, text[:6000]))
                exclude.add(u)
        return out

    def _llm_call(prompt: str, max_tokens: int = 1800,
                  temperature: float = 0.2) -> str:
        try:
            from openai import OpenAI
            from omnicli.auth import get_api_key
            key = get_api_key()
            if not key:
                return ""
            model = ((get_config("router_model", "") or "").strip() or
                     (get_config("main_model",   "") or "").strip() or
                     "gpt-4o-mini")
            base  = ((get_config("router_url",   "") or "").strip() or
                     (get_config("main_url",     "") or "").strip() or None)
            client = OpenAI(api_key=key, base_url=base)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens, temperature=temperature,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            log.debug("llm call failed: %s", e)
            return ""

    # Give the user ONE status line up front so they know work is in flight.
    # The rest of the scraping is silent — result goes to the final summary.
    import sys as _sys
    _sys.stdout.write(f"🌐 Researching: {query[:80]}{'…' if len(query) > 80 else ''}\n")
    _sys.stdout.write("   (2 rounds · scraping live · ~30-60s)\n")
    _sys.stdout.flush()

    # Silence the browser module's "Phantom Browser launching…" console
    # prints for the duration of this call so the final output isn't
    # drowned in per-URL log lines.
    from omnicli import browser as _browser
    _browser.set_quiet(True)
    try:
        # ── Round 1 ───────────────────────────────────────────────────────
        round1_urls = _search(query, n=6)
        if not round1_urls:
            return CommandResult(True, f"`/web`: no search results for {query!r}.")
        seen: set[str] = set()
        scraped = _scrape_many(round1_urls[:5], cap=5, exclude=seen)
        if not scraped:
            return CommandResult(True,
                "All pages blocked / unreachable. Corporate firewall, DNS, "
                "or bot-wall. Try a more specific query.")

        # ── Refinement: ask for 2 follow-up queries ───────────────────────
        round1_digest = "\n\n---\n\n".join(
            f"[SOURCE {i+1}: {u}]\n{t[:3000]}"
            for i, (u, t) in enumerate(scraped)
        )
        refine_prompt = (
            f"The user wants comprehensive, current information about: {query!r}\n\n"
            f"Round 1 scraped {len(scraped)} pages (excerpts below). Based on "
            f"what they say AND what's missing, propose exactly 2 follow-up "
            f"SEARCH QUERIES that fill the gaps. Each MUST be specific — use "
            f"concrete names, dates, entities found in round 1. Return ONLY a "
            f"JSON array of 2 strings. No preamble, no fences.\n\n"
            f"ROUND 1 CONTENT:\n{round1_digest}"
        )
        refine_raw = _llm_call(refine_prompt, max_tokens=300, temperature=0.2)
        followups: list[str] = []
        try:
            m = _re.search(r'\[[\s\S]*\]', refine_raw)
            if m:
                parsed = _json.loads(m.group())
                if isinstance(parsed, list):
                    followups = [str(q).strip() for q in parsed[:2] if str(q).strip()]
        except Exception as e:
            log.debug("refine parse failed: %s", e)

        # ── Round 2 ───────────────────────────────────────────────────────
        for fq in followups:
            r2 = _search(fq, n=4)
            more = _scrape_many(r2, cap=2, exclude=seen)
            scraped.extend(more)

        # ── Stash sources for /sources ────────────────────────────────────
        global _LAST_WEB_SOURCES, _LAST_WEB_QUERY
        _LAST_WEB_SOURCES = list(scraped)
        _LAST_WEB_QUERY = query

        # ── Synthesis — NARRATIVE output, no Step 1/2/3 chain-of-thought ─
        all_digest = "\n\n---\n\n".join(
            f"[SOURCE {i+1}: {u}]\n{t}"
            for i, (u, t) in enumerate(scraped)
        )
        synth_prompt = (
            f"User's question: {query!r}\n\n"
            f"{len(scraped)} web pages scraped live below. Answer the user in "
            f"CLEAN NARRATIVE FORM — not a step-by-step research walkthrough.\n\n"
            f"FORMAT RULES (strict):\n"
            f"  • DO NOT write 'Step 1:', 'Step 2:', or explain your research "
            f"process. The user does not want to read about HOW you found the "
            f"answer — just the answer itself.\n"
            f"  • DO NOT cite sources inline with [Source 1] [Source 2] etc. "
            f"Source attribution is handled separately by /sources. Write as if "
            f"you're a confident analyst, not a footnote-heavy wiki editor.\n"
            f"  • Use short section headings (2-4 words, bolded markdown) ONLY "
            f"when the topic genuinely needs structure (e.g. 'Current match', "
            f"'Recent form', 'Head-to-head', 'Expert take'). No more than 5 "
            f"headings total. Skip headings entirely for short factual queries.\n"
            f"  • Include SPECIFIC facts: names, scores, numbers, dates, venues, "
            f"quotes when present.\n"
            f"  • If the user asked for expert analysis / predictions, give "
            f"direct reasoning grounded in the scraped data — NOT generic "
            f"hedging. Pick a side and defend it briefly.\n"
            f"  • Length matches the ask: factual queries get 2-4 sentences; "
            f"analyst queries get ~400 words.\n"
            f"  • If the pages don't answer something, say one short line about "
            f"it (e.g. 'Live score not yet published.') — don't fabricate.\n\n"
            f"SCRAPED PAGES:\n{all_digest}"
        )
        summary = _llm_call(synth_prompt, max_tokens=2400, temperature=0.25)

        if summary:
            lines = [summary, ""]
            lines.append(
                f"_{len(scraped)} source{'s' if len(scraped) != 1 else ''} "
                f"scraped live · type `/sources` to see them_"
            )
            return CommandResult(True, "\n".join(lines))

        # Synth failed — raw excerpts as last-resort
        lines = [
            "_(summariser unavailable — raw excerpts below, "
            f"type `/sources` for URLs)_",
        ]
        for u, t in scraped:
            lines.append("")
            lines.append(f"**{u}**")
            lines.append(t[:600] + ("…" if len(t) > 600 else ""))
        return CommandResult(True, "\n".join(lines))
    finally:
        _browser.set_quiet(False)


def _workdir(args: str) -> CommandResult:
    """Show or change the persistent PhantomCLI working directory.
    The chosen path is the cwd Phantom uses across sessions for new projects."""
    arg = (args or "").strip()
    current = (get_config("work_dir", "") or "").strip()

    if not arg:
        if not current:
            return CommandResult(True,
                "No working directory set. Choose one with `/workdir <path>`.\n"
                f"Current process cwd: `{os.getcwd()}`")
        exists = "✅" if os.path.isdir(current) else "❌ (missing)"
        same   = "  *(matches current cwd)*" if os.path.realpath(current) == os.path.realpath(os.getcwd()) else ""
        return CommandResult(True,
            "📁 *WORKING DIRECTORY*\n"
            f"Path : `{current}`  {exists}{same}\n"
            f"Cwd  : `{os.getcwd()}`\n\n"
            "Change it with `/workdir <path>`.  Takes effect immediately.")

    target = os.path.expanduser(arg).rstrip("/\\")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as e:
        return CommandResult(True, f"❌ Could not create `{target}`: {e}")
    if not os.path.isdir(target):
        return CommandResult(True, f"❌ Not a directory: `{target}`")
    save_config("work_dir", target)
    try:
        os.chdir(target)
    except OSError as e:
        return CommandResult(True, f"⚠ Saved `{target}`, but couldn't chdir into it: {e}")
    return CommandResult(True,
        f"✅ Working directory set: `{target}`\n"
        "Persists across sessions. Future projects and `/run` commands use this path.")


def _undo() -> CommandResult:
    """Undo the last write_file operation performed by the AI."""
    try:
        from omnicli.engine import _write_undo_stack
    except ImportError:
        return CommandResult(True, "❌ Undo not available (engine not loaded).")

    if not _write_undo_stack:
        return CommandResult(True, "Nothing to undo — no write_file operations in this session.")

    path, old_content = _write_undo_stack.pop()
    import os as _os
    from pathlib import Path as _Path
    p = _Path(path)

    try:
        if old_content is None:
            # File didn't exist before — delete it
            if p.exists():
                p.unlink()
            return CommandResult(True, f"✅ Undo: deleted `{p}` (was newly created)")
        else:
            # Restore previous content
            p.write_text(old_content, encoding="utf-8")
            lines = len(old_content.splitlines())
            return CommandResult(True,
                f"✅ Undo: restored `{p}` to previous version\n"
                f"  ({lines} lines · {len(old_content):,} chars)"
            )
    except Exception as e:
        return CommandResult(True, f"❌ Undo failed: {e}")
