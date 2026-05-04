"""Tests for :func:`phantom.agent.tool` decorator."""

from __future__ import annotations

import json

import pytest

from phantom.agent import tool
from phantom.agent.session import ToolDefinition
from phantom.errors import PhantomError


class TestToolDecoratorHappyPath:
    def test_returns_tooldefinition(self):
        @tool(
            name="echo",
            description="Echo a string back.",
            schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
        def echo(args):
            return json.dumps({"echoed": args["text"]})

        assert isinstance(echo, ToolDefinition)
        assert echo.name == "echo"
        assert echo.description == "Echo a string back."
        assert echo.input_schema["properties"]["text"]["type"] == "string"

    def test_handler_is_callable_directly(self):
        @tool(name="add", description="add", schema={"type": "object"})
        def add(args):
            return json.dumps({"sum": args["a"] + args["b"]})

        # Bypass the agent loop and call the handler directly.
        out = add.handler({"a": 2, "b": 3})
        assert json.loads(out) == {"sum": 5}

    def test_default_schema_is_object(self):
        @tool(name="x", description="x")
        def fn(args):
            return ""

        assert fn.input_schema == {"type": "object"}

    def test_to_provider_dict_shape(self):
        @tool(
            name="x", description="x",
            schema={"type": "object", "properties": {}},
        )
        def fn(args):
            return ""

        d = fn.to_provider_dict()
        assert d["type"] == "function"
        assert d["function"]["name"] == "x"


class TestToolDecoratorValidation:
    def test_empty_name_rejected_at_decoration(self):
        with pytest.raises(PhantomError, match="non-empty name"):
            @tool(name="", description="x")  # noqa: F841
            def fn(args):
                return ""

    def test_empty_description_rejected(self):
        with pytest.raises(PhantomError, match="non-empty description"):
            @tool(name="x", description="")  # noqa: F841
            def fn(args):
                return ""

    def test_non_callable_rejected(self):
        # Apply the decorator to a non-callable manually.
        decorator = tool(name="x", description="x")
        with pytest.raises(PhantomError, match="must decorate a callable"):
            decorator("not a function")  # type: ignore[arg-type]


class TestDecoratedToolWorksInSession:
    def test_session_uses_decorated_tool(self):
        from phantom.agent import AgentSession, ScriptedProvider
        from phantom.agent.provider import ProviderResponse, ToolCall

        @tool(name="ping", description="ping")
        def ping(args):
            return json.dumps({"pong": True})

        provider = ScriptedProvider.from_responses([
            ProviderResponse(
                text="",
                tool_calls=(ToolCall(id="t", name="ping", arguments={}),),
            ),
            ProviderResponse(text="done"),
        ])
        session = AgentSession(provider=provider, tools=[ping])
        out = session.respond_to("call ping")
        assert out == "done"
        # The provider's second call carries the tool result.
        tool_msg = next(m for m in provider.received[1] if m.role == "tool")
        assert json.loads(tool_msg.content) == {"pong": True}
