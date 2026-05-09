"""Tests for v1.1.13: tighter HTTP timeout default + clearer timeout error.

Triggered by the v1.1.12 user report: a single LLM call hung for 11+
minutes on kimi-k2.6 because the httpx default of 120s wasn't being
hit (NVIDIA's gateway kept the connection alive).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
)
from phantom.errors import PhantomError


def _msgs():
    return [ProviderMessage(role="user", content="hi")]


def test_default_timeout_is_60s_not_120s(monkeypatch):
    monkeypatch.delenv("PHANTOM_HTTP_TIMEOUT_S", raising=False)
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
    )
    assert p._timeout == 60.0


def test_env_override_wins_over_default(monkeypatch):
    monkeypatch.setenv("PHANTOM_HTTP_TIMEOUT_S", "15")
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
    )
    assert p._timeout == 15.0


def test_explicit_timeout_overrides_env(monkeypatch):
    monkeypatch.setenv("PHANTOM_HTTP_TIMEOUT_S", "15")
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        timeout_s=42.0,
    )
    assert p._timeout == 42.0


def test_invalid_env_falls_back_to_60(monkeypatch):
    monkeypatch.setenv("PHANTOM_HTTP_TIMEOUT_S", "not-a-number")
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
    )
    assert p._timeout == 60.0


def test_timeout_exception_surfaces_actionable_error(monkeypatch):
    """When httpx raises a ReadTimeout, the user-facing message must mention
    the timeout, the model, and how to switch."""
    class _ReadTimeout(Exception):
        pass
    _ReadTimeout.__name__ = "ReadTimeout"

    client = MagicMock()
    client.post = MagicMock(side_effect=_ReadTimeout("read timed out"))
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="kimi-k2.6",
        client=client, timeout_s=60,
    )
    with pytest.raises(PhantomError) as exc_info:
        p.complete(_msgs(), tools=[])
    msg = str(exc_info.value).lower()
    assert "timed out" in msg
    assert "60s" in msg or "60.0" in msg
    assert "kimi-k2.6" in msg
    assert "/model" in msg or "switch" in msg


def test_non_timeout_error_keeps_old_message(monkeypatch):
    """Connection refused etc. must still surface as 'request failed', not
    falsely-classified as timeout."""
    client = MagicMock()
    client.post = MagicMock(side_effect=ConnectionError("refused"))
    p = OpenAICompatibleProvider(
        base_url="https://x.test/v1", api_key="k", model="m",
        client=client, timeout_s=60,
    )
    with pytest.raises(PhantomError) as exc_info:
        p.complete(_msgs(), tools=[])
    assert "timed out" not in str(exc_info.value)
    assert "request failed" in str(exc_info.value)
