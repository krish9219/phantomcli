"""
PhantomCLI Telegram Bot  (v2)
─────────────────────────────
Two-way chat with PhantomCLI directly from Telegram.
- Separate trust level from local CLI (max Trust 3 — God Mode blocked)
- Full /command support (same as terminal, with Telegram-safe restrictions)
- Security: owner-only by Chat ID
- Permanent dangerous command blocklist applies here too
- Error messages are sanitized before sending to user
"""

import threading
import time
import requests
from omnicli.memory import get_config, init_db, save_message, get_recent_history
from omnicli.commands import handle as handle_command

_stop_event  = threading.Event()
_stop_typing = threading.Event()
_bot_thread: threading.Thread | None = None

BRANDING = "⚡ PhantomCLI · Aravind Labs"

# ─── RATE LIMITING ────────────────────────────────────────────────────────────

_msg_timestamps: list[float] = []
_RATE_LIMIT   = 20   # max messages per window
_RATE_WINDOW  = 60   # seconds


def _rate_ok() -> bool:
    """True if within rate limit, False if exceeded."""
    now = time.time()
    _msg_timestamps[:] = [t for t in _msg_timestamps if t > now - _RATE_WINDOW]
    if len(_msg_timestamps) >= _RATE_LIMIT:
        return False
    _msg_timestamps.append(now)
    return True


# ─── CONFIG HELPERS ───────────────────────────────────────────────────────────

def _token()   -> str: return get_config("telegram_token",   "")
def _chat_id() -> str: return get_config("telegram_chat_id", "")


def _tg_trust() -> int:
    """
    Telegram has its own trust level, defaults to 2 (Standard).
    Hard-capped at 3 — God Mode (4) is blocked on Telegram for security.
    """
    try:
        val = int(get_config("telegram_trust", "2"))
        return min(val, 3)  # Never allow God Mode remotely
    except Exception:
        return 2


# ─── TELEGRAM API HELPERS ─────────────────────────────────────────────────────

def _send(text: str, chat_id: str = "", parse_mode: str = "Markdown") -> bool:
    cid = chat_id or _chat_id()
    tok = _token()
    if not tok or not cid:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        if not r.ok:
            # Retry without parse mode if Markdown fails
            requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": cid, "text": text},
                timeout=10,
            )
        return True
    except Exception:
        return False


def _send_typing(chat_id: str):
    tok = _token()
    if not tok:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception:
        pass


def _keep_typing(chat_id: str):
    _stop_typing.clear()
    while not _stop_typing.wait(4):
        _send_typing(chat_id)
    _stop_typing.clear()


def _get_updates(offset: int) -> list:
    tok = _token()
    if not tok:
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{tok}/getUpdates",
            params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
            timeout=35,
        )
        if r.ok:
            return r.json().get("result", [])
    except Exception:
        pass
    return []


def _safe_error(err: Exception) -> str:
    """Return a safe, non-leaking error message for the user."""
    msg = str(err)
    # Strip file paths, stack info, and sensitive-looking tokens
    if len(msg) > 100 or "/" in msg or "Error" in type(err).__name__:
        return "An error occurred. Please try again."
    return f"Error: {msg[:80]}"


# ─── MESSAGE HANDLER ──────────────────────────────────────────────────────────

def _handle(update: dict):
    msg     = update.get("message", {})
    text    = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    sender  = msg.get("from", {}).get("first_name", "User")

    # ── Owner-only security gate ──────────────────────────────────────────────
    if chat_id != str(_chat_id()):
        _send("⛔ *Unauthorized.* This bot is private.", chat_id)
        return

    if not text:
        return

    # ── Rate limiting ─────────────────────────────────────────────────────────
    if not _rate_ok():
        _send("⚠️ Rate limit reached. Please slow down.", chat_id)
        return

    trust = _tg_trust()  # Always server-side, always capped at 3

    # ── /start special case ───────────────────────────────────────────────────
    if text.strip().lower() in ("/start", "/help"):
        _send(
            f"⚡ *PHANTOM CLI* · Aravind Labs\n\n"
            f"God Mode AI OS — now in your pocket.\n"
            f"Trust level: `{trust}` ({'Paranoid' if trust==1 else 'Standard' if trust==2 else 'Developer'})\n\n"
            "*Commands:*\n"
            "`/help` — this message\n"
            "`/status` — system status\n"
            "`/model <name>` — switch AI model\n"
            "`/clear` — clear conversation history\n"
            "`/memory` — memory stats\n"
            "`/image <prompt>` — generate an image\n"
            "`/voice <text>` — text-to-speech\n"
            "`/keys` — show API key pool\n"
            "`/export` — export conversation\n"
            "`/recall <query>` — search long-term memory\n"
            "`/tg-trust <1-3>` — set Telegram trust level\n\n"
            "Type anything else to chat with Phantom.",
        )
        return

    # ── Slash command dispatcher ──────────────────────────────────────────────
    result = handle_command(text, trust_level=trust, context="telegram")
    if result.handled:
        if result.reply:
            for chunk in [result.reply[i:i+4000] for i in range(0, len(result.reply), 4000)]:
                _send(chunk)
        return

    # ── Regular AI message ───────────────────────────────────────────────────
    from rich.console import Console
    Console().print(f"[dim]📱 Telegram [{sender}]: {text[:60]}{'…' if len(text)>60 else ''}[/dim]")

    _send_typing(chat_id)
    typing_thread = threading.Thread(target=_keep_typing, args=(chat_id,), daemon=True)
    typing_thread.start()

    try:
        init_db()
        from omnicli.engine import generate_response
        history  = get_recent_history(limit=20)
        save_message("user", text)
        response, _ = generate_response(text, history, trust)
        save_message("assistant", response)
        for chunk in [response[i:i+4000] for i in range(0, len(response), 4000)]:
            _send(chunk)
            if len(response) > 4000:
                time.sleep(0.3)
    except Exception as e:
        _send(f"❌ {_safe_error(e)}")
    finally:
        _stop_typing.set()


# ─── POLLING LOOP ─────────────────────────────────────────────────────────────

def _poll_loop():
    from rich.console import Console
    console = Console()
    offset  = 0
    console.print("[dim]📱 Telegram bot polling started[/dim]")
    while not _stop_event.is_set():
        try:
            updates = _get_updates(offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                _handle(upd)
        except Exception:
            time.sleep(5)
    console.print("[dim]📱 Telegram bot stopped[/dim]")


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def start() -> bool:
    global _bot_thread
    if not _token() or not _chat_id():
        return False
    if _bot_thread and _bot_thread.is_alive():
        return True
    _stop_event.clear()
    _bot_thread = threading.Thread(target=_poll_loop, daemon=True, name="tg-bot")
    _bot_thread.start()
    return True


def stop():
    _stop_event.set()


def is_running() -> bool:
    return _bot_thread is not None and _bot_thread.is_alive()


def notify(message: str) -> bool:
    """Send a one-way notification (used by engine for long task completion)."""
    return _send(message)
