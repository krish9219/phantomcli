"""Tests for :mod:`phantom.plugins.signature`."""

from __future__ import annotations

import base64

import pytest

from phantom.errors import PluginError
from phantom.plugins.signature import (
    canonical_payload,
    sign_manifest,
    verify_signature,
)


pynacl = pytest.importorskip("nacl.signing")


@pytest.fixture
def signing_key_bytes():
    # Deterministic test key. Do NOT reuse outside tests.
    return bytes(32)  # 32 zero bytes; pynacl accepts.


# ─── canonical_payload ────────────────────────────────────────────────────────


class TestCanonicalPayload:
    def test_strips_signature(self):
        d = {"a": 1, "signature": {"public_key": "x", "value": "y"}}
        out = canonical_payload(d)
        assert b"signature" not in out
        assert out == b'{"a":1}'

    def test_sorted_keys(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        assert canonical_payload(d1) == canonical_payload(d2)

    def test_compact(self):
        out = canonical_payload({"a": 1, "b": 2})
        assert out == b'{"a":1,"b":2}'  # no spaces


# ─── sign_manifest + verify_signature ─────────────────────────────────────────


class TestSignAndVerify:
    def test_round_trip(self, signing_key_bytes):
        manifest = {
            "name": "demo", "version": "1.0.0", "entry_point": "m:C",
        }
        signed = sign_manifest(manifest, signing_key_bytes)
        assert "signature" in signed
        assert verify_signature(signed) is True

    def test_unsigned_returns_false(self):
        assert verify_signature({"name": "x"}) is False

    def test_bad_signature_raises(self, signing_key_bytes):
        manifest = {
            "name": "demo", "version": "1.0.0", "entry_point": "m:C",
        }
        signed = sign_manifest(manifest, signing_key_bytes)
        # Tamper with the manifest after signing.
        signed["name"] = "demo-tampered"
        with pytest.raises(PluginError, match="invalid"):
            verify_signature(signed)

    def test_malformed_signature_field(self):
        with pytest.raises(PluginError, match="malformed"):
            verify_signature({
                "name": "demo",
                "signature": {"public_key": "!!!not-b64!!!", "value": "abc"},
            })

    def test_signature_field_missing_keys(self):
        with pytest.raises(PluginError, match="malformed"):
            verify_signature({
                "name": "demo",
                "signature": {"public_key": "abc"},  # no 'value'
            })
