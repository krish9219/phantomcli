"""Provider abstraction — the LLM client.

Two concrete providers ship in v4:

* :class:`OpenAICompatibleProvider` — talks the OpenAI Chat Completions
  shape over httpx. Works with OpenAI, NVIDIA NIM, OpenRouter, Groq,
  any host that implements the spec.
* :class:`ScriptedProvider` — deterministic test double. The agent
  loop tests use it; production never does.

The :class:`Provider` Protocol is the single seam between the agent
loop and the model. Adding Anthropic-native (``messages`` API) is
mechanical.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

from phantom.errors import PhantomError

__all__ = [
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderMessage",
    "ProviderResponse",
    "ScriptedProvider",
    "ToolCall",
]


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    """One message in the conversation.

    ``role`` ∈ {"system", "user", "assistant", "tool"}. ``tool_call_id``
    is set on ``role == "tool"`` rows so the provider can correlate
    results with the assistant's tool calls.
    """

    role: str
    content: str
    tool_call_id: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """One model turn. Has either text or tool_calls (or both)."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class Provider(Protocol):
    """Minimum surface a provider must implement."""

    name: str

    def complete(
        self,
        messages: list[ProviderMessage],
        *,
        tools: list[dict[str, Any]],
    ) -> ProviderResponse: ...


# ─── ScriptedProvider (tests) ────────────────────────────────────────────────


@dataclass
class ScriptedProvider:
    """Test double. Returns a queue of pre-baked :class:`ProviderResponse`s.

    Records every message it received so tests can assert prompt shape.
    """

    name: str = "scripted"
    _responses: list[ProviderResponse] = field(default_factory=list)
    received: list[list[ProviderMessage]] = field(default_factory=list)

    @classmethod
    def from_responses(cls, responses: Iterable[ProviderResponse]) -> "ScriptedProvider":
        return cls(_responses=list(responses))

    def complete(
        self,
        messages: list[ProviderMessage],
        *,
        tools: list[dict[str, Any]],
    ) -> ProviderResponse:
        self.received.append(list(messages))
        if not self._responses:
            raise PhantomError("ScriptedProvider exhausted")
        return self._responses.pop(0)


# ─── OpenAICompatibleProvider ────────────────────────────────────────────────


class OpenAICompatibleProvider:
    """OpenAI Chat Completions client.

    Works against any host that implements the spec: OpenAI,
    NVIDIA NIM, OpenRouter, Groq, Together, Fireworks. Send the
    matching ``base_url`` + ``api_key`` + ``model``.

    The provider is **synchronous** and uses ``httpx.Client``. An
    ``httpx.AsyncClient`` variant is a v4.1 follow-up.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        name: str = "openai-compat",
        timeout_s: float = 120.0,
        client: Any = None,  # httpx.Client; injected by tests
    ) -> None:
        if not base_url:
            raise PhantomError("provider requires base_url")
        if not model:
            raise PhantomError("provider requires model")
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_s
        self._client = client  # lazy-imported below

    def _http(self) -> Any:
        if self._client is not None:
            return self._client
        import httpx  # imported lazily to keep `phantom` import cheap
        self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def complete(
        self,
        messages: list[ProviderMessage],
        *,
        tools: list[dict[str, Any]],
    ) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._encode_message(m) for m in messages],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"
        try:
            response = self._http().post(url, headers=headers, json=payload)
        except Exception as exc:  # network errors
            raise PhantomError(f"provider {self.name!r} request failed: {exc}") from exc
        if response.status_code >= 400:
            raise PhantomError(
                f"provider {self.name!r} returned {response.status_code}: "
                f"{response.text[:200]}"
            )
        data = response.json()
        return self._parse(data)

    # ─── encoding / decoding ──────────────────────────────────────────

    @staticmethod
    def _encode_message(m: ProviderMessage) -> dict[str, Any]:
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "tool":
            if not m.tool_call_id:
                raise PhantomError("tool message requires tool_call_id")
            out["tool_call_id"] = m.tool_call_id
            if m.name:
                out["name"] = m.name
        return out

    @staticmethod
    def _parse(data: dict[str, Any]) -> ProviderResponse:
        choices = data.get("choices") or []
        if not choices:
            return ProviderResponse(text="")
        choice = choices[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        finish = choice.get("finish_reason", "stop")

        raw_calls = msg.get("tool_calls") or []
        calls: list[ToolCall] = []
        for c in raw_calls:
            if not isinstance(c, dict):
                continue
            fn = c.get("function") or {}
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(
                id=str(c.get("id", "")),
                name=str(fn.get("name", "")),
                arguments=args,
            ))
        return ProviderResponse(
            text=text,
            tool_calls=tuple(calls),
            finish_reason=finish,
        )
