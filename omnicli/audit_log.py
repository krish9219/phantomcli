"""
Append-only structured audit log for security-relevant events.

Phantom records, one JSON line per event, every:
  * permission decision (allow / deny / ask)
  * web-dashboard auth attempt (success / fail)
  * subagent dispatch
  * god-mode activation / downgrade
  * hook blocks

The file is owner-read-only (chmod 0o600) and is never rotated by us —
admins point logrotate at it. Tampering is detectable only at the
line-level: we include a hash chain so a deleted middle record breaks the
chain (simple forward hash, no signatures — that's a separate signing
layer if you need compliance).

API:
  record(category, decision, subject="", resource="", reason="", **extra)
    → AuditRecord
  tail(n=100) → list[dict]
  verify_chain() → (ok, first_broken_index | None)
  clear() → int (count removed; only in tests)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal, Optional

log = logging.getLogger("omnicli.audit_log")

_DEFAULT_PATH = os.path.expanduser("~/.phantom/audit.jsonl")
_lock = threading.Lock()

Decision = Literal["allow", "deny", "ask", "info"]


@dataclass
class AuditRecord:
    ts:       float
    iso:      str
    category: str
    decision: Decision
    subject:  str = ""
    resource: str = ""
    reason:   str = ""
    extra:    dict = field(default_factory=dict)
    prev_hash: str = ""
    hash:      str = ""


def _log_path() -> str:
    return os.environ.get("PHANTOM_AUDIT_LOG", _DEFAULT_PATH)


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _hash(prev_hash: str, record_dict: dict) -> str:
    """Forward hash — sha256 of prev_hash + stable json of this record
    (excluding its own hash field so it's a pure forward chain)."""
    copy = dict(record_dict)
    copy.pop("hash", None)
    blob = prev_hash + json.dumps(copy, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _last_hash() -> str:
    """Read the last line's hash to chain onto, or '' if empty."""
    path = _log_path()
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            # Read last ~2KB for speed
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2048))
            chunk = f.read().decode("utf-8", errors="replace")
        lines = [l for l in chunk.splitlines() if l.strip()]
        if not lines:
            return ""
        last = json.loads(lines[-1])
        return last.get("hash", "")
    except (OSError, json.JSONDecodeError):
        return ""


# ─── Public API ──────────────────────────────────────────────────────────────


def record(
    category: str,
    decision: Decision,
    subject:  str = "",
    resource: str = "",
    reason:   str = "",
    **extra,
) -> AuditRecord:
    """Append one record to the audit log. Returns the record (including
    its hash), so callers can attach it to structured logs elsewhere.

    `category` is a stable short name: "permission", "auth", "subagent",
    "god_mode", "hook_block", "session", etc.
    `subject`  is who/what took the action (e.g. tool name).
    `resource` is what was acted on (e.g. file path, URL).
    `reason`   is a short explanation string.
    """
    ts = time.time()
    rec = AuditRecord(
        ts=ts,
        iso=datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds"),
        category=category,
        decision=decision,
        subject=subject,
        resource=resource,
        reason=reason,
        extra=dict(extra),
    )
    with _lock:
        prev = _last_hash()
        rec.prev_hash = prev
        rec.hash = _hash(prev, asdict(rec))
        _append(rec)
    return rec


def _append(rec: AuditRecord) -> None:
    path = _log_path()
    _ensure_dir(path)
    line = json.dumps(asdict(rec), default=str) + "\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        # 0600: owner read/write, no group/world.
        try:
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as e:
        log.warning("audit log write failed: %s", e)


def tail(n: int = 100) -> list[dict]:
    """Return the last `n` records as dicts, newest LAST (chronological)."""
    path = _log_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def verify_chain() -> tuple[bool, Optional[int]]:
    """Recompute each record's forward hash and check it matches stored.
    Returns (ok, first_broken_1based_line_number | None)."""
    path = _log_path()
    if not os.path.isfile(path):
        return True, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False, None
    prev = ""
    for idx, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return False, idx
        expected = _hash(prev, rec)
        if rec.get("hash") != expected:
            return False, idx
        if rec.get("prev_hash", "") != prev:
            return False, idx
        prev = rec.get("hash", "")
    return True, None


def clear() -> int:
    """Test-only helper: delete the audit file and return 1 if it existed."""
    path = _log_path()
    if not os.path.isfile(path):
        return 0
    try:
        os.remove(path)
        return 1
    except OSError:
        return 0


__all__ = ["record", "tail", "verify_chain", "clear", "AuditRecord"]
