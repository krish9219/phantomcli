"""Daemon wire protocol — newline-delimited JSON envelopes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_SOCKET_PATH",
    "DaemonRequest",
    "DaemonResponse",
    "decode_request",
    "decode_response",
    "encode_request",
    "encode_response",
]


def _runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")


DEFAULT_SOCKET_PATH: str = str(_runtime_dir() / f"phantom-{os.getuid() if hasattr(os, 'getuid') else 0}.sock")


@dataclass(frozen=True, slots=True)
class DaemonRequest:
    op: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DaemonResponse:
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def encode_request(req: DaemonRequest) -> bytes:
    return (json.dumps({"op": req.op, "payload": req.payload}) + "\n").encode("utf-8")


def decode_request(line: bytes | str) -> DaemonRequest:
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    obj = json.loads(text)
    if not isinstance(obj, dict) or "op" not in obj:
        raise ValueError("invalid daemon request envelope")
    return DaemonRequest(op=str(obj["op"]), payload=dict(obj.get("payload") or {}))


def encode_response(resp: DaemonResponse) -> bytes:
    body = {"ok": bool(resp.ok)}
    if resp.ok:
        body["result"] = resp.result
    else:
        body["error"] = resp.error
    return (json.dumps(body) + "\n").encode("utf-8")


def decode_response(line: bytes | str) -> DaemonResponse:
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    obj = json.loads(text)
    if not isinstance(obj, dict) or "ok" not in obj:
        raise ValueError("invalid daemon response envelope")
    return DaemonResponse(
        ok=bool(obj["ok"]),
        result=dict(obj.get("result") or {}),
        error=str(obj.get("error") or ""),
    )
