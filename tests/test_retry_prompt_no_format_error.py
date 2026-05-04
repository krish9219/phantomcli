"""Regression for v3.0.6: the retry prompt used .format() on a string
that contained literal `{` and `}` characters — Python tried to
interpret them as placeholders and raised KeyError. v3.0.7 builds the
retry prompt with plain concatenation + f-strings instead.

We can't run plan() end-to-end without an API key, but we CAN force the
code path that constructs retry_prompt and confirm it doesn't raise."""
from __future__ import annotations

import pytest


class TestRetryPromptBuilds:
    def test_orchestrator_plan_no_format_crash(self, monkeypatch):
        """plan() builds the retry prompt before calling any LLM. Stubbing
        the LLM client so the call fails with a simple Exception, we
        force the retry-prompt-building code to execute. If the format()
        bug returns, this test raises KeyError before we get to the stub."""
        from omnicli.agents import AgentOrchestrator

        orch = AgentOrchestrator(
            "build something",
            trust_level=2,
        )
        # Force max_agents to a known value so the f-string substitution
        # is exercised with a real integer.
        orch.max_agents = 4

        class _ClientStub:
            class _Chat:
                class _Completions:
                    @staticmethod
                    def create(**kw):
                        raise RuntimeError("stub — no real API call")
                completions = _Completions()
            chat = _Chat()

        import omnicli.agents as _agents
        monkeypatch.setattr(_agents, "OpenAI", lambda **kw: _ClientStub())

        # plan() should not raise KeyError — the crash site was during
        # retry_prompt construction, which happens BEFORE the LLM call.
        # The stub client will raise on the first call, plan() returns
        # None (or the fallback plan), but critically — no KeyError.
        try:
            orch.plan()
        except KeyError as e:
            pytest.fail(f"retry prompt regressed to format() bug: KeyError {e}")
        except Exception:
            # Other exceptions (LLM stub errors, etc.) are fine — we only
            # care that the format-literal-braces bug stays fixed.
            pass

    def test_retry_prompt_contains_literal_braces(self, monkeypatch):
        """Confirm the retry prompt string itself still tells the model
        'start with `{` end with `}`'. If someone 'fixes' this by using
        .format() again, those braces will get eaten."""
        # We can't access the retry_prompt directly (it's a local variable
        # inside plan()), but we can verify the source code contains the
        # literal text. That's a structural test that catches regressions.
        import inspect
        from omnicli.agents import AgentOrchestrator
        src = inspect.getsource(AgentOrchestrator.plan)
        # The critical literals from the string
        assert "start with `{` and end with `}`" in src, \
            "retry_prompt lost its literal-brace instruction"
        # And the ANTI-pattern marker: .format() must not be called on the
        # retry_prompt string
        assert ".format(max_ag=" not in src, \
            "retry_prompt regressed to using .format() — will crash on literal braces"
