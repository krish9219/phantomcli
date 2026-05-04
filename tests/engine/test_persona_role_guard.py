"""Regression tests for v4.0.10 — explicit role beats dynamic persona.

In v4.0.9 a 3,500-char data-scientist prompt was still hijacked by
``get_dynamic_persona``: the small-talk path didn't trigger, the
keyword map didn't match cleanly, and the slow-path LLM router asked
the model "what title fits" — which decided **Frontend Developer**
because a ``Streamlit UI`` was mentioned. The user's explicit role
("You are a senior data scientist") was overridden, the model
returned an empty response, and the user got an unrelated IPL Flask
dashboard build attempt.

This test pins the new precedence:

  1. Explicit role assignment ALWAYS wins. ``"You are a senior data
     scientist…"`` → ``"Senior Data Scientist"`` — no LLM call, no
     keyword shuffle, no override.
  2. Small-talk still maps to ``"AI Assistant"``.
  3. Build prompts still hit the keyword map (Flask → Full Stack Web
     Developer).
"""

from __future__ import annotations

import pytest

from omnicli.engine import _persona_from_explicit_role, get_dynamic_persona


# ─── _persona_from_explicit_role: extraction correctness ─────────────────


@pytest.mark.parametrize("prompt, expected", [
    ("You are a senior data scientist. Help me…", "Senior Data Scientist"),
    ("You are an expert ML engineer working on time series.",
     "Expert Ml Engineer"),
    ("Act as a Python tutor and explain decorators.", "Python Tutor"),
    ("Imagine you are a SQL optimiser.", "Sql Optimiser"),
    ("You are a security researcher tasked with finding flaws.",
     "Security Researcher"),
    ("Pretend you are a kubernetes troubleshooter.",
     "Kubernetes Troubleshooter"),
    ("  You're a senior backend developer.\n", "Senior Backend Developer"),
])
def test_extracts_role_from_explicit_assignment(prompt: str, expected: str) -> None:
    assert _persona_from_explicit_role(prompt) == expected


@pytest.mark.parametrize("prompt", [
    "build a flask web app",
    "hi",
    "what's the weather today",
    "",
    "    ",
    "the senior data scientist on my team said…",  # role word, not at start
    "Tell me about Python decorators.",
])
def test_no_false_positive_on_non_role_prompts(prompt: str) -> None:
    assert _persona_from_explicit_role(prompt) is None


# ─── get_dynamic_persona: explicit role overrides every other path ──────


def test_user_bug_prompt_now_returns_role(monkeypatch) -> None:
    """The exact production bug — Streamlit-mentioning data-science prompt
    used to shapeshift to ``Frontend Developer`` via the LLM router.
    Explicit role must now win without any API call."""
    # Sentinel — fail the test if either OpenAI client path gets called.
    def _fail(*a, **kw):
        raise AssertionError("LLM router must NOT be called when role is explicit")
    monkeypatch.setattr("omnicli.engine.OpenAI", _fail)

    prompt = (
        "You are a senior data scientist. Your workdir is your current "
        "session directory. Two data files are at /home/user/data/posts "
        "and /home/user/data/comments. Build a Streamlit UI and a "
        "machine learning pipeline. Phase 1 — Discover. Phase 2 — Build."
    )
    assert get_dynamic_persona(prompt) == "Senior Data Scientist"


def test_small_talk_still_returns_ai_assistant(monkeypatch) -> None:
    monkeypatch.setattr(
        "omnicli.engine.OpenAI",
        lambda *a, **kw: pytest.fail("must not call LLM for small talk"),
    )
    assert get_dynamic_persona("hi") == "AI Assistant"
    assert get_dynamic_persona("thanks") == "AI Assistant"


def test_keyword_path_still_works_for_build_prompts(monkeypatch) -> None:
    monkeypatch.setattr(
        "omnicli.engine.OpenAI",
        lambda *a, **kw: pytest.fail("must not call LLM when keyword matches"),
    )
    assert get_dynamic_persona(
        "build a flask web app with sqlite backend"
    ) == "Full Stack Web Developer"


def test_explicit_role_wins_over_keyword_match(monkeypatch) -> None:
    """If user says 'You are X' AND mentions Flask, role still wins."""
    monkeypatch.setattr(
        "omnicli.engine.OpenAI",
        lambda *a, **kw: pytest.fail("must not call LLM when role is explicit"),
    )
    # Without the role guard, "flask" + "machine learning" would match
    # the keyword map. The role must win.
    assert get_dynamic_persona(
        "You are a senior data scientist. Build me a flask app with "
        "machine learning."
    ) == "Senior Data Scientist"
