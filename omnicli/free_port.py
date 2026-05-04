"""
Free-port detection — matches Claude Code's behavior of never hard-coding
port 8000. On generated apps, we:
  1. Pick a free port at BUILD time using an OS-assigned ephemeral bind.
  2. Inject it into the agent prompts so `app.py` hard-codes THAT port.
  3. Also write a `PHANTOM_PORT` env hint so the user can override.
  4. Fall back gracefully if the chosen port gets taken between pick + launch.

Strategy:
  * Prefer ports in the "app dev" range 3000–9000 that aren't already bound.
  * If that range is exhausted, ask the OS for any free port (bind(''0)).
  * Validate the chosen port is still free right before launch.

Public API:
  * pick_free_port(preferred: list[int] = None, low=3000, high=9000) -> int
  * port_is_free(port: int) -> bool
  * find_candidate(preferred) -> int  (alias)
"""
from __future__ import annotations

import random
import socket
from typing import Iterable, Optional


# Ports users are most likely to recognise for a dev dashboard. We try these
# first — if they're free. If none are, we hand-pick a random free port in
# the broader 3000-9000 range.
_PREFERRED_DEFAULTS: tuple[int, ...] = (
    8000, 8080, 5000, 5001, 3000, 3001, 4000, 7000, 7860, 8008, 8888, 9000,
)


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True iff no one is currently bound to (host, port) on TCP.

    We try to BIND rather than CONNECT — binding tells us the slot is
    reservable, which is what actually matters when we launch Flask there."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # SO_REUSEADDR off — we want a true clean slot
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try: s.close()
        except Exception: pass


def pick_free_port(
    preferred: Optional[Iterable[int]] = None,
    low: int = 3000,
    high: int = 9000,
    host: str = "127.0.0.1",
) -> int:
    """Return a free port. Preference order:
      1. Explicit `preferred` list (if supplied)
      2. Well-known dev ports (_PREFERRED_DEFAULTS)
      3. Random sample in [low, high]
      4. OS-assigned ephemeral port (bind to 0)
    """
    tried: set[int] = set()

    def _try(p: int) -> Optional[int]:
        if p in tried:
            return None
        tried.add(p)
        return p if port_is_free(p, host) else None

    # 1) caller-supplied preferences
    for p in list(preferred or ()):
        got = _try(int(p))
        if got is not None:
            return got

    # 2) built-in common dev ports
    for p in _PREFERRED_DEFAULTS:
        got = _try(p)
        if got is not None:
            return got

    # 3) random sample in the broader range
    rng = random.Random()  # non-seeded — we want variability across runs
    pool = list(range(low, high + 1))
    rng.shuffle(pool)
    for p in pool[:50]:   # 50 tries is plenty in practice
        got = _try(p)
        if got is not None:
            return got

    # 4) ask the OS for anything free
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        try: s.close()
        except Exception: pass


# Alias for clarity in agent-spawn call sites
find_candidate = pick_free_port


__all__ = ["pick_free_port", "port_is_free", "find_candidate"]
