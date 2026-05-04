"""Real Telegram Bot API transport using httpx.

Implements the two methods :class:`TelegramAdapter` needs:
:meth:`get_updates` and :meth:`send_message`. Operators wire this into
the adapter at startup; tests use the in-memory fake from
``test_telegram.py``.
"""

from __future__ import annotations

from typing import Any

from phantom.errors import ChannelError

__all__ = ["HttpxTelegramTransport"]


class HttpxTelegramTransport:
    """HTTPS transport against api.telegram.org.

    Honours Telegram's long-poll convention: a single ``getUpdates``
    request blocks for up to ``timeout_s`` seconds, returning whatever
    has accumulated.

    The transport is **synchronous** — one thread can drive one bot.
    Operators who need many bots run one transport each.
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        *,
        token: str,
        client: Any = None,  # httpx.Client; injected for tests
        base_url: str | None = None,
    ) -> None:
        if not token:
            raise ChannelError("HttpxTelegramTransport requires a token")
        self._token = token
        self._base = (base_url or self.BASE_URL).rstrip("/")
        self._client = client

    def _http(self) -> Any:
        if self._client is not None:
            return self._client
        import httpx
        self._client = httpx.Client(timeout=60.0)
        return self._client

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            resp = self._http().post(url, json=params)
        except Exception as exc:
            raise ChannelError(f"telegram transport request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelError(
                f"telegram API returned {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        if not body.get("ok"):
            raise ChannelError(
                f"telegram API not ok: {body.get('description', '?')}"
            )
        return body.get("result", {})

    def get_updates(self, *, offset: int, timeout: float) -> list[dict[str, Any]]:
        """Long-poll for new updates."""
        result = self._call("getUpdates", {
            "offset": offset,
            "timeout": int(timeout),
        })
        if isinstance(result, list):
            return result
        return []

    def send_message(self, *, chat_id: str, text: str) -> dict[str, Any]:
        """Send a text message. Returns the API result object."""
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
        })
