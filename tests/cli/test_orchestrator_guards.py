"""Regression tests for the v4.0.9 orchestrator-guard fixes.

In v4.0.8 a real-world data-science prompt was hijacked twice:

  1. ``_looks_like_fix_request`` matched 3 fix patterns (`traceback`,
     `error`, `diagnose`) inside instructional text and routed the
     entire conversation into focused-fix mode against an unrelated
     active project.
  2. ``AgentOrchestrator.should_spawn`` then would have matched on
     keyword surface (machine learning / pipeline / etc.) and spawned
     a multi-agent web-app build under the FULL STACK WEB DEVELOPER
     persona.

These tests enshrine the new guards: explicit role assignments
(``"You are a senior data scientist…"``) and structured multi-phase
plans (``"Phase 1 / Phase 2 / …"``) are honoured verbatim and never
hijacked. Real tracebacks still trigger fix mode. Real build prompts
still trigger spawn.
"""

from __future__ import annotations

import pytest

from omnicli.agents import AgentOrchestrator
from omnicli.cli import (
    _has_explicit_role,
    _has_structured_phases,
    _looks_like_fix_request,
)


# ─── The exact prompt that hit the bug in production ────────────────────


USER_BUG_PROMPT = """You are a senior data scientist. Your workdir is your current
session directory. Two data files are at:

  /home/user/data/posts
  /home/user/data/comments

I have NOT told you what they contain. Your job is to figure it out,
decide what's worth doing, and ship it. No questions back to me —
investigate with tools and commit to a plan.

Phase 1 — Discover (use read_file / list_dir / run_bash):
  1. Inspect both files.
  2. Decide what each file is.
  3. Decide the most valuable task this data supports.

Phase 2 — Build (use write_file for every script):
  4. Write `eda.py` that runs ydata-profiling.
  5. Write `train.py`.
  6. Write `app.py` — a Streamlit UI.

Phase 3 — Execute (use run_bash):
  7. Install requirements.
  8. If anything errors, use read_file to read the traceback, then
     edit_file to patch the specific failing line.
"""


def test_user_bug_prompt_no_longer_hijacked_by_fix_request() -> None:
    """The exact prompt that broke in v4.0.8 must now route correctly."""
    assert _looks_like_fix_request(USER_BUG_PROMPT) is False


def test_user_bug_prompt_no_longer_hijacked_by_spawn() -> None:
    """And it must not trigger the multi-agent orchestrator either."""
    assert AgentOrchestrator.should_spawn(USER_BUG_PROMPT) is False


# ─── Guard helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "You are a senior data scientist working on…",
    "You are an expert ML engineer.",
    "  You're a Python tutor; explain decorators.",
    "Act as a senior backend developer.",
    "Your job is to write a CSV parser.",
    "Your role is to review this PR.",
    "As a senior infrastructure engineer, design a rollout plan.",
    "Imagine you are a SQL optimiser.",
])
def test_has_explicit_role_recognises_role_assignments(prompt: str) -> None:
    assert _has_explicit_role(prompt) is True


@pytest.mark.parametrize("prompt", [
    "build a flask app",
    "create a todo list",
    "Hi! Can you help me debug this script?",
    "",
    "    ",
    "the senior dev told me to use Python",  # role word, not at start
])
def test_has_explicit_role_does_not_false_positive(prompt: str) -> None:
    assert _has_explicit_role(prompt) is False


def test_has_structured_phases_recognises_multi_phase_plans() -> None:
    assert _has_structured_phases(
        "Phase 1 — Discover\nPhase 2 — Build\nPhase 3 — Ship"
    ) is True
    assert _has_structured_phases(
        "Step 1: do X. Step 2: do Y. Step 3: do Z."
    ) is True


def test_has_structured_phases_ignores_single_phase_mentions() -> None:
    assert _has_structured_phases(
        "I'm in phase 1 of my project — what do I do next?"
    ) is False
    assert _has_structured_phases("step 1 only") is False


# ─── _looks_like_fix_request — positive cases still trigger ─────────────


def test_real_traceback_still_triggers_fix_mode() -> None:
    text = """The app crashed:
Traceback (most recent call last):
  File "app.py", line 42, in <module>
    result = process(data)
NameError: name 'process' is not defined"""
    assert _looks_like_fix_request(text) is True


def test_short_unstructured_fix_request_still_triggers() -> None:
    """Short ad-hoc fix asks ('the app crashes with TypeError, please debug')
    must still route to focused-fix when there's an active project."""
    assert _looks_like_fix_request(
        "the app crashes with TypeError on launch — please debug and fix"
    ) is True


def test_long_prompt_without_traceback_is_not_a_fix_request() -> None:
    """A 1500+-char structured prompt without a real traceback must not
    be treated as a fix request even if it mentions error/fix words."""
    long_text = (
        "Here is a comprehensive guide. " * 80  # ~2400 chars
        + "If anything errors, debug and fix it carefully."
    )
    assert len(long_text) > 1500
    assert _looks_like_fix_request(long_text) is False


def test_role_prompt_with_fix_words_is_not_a_fix_request() -> None:
    assert _looks_like_fix_request(
        "You are a Python tutor. Explain how to debug a TypeError "
        "and fix common ImportError patterns. Show me an example."
    ) is False


# ─── AgentOrchestrator.should_spawn — explicit-role override ────────────


def test_role_prompt_does_not_spawn_orchestrator() -> None:
    """Even when body keywords would otherwise trigger spawn (flask,
    dashboard, machine learning), an explicit role wins."""
    assert AgentOrchestrator.should_spawn(
        "You are a senior data scientist. Build me a Flask dashboard "
        "with a machine learning pipeline and a postgres backend."
    ) is False


def test_normal_build_prompt_still_spawns() -> None:
    """The role guard must not break normal multi-agent triggers."""
    assert AgentOrchestrator.should_spawn(
        "build a flask web app with a sqlite backend"
    ) is True


def test_normal_info_query_still_short_circuits() -> None:
    """Pre-existing negation/info-query guards still work."""
    assert AgentOrchestrator.should_spawn(
        "what is the difference between flask and django"
    ) is False
