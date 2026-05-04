"""Discord polling transport via httpx.

Discord's *real* push transport is the gateway WebSocket; this polling
shape is intended for local testing and constrained deployments. A
gateway-based transport is a v4.1 follow-up.
"""

from __future__ import annotations

from typing import Any

from phantom.errors import ChannelError

__all__ = ["HttpxDiscordTransport"]


class HttpxDiscordTransport:
    """REST polling against the Discord API."""

    BASE_URL = "https://discord.com/api/v10"

    def __init__(
        self,
        *,
        token: str,
        channel_ids: list[str],
        client: Any = None,
        base_url: str | None = None,
    ) -> None:
        if not token:
            raise ChannelError("HttpxDiscordTransport requires a token")
        if not channel_ids:
            raise ChannelError("HttpxDiscordTransport requires at least one channel_id")
        self._token = token
        self._channel_ids = list(channel_ids)
        self._base = (base_url or self.BASE_URL).rstrip("/")
        self._client = client
        # Track the last-seen message id per channel for de-dup.
        self._last_seen: dict[str, str] = {}

    def _http(self) -> Any:
        if self._client is not None:
            return self._client
        import httpx
        self._client = httpx.Client(timeout=30.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "phantom/4.0.0-dev",
        }

    def fetch_messages(self) -> list[dict[str, Any]]:
        """Poll every configured channel for messages newer than the
        last id we've seen.
        """
        out: list[dict[str, Any]] = []
        for cid in self._channel_ids:
            params: dict[str, Any] = {"limit": 50}
            after = self._last_seen.get(cid)
            if after:
                params["after"] = after
            url = f"{self._base}/channels/{cid}/messages"
            try:
                resp = self._http().get(url, headers=self._headers(), params=params)
            except Exception as exc:
                raise ChannelError(f"discord fetch failed: {exc}") from exc
            if resp.status_code >= 400:
                raise ChannelError(
                    f"discord API returned {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            if not isinstance(body, list):
                continue
            # Discord returns newest-first; reverse so we hand events
            # in chronological order.
            for msg in reversed(body):
                self._last_seen[cid] = msg.get("id", self._last_seen.get(cid, ""))
                out.append({
                    "id": msg.get("id"),
                    "channel_id": cid,
                    "guild_id": msg.get("guild_id"),
                    "author_id": (msg.get("author") or {}).get("id"),
                    "content": msg.get("content", ""),
                })
        return out

    def send_message(self, *, channel_id: str, text: str) -> dict[str, Any]:
        url = f"{self._base}/channels/{channel_id}/messages"
        try:
            resp = self._http().post(url, headers=self._headers(),
                                      json={"content": text})
        except Exception as exc:
            raise ChannelError(f"discord send failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelError(
                f"discord send returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()
