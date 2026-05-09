"""Tests for v1.1.15 dual-model mode (planner + executor).

The user requested: use one model that's strong at writing code (kimi,
qwen3-coder, deepseek) for the plan, and a second model with reliable
OpenAI-format tool calling (llama-3.3, llama-4-maverick) to actually
write the files and run the commands.

Pattern: on each user turn,
  Stage 1 — call coder model, NO tools, get plan + code text.
  Stage 2 — call executor model, WITH tools, prepending the coder
            output so the executor materialises the files + runs cmds.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from phantom.cli.chat import (
    _CODER_SYSTEM_PROMPT,
    _EXECUTOR_SYSTEM_PROMPT_PREFIX,
    _handle_slash,
    _resolve_provider_or_model_arg,
    _run_coder_stage,
)
from phantom.config.providers import CustomProvider, ProviderRegistry
from phantom.profile import Profile, load_profile, save_profile


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path))
    # Pre-seed a default provider so /coder + /executor have an endpoint
    # + key to clone when given a raw model id.
    reg = ProviderRegistry.load()
    reg.add(CustomProvider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        model="meta/llama-3.3-70b-instruct",
        api_key_inline="nvapi-test",
    ))
    return tmp_path


def _capture():
    out: list[str] = []
    return out, out.append


def _scripted_session():
    from phantom.agent import AgentSession, ScriptedProvider
    return AgentSession(provider=ScriptedProvider(), tools=[])


# ─── Profile fields persist ─────────────────────────────────────────────────

def test_profile_serializes_dual_mode_fields(home):
    p = Profile(
        user_name="Aravind", assistant_name="Ghost", workspace_path="/x",
        coder_provider="kimi", executor_provider="llama", dual_mode=True,
    )
    save_profile(p)
    again = load_profile()
    assert again.coder_provider == "kimi"
    assert again.executor_provider == "llama"
    assert again.dual_mode is True


def test_profile_back_compat_old_files_have_no_dual_fields(home, tmp_path):
    """A profile.json saved by v1.1.10 won't have the new fields. Loader
    must default them to empty / False."""
    p = home / "profile.json"
    p.write_text(json.dumps({
        "user_name": "Aravind", "assistant_name": "Ghost",
        "workspace_path": "/x", "first_seen": "2026-05-09T00:00:00Z",
    }))
    loaded = load_profile()
    assert loaded.coder_provider == ""
    assert loaded.executor_provider == ""
    assert loaded.dual_mode is False


# ─── /coder and /executor accept registered names ────────────────────────────

def test_slash_coder_with_registered_name(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/coder", arg="nvidia", write=write)
    assert load_profile().coder_provider == "nvidia"


def test_slash_executor_with_registered_name(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/executor", arg="nvidia", write=write)
    assert load_profile().executor_provider == "nvidia"


# ─── /coder + /executor accept raw model ids (auto-clone endpoint) ──────────

def test_slash_coder_with_raw_model_id_clones_default(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(
        session=session, head="/coder",
        arg="qwen/qwen3-coder-480b-a35b-instruct", write=write,
    )
    profile = load_profile()
    assert profile.coder_provider != ""
    new_provider = ProviderRegistry.load().get(profile.coder_provider)
    assert new_provider is not None
    assert new_provider.model == "qwen/qwen3-coder-480b-a35b-instruct"
    assert new_provider.base_url == "https://integrate.api.nvidia.com/v1"
    assert new_provider.api_key_inline == "nvapi-test"


def test_slash_executor_with_raw_model_id(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(
        session=session, head="/executor",
        arg="meta/llama-3.3-70b-instruct", write=write,
    )
    profile = load_profile()
    assert profile.executor_provider != ""
    p = ProviderRegistry.load().get(profile.executor_provider)
    assert p.model == "meta/llama-3.3-70b-instruct"


# ─── /dual on requires both halves set ──────────────────────────────────────

def test_slash_dual_on_without_both_halves_warns(home):
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/dual", arg="on", write=write)
    text = "".join(out)
    assert "set /coder and /executor" in text
    assert load_profile().dual_mode is False  # not flipped


def test_slash_dual_on_succeeds_when_both_set(home):
    save_profile(Profile(
        user_name="A", assistant_name="P", workspace_path="/x",
        coder_provider="nvidia", executor_provider="nvidia",
    ))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/dual", arg="on", write=write)
    assert load_profile().dual_mode is True


def test_slash_dual_off_disables(home):
    save_profile(Profile(
        user_name="A", assistant_name="P", workspace_path="/x",
        coder_provider="nvidia", executor_provider="nvidia",
        dual_mode=True,
    ))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/dual", arg="off", write=write)
    assert load_profile().dual_mode is False


def test_slash_dual_no_arg_reports_status(home):
    save_profile(Profile(
        user_name="A", assistant_name="P", workspace_path="/x",
        coder_provider="kimi", executor_provider="llama", dual_mode=True,
    ))
    session = _scripted_session()
    out, write = _capture()
    _handle_slash(session=session, head="/dual", arg="", write=write)
    text = "".join(out)
    assert "kimi" in text
    assert "llama" in text


# ─── _resolve_provider_or_model_arg ──────────────────────────────────────────

def test_resolve_returns_provider_name_directly_when_registered(home):
    out, write = _capture()
    ok, name = _resolve_provider_or_model_arg("nvidia", write)
    assert ok is True
    assert name == "nvidia"


def test_resolve_clones_default_for_unknown_model_id(home):
    out, write = _capture()
    ok, name = _resolve_provider_or_model_arg(
        "deepseek-ai/deepseek-v4-pro", write,
    )
    assert ok is True
    assert name != "nvidia"  # got a new entry
    saved = ProviderRegistry.load().get(name)
    assert saved.model == "deepseek-ai/deepseek-v4-pro"


def test_resolve_fails_when_no_default_to_clone(home, monkeypatch):
    """Wipe the registry → no default to clone → /coder <model-id> fails
    cleanly with a hint."""
    (home / "providers.json").unlink()
    out, write = _capture()
    ok, name = _resolve_provider_or_model_arg("some/model", write)
    assert ok is False
    assert "no providers" in "".join(out).lower()


# ─── _run_coder_stage ────────────────────────────────────────────────────────

def test_run_coder_stage_calls_coder_with_no_tools(home, monkeypatch):
    """The coder must be invoked WITHOUT tools (it's pure code generation)
    and its raw text must be returned."""
    fake_text = (
        "I'll create a Flask app.\n\n"
        "```python file=app.py\n"
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "```\n\n"
        "$ pip install flask\n"
        "$ python app.py"
    )

    captured = {}
    def fake_post(url, headers, json):
        captured["url"] = url
        captured["payload"] = json
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            "choices": [{
                "message": {"content": fake_text, "tool_calls": []},
                "finish_reason": "stop",
            }],
        }
        return r

    fake_client = MagicMock()
    fake_client.post.side_effect = fake_post
    monkeypatch.setattr(
        "httpx.Client", lambda *a, **kw: fake_client,
    )

    out, write = _capture()
    text = _run_coder_stage(
        user_prompt="create a flask app",
        coder_provider_name="nvidia",
        write=write,
    )
    assert text == fake_text
    # The payload must NOT have tools — the coder is pure generation.
    assert "tools" not in captured["payload"]
    # Coder system prompt was sent.
    sys_msg = captured["payload"]["messages"][0]
    assert sys_msg["role"] == "system"
    assert "planner/coder role" in sys_msg["content"]


def test_run_coder_stage_raises_on_unknown_provider(home):
    out, write = _capture()
    from phantom.errors import PhantomError
    with pytest.raises(PhantomError, match="not found"):
        _run_coder_stage(
            user_prompt="x", coder_provider_name="ghost", write=write,
        )


# ─── Smoke: the executor prompt prefix is what we pass at runtime ────────────

def test_executor_prefix_is_well_formed():
    """Sanity: the executor system prefix mentions the role + tools."""
    assert "executor" in _EXECUTOR_SYSTEM_PROMPT_PREFIX.lower()
    assert "write each file" in _EXECUTOR_SYSTEM_PROMPT_PREFIX.lower() \
        or "write_file" in _EXECUTOR_SYSTEM_PROMPT_PREFIX.lower() \
        or "tools" in _EXECUTOR_SYSTEM_PROMPT_PREFIX.lower()


def test_coder_prefix_says_no_tool_calls():
    assert "no tool calls" in _CODER_SYSTEM_PROMPT.lower()
    # And the file format is documented.
    assert "file=" in _CODER_SYSTEM_PROMPT
