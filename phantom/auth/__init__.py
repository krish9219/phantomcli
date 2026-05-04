"""Phantom auth — API key pool with rotation + per-key cooldown.

Each provider (Anthropic, OpenAI, NVIDIA, …) gets its own
:class:`KeyPool`. The pool tracks last-used + cooldown-until for each
key; on a 429 / auth error the caller marks the key cooled-down for a
configurable interval. Subsequent calls round-robin among the
non-cooling keys.

Storage is in-memory by default; operators with the Pro tier wire it
to the license server's per-tenant key store.
"""

from __future__ import annotations

from phantom.auth.pool import (
    KeyEntry,
    KeyPool,
    KeyPoolEmptyError,
)

__all__ = [
    "KeyEntry",
    "KeyPool",
    "KeyPoolEmptyError",
]
