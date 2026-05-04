"""
Session persistence — save/load/list/delete conversation state.

A session is a JSON-serializable snapshot of:
  * `id`       — short unique identifier (nanoid-like 12 chars)
  * `name`     — optional human label
  * `messages` — full chat history list-of-dicts (same shape engine uses)
  * `config`   — relevant config keys at save time (main_model, trust, etc.)
  * `tools`    — list of {tool, args_summary, ok} for the turn history
  * `created`, `updated` — ISO timestamps

Storage: ~/.phantom/sessions/<id>/session.json (+ optional metadata files).

API:
  save_session(ctx, name="") → id
  load_session(id) → dict | None
  list_sessions() → list[dict]   (id + name + counts, sorted newest first)
  delete_session(id) → bool
  export_session(id, path) → path   (copies JSON to the given path)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("omnicli.sessions")

_DEFAULT_DIR = os.path.expanduser("~/.phantom/sessions")

# Snapshot these config keys alongside the conversation so that reloaded
# sessions resume with the same model, trust level, etc.
_CONFIG_SNAPSHOT_KEYS = (
    "main_model", "main_url",
    "router_model", "router_url",
    "default_trust",
    "bot_name", "work_dir",
)


def _sessions_dir() -> str:
    return os.environ.get("PHANTOM_SESSIONS_DIR", _DEFAULT_DIR)


def _new_id() -> str:
    # 12-char URL-safe id — enough entropy (>62 bits) and short enough to type.
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _snap_config() -> dict:
    try:
        from omnicli.memory import get_config
    except ImportError:
        return {}
    return {k: get_config(k, "") for k in _CONFIG_SNAPSHOT_KEYS}


def _session_path(sid: str) -> str:
    return os.path.join(_sessions_dir(), sid, "session.json")


def _ensure_dir(sid: str) -> str:
    path = os.path.join(_sessions_dir(), sid)
    os.makedirs(path, exist_ok=True)
    return path


def save_session(ctx: Optional[dict] = None, name: str = "", sid: Optional[str] = None) -> str:
    """Persist the current conversation state. Returns the session id.

    `ctx` contains at minimum `messages` and optionally `tools` (recent tool
    use summaries). If `sid` is given, overwrite that session's snapshot
    (useful for auto-save on each turn); otherwise a new id is generated."""
    ctx = ctx or {}
    sid = sid or _new_id()
    _ensure_dir(sid)
    path = _session_path(sid)
    previous = _safe_read(path)
    data = {
        "id":       sid,
        "name":     (name or previous.get("name", "") or "").strip(),
        "messages": ctx.get("messages", []),
        "tools":    ctx.get("tools", []),
        "config":   _snap_config(),
        "created":  previous.get("created") or _now_iso(),
        "updated":  _now_iso(),
    }
    _atomic_write_json(path, data)
    return sid


def load_session(sid: str) -> Optional[dict]:
    """Return the session dict or None if not found / unreadable."""
    data = _safe_read(_session_path(sid))
    return data or None


def list_sessions() -> list[dict]:
    """Return one brief descriptor per saved session, newest-first."""
    d = _sessions_dir()
    if not os.path.isdir(d):
        return []
    rows = []
    for entry in os.listdir(d):
        p = os.path.join(d, entry, "session.json")
        if not os.path.isfile(p):
            continue
        data = _safe_read(p)
        if not data:
            continue
        rows.append({
            "id":       data.get("id", entry),
            "name":     data.get("name", ""),
            "messages": len(data.get("messages", [])),
            "tools":    len(data.get("tools", [])),
            "created":  data.get("created", ""),
            "updated":  data.get("updated", ""),
        })
    # Newest first by updated, falling back to created.
    rows.sort(key=lambda r: (r.get("updated", ""), r.get("created", "")), reverse=True)
    return rows


def delete_session(sid: str) -> bool:
    path = os.path.join(_sessions_dir(), sid)
    if not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path)
        return True
    except OSError as e:
        log.warning("delete_session failed: %s", e)
        return False


def export_session(sid: str, dest: str) -> Optional[str]:
    """Copy the session's JSON file to `dest`. Returns dest on success."""
    src = _session_path(sid)
    if not os.path.isfile(src):
        return None
    try:
        shutil.copy2(src, dest)
        return dest
    except OSError as e:
        log.warning("export failed: %s", e)
        return None


# ─── Internals ───────────────────────────────────────────────────────────────


def _safe_read(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("session read failed %s: %s", path, e)
        return {}


def _atomic_write_json(path: str, data: dict) -> None:
    """Write via tmp + os.replace so a crash mid-write doesn't corrupt the
    session file."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


__all__ = [
    "save_session", "load_session", "list_sessions",
    "delete_session", "export_session",
]
