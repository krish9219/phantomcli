"""Tests for the v4 agent session.

These exercise the full integration: scripted provider → tool dispatch →
sandbox-mediated bash → result back into the model loop. The "real
sandbox" path runs an actual ``echo`` through Stage-1 isolation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from phantom.agent import (
    AgentSession,
    ScriptedProvider,
    ToolDefinition,
    default_tools,
)
from phantom.agent.provider import ProviderResponse, ToolCall
from phantom.errors import PhantomError
from phantom.memory import MemoryStore


sandbox_capable = pytest.mark.skipif(
    shutil.which("unshare") is None and shutil.which("bwrap") is None,
    reason="no sandbox backend available",
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


# ─── basic loop, no tools ────────────────────────────────────────────────────


class TestPlainResponse:
    def test_one_turn(self):
        provider = ScriptedProvider.from_responses([
            ProviderResponse(text="Hello, world!"),
        ])
        session = AgentSession(provider=provider, tools=[])
        out = session.respond_to("hi")
        assert out == "Hello, world!"
        assert len(session.history) == 2  # user + assistant

    def test_provider_sees_system_prompt(self):
        provider = ScriptedProvider.from_responses([
            ProviderResponse(text="ok"),
        ])
        session = AgentSession(
            provider=provider, tools=[],
            system_prompt="You are Phantom (custom).",
        )
        session.respond_to("hi")
        # The first call gets [system, user].
        first_call = provider.received[0]
        assert first_call[0].role == "system"
        assert "custom" in first_call[0].content
        assert first_call[1].role == "user"
        assert first_call[1].content == "hi"

    def test_history_carries_across_turns(self):
        provider = ScriptedProvider.from_responses([
            ProviderResponse(text="first"),
            ProviderResponse(text="second"),
        ])
        session = AgentSession(provider=provider)
        session.respond_to("turn 1")
        session.respond_to("turn 2")
        # Second call should see the full prior conversation.
        second_call = provider.received[1]
        roles = [m.role for m in second_call]
        assert roles == ["system", "user", "assistant", "user"]

    def test_empty_user_message_rejected(self):
        provider = ScriptedProvider.from_responses([])
        with pytest.raises(PhantomError, match="empty"):
            AgentSession(provider=provider).respond_to("")


# ─── tool-call dispatch ─────────────────────────────────────────────────────


class TestToolCallDispatch:
    def test_single_tool_round(self):
        # Round 1: model asks for tool. Round 2: model returns final text.
        provider = ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="tc1", name="add",
                                     arguments={"a": 2, "b": 3}),),
            ),
            ProviderResponse(text="The answer is 5."),
        ])

        def add_handler(args):
            return json.dumps({"sum": args["a"] + args["b"]})

        session = AgentSession(
            provider=provider,
            tools=[ToolDefinition(
                name="add", description="add two ints",
                input_schema={"type": "object"},
                handler=add_handler,
            )],
        )
        out = session.respond_to("what is 2 + 3?")
        assert out == "The answer is 5."
        # The provider's second call must contain the tool result.
        second = provider.received[1]
        roles = [m.role for m in second]
        assert "tool" in roles
        tool_msg = next(m for m in second if m.role == "tool")
        assert tool_msg.tool_call_id == "tc1"
        assert json.loads(tool_msg.content) == {"sum": 5}

    def test_unknown_tool_returns_error_to_model(self):
        provider = ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="tc1", name="bogus", arguments={}),),
            ),
            ProviderResponse(text="ok"),
        ])
        session = AgentSession(provider=provider, tools=[])
        session.respond_to("hi")
        second = provider.received[1]
        tool_msg = next(m for m in second if m.role == "tool")
        assert "unknown tool" in tool_msg.content

    def test_handler_exception_becomes_error_message(self):
        def boom(args):
            raise ValueError("nope")

        provider = ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="tc1", name="boom", arguments={}),),
            ),
            ProviderResponse(text="recovered"),
        ])
        session = AgentSession(
            provider=provider,
            tools=[ToolDefinition(
                name="boom", description="raise",
                input_schema={"type": "object"}, handler=boom,
            )],
        )
        out = session.respond_to("trigger")
        assert out == "recovered"
        tool_msg = next(m for m in provider.received[1] if m.role == "tool")
        assert "ValueError" in tool_msg.content

    def test_round_limit_returns_partial(self):
        # Provider keeps asking for tools forever.
        many_calls = [
            ProviderResponse(
                text=f"thought-{i}",
                tool_calls=(ToolCall(id=f"tc{i}", name="noop", arguments={}),),
            )
            for i in range(20)
        ]
        provider = ScriptedProvider.from_responses(many_calls)
        session = AgentSession(
            provider=provider,
            tools=[ToolDefinition(
                name="noop", description="no-op",
                input_schema={"type": "object"},
                handler=lambda args: json.dumps({"ok": True}),
            )],
            max_tool_rounds=3,
        )
        out = session.respond_to("loop")
        # v1.1.12 changed the marker to include the round count and a hint;
        # the stable substring is "tool-round limit".
        assert "tool-round limit" in out


class TestDuplicateToolNamesRejected:
    def test_construction_rejects(self):
        with pytest.raises(PhantomError, match="duplicate"):
            AgentSession(
                provider=ScriptedProvider.from_responses([]),
                tools=[
                    ToolDefinition(name="x", description="a",
                                   input_schema={"type": "object"},
                                   handler=lambda a: ""),
                    ToolDefinition(name="x", description="b",
                                   input_schema={"type": "object"},
                                   handler=lambda a: ""),
                ],
            )


# ─── full integration: sandboxed bash from a model tool call ────────────────


@sandbox_capable
class TestRealSandboxIntegration:
    def test_run_bash_round_trip(self, tmp_path):
        # The model "decides" to run echo via the run_bash tool, then
        # summarises. The default_tools()['run_bash'] handler routes
        # through phantom.engine.execute_bash which actually runs the
        # command in the sandbox.
        provider = ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(
                    id="tc1",
                    name="run_bash",
                    arguments={"command": "echo agent-loop-ok"},
                ),),
            ),
            ProviderResponse(text="The command printed agent-loop-ok."),
        ])
        session = AgentSession(
            provider=provider,
            tools=default_tools(workdir=str(tmp_path)),
        )
        out = session.respond_to("please run echo agent-loop-ok")
        assert "agent-loop-ok" in out
        # The tool result fed back to the model should carry the
        # sandbox's actual stdout.
        tool_msg = next(m for m in provider.received[1] if m.role == "tool")
        result = json.loads(tool_msg.content)
        assert result["exit_code"] == 0
        assert "agent-loop-ok" in result["stdout"]
        assert result["tier"] in {"bwrap", "firejail", "unshare", "docker"}


@sandbox_capable
class TestMemoryIntegration:
    def test_memory_add_then_search(self, tmp_path):
        # memory_add → memory_search round-trip.
        store = MemoryStore.open(tmp_path / "m.db")
        try:
            ns = {"user": "u", "project": "p", "session": "s"}
            tools = default_tools(workdir=str(tmp_path), memory=store, namespace=ns)

            provider = ScriptedProvider.from_responses([
                # Round 1: add a note.
                ProviderResponse(
                    text="",
                    tool_calls=(ToolCall(
                        id="add1", name="memory_add",
                        arguments={"text": "Phantom uses bubblewrap as its sandbox tier 1."},
                    ),),
                ),
                # Round 2: search.
                ProviderResponse(
                    text="",
                    tool_calls=(ToolCall(
                        id="srch1", name="memory_search",
                        arguments={"query": "bubblewrap sandbox", "top_k": 5},
                    ),),
                ),
                # Round 3: final.
                ProviderResponse(text="I added that note and confirmed it."),
            ])
            session = AgentSession(provider=provider, tools=tools)
            out = session.respond_to("remember that and then check")
            assert "added" in out

            # The third call's history should include both tool results.
            third = provider.received[2]
            tool_msgs = [m for m in third if m.role == "tool"]
            assert len(tool_msgs) == 2
            # Search result must contain the note we just added.
            search_payload = json.loads(tool_msgs[1].content)
            assert any("bubblewrap" in m["text"] for m in search_payload)
        finally:
            store.close()


# ─── Provider error surface ─────────────────────────────────────────────────


class TestProviderErrorWrapping:
    def test_provider_exception_wrapped_as_phantom_error(self):
        class _Boom:
            name = "boom"
            def complete(self, msgs, *, tools): raise RuntimeError("net down")

        session = AgentSession(provider=_Boom())
        with pytest.raises(PhantomError, match="provider call failed"):
            session.respond_to("hi")


# ─── Default system prompt teaches surgical-fix behaviour ───────────────────


class TestDefaultSystemPrompt:
    """The default system prompt is the contract Phantom makes with every
    model it talks to. If this regresses, the model stops preferring
    edit_file over write_file and bug fixes turn into whole-file
    rewrites — exactly what users complain about with weaker agents."""

    def test_default_prompt_mentions_edit_file_preference(self):
        from phantom.agent.session import DEFAULT_SYSTEM_PROMPT
        assert "edit_file" in DEFAULT_SYSTEM_PROMPT
        assert "write_file" in DEFAULT_SYSTEM_PROMPT
        # Surgical-fix philosophy keywords.
        assert "minimum change" in DEFAULT_SYSTEM_PROMPT.lower()
        assert "root cause" in DEFAULT_SYSTEM_PROMPT.lower()

    def test_session_uses_default_prompt_when_unspecified(self):
        from phantom.agent.session import DEFAULT_SYSTEM_PROMPT

        class _Stub:
            name = "stub"
            def complete(self, msgs, *, tools):
                return ProviderResponse(text="ok", tool_calls=())

        session = AgentSession(provider=_Stub())
        assert session.system_prompt == DEFAULT_SYSTEM_PROMPT

    def test_session_system_prompt_overridable(self):
        class _Stub:
            name = "stub"
            def complete(self, msgs, *, tools):
                return ProviderResponse(text="ok", tool_calls=())

        custom = "You are a code reviewer."
        session = AgentSession(provider=_Stub(), system_prompt=custom)
        assert session.system_prompt == custom
