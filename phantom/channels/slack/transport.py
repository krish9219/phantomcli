"""Slack Web API transport via httpx.

Implements the two methods :class:`SlackAdapter` needs:
:meth:`fetch_events` (polling against ``conversations.history``) and
:meth:`post_message`. A Socket-Mode push transport is a v4.1
follow-up.
"""

from __future__ import annotations

from typing import Any

from phantom.errors import ChannelError

__all__ = ["HttpxSlackTransport"]


class HttpxSlackTransport:
    """REST polling against api.slack.com."""

    BASE_URL = "https://slack.com/api"

    def __init__(
        self,
        *,
        bot_token: str,
        channel_ids: list[str],
        client: Any = None,
        base_url: str | None = None,
    ) -> None:
        if not bot_token:
            raise ChannelError("HttpxSlackTransport requires a bot_token")
        if not channel_ids:
            raise ChannelError("HttpxSlackTransport requires at least one channel_id")
        self._token = bot_token
        self._channel_ids = list(channel_ids)
        self._base = (base_url or self.BASE_URL).rstrip("/")
        self._client = client
        self._oldest_per_channel: dict[str, str] = {}

    def _http(self) -> Any:
        if self._client is not None:
            return self._client
        import httpx
        self._client = httpx.Client(timeout=30.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "phantom/4.0.0-dev",
        }

    def fetch_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cid in self._channel_ids:
            url = f"{self._base}/conversations.history"
            params: dict[str, Any] = {"channel": cid, "limit": 50}
            oldest = self._oldest_per_channel.get(cid)
            if oldest:
                params["oldest"] = oldest
            try:
                resp = self._http().get(url, headers=self._headers(), params=params)
            except Exception as exc:
                raise ChannelError(f"slack fetch failed: {exc}") from exc
            if resp.status_code >= 400:
                raise ChannelError(
                    f"slack API returned {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            if not body.get("ok"):
                raise ChannelError(
                    f"slack API not ok: {body.get('error', '?')}"
                )
            messages = body.get("messages") or []
            # Slack returns newest first; reverse for chronological feed.
            for msg in reversed(messages):
                ts = msg.get("ts", "")
                if ts and (not oldest or ts > oldest):
                    self._oldest_per_channel[cid] = ts
                out.append({
                    "type": msg.get("type", "message"),
                    "user": msg.get("user", ""),
                    "text": msg.get("text", ""),
                    "channel": cid,
                    "team": msg.get("team"),
                    "ts": ts,
                    "thread_ts": msg.get("thread_ts"),
                })
        return out

    def post_message(self, *, channel: str, text: str) -> dict[str, Any]:
        url = f"{self._base}/chat.postMessage"
        try:
            resp = self._http().post(
                url, headers=self._headers(),
                json={"channel": channel, "text": text},
            )
        except Exception as exc:
            raise ChannelError(f"slack post failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelError(
                f"slack post returned {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        if not body.get("ok"):
            raise ChannelError(
                f"slack post not ok: {body.get('error', '?')}"
            )
        return body
