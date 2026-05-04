"""Tests for llm_summarizer — LLM-backed conversation summary with
caching, retries, fallback."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from omnicli import llm_summarizer as sm
from omnicli.context_compact import compact


@pytest.fixture(autouse=True)
def _clear_cache():
    sm.clear_cache()


def _fake_client(text: str = "SUMMARY-OK"):
    """A MagicMock shaped like an OpenAI client with .chat.completions.create."""
    c = MagicMock()
    # resp.choices[0].message.content
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = text
    c.chat.completions.create.return_value = resp
    return c


def _msgs(n: int = 10) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant",
                    "content": f"message {i}"})
    return out


class TestHappyPath:
    def test_returns_llm_output(self):
        c = _fake_client("This conversation was about X.")
        out = sm.summarise(_msgs(10), client=c, model="gpt-test")
        assert out == "This conversation was about X."

    def test_strips_whitespace(self):
        c = _fake_client("   trimmed   \n")
        assert sm.summarise(_msgs(5), client=c) == "trimmed"

    def test_client_called_with_transcript(self):
        c = _fake_client("ok")
        sm.summarise(_msgs(5), client=c, model="some-model")
        call = c.chat.completions.create.call_args
        assert call.kwargs["model"] == "some-model"
        # The transcript JSON should appear in the user message content
        user_msg = call.kwargs["messages"][1]
        assert user_msg["role"] == "user"
        assert "message 0" in user_msg["content"]


class TestCaching:
    def test_same_messages_cached(self):
        c = _fake_client("first")
        msgs = _msgs(5)
        sm.summarise(msgs, client=c)
        sm.summarise(msgs, client=c)
        sm.summarise(msgs, client=c)
        # LLM should only have been called once
        assert c.chat.completions.create.call_count == 1

    def test_different_messages_not_cached(self):
        c = _fake_client("summary")
        sm.summarise(_msgs(5), client=c)
        sm.summarise(_msgs(10), client=c)
        assert c.chat.completions.create.call_count == 2

    def test_clear_cache_forces_rerun(self):
        c = _fake_client("s")
        msgs = _msgs(3)
        sm.summarise(msgs, client=c)
        sm.clear_cache()
        sm.summarise(msgs, client=c)
        assert c.chat.completions.create.call_count == 2


class TestRetry:
    def test_transient_error_retried(self, monkeypatch):
        calls = {"n": 0}
        def _flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            r = MagicMock()
            r.choices = [MagicMock()]
            r.choices[0].message = MagicMock()
            r.choices[0].message.content = "got-it"
            return r
        c = MagicMock()
        c.chat.completions.create = _flaky
        # Patch sleep so the test doesn't actually wait
        monkeypatch.setattr("omnicli.llm_summarizer.time.sleep", lambda s: None)
        out = sm.summarise(_msgs(3), client=c, max_retries=5)
        assert out == "got-it"
        assert calls["n"] == 3

    def test_persistent_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr("omnicli.llm_summarizer.time.sleep", lambda s: None)
        c = MagicMock()
        c.chat.completions.create.side_effect = RuntimeError("dead")
        out = sm.summarise(_msgs(5), client=c, max_retries=2)
        # Falls back to deterministic summariser — output contains its signature
        assert "COMPACTED CONTEXT" in out


class TestEmptyInputs:
    def test_empty_messages_returns_empty(self):
        c = _fake_client("x")
        assert sm.summarise([], client=c) == ""
        # And the client wasn't called
        c.chat.completions.create.assert_not_called()

    def test_empty_llm_response_falls_back(self):
        c = _fake_client("")   # LLM returns empty string
        out = sm.summarise(_msgs(5), client=c, max_retries=1)
        # Falls back
        assert "COMPACTED CONTEXT" in out


class TestNoClientConfigured:
    def test_no_client_and_no_default_falls_back(self, monkeypatch):
        # Force default_client to None
        monkeypatch.setattr(sm, "_default_client", lambda: None)
        out = sm.summarise(_msgs(5))
        assert "COMPACTED CONTEXT" in out


class TestCompactIntegration:
    def test_compact_uses_llm_summariser_when_supplied(self):
        c = _fake_client("LLM-SUMMARY-GOES-HERE")
        fn = sm.make_callable(client=c, model="gpt-test")
        msgs = [{"role": "system", "content": "S"}]
        for _ in range(30):
            msgs.append({"role": "user", "content": "x" * 2000})
        new, stats = compact(msgs, budget=3000, ratio=0.5, keep_recent=5, summariser=fn)
        assert stats.summary_text == "LLM-SUMMARY-GOES-HERE"
        # The synthetic summary message is the LLM output
        sys_msgs = [m for m in new if m.get("role") == "system"]
        assert any("LLM-SUMMARY-GOES-HERE" in m["content"] for m in sys_msgs)

    def test_compact_survives_broken_llm(self):
        c = MagicMock()
        c.chat.completions.create.side_effect = Exception("boom")
        # Call the wrapper directly (not via make_callable) with retries=0
        # to verify the integration still compacts.
        msgs = [{"role": "system", "content": "S"}]
        for _ in range(30):
            msgs.append({"role": "user", "content": "x" * 2000})
        # Plug in a summariser that uses the broken client.
        def fn(old):
            return sm.summarise(old, client=c, max_retries=1)
        new, stats = compact(msgs, budget=3000, ratio=0.5, keep_recent=5, summariser=fn)
        # Falls back to deterministic summariser — still compacts successfully
        assert stats.compressed is True
