"""Phantom onboarding wizard.

A pure-data state machine: each step has a prompt key (i18n) and an
input validator. The CLI shell drives the machine; tests drive it
synchronously without a TTY.
"""

from __future__ import annotations

from phantom.onboarding.wizard import (
    OnboardingState,
    Step,
    Wizard,
    default_steps,
)

__all__ = [
    "OnboardingState",
    "Step",
    "Wizard",
    "default_steps",
]
