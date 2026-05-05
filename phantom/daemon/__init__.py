"""Phantom daemon — long-lived backend + thin client.

Cold-starting Python costs us 800-1500 ms even on a hot SSD. Rust agents
beat us on that axis no matter how lean we keep our imports. The
workaround is a daemon: the user pays the import cost once, then every
subsequent ``phantom connect`` is a tiny TCP/unix-socket roundtrip.

Public surface
--------------

``phantom.daemon.server`` — the long-lived process.
``phantom.daemon.client`` — the thin client invoked by ``phantom connect``.
``phantom.daemon.protocol`` — request/response envelopes shared by both.

Wire format (newline-delimited JSON, one envelope per line)::

    >>> {"op": "echo", "payload": {"text": "hi"}}
    <<< {"ok": true, "result": {"text": "hi"}}

The protocol is deliberately tiny so a future Rust client could speak
it without touching Python.
"""

from __future__ import annotations

from phantom.daemon.protocol import (
    DEFAULT_SOCKET_PATH,
    DaemonRequest,
    DaemonResponse,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)
from phantom.daemon.transport import (
    Endpoint,
    default_endpoint,
    is_windows,
)

__all__ = [
    "DEFAULT_SOCKET_PATH",
    "DaemonRequest",
    "DaemonResponse",
    "Endpoint",
    "decode_request",
    "decode_response",
    "default_endpoint",
    "encode_request",
    "encode_response",
    "is_windows",
]
