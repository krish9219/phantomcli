"""Matrix Client-Server transport via httpx.

Polls ``/sync`` (long-poll, configurable timeout) and POSTs to
``/rooms/{roomId}/send/m.room.message/{txnId}`` for outbound. The next-
batch token is persisted in-memory so repeated ``fetch_events`` calls
return only fresh events.

End-to-end encryption is out of scope today — operators who need E2EE
should run a Pantalaimon proxy and point this transport at it.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from phantom.errors import ChannelError

__all__ = ["HttpxMatrixTransport"]

log = logging.getLogger("phantom.channels.matrix")


class HttpxMatrixTransport:
    """Long-polling Matrix Client-Server transport."""

    def __init__(
        self,
        *,
        homeserver_url: str,
        access_token: str,
        rooms: list[str] | None = None,
        client: Any = None,
        sync_timeout_ms: int = 30_000,
    ) -> None:
        if not homeserver_url:
            raise ChannelError("HttpxMatrixTransport requires a homeserver_url")
        if not access_token:
            raise ChannelError("HttpxMatrixTransport requires an access_token")
        self._base = homeserver_url.rstrip("/")
        self._token = access_token
        self._rooms = set(rooms or [])  # if non-empty, filter inbound
        self._client = client
        self._sync_timeout_ms = sync_timeout_ms
        self._next_batch: str = ""

    def _http(self) -> Any:
        if self._client is not None:
            return self._client
        import httpx
        # Long-poll → use a generous read timeout (sync_timeout + buffer).
        timeout_s = (self._sync_timeout_ms / 1000.0) + 10.0
        self._client = httpx.Client(timeout=timeout_s)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "phantom/1.0",
        }

    # ── inbound ──────────────────────────────────────────────────────

    def fetch_events(self) -> list[dict[str, Any]]:
        url = f"{self._base}/_matrix/client/v3/sync"
        params: dict[str, Any] = {"timeout": self._sync_timeout_ms}
        if self._next_batch:
            params["since"] = self._next_batch
        try:
            resp = self._http().get(url, headers=self._headers(), params=params)
        except Exception as exc:
            raise ChannelError(f"matrix sync failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelError(
                f"matrix sync returned {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        self._next_batch = body.get("next_batch", self._next_batch)

        out: list[dict[str, Any]] = []
        joined = (body.get("rooms") or {}).get("join") or {}
        for room_id, room_data in joined.items():
            if self._rooms and room_id not in self._rooms:
                continue
            timeline = (room_data.get("timeline") or {}).get("events") or []
            for ev in timeline:
                if ev.get("type") != "m.room.message":
                    continue
                ev = {**ev, "room_id": room_id}
                out.append(ev)
        return out

    # ── outbound ─────────────────────────────────────────────────────

    def send_message(self, *, room_id: str, body: str) -> dict[str, Any]:
        txn_id = f"phantom-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        url = f"{self._base}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
        try:
            resp = self._http().put(
                url, headers=self._headers(),
                json={"msgtype": "m.text", "body": body},
            )
        except Exception as exc:
            raise ChannelError(f"matrix send failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelError(
                f"matrix send returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()
