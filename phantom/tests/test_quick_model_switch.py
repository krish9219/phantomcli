"""Tests for v1.1.14: `/model <model-id>` reuses current endpoint+key,
and `_looks_garbled` detects broken model output.

Triggered by the v1.1.13 user report: kimi-k2.6 returned token soup, and
the only way to switch off it was the multi-step `/add` wizard. Now
`/model meta/llama-3.3-70b-instruct` works directly, reusing the
current provider's URL + key and registering the new entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phantom.agent import AgentSession, ScriptedProvider
from phantom.agent.provider import OpenAICompatibleProvider
from phantom.cli.chat import (
    _handle_slash,
    _looks_garbled,
    _switch_model_only,
)
from phantom.config.providers import CustomProvider, ProviderRegistry


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    return tmp_path


def _capture():
    out: list[str] = []
    return out, out.append


def _live_session() -> AgentSession:
    """A session whose provider is a real OpenAICompatibleProvider so the
    `/model <id>` fallback can read its base_url + api_key."""
    p = OpenAICompatibleProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="nvapi-secret",
        model="moonshotai/kimi-k2.6",
    )
    return AgentSession(provider=p, tools=[])


# ─── _switch_model_only ──────────────────────────────────────────────────────

def test_switch_model_only_keeps_endpoint_and_key(home: Path):
    session = _live_session()
    out, write = _capture()
    ok = _switch_model_only(
        session,
        "meta/llama-3.3-70b-instruct",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="nvapi-secret",
        write=write,
    )
    assert ok is True
    assert session.provider._model == "meta/llama-3.3-70b-instruct"
    assert session.provider._base_url == "https://integrate.api.nvidia.com/v1"
    assert session.provider._api_key == "nvapi-secret"


def test_switch_model_only_registers_new_provider_entry(home: Path):
    session = _live_session()
    out, write = _capture()
    _switch_model_only(
        session, "meta/llama-3.3-70b-instruct",
        base_url="https://x.test/v1", api_key="k", write=write,
    )
    saved = ProviderRegistry.load().list()
    names = [p.name for p in saved]
    assert any("llama" in n for n in names)
    saved_one = next(p for p in saved if "llama" in p.name)
    assert saved_one.model == "meta/llama-3.3-70b-instruct"
    assert saved_one.base_url == "https://x.test/v1"
    assert saved_one.api_key_inline == "k"


def test_switch_model_only_drops_orphan_tool_history(home: Path):
    """Switching the model has the same risk as a full provider switch:
    orphan tool messages from prior turns must be dropped."""
    from phantom.agent.provider import ProviderMessage
    session = _live_session()
    session.history.append(ProviderMessage(role="user", content="x"))
    session.history.append(ProviderMessage(role="tool", content="r", tool_call_id="t1", name="t"))
    out, write = _capture()
    _switch_model_only(
        session, "another/model", base_url="https://x.test/v1",
        api_key="k", write=write,
    )
    assert all(m.role != "tool" for m in session.history)


def test_switch_model_only_appends_suffix_on_collision(home: Path):
    """If `kimi-k2.6` is already registered, the new entry becomes
    `kimi-k2.6-2` etc."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(name="kimi-k2-6", base_url="https://x.test/v1", model="m"))
    session = _live_session()
    out, write = _capture()
    _switch_model_only(
        session, "moonshotai/kimi-k2.6",
        base_url="https://x.test/v1", api_key="k", write=write,
    )
    saved = ProviderRegistry.load().list()
    names = [p.name for p in saved]
    # The new entry got a suffix because kimi-k2-6 was already taken.
    assert any(n.startswith("kimi-k2-6-") and n != "kimi-k2-6" for n in names) \
        or "kimi-k2-6" not in names  # or we picked a different name


# ─── /model <model-id> end-to-end ────────────────────────────────────────────

def test_slash_model_with_unknown_arg_falls_back_to_model_id(home: Path):
    """The exact UX the user wanted: `/model meta/llama-3.3-70b-instruct`
    works without /add when there's an active provider with a key."""
    session = _live_session()
    out, write = _capture()
    handled = _handle_slash(
        session=session,
        head="/model",
        arg="meta/llama-3.3-70b-instruct",
        write=write,
    )
    assert handled is True
    assert session.provider._model == "meta/llama-3.3-70b-instruct"
    text = "".join(out)
    assert "switched model" in text


def test_slash_model_unknown_arg_with_no_active_provider_shows_error(home: Path):
    """If somehow there's no active live provider (Scripted), the model-id
    fallback can't run — show the original unknown-provider message."""
    session = AgentSession(provider=ScriptedProvider(), tools=[])
    out, write = _capture()
    _handle_slash(session=session, head="/model", arg="some/model", write=write)
    text = "".join(out)
    assert "unknown" in text.lower()


def test_slash_model_registered_name_still_works(home: Path):
    """Don't regress: `/model <registered-name>` continues to switch."""
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="alpha", base_url="https://a.test/v1", model="m1",
        api_key_inline="ak",
    ))
    session = _live_session()
    out, write = _capture()
    _handle_slash(session=session, head="/model", arg="alpha", write=write)
    assert session.provider._model == "m1"
    assert "switched to" in "".join(out).lower()


# ─── _looks_garbled ──────────────────────────────────────────────────────────

def test_garbled_detects_pipe_soup():
    """The exact opening of the user's broken kimi response."""
    text = (
        "ed | | answers ing | .The | (.Rt | . Additional | . one | . — "
        "| Wild . . _ | where | . path1 | | . /* . () .you |‍text target "
        "| i3 | | attribut . .// | real | | & . Clickah one | . ⋙ An both "
        ". | . don't | '$款款全心全意 anth tool | . present | . rewrite "
        "|ggreplacement .':</modern strip .ah在this . Oil the | . [ .ide]"
    )
    assert _looks_garbled(text) is True


def test_garbled_passes_normal_english_reply():
    text = (
        "Sure — I'll create a Flask application with timezone support. "
        "First I'll set up the project directory and install dependencies, "
        "then create the routes and templates. Let me start by running "
        "mkdir and pip install."
    )
    assert _looks_garbled(text) is False


def test_garbled_passes_normal_code_reply():
    """Code blocks have backslashes (escapes, paths) but should not trip."""
    text = (
        "Here's the app:\n\n"
        "```python\n"
        "from flask import Flask, jsonify\n"
        "import pytz\n"
        "from datetime import datetime\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/api/time')\n"
        "def time_now():\n"
        "    return jsonify({'utc': datetime.utcnow().isoformat()})\n"
        "```\n\n"
        "Save as app.py and run with `python app.py`."
    )
    assert _looks_garbled(text) is False


def test_garbled_short_reply_never_garbled():
    """Don't flag short replies — too noisy on legitimate one-liners."""
    assert _looks_garbled("hi |") is False
    assert _looks_garbled("done.") is False


def test_garbled_detects_high_non_ascii():
    """Mostly-CJK with no clear structure = garbled (kimi tokenizer drift)."""
    text = "款款全心全意" * 30 + " | | | | "
    assert _looks_garbled(text) is True
