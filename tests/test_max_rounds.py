"""Tests for engine._max_rounds — config-overridable tool-round cap."""
from __future__ import annotations

import pytest

from omnicli import engine
from omnicli.memory import save_config


class TestMaxRounds:
    def test_default_is_24(self):
        assert engine.MAX_ROUNDS == 24

    def test_default_returned_when_no_config(self):
        assert engine._max_rounds() == 24

    def test_config_override_respected(self):
        save_config("max_tool_rounds", "12")
        assert engine._max_rounds() == 12

    def test_floor_at_one(self):
        save_config("max_tool_rounds", "0")
        assert engine._max_rounds() == 1

    def test_ceiling_at_sixty_four(self):
        save_config("max_tool_rounds", "9999")
        assert engine._max_rounds() == 64

    def test_negative_floored_to_one(self):
        save_config("max_tool_rounds", "-5")
        assert engine._max_rounds() == 1

    def test_non_numeric_falls_back_to_default(self):
        save_config("max_tool_rounds", "abc")
        assert engine._max_rounds() == 24
