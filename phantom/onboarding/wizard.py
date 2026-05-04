"""Onboarding wizard state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from phantom.errors import ConfigError
from phantom.i18n import AVAILABLE_LOCALES

__all__ = ["OnboardingState", "Step", "Wizard", "default_steps"]


@dataclass(frozen=True, slots=True)
class Step:
    """One question in the wizard."""

    key: str
    prompt_i18n_key: str
    validator: Callable[[str], str]
    """The validator parses + validates raw user input. Returns the
    cleaned value (possibly identical) or raises :class:`ConfigError`
    with a human-readable message that the wizard surfaces."""


@dataclass
class OnboardingState:
    """Accumulated answers across the wizard."""

    answers: dict[str, str] = field(default_factory=dict)


class Wizard:
    """Drive a list of :class:`Step` objects against an input source."""

    def __init__(self, steps: list[Step]) -> None:
        if not steps:
            raise ConfigError("Wizard requires at least one Step")
        self._steps = steps
        self._index = 0
        self.state = OnboardingState()

    @property
    def current(self) -> Step | None:
        if self._index >= len(self._steps):
            return None
        return self._steps[self._index]

    def submit(self, raw: str) -> Step | None:
        """Validate *raw* against the current step; advance on success.

        Returns the new current step (or None when the wizard is done).
        Raises :class:`ConfigError` on validation failure; the caller
        re-prompts.
        """
        if self.current is None:
            raise ConfigError("wizard already completed")
        value = self.current.validator(raw)
        self.state.answers[self.current.key] = value
        self._index += 1
        return self.current

    @property
    def done(self) -> bool:
        return self._index >= len(self._steps)


# ─── default steps ────────────────────────────────────────────────────────────


def _validate_locale(raw: str) -> str:
    raw = raw.strip().lower()
    if raw not in AVAILABLE_LOCALES:
        raise ConfigError(
            f"locale must be one of {AVAILABLE_LOCALES}, got {raw!r}"
        )
    return raw


def _validate_nonempty(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise ConfigError("value must be non-empty")
    return raw


def _validate_yes_no(raw: str) -> str:
    raw = raw.strip().lower()
    if raw in ("y", "yes", "true", "1"):
        return "yes"
    if raw in ("n", "no", "false", "0"):
        return "no"
    raise ConfigError("answer with yes or no")


def default_steps() -> list[Step]:
    """The Stage-7 wizard: locale, model, key, channel choice."""
    return [
        Step("locale", "wizard.choose_lang", _validate_locale),
        Step("model", "wizard.welcome", _validate_nonempty),
        Step("api_key", "wizard.welcome", _validate_nonempty),
        Step("enable_telegram", "wizard.welcome", _validate_yes_no),
    ]
