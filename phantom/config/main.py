"""Phantom v4 configuration loader.

Single-source for ``~/.phantom/config.json``. Schema-validated at load
time; sensible defaults for every key. Operator overrides via env vars
follow the convention ``PHANTOM_<SECTION>_<KEY>``.

Example
-------

>>> from phantom.config import Config, default_config_path
>>> # Loaded with defaults when the file is absent.
>>> import tempfile, os
>>> with tempfile.TemporaryDirectory() as d:
...     os.environ["PHANTOM_HOME"] = d
...     cfg = Config.load()
>>> cfg.sandbox.preferred is None
True
>>> cfg.sandbox.disabled
()
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phantom.errors import ConfigError

__all__ = [
    "Config",
    "SandboxConfig",
    "default_config_path",
]


def default_config_path() -> Path:
    """Return ``$PHANTOM_HOME/config.json`` (or ``~/.phantom/config.json``).

    Honours ``$PHANTOM_HOME`` so tests can isolate. Does *not* create the
    file; loaders return defaults when it is missing.
    """
    base = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
    return Path(base) / "config.json"


@dataclass(frozen=True, slots=True)
class SandboxConfig:
    """Sandbox-related configuration.

    Attributes
    ----------
    preferred:
        Backend name to pin (``"bwrap"`` / ``"firejail"`` / ``"unshare"``
        / ``"docker"``). ``None`` means use the highest-ranked available.
    disabled:
        Tuple of backend names to skip during selection.
    audit_log_path:
        Override the audit-log path. ``None`` uses the default.
    """

    preferred: str | None = None
    disabled: tuple[str, ...] = ()
    audit_log_path: str | None = None


@dataclass(frozen=True, slots=True)
class Config:
    """Top-level configuration object."""

    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Load config from *path* (or :func:`default_config_path`).

        Returns a default-filled :class:`Config` when the file does not
        exist. Raises :class:`ConfigError` for malformed JSON or
        type-mismatched values.
        """
        target = Path(path) if path is not None else default_config_path()
        if not target.exists():
            return cls._with_env_overrides(cls())

        try:
            data: Any = json.loads(target.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{target} is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ConfigError(f"{target} root must be a JSON object")

        sandbox_raw = data.get("sandbox", {})
        if not isinstance(sandbox_raw, dict):
            raise ConfigError(f"{target}: sandbox must be an object")

        preferred = sandbox_raw.get("preferred")
        if preferred is not None and not isinstance(preferred, str):
            raise ConfigError(f"{target}: sandbox.preferred must be a string or null")

        disabled_raw = sandbox_raw.get("disabled", [])
        if not isinstance(disabled_raw, list) or not all(
            isinstance(x, str) for x in disabled_raw
        ):
            raise ConfigError(f"{target}: sandbox.disabled must be a list of strings")

        audit_log_path = sandbox_raw.get("audit_log_path")
        if audit_log_path is not None and not isinstance(audit_log_path, str):
            raise ConfigError(
                f"{target}: sandbox.audit_log_path must be a string or null"
            )

        cfg = cls(
            sandbox=SandboxConfig(
                preferred=preferred,
                disabled=tuple(disabled_raw),
                audit_log_path=audit_log_path,
            ),
            raw=data,
        )
        return cls._with_env_overrides(cfg)

    @staticmethod
    def _with_env_overrides(cfg: "Config") -> "Config":
        """Apply ``PHANTOM_*`` env-var overrides to *cfg*."""
        # PHANTOM_SANDBOX_TIER takes precedence over sandbox.preferred.
        env_tier = os.environ.get("PHANTOM_SANDBOX_TIER", "").strip()
        if env_tier:
            return Config(
                sandbox=SandboxConfig(
                    preferred=env_tier,
                    disabled=cfg.sandbox.disabled,
                    audit_log_path=cfg.sandbox.audit_log_path,
                ),
                raw=cfg.raw,
            )
        return cfg
