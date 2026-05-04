"""MQTT peripheral — publish/subscribe to a broker.

Uses ``paho-mqtt`` when present (optional dep). The adapter exposes a
high-level ``publish`` / ``subscribe`` surface so the agent can talk
to IoT devices via the broker without having to think about packets.

Threading model: the underlying paho client runs its own background
loop; we receive messages into a thread-safe queue our caller drains.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from phantom.errors import PhantomError
from phantom.hardware.peripheral import Peripheral, PeripheralInfo

__all__ = ["MqttPeripheral"]


@dataclass
class MqttPeripheral(Peripheral):
    """MQTT publish/subscribe adapter.

    The peripheral ID is ``mqtt:<scheme>://<host>:<port>``.
    """

    host: str
    port: int = 1883
    scheme: str = "tcp"
    client_id: str = "phantom-agent"
    username: str = ""
    password: str = ""
    keepalive_s: int = 60
    description: str = ""

    client_factory: Any = None
    """Override for tests; receives ``client_id`` and returns a
    paho-shaped client (``connect``/``loop_start``/``publish``/
    ``subscribe``/``message_callback_add``/``disconnect``)."""

    _client: Any = field(default=None, init=False, repr=False)
    _inbox: deque[tuple[str, bytes]] = field(default_factory=deque, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def info(self) -> PeripheralInfo:
        return PeripheralInfo(
            id=f"mqtt:{self.scheme}://{self.host}:{self.port}",
            kind="mqtt",
            description=self.description,
            extras={"host": self.host, "port": self.port},
        )

    def open(self) -> None:
        if self._client is not None:
            return
        if self.client_factory is not None:
            self._client = self.client_factory(client_id=self.client_id)
        else:
            try:
                from paho.mqtt.client import Client  # type: ignore[import-not-found]
            except ImportError as exc:
                raise PhantomError(
                    "paho-mqtt is not installed; "
                    "install via `pip install phantom-cli[hardware]`."
                ) from exc
            self._client = Client(client_id=self.client_id)
        if self.username:
            self._client.username_pw_set(self.username, self.password)
        self._client.on_message = self._on_message
        self._client.connect(self.host, self.port, self.keepalive_s)
        self._client.loop_start()

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.loop_stop()
        except Exception:
            pass
        try:
            self._client.disconnect()
        except Exception:
            pass
        self._client = None
        with self._lock:
            self._inbox.clear()

    def publish(self, topic: str, payload: bytes | str, *, qos: int = 0) -> None:
        self._require_open()
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._client.publish(topic, payload=payload, qos=qos)

    def subscribe(self, topic: str, *, qos: int = 0) -> None:
        self._require_open()
        self._client.subscribe(topic, qos=qos)

    def next_message(self) -> tuple[str, bytes] | None:
        """Pop one inbound (topic, payload) pair. None when empty."""
        with self._lock:
            return self._inbox.popleft() if self._inbox else None

    def queued(self) -> int:
        with self._lock:
            return len(self._inbox)

    # paho callbacks -------------------------------------------------------

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        # Both real paho.Client and our test fakes call this with
        # objects exposing ``topic`` and ``payload`` attributes.
        topic = getattr(message, "topic", "")
        payload = getattr(message, "payload", b"")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        with self._lock:
            self._inbox.append((str(topic), bytes(payload)))

    def _require_open(self) -> None:
        if self._client is None:
            raise PhantomError("MQTT peripheral is not open")
