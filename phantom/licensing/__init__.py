"""
Phantom licensing — client-side state machine.

Three states: ``pro`` (licensed or grandfathered), ``trial`` (within 14-day
trial window from first run), ``free`` (trial expired or invalid licence).

Pro features (gated): ``phantom serve``, ``phantom swarm``, ``phantom dictate``,
``phantom self-dev``. Free features (always available): ``phantom chat``,
``phantom plugin*``, ``phantom doctor``, ``phantom config*``, ``phantom memory*``,
``phantom mcp*``, ``phantom bench``, ``phantom version``.

Licence state lives in ``~/.phantom/.license`` as Fernet-encrypted JSON,
machine-bound via ``~/.phantom/.machine_key`` (HMAC-SHA256(seed, hw_fingerprint)).
Copying ``.license`` to another machine yields an unreadable blob.

Online validation hits ``/api/phantomcli/check-license``. Successful validation
is cached for 30 days (offline grace 90 days) so the daemon-mode hot path
never depends on network.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "license_status",
    "require_pro",
    "activate",
    "deactivate",
    "list_devices",
    "device_id",
    "device_name",
    "KEY_PATTERN",
    "TRIAL_DAYS",
    "ProGateExit",
]

# ─── Paths ───────────────────────────────────────────────────────────────────

PHANTOM_HOME = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
LICENSE_FILE = PHANTOM_HOME / ".license"
MACHINE_KEY_FILE = PHANTOM_HOME / ".machine_key"

# ─── Constants ───────────────────────────────────────────────────────────────

TRIAL_DAYS = 14
ONLINE_CACHE_DAYS = 30
OFFLINE_GRACE_DAYS = 90
GRANDFATHER_MIN_AGE_SECONDS = 3600  # files older than this in ~/.phantom → pre-gate install

KEY_PATTERN_STR = r"^PHC-[A-F0-9]{8}-[A-F0-9]{8}-[A-F0-9]{8}$"

import re as _re
KEY_PATTERN = _re.compile(KEY_PATTERN_STR, _re.IGNORECASE)

API_BASE = os.environ.get("PHANTOM_API_BASE", "https://phantom.aravindlabs.tech")
CHECK_URL = f"{API_BASE}/api/phantomcli/check-license"
DEACT_URL = f"{API_BASE}/api/phantomcli/deactivate-device"
LIST_URL = f"{API_BASE}/api/phantomcli/devices"

# ─── Errors ──────────────────────────────────────────────────────────────────

class ProGateExit(SystemExit):
    """Raised by require_pro() to terminate the CLI when a feature needs Pro."""

# ─── Machine-bound encryption ────────────────────────────────────────────────

def _machine_key() -> bytes:
    """Fernet-compatible key derived from a per-install seed + hardware fingerprint."""
    PHANTOM_HOME.mkdir(parents=True, exist_ok=True)
    if not MACHINE_KEY_FILE.exists():
        MACHINE_KEY_FILE.write_bytes(os.urandom(32))
        try:
            os.chmod(MACHINE_KEY_FILE, 0o600)
        except OSError:
            pass

    seed = MACHINE_KEY_FILE.read_bytes()[:32]
    hw = hashlib.sha256(str(uuid.getnode()).encode()).digest()
    raw = hmac.new(seed, hw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw)

def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_machine_key())

# ─── State persistence ───────────────────────────────────────────────────────

def _load_state() -> dict[str, Any]:
    if not LICENSE_FILE.exists():
        return {}
    try:
        blob = LICENSE_FILE.read_bytes()
        from cryptography.fernet import InvalidToken
        try:
            decrypted = _fernet().decrypt(blob)
        except InvalidToken:
            return {}
        data = json.loads(decrypted.decode())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_state(state: dict[str, Any]) -> None:
    PHANTOM_HOME.mkdir(parents=True, exist_ok=True)
    blob = _fernet().encrypt(json.dumps(state).encode())
    LICENSE_FILE.write_bytes(blob)
    try:
        os.chmod(LICENSE_FILE, 0o600)
    except OSError:
        pass

# ─── Device identity ─────────────────────────────────────────────────────────

def device_id() -> str:
    """Stable per-machine fingerprint (SHA256 of MAC + hostname + platform, 32 hex)."""
    mac = str(uuid.getnode())
    host = socket.gethostname()
    plat = platform.system() + platform.release()
    return hashlib.sha256(f"{mac}:{host}:{plat}".encode()).hexdigest()[:32]

def device_name() -> str:
    try:
        return f"{socket.gethostname()} ({platform.system()} {platform.release()})"[:64]
    except Exception:
        return "Unknown Device"

def _platform_tag() -> str:
    return platform.system().lower()

# ─── Time helpers ────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now().isoformat()

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None

def _within(iso: str | None, days: int) -> bool:
    dt = _parse_iso(iso)
    return dt is not None and (_now() - dt) < timedelta(days=days)

# ─── Grandfather detection ───────────────────────────────────────────────────

def _looks_like_pre_gate_install() -> bool:
    """True if ~/.phantom existed with content well before this code first ran.

    The licence cache file is brand-new on every install. Other phantom files
    (sessions, memory, plugins, the binary's own files) carry the mtime of the
    *previous* install. If we find any file in ~/.phantom older than the
    grandfather threshold, the user upgraded into the gate rather than starting
    fresh, and we honour their pre-existing usage.
    """
    if not PHANTOM_HOME.exists():
        return False
    cutoff = time.time() - GRANDFATHER_MIN_AGE_SECONDS
    for p in PHANTOM_HOME.rglob("*"):
        if p.name in {".license", ".machine_key"}:
            continue
        try:
            if p.stat().st_mtime < cutoff:
                return True
        except OSError:
            continue
    return False

# ─── Online validation ───────────────────────────────────────────────────────

def _check_online(key: str) -> tuple[bool, dict[str, Any]]:
    """POST to the licence server. Returns (valid, payload)."""
    try:
        import urllib.request
        body = json.dumps({
            "key": key,
            "device_id": device_id(),
            "device_name": device_name(),
            "platform": _platform_tag(),
        }).encode()
        req = urllib.request.Request(
            CHECK_URL, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": f"phantom-cli/{_phantom_version()}"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        return bool(payload.get("valid")), payload
    except Exception:
        return False, {"reason": "network_error"}

def _phantom_version() -> str:
    try:
        from phantom._version import __version__
        return __version__
    except Exception:
        return "0.0.0"

# ─── Public state machine ────────────────────────────────────────────────────

@dataclass
class LicenseState:
    tier: str            # "pro" | "trial" | "free"
    reason: str
    days_remaining: int | None = None
    email: str | None = None
    devices_used: int | None = None
    max_devices: int | None = None

    @property
    def is_pro(self) -> bool:
        return self.tier in ("pro", "trial")

    def to_dict(self) -> dict[str, Any]:
        d = {"tier": self.tier, "reason": self.reason}
        if self.days_remaining is not None: d["days_remaining"] = self.days_remaining
        if self.email is not None:          d["email"] = self.email
        if self.devices_used is not None:   d["devices_used"] = self.devices_used
        if self.max_devices is not None:    d["max_devices"] = self.max_devices
        return d

def license_status(*, allow_network: bool = True) -> LicenseState:
    """Resolve the current licensing state. Side-effect: may write state on first run."""
    state = _load_state()

    if state.get("grandfathered"):
        return LicenseState(tier="pro", reason="grandfathered", email=state.get("email"))

    key = state.get("key")
    if key:
        validated_at = state.get("validated_at")
        if _within(validated_at, ONLINE_CACHE_DAYS):
            return LicenseState(
                tier="pro", reason="licensed", email=state.get("email"),
                devices_used=state.get("devices_used"), max_devices=state.get("max_devices"),
            )
        if allow_network:
            ok, payload = _check_online(key)
            if ok:
                state["validated_at"] = _now_iso()
                state["devices_used"] = payload.get("devices_used")
                state["max_devices"] = payload.get("max_devices")
                if payload.get("email"):
                    state["email"] = payload["email"]
                _save_state(state)
                return LicenseState(
                    tier="pro", reason="licensed", email=state.get("email"),
                    devices_used=state.get("devices_used"), max_devices=state.get("max_devices"),
                )
            if payload.get("reason") in {"not_found", "revoked", "refunded", "device_limit"}:
                return LicenseState(tier="free", reason=payload["reason"], email=state.get("email"))
        if _within(validated_at, OFFLINE_GRACE_DAYS):
            return LicenseState(
                tier="pro", reason="licensed_offline_grace", email=state.get("email"),
            )
        return LicenseState(tier="free", reason="licence_unverified", email=state.get("email"))

    trial_start = state.get("trial_start")
    if not trial_start:
        if _looks_like_pre_gate_install():
            state["grandfathered"] = True
            state["validated_at"] = _now_iso()
            _save_state(state)
            return LicenseState(tier="pro", reason="grandfathered")
        state["trial_start"] = _now_iso()
        _save_state(state)
        return LicenseState(tier="trial", reason="trial_started", days_remaining=TRIAL_DAYS)

    started = _parse_iso(trial_start) or _now()
    days_used = (_now() - started).days
    days_left = TRIAL_DAYS - days_used
    if days_left > 0:
        return LicenseState(tier="trial", reason="trial_active", days_remaining=days_left)
    return LicenseState(tier="free", reason="trial_expired")

# ─── CLI gate ────────────────────────────────────────────────────────────────

UPGRADE_BANNER = (
    "\n\033[1;33m╭─ Phantom Pro feature ─────────────────────────────────╮\033[0m\n"
    "\033[1;33m│\033[0m  This command requires a Phantom Pro licence.            \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m                                                            \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m  Free tier:  chat, plugins, mcp, memory, doctor, bench    \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m  Pro tier:   serve, swarm, dictate, self-dev              \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m                                                            \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m  ₹999 lifetime · all features · every future patch free  \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m  https://phantom.aravindlabs.tech/buy                     \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m                                                            \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m  Already have a key? Run:                                  \033[1;33m│\033[0m\n"
    "\033[1;33m│\033[0m    phantom license activate PHC-XXXX-XXXX-XXXX           \033[1;33m│\033[0m\n"
    "\033[1;33m╰────────────────────────────────────────────────────────────╯\033[0m\n"
)

def require_pro(feature: str = "this command") -> LicenseState:
    """Gate a Pro feature. Returns LicenseState on pass, exits non-zero on free."""
    s = license_status()
    if s.tier == "pro":
        return s
    if s.tier == "trial":
        import sys
        print(
            f"\033[1;36m[Phantom Pro · trial: {s.days_remaining} day(s) remaining]\033[0m",
            file=sys.stderr,
        )
        return s
    import sys
    sys.stderr.write(UPGRADE_BANNER)
    sys.stderr.write(f"  (requested feature: {feature})\n\n")
    raise ProGateExit(1)

# ─── Activation ──────────────────────────────────────────────────────────────

def activate(key: str) -> LicenseState:
    """Validate `key` online + register this device. Persists state on success."""
    key = key.strip().upper()
    if not KEY_PATTERN.match(key):
        raise ValueError("invalid key format (expected PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX)")
    ok, payload = _check_online(key)
    if not ok:
        reason = payload.get("reason", "unknown")
        if reason == "device_limit":
            used = payload.get("devices_used"); maxd = payload.get("max_devices")
            raise RuntimeError(
                f"device limit reached ({used}/{maxd}). "
                "Deactivate one with `phantom license deactivate` from another machine, "
                "or visit https://phantom.aravindlabs.tech/account."
            )
        raise RuntimeError(f"activation failed: {reason}")

    state = _load_state()
    state.update({
        "version": 1,
        "key": key,
        "email": payload.get("email"),
        "device_id": device_id(),
        "validated_at": _now_iso(),
        "devices_used": payload.get("devices_used"),
        "max_devices": payload.get("max_devices"),
    })
    state.pop("grandfathered", None)
    state.pop("trial_start", None)
    _save_state(state)

    return LicenseState(
        tier="pro", reason="licensed", email=payload.get("email"),
        devices_used=payload.get("devices_used"), max_devices=payload.get("max_devices"),
    )

def deactivate() -> bool:
    """Remove this device from the active licence (if any) and clear local state."""
    state = _load_state()
    key = state.get("key")
    if not key:
        return False
    try:
        import urllib.request
        body = json.dumps({"key": key, "device_id": device_id()}).encode()
        req = urllib.request.Request(
            DEACT_URL, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass
    try:
        LICENSE_FILE.unlink()
    except FileNotFoundError:
        pass
    return True

def list_devices(key: str | None = None) -> list[dict[str, Any]]:
    """List devices currently registered to the licence (uses local key if none passed)."""
    if key is None:
        key = _load_state().get("key")
    if not key:
        return []
    try:
        import urllib.request
        from urllib.parse import urlencode
        url = f"{LIST_URL}?{urlencode({'key': key})}"
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        return list(payload.get("devices", []))
    except Exception:
        return []
