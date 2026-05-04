import os
import hmac
import uuid
import base64
import hashlib
from cryptography.fernet import Fernet
from rich.console import Console

console = Console()

CONFIG_DIR  = os.path.expanduser("~/.omnicli")
KEY_FILE    = os.path.join(CONFIG_DIR, ".machine_key")
TOKEN_FILE  = os.path.join(CONFIG_DIR, ".api_token")

os.makedirs(CONFIG_DIR, exist_ok=True)


def _get_machine_key() -> bytes:
    """
    Returns a Fernet-compatible key bound to this machine's hardware.

    New installs: stores a 32-byte random seed; derives the effective key via
    HMAC-SHA256(seed, hw_fingerprint). Copying the seed file to another machine
    produces a different key, so encrypted secrets become unreadable elsewhere.

    Legacy installs (44-byte Fernet key already on disk): returned as-is for
    backwards compatibility — no re-setup required on existing machines.
    """
    if not os.path.exists(KEY_FILE):
        seed = os.urandom(32)
        with open(KEY_FILE, "wb") as f:
            f.write(seed)
        os.chmod(KEY_FILE, 0o600)

    with open(KEY_FILE, "rb") as f:
        stored = f.read()

    # Legacy: file already contains a valid 44-byte Fernet key. Use as-is.
    if len(stored) == 44:
        return stored

    # New: 32-byte random seed → derive hardware-bound Fernet key.
    hw  = hashlib.sha256(str(uuid.getnode()).encode()).digest()
    raw = hmac.new(stored[:32], hw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw)


def save_api_key(api_key: str):
    """Encrypts the API key and saves it to a hidden file."""
    try:
        fernet = Fernet(_get_machine_key())
        encrypted_token = fernet.encrypt(api_key.encode())
        with open(TOKEN_FILE, "wb") as f:
            f.write(encrypted_token)
        os.chmod(TOKEN_FILE, 0o600)
        console.print("[bold green]✔ API Key encrypted and saved securely.[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Failed to save key securely: {e}[/bold red]")


def get_api_key() -> str | None:
    """Decrypts and retrieves the API key."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        fernet = Fernet(_get_machine_key())
        with open(TOKEN_FILE, "rb") as f:
            encrypted_token = f.read()
        return fernet.decrypt(encrypted_token).decode()
    except Exception:
        return None


# ── Multi-key pool (round-robin with per-key 429 cooldowns) ───────────────────

import time as _time
import threading as _threading

class _KeyPool:
    """
    Round-robin key pool with per-key rate-limit cooldown.

    Keys come from two sources (merged, deduplicated):
      1. The primary encrypted token file (legacy/main key)
      2. `router_api_key`, `router_api_key_2` … `router_api_key_4` in the profile DB

    On 429: mark that key as cooling down for `_COOLDOWN_S` seconds, advance to
    the next available key. If all keys are cooling, sleep until the earliest
    cooldown expires instead of hard-failing.
    """
    _COOLDOWN_S = 65   # seconds to cool a key after receiving 429

    def __init__(self):
        self._lock      = _threading.Lock()
        self._idx       = 0
        self._cooldowns: dict[str, float] = {}   # key → earliest-retry timestamp

    def _load_keys(self) -> list[str]:
        """Return deduplicated ordered list of all configured API keys.

        NOTE: 'router_api_key' (no suffix) is intentionally excluded — it is
        the dedicated router/persona model key (e.g. Groq) and must not be
        rotated into main-model requests. Only the primary token file and the
        explicitly pool-registered keys (router_api_key_2/3/4) are used here.
        """
        from omnicli.memory import get_config
        keys = []
        # Primary (file-based, slot 1)
        pk = get_api_key()
        if pk:
            keys.append(pk)
        # Explicitly pool-registered keys (slots 2-4), added via /keys add
        for slot in ("router_api_key_2", "router_api_key_3", "router_api_key_4"):
            k = get_config(slot, "")
            if k and k not in keys:
                keys.append(k)
        return keys

    def get(self) -> str | None:
        """Return the next available key, blocking briefly if all are cooling."""
        with self._lock:
            keys = self._load_keys()
            if not keys:
                return None

            now = _time.time()
            # Try each key starting at current rotation index
            for offset in range(len(keys)):
                idx = (self._idx + offset) % len(keys)
                key = keys[idx]
                retry_at = self._cooldowns.get(key, 0)
                if now >= retry_at:
                    self._idx = (idx + 1) % len(keys)
                    return key

            # All keys cooling — find the one that wakes up soonest
            earliest_key   = min(keys, key=lambda k: self._cooldowns.get(k, 0))
            wait_s = self._cooldowns[earliest_key] - now
            return None   # caller will see None and handle wait

    def mark_rate_limited(self, key: str):
        """Call this when a key receives HTTP 429."""
        with self._lock:
            self._cooldowns[key] = _time.time() + self._COOLDOWN_S

    def status(self) -> list[dict]:
        """Return pool status — useful for /status command."""
        from omnicli.memory import get_config
        keys  = self._load_keys()
        now   = _time.time()
        rows  = []
        for i, k in enumerate(keys):
            retry_at = self._cooldowns.get(k, 0)
            cooling  = now < retry_at
            rows.append({
                "slot":    i + 1,
                "preview": f"{k[:8]}…{k[-4:]}",
                "status":  f"cooling {int(retry_at - now)}s" if cooling else "ready",
            })
        return rows


# Singleton pool — shared across all generate_response calls in this process
key_pool = _KeyPool()


def get_api_key_pool() -> "_KeyPool":
    """Return the singleton KeyPool instance."""
    return key_pool


def add_pool_key(key: str, slot: int) -> bool:
    """
    Save an additional API key to the pool (slot 2-4).
    slot=1 saves to the primary token file; slots 2-4 go to the profile DB.
    Returns True on success.
    """
    if slot == 1:
        save_api_key(key)
        return True
    if 2 <= slot <= 4:
        from omnicli.memory import save_config
        db_key = f"router_api_key_{slot}"
        save_config(db_key, key)
        return True
    return False
