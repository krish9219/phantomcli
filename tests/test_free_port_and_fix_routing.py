"""Tests for free-port detection + the fix-vs-rebuild router."""
from __future__ import annotations

import socket

import pytest

from omnicli.free_port import pick_free_port, port_is_free


class TestFreePort:
    def test_port_is_free_returns_true_for_random(self):
        # OS-assigned port: bind(0), grab number, close, then check free.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        assert port_is_free(p) is True

    def test_port_is_free_false_when_bound(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        try:
            assert port_is_free(p) is False
        finally:
            s.close()

    def test_pick_free_port_returns_int(self):
        p = pick_free_port()
        assert isinstance(p, int)
        assert p > 0

    def test_pick_free_port_skips_busy(self):
        # Hold a port then ask pick_free_port to prefer it
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        try:
            picked = pick_free_port(preferred=[busy])
            # Picker should NOT return the busy one
            assert picked != busy
        finally:
            s.close()

    def test_pick_free_port_honours_preferred_when_free(self):
        # Find something free right now
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        picked = pick_free_port(preferred=[free])
        # It SHOULD return our preferred — it's free at this moment
        assert picked == free


class TestFixRouting:
    def _load(self):
        # Import the private helpers we just added
        from omnicli.cli import _looks_like_fix_request
        return _looks_like_fix_request

    def test_traceback_detected(self):
        f = self._load()
        assert f("Traceback (most recent call last):\n  File ...") is True

    def test_multiple_fix_cues(self):
        f = self._load()
        # "error" + "fix" in one message
        assert f("I got an error, can you fix it?") is True

    def test_single_cue_not_enough(self):
        f = self._load()
        # Just one weak cue — should NOT trigger (avoids false positives
        # on normal prompts like "create an error-handling demo")
        assert f("create an error-handling demo") is False

    def test_typeerror_traceback(self):
        f = self._load()
        assert f("TypeError: Object of type generator is not JSON serializable") is True

    def test_empty_returns_false(self):
        f = self._load()
        assert f("") is False
        assert f(None) is False

    def test_regular_directive_not_triggered(self):
        f = self._load()
        assert f("build me a flask dashboard with charts") is False
