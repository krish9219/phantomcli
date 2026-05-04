"""Tests for :mod:`phantom.onboarding.wizard`."""

from __future__ import annotations

import pytest

from phantom.errors import ConfigError
from phantom.onboarding import Step, Wizard, default_steps


def _identity(raw: str) -> str:
    if not raw:
        raise ConfigError("empty")
    return raw.strip()


class TestWizardCore:
    def test_empty_steps_rejected(self):
        with pytest.raises(ConfigError):
            Wizard([])

    def test_simple_walk_through(self):
        steps = [
            Step("a", "wizard.welcome", _identity),
            Step("b", "wizard.welcome", _identity),
        ]
        w = Wizard(steps)
        assert w.current.key == "a"
        w.submit("alpha")
        assert w.current.key == "b"
        w.submit("beta")
        assert w.done
        assert w.state.answers == {"a": "alpha", "b": "beta"}

    def test_validation_error_does_not_advance(self):
        w = Wizard([Step("x", "wizard.welcome", _identity)])
        with pytest.raises(ConfigError):
            w.submit("")
        assert w.current.key == "x"

    def test_submit_after_done_raises(self):
        w = Wizard([Step("x", "wizard.welcome", _identity)])
        w.submit("ok")
        with pytest.raises(ConfigError, match="completed"):
            w.submit("more")


class TestDefaultSteps:
    def test_locale_validator(self):
        steps = default_steps()
        locale_step = steps[0]
        assert locale_step.key == "locale"
        # 'en' is allowed.
        assert locale_step.validator("en") == "en"
        with pytest.raises(ConfigError):
            locale_step.validator("klingon")

    def test_yes_no_validator(self):
        steps = default_steps()
        yn = next(s for s in steps if s.key == "enable_telegram")
        assert yn.validator("yes") == "yes"
        assert yn.validator("y") == "yes"
        assert yn.validator("NO") == "no"
        with pytest.raises(ConfigError):
            yn.validator("maybe")

    def test_full_walk(self):
        w = Wizard(default_steps())
        w.submit("en")
        w.submit("claude-opus-4-5")
        w.submit("sk-test")
        w.submit("yes")
        assert w.done
        assert w.state.answers == {
            "locale": "en",
            "model": "claude-opus-4-5",
            "api_key": "sk-test",
            "enable_telegram": "yes",
        }
