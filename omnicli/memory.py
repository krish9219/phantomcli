import os
import re
import sqlite3
from datetime import datetime

DB_PATH = os.path.expanduser("~/.omnicli/memory.db")

# ─── SENSITIVE KEYS ───────────────────────────────────────────────────────────
# Values stored under these keys are encrypted at rest using the machine key.

_SENSITIVE_KEYS = frozenset({
    "router_api_key",
    "router_api_key_2",
    "router_api_key_3",
    "router_api_key_4",
    "fal_api_key",
    "openai_image_key",
    "stability_key",
    "replicate_key",
    "elevenlabs_key",
    "openai_tts_key",
    "playht_key",
    "deepgram_key",
    "assemblyai_key",
    "telegram_token",
    "runwayml_key",
    "kling_key",
    "suno_key",
})

_ENC_PREFIX = "enc:"

# ─── PRUNING THRESHOLDS ───────────────────────────────────────────────────────

_PRUNE_TRIGGER = 600   # start pruning when episodic log exceeds this
_PRUNE_KEEP    = 500   # keep only the most recent N entries


def _encrypt(value: str) -> str:
    try:
        from omnicli.auth import _get_machine_key
        from cryptography.fernet import Fernet
        return _ENC_PREFIX + Fernet(_get_machine_key()).encrypt(value.encode()).decode()
    except Exception:
        return value


def _decrypt(value: str) -> str:
    if not value.startswith(_ENC_PREFIX):
        return value
    try:
        from omnicli.auth import _get_machine_key
        from cryptography.fernet import Fernet
        return Fernet(_get_machine_key()).decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except Exception:
        return ""


# ─── FTS5 SANITISATION ────────────────────────────────────────────────────────

_FTS5_SPECIAL = re.compile(r'["\*\(\)\^\+\-]')


def _sanitize_fts5(query: str) -> str:
    clean = _FTS5_SPECIAL.sub(' ', query)
    return clean.strip()[:500]


# ─── SCHEMA ───────────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS episodic_logs (
                id        INTEGER PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                role      TEXT,
                content   TEXT
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS rag_memory USING fts5(
                content,
                topic
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)


# ─── EPISODIC LOGS ────────────────────────────────────────────────────────────

def save_message(role: str, content: str):
    """Saves a message to the episodic log. Auto-prunes when the log exceeds the threshold."""
    if role not in ("user", "assistant", "system"):
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO episodic_logs (role, content) VALUES (?, ?)",
            (role, content[:32_000]),
        )
    _auto_prune()


def _auto_prune():
    """Delete oldest entries if the log exceeds _PRUNE_TRIGGER rows."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM episodic_logs")
            total = cur.fetchone()[0]
            if total > _PRUNE_TRIGGER:
                conn.execute(
                    "DELETE FROM episodic_logs WHERE id IN "
                    "(SELECT id FROM episodic_logs ORDER BY id ASC LIMIT ?)",
                    (total - _PRUNE_KEEP,),
                )
    except Exception:
        pass


def prune_episodic(keep: int = _PRUNE_KEEP):
    """Explicitly prune the episodic log to the most recent `keep` entries."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM episodic_logs")
            total = cur.fetchone()[0]
            if total > keep:
                conn.execute(
                    "DELETE FROM episodic_logs WHERE id IN "
                    "(SELECT id FROM episodic_logs ORDER BY id ASC LIMIT ?)",
                    (total - keep,),
                )
    except Exception:
        pass


def clear_history():
    """Delete all episodic log entries."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM episodic_logs")


def get_recent_history(limit: int = 10):
    """Retrieves the most recent chat history."""
    limit = max(1, min(limit, 200))
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content FROM episodic_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [{"role": row[0], "content": row[1]} for row in reversed(cur.fetchall())]


# ─── RAG MEMORY ───────────────────────────────────────────────────────────────

def save_rag_memory(topic: str, content: str):
    """Saves a fact to long-term memory. Skips exact duplicates."""
    content = content[:4000]
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rag_memory WHERE content = ?", (content,))
        if cur.fetchone()[0] > 0:
            return
        conn.execute(
            "INSERT INTO rag_memory (topic, content) VALUES (?, ?)",
            (topic[:200], content),
        )


def search_rag_memory(query: str, limit: int = 3):
    """Searches long-term memory for relevant context using FTS5."""
    limit = max(1, min(limit, 50))
    safe  = _sanitize_fts5(query)
    if not safe:
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT content FROM rag_memory WHERE rag_memory MATCH ? ORDER BY rank LIMIT ?",
                (safe, limit),
            )
            return [row[0] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


# ─── CONFIG (PROFILE) ─────────────────────────────────────────────────────────

def save_config(key: str, value: str):
    """Saves a configuration value. Sensitive keys are encrypted at rest."""
    init_db()
    stored = _encrypt(value) if key in _SENSITIVE_KEYS else value
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)",
            (key, stored),
        )


def get_config(key: str, default: str = None) -> str:
    """Retrieves a configuration value. Sensitive keys are decrypted transparently."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM profile WHERE key = ?", (key,))
        row = cur.fetchone()
    if row is None:
        return default
    value = row[0]
    return _decrypt(value) if key in _SENSITIVE_KEYS else value


# ── Owner Profile ─────────────────────────────────────────────────────────────

_OWNER_KEYS = (
    "owner_name", "owner_first_name", "bot_name", "owner_role",
    "owner_domain", "owner_company", "owner_email", "owner_language",
    "bot_personality", "voice_mode",
)

def save_owner_profile(data: dict):
    """Save all owner profile fields to DB."""
    for key, value in data.items():
        if key in _OWNER_KEYS and value:
            save_config(key, str(value))

def get_owner_profile() -> dict:
    """Retrieve the full owner profile."""
    return {k: (get_config(k, "") or "") for k in _OWNER_KEYS}

def is_first_run() -> bool:
    """True if the onboarding wizard has never been completed."""
    return not bool(get_config("owner_name", ""))


# ── System Info ───────────────────────────────────────────────────────────────

_SYS_KEYS = (
    "sys_os", "sys_distro", "sys_arch", "sys_hostname",
    "sys_cpu_model", "sys_cpu_cores", "sys_ram_gb", "sys_gpu",
    "sys_python", "max_agents",
)

def save_system_info(info: dict):
    """Persist detected system info to DB."""
    mapping = {
        "os":         "sys_os",
        "distro":     "sys_distro",
        "arch":       "sys_arch",
        "hostname":   "sys_hostname",
        "cpu_model":  "sys_cpu_model",
        "cpu_cores":  "sys_cpu_cores",
        "ram_gb":     "sys_ram_gb",
        "gpu":        "sys_gpu",
        "python":     "sys_python",
        "max_agents": "max_agents",
    }
    for src_key, db_key in mapping.items():
        val = info.get(src_key)
        if val is not None:
            save_config(db_key, str(val))

def get_system_info() -> dict:
    """Retrieve persisted system info."""
    return {k: (get_config(k, "") or "") for k in _SYS_KEYS}
