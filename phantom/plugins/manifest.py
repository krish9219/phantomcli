"""Plugin manifest — JSON-schema-validated metadata.

Each plugin ships a ``manifest.json`` (or supplies an inline
:class:`PluginManifest` from code). The manifest declares: name, version,
description, entry point, capabilities, and an optional signature.

The schema is small on purpose — extensions go in
:attr:`PluginManifest.extras` so the v4.0 schema stays stable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phantom.errors import PluginError
from phantom.plugins.capability import Capability

__all__ = ["MANIFEST_SCHEMA", "PluginManifest"]


# Stable v1 manifest schema. Backwards-compatible additions are allowed;
# breaking changes require a v2 schema and a migration path.
MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "version", "entry_point"],
    "properties": {
        "name": {
            "type": "string",
            # PEP 503-style normalised name to avoid collisions on case.
            "pattern": r"^[a-z][a-z0-9_-]*[a-z0-9]$",
            "minLength": 2,
            "maxLength": 64,
        },
        "version": {
            "type": "string",
            # Loose semver — major.minor.patch with optional -prerelease.
            "pattern": r"^\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$",
        },
        "description": {"type": "string", "maxLength": 280},
        "entry_point": {
            "type": "string",
            # ``module:Class`` form, e.g. "phantom.plugins.builtin.clock:ClockPlugin".
            "pattern": r"^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$",
        },
        "capabilities": {
            "type": "array",
            "items": {"type": "string", "enum": [c.value for c in Capability]},
            "uniqueItems": True,
        },
        "homepage": {"type": "string"},
        "author": {"type": "string"},
        "license": {"type": "string"},
        "signature": {
            "type": "object",
            "required": ["public_key", "value"],
            "properties": {
                "public_key": {"type": "string"},
                "value": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "extras": {"type": "object"},
    },
    "additionalProperties": False,
}


_NAME_RE = re.compile(MANIFEST_SCHEMA["properties"]["name"]["pattern"])
_VERSION_RE = re.compile(MANIFEST_SCHEMA["properties"]["version"]["pattern"])
_ENTRY_RE = re.compile(MANIFEST_SCHEMA["properties"]["entry_point"]["pattern"])


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Validated plugin metadata.

    Construct via :meth:`from_dict` or :meth:`load`. Direct construction
    is allowed for tests but bypasses validation.
    """

    name: str
    version: str
    entry_point: str
    description: str = ""
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    homepage: str = ""
    author: str = ""
    license: str = ""
    signature: dict[str, str] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    # ─── factory helpers ───────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        """Validate *data* against the schema and return a manifest.

        Raises :class:`phantom.errors.PluginError` for any deviation.
        We hand-validate the small schema instead of pulling in the
        full ``jsonschema`` package — keeps the dependency footprint
        small and lets us produce friendlier error messages.
        """
        if not isinstance(data, dict):
            raise PluginError(f"manifest must be an object, got {type(data).__name__}")
        # Forbid unknown top-level keys per schema.
        unknown = set(data) - set(MANIFEST_SCHEMA["properties"])
        if unknown:
            raise PluginError(f"unknown manifest keys: {sorted(unknown)}")

        for required in MANIFEST_SCHEMA["required"]:
            if required not in data:
                raise PluginError(f"manifest is missing required key {required!r}")

        # Type + format checks.
        name = data["name"]
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise PluginError(
                f"manifest.name {name!r} must match {MANIFEST_SCHEMA['properties']['name']['pattern']}"
            )
        if not 2 <= len(name) <= 64:
            raise PluginError(
                f"manifest.name length must be 2..64 chars; got {len(name)}"
            )
        if not isinstance(data["version"], str) or not _VERSION_RE.match(data["version"]):
            raise PluginError(
                f"manifest.version {data['version']!r} must be semver-shaped"
            )
        if not isinstance(data["entry_point"], str) or not _ENTRY_RE.match(data["entry_point"]):
            raise PluginError(
                f"manifest.entry_point {data['entry_point']!r} must be 'module:Class'"
            )

        description = data.get("description", "")
        if not isinstance(description, str) or len(description) > 280:
            raise PluginError("manifest.description must be a string ≤ 280 chars")

        caps_raw = data.get("capabilities", [])
        if not isinstance(caps_raw, list) or not all(
            isinstance(c, str) for c in caps_raw
        ):
            raise PluginError("manifest.capabilities must be a list of strings")
        try:
            caps = Capability.parse_set(caps_raw)
        except ValueError as exc:
            raise PluginError(str(exc)) from exc

        homepage = data.get("homepage", "")
        author = data.get("author", "")
        lic = data.get("license", "")
        for v, name in ((homepage, "homepage"), (author, "author"), (lic, "license")):
            if not isinstance(v, str):
                raise PluginError(f"manifest.{name} must be a string")

        signature = data.get("signature")
        if signature is not None:
            if (
                not isinstance(signature, dict)
                or set(signature) != {"public_key", "value"}
                or not all(isinstance(v, str) for v in signature.values())
            ):
                raise PluginError(
                    "manifest.signature must be an object with string keys "
                    "{public_key, value}"
                )

        extras = data.get("extras", {})
        if not isinstance(extras, dict):
            raise PluginError("manifest.extras must be an object")

        return cls(
            name=data["name"],
            version=data["version"],
            entry_point=data["entry_point"],
            description=description,
            capabilities=caps,
            homepage=homepage,
            author=author,
            license=lic,
            signature=dict(signature) if signature else None,
            extras=dict(extras),
        )

    @classmethod
    def load(cls, path: str | Path) -> "PluginManifest":
        """Load and validate a manifest from a JSON file path."""
        p = Path(path)
        try:
            data = json.loads(p.read_text())
        except FileNotFoundError as exc:
            raise PluginError(f"manifest not found: {p}") from exc
        except json.JSONDecodeError as exc:
            raise PluginError(f"manifest is not valid JSON: {p}: {exc}") from exc
        return cls.from_dict(data)

    # ─── conversion helpers ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "entry_point": self.entry_point,
        }
        if self.description:
            out["description"] = self.description
        if self.capabilities:
            out["capabilities"] = sorted(c.value for c in self.capabilities)
        if self.homepage:
            out["homepage"] = self.homepage
        if self.author:
            out["author"] = self.author
        if self.license:
            out["license"] = self.license
        if self.signature is not None:
            out["signature"] = dict(self.signature)
        if self.extras:
            out["extras"] = dict(self.extras)
        return out
