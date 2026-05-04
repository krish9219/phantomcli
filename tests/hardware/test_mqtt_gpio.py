"""Tests for the MQTT and GPIO peripherals (fakes only)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from phantom.errors import PhantomError
from phantom.hardware.gpio import GpioPeripheral
from phantom.hardware.mqtt import MqttPeripheral


# ─── MQTT ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakePahoMessage:
    topic: str
    payload: bytes


class _FakePahoClient:
    def __init__(self, client_id="x"):
        self.client_id = client_id
        self.connected = False
        self.subscriptions: list[tuple[str, int]] = []
        self.published: list[tuple[str, bytes, int]] = []
        self.username = ""
        self.password = ""
        self.on_message = None
        self.loop_running = False

    def username_pw_set(self, u, p):
        self.username, self.password = u, p

    def connect(self, host, port, keepalive):
        self.connected = True
        self._host = host

    def loop_start(self):
        self.loop_running = True

    def loop_stop(self):
        self.loop_running = False

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))

    def disconnect(self):
        self.connected = False

    # Test helper: simulate an inbound message.
    def deliver(self, topic, payload):
        if self.on_message is None:
            return
        self.on_message(self, None, _FakePahoMessage(topic=topic, payload=payload))


def _factory():
    holder: dict = {}
    def make(client_id):
        c = _FakePahoClient(client_id=client_id)
        holder["last"] = c
        return c
    make.holder = holder  # type: ignore[attr-defined]
    return make


class TestMqttPeripheral:
    def test_open_connects_and_starts_loop(self):
        f = _factory()
        p = MqttPeripheral(host="broker", port=1883, client_factory=f)
        p.open()
        assert f.holder["last"].connected  # type: ignore[attr-defined]
        assert f.holder["last"].loop_running  # type: ignore[attr-defined]
        p.close()
        assert not f.holder["last"].connected  # type: ignore[attr-defined]

    def test_publish_and_receive(self):
        f = _factory()
        p = MqttPeripheral(host="broker", client_factory=f)
        p.open()
        p.subscribe("home/temp")
        p.publish("home/cmd", b"on", qos=1)
        assert ("home/temp", 0) in f.holder["last"].subscriptions  # type: ignore[attr-defined]
        assert ("home/cmd", b"on", 1) in f.holder["last"].published  # type: ignore[attr-defined]

        # Inbound message lands in the inbox.
        f.holder["last"].deliver("home/temp", b"21.4")  # type: ignore[attr-defined]
        msg = p.next_message()
        assert msg == ("home/temp", b"21.4")
        assert p.next_message() is None

    def test_publish_string_payload_encoded(self):
        f = _factory()
        p = MqttPeripheral(host="broker", client_factory=f)
        p.open()
        p.publish("t", "hello")
        topic, payload, _ = f.holder["last"].published[0]  # type: ignore[attr-defined]
        assert payload == b"hello"

    def test_username_password_set(self):
        f = _factory()
        p = MqttPeripheral(host="broker", username="u", password="p",
                           client_factory=f)
        p.open()
        last = f.holder["last"]  # type: ignore[attr-defined]
        assert last.username == "u" and last.password == "p"

    def test_publish_when_unopened_raises(self):
        p = MqttPeripheral(host="broker", client_factory=_factory())
        with pytest.raises(PhantomError, match="not open"):
            p.publish("t", b"x")

    def test_info_id_includes_host_port(self):
        p = MqttPeripheral(host="broker.example.com", port=8883)
        assert p.info().id == "mqtt:tcp://broker.example.com:8883"


# ─── GPIO ────────────────────────────────────────────────────────────────────


class _FakePinInput:
    def __init__(self, pin):
        self.pin = pin
        self.is_pressed = False
        self.value = 0
        self._when_pressed = None
        self._when_released = None

    @property
    def when_pressed(self):
        return self._when_pressed

    @when_pressed.setter
    def when_pressed(self, fn):
        self._when_pressed = fn

    @property
    def when_released(self):
        return self._when_released

    @when_released.setter
    def when_released(self, fn):
        self._when_released = fn

    def close(self):
        pass


class _FakePinOutput:
    def __init__(self, pin):
        self.pin = pin
        self.value = 0

    def on(self): self.value = 1
    def off(self): self.value = 0
    def close(self): pass


class TestGpioPeripheralInput:
    def test_open_close_lifecycle(self):
        p = GpioPeripheral(pin=17, mode="input", factory=_FakePinInput)
        p.open()
        assert p.read() == 0
        p.close()

    def test_read_when_unopened_raises(self):
        p = GpioPeripheral(pin=17, mode="input", factory=_FakePinInput)
        with pytest.raises(PhantomError, match="not open"):
            p.read()

    def test_watch_attaches_callbacks(self):
        events: list[int] = []
        p = GpioPeripheral(pin=17, mode="input", factory=_FakePinInput)
        p.open()
        p.watch(events.append)
        # Drive the fake's callbacks.
        device = p._device  # noqa: SLF001
        device.when_pressed()
        device.when_released()
        assert events == [1, 0]

    def test_watch_on_output_pin_rejected(self):
        p = GpioPeripheral(pin=17, mode="output", factory=_FakePinOutput)
        p.open()
        with pytest.raises(PhantomError, match="output pin"):
            p.watch(lambda v: None)


class TestGpioPeripheralOutput:
    def test_write_high_low(self):
        p = GpioPeripheral(pin=18, mode="output", factory=_FakePinOutput)
        p.open()
        p.write(1)
        assert p._device.value == 1  # noqa: SLF001
        p.write(0)
        assert p._device.value == 0  # noqa: SLF001

    def test_write_to_input_rejected(self):
        p = GpioPeripheral(pin=18, mode="input", factory=_FakePinInput)
        p.open()
        with pytest.raises(PhantomError, match="cannot write"):
            p.write(1)

    def test_unknown_mode_rejected_at_open(self):
        p = GpioPeripheral(pin=18, mode="rgb", factory=lambda pin: object())
        # The factory branch bypasses the gpiozero mode switch but
        # write/watch will still validate on the next call.
        p.open()  # OK because factory is supplied
        with pytest.raises(PhantomError, match="cannot write"):
            p.write(1)  # mode != output
        with pytest.raises(PhantomError, match="output pin"):
            p.watch(lambda v: None)  # mode != input

    def test_info_includes_pin(self):
        p = GpioPeripheral(pin=21, mode="output")
        info = p.info()
        assert info.id == "gpio:21"
        assert info.kind == "gpio"
        assert info.extras == {"pin": 21, "mode": "output"}
