"""Web Push subscription store + VAPID key helpers.

Phantom v1.0 ships push as opt-in. Browser subscribes via the
service worker's PushManager and POSTs the resulting subscription to
``/pwa/subscribe``. We persist subscriptions in a JSON file under
``$PHANTOM_HOME/pwa/subscriptions.json`` and expose a
:class:`SubscriptionStore` that the operator can read to dispatch
push deliveries via pywebpush (an optional runtime dependency).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

__all__ = [
    "PushSubscription",
    "SubscriptionStore",
    "default_subscription_path",
    "generate_vapid_keys",
]


def default_subscription_path() -> Path:
    base = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    p = base / "pwa"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p / "subscriptions.json"


# ─── subscription record ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PushSubscription:
    endpoint: str
    p256dh: str           # base64url ECDH key
    auth: str             # base64url auth secret
    user_agent: str = ""
    subscribed_at: float = field(default_factory=time.time)

    @property
    def id(self) -> str:
        # Stable identifier — endpoints don't repeat across subscriptions.
        return self.endpoint

    def to_pywebpush(self) -> dict[str, Any]:
        """Shape pywebpush expects."""
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


# ─── store ───────────────────────────────────────────────────────────────────


@dataclass
class SubscriptionStore:
    path: Path = field(default_factory=default_subscription_path)
    _subs: dict[str, PushSubscription] = field(default_factory=dict)
    _loaded: bool = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in data.get("subscriptions", []):
            try:
                sub = PushSubscription(
                    endpoint=str(item["endpoint"]),
                    p256dh=str(item["p256dh"]),
                    auth=str(item["auth"]),
                    user_agent=str(item.get("user_agent", "")),
                    subscribed_at=float(item.get("subscribed_at", time.time())),
                )
            except (KeyError, ValueError):
                continue
            self._subs[sub.id] = sub

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        body = {"subscriptions": [asdict(s) for s in self._subs.values()]}
        out = json.dumps(body, indent=2, sort_keys=True)
        self.path.write_text(out, encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ── public API ───────────────────────────────────────────────────

    def add(self, sub: PushSubscription) -> bool:
        """Add (or overwrite) a subscription. Returns True if new."""
        self._load()
        is_new = sub.id not in self._subs
        self._subs[sub.id] = sub
        self._save()
        return is_new

    def remove(self, endpoint: str) -> bool:
        self._load()
        if endpoint not in self._subs:
            return False
        del self._subs[endpoint]
        self._save()
        return True

    def __len__(self) -> int:
        self._load()
        return len(self._subs)

    def all(self) -> list[PushSubscription]:
        self._load()
        return list(self._subs.values())


# ─── VAPID key generation ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VapidKeys:
    private_key_b64url: str   # 32-byte EC private key (P-256), base64url
    public_key_b64url: str    # 65-byte uncompressed EC public key, base64url
    application_server_key_b64url: str  # alias for public_key_b64url, the JS API name


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> VapidKeys:
    """Generate a P-256 keypair suitable for Web Push VAPID auth.

    Uses the cryptography stdlib bridge (already a runtime dep). The
    public key is in the uncompressed-point format JS PushManager
    expects for ``applicationServerKey``.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as e:  # pragma: no cover — cryptography is required
        raise RuntimeError("cryptography package required for VAPID key generation") from e

    private = ec.generate_private_key(ec.SECP256R1())
    private_bytes = private.private_numbers().private_value.to_bytes(32, "big")
    public_numbers = private.public_key().public_numbers()
    # Uncompressed point: 0x04 || X(32) || Y(32).
    pub_bytes = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")
    pub_b64 = _b64url_encode(pub_bytes)
    return VapidKeys(
        private_key_b64url=_b64url_encode(private_bytes),
        public_key_b64url=pub_b64,
        application_server_key_b64url=pub_b64,
    )
