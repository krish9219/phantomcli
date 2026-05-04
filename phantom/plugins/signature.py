"""Ed25519 signature verification for plugin bundles.

A plugin can publish a signature in its manifest; operators verify it
before loading. The signature is over the canonical JSON form of the
manifest *with the signature field removed*.

Unsigned plugins still load — the loader emits a warning and records
the signature status in the audit log. Operators who want to refuse
unsigned plugins set ``plugins.require_signed = true`` in
``~/.phantom/config.json`` (lands as a Stage-8 hardening knob).

We use PyNaCl's ``nacl.signing`` for Ed25519 — small, audited, and
already a transitive dependency of multiple Python packages we ship.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from phantom.errors import PluginError

__all__ = ["canonical_payload", "sign_manifest", "verify_signature"]


def canonical_payload(manifest_dict: dict[str, Any]) -> bytes:
    """Return the deterministic byte serialization signed by publishers.

    The signature covers the manifest *with the signature field stripped*
    — otherwise verification would have to predict the signature it was
    going to produce. JSON keys are sorted; whitespace is suppressed;
    UTF-8 NFC normalisation is the implicit contract.
    """
    body = {k: v for k, v in manifest_dict.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_signature(manifest_dict: dict[str, Any]) -> bool:
    """Verify the manifest's Ed25519 signature.

    Returns True iff the signature is present and valid. False if the
    signature is absent. Raises :class:`PluginError` if the signature is
    present but invalid (so callers cannot silently accept tampering).
    """
    sig = manifest_dict.get("signature")
    if sig is None:
        return False
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError as exc:
        raise PluginError(
            "PyNaCl is not installed; signed plugins require pynacl"
        ) from exc

    try:
        vk_bytes = base64.b64decode(sig["public_key"], validate=True)
        sig_bytes = base64.b64decode(sig["value"], validate=True)
    except (ValueError, KeyError) as exc:
        raise PluginError(f"signature fields malformed: {exc}") from exc

    payload = canonical_payload(manifest_dict)

    try:
        VerifyKey(vk_bytes).verify(payload, sig_bytes)
    except BadSignatureError as exc:
        raise PluginError("plugin signature invalid") from exc
    return True


def sign_manifest(manifest_dict: dict[str, Any], signing_key_bytes: bytes) -> dict[str, Any]:
    """Sign a manifest in-place; return the manifest dict with the signature added.

    Used by tests and by the (Stage-8) `phantom plugin sign` CLI. Not
    used by the loader.
    """
    try:
        from nacl.signing import SigningKey
    except ImportError as exc:
        raise PluginError("PyNaCl is not installed") from exc

    sk = SigningKey(signing_key_bytes)
    payload = canonical_payload(manifest_dict)
    signed = sk.sign(payload)
    out = dict(manifest_dict)
    out["signature"] = {
        "public_key": base64.b64encode(sk.verify_key.encode()).decode("ascii"),
        "value": base64.b64encode(signed.signature).decode("ascii"),
    }
    return out
