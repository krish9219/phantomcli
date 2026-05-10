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


class _ToolsNotSupported(Exception):
    """Internal: raised by _post_chat when the model rejects the tool payload.

    OpenAICompatibleProvider catches this, latches off tool support for the
    rest of the session, and retries the same prompt without tools.
    """


_TOOL_REJECTION_HINTS = (
    "object of type undefined",          # NVIDIA NIM minimax bug
    "tools are not supported",
    "tool_calls is not supported",
    "tool_choice is not supported",
    "this model does not support tool",
    "function calling is not supported",
    "tool_use_failed",
    "no tool support",
    "tools parameter",
)


def _looks_like_tool_rejection(body: str) -> bool:
    """Pattern-match the response body for known 'no tools' error shapes.

    We only call this when *tools* were in the payload. False positives
    would just trigger a retry without tools, which is harmless on a
    transient 5xx — the retry either succeeds or raises with the real
    error message preserved.
    """
    low = body.lower()
    return any(h in low for h in _TOOL_REJECTION_HINTS)


# ─── inline tool-call extraction (kimi/minimax delimited format) ──────────────

import re as _re  # local alias so the public re import stays where the body uses it

_KIMI_BLOCK = _re.compile(
    r"<\|tool_calls_section_begin\|>(.*?)<\|tool_calls_section_end\|>",
    _re.DOTALL,
)
_KIMI_CALL = _re.compile(
    r"<\|tool_call_begin\|>?\s*(.*?)\s*<\|tool_call_end\|>",
    _re.DOTALL,
)
# Each call body looks like: ``functions.run_bash:{"command": "..."}``.
_KIMI_CALL_HEAD = _re.compile(
    r"^(?:functions\.)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(?P<json>\{.*\})\s*$",
    _re.DOTALL,
)


def _extract_inline_tool_calls(text: str) -> tuple[list["ToolCall"], str]:
    """Pull tool calls out of kimi/minimax-style delimited text.

    Returns (calls, text_with_markers_stripped). If no markers are found,
    returns ([], original_text). Each call gets a synthetic id so the
    downstream tool-result message can reference it.
    """
    if not text or "<|tool_call" not in text:
        return [], text

    blocks = _KIMI_BLOCK.findall(text)
    cleaned = _KIMI_BLOCK.sub("", text).strip()

    calls: list[ToolCall] = []
    for i, block in enumerate(blocks):
        for j, raw in enumerate(_KIMI_CALL.findall(block)):
            m = _KIMI_CALL_HEAD.search(raw.strip())
            if not m:
                continue
            try:
                args = json.loads(m.group("json"))
            except json.JSONDecodeError:
                continue
            if not isinstance(args, dict):
                continue
            calls.append(ToolCall(
                id=f"inline-{i}-{j}",
                name=m.group("name"),
                arguments=args,
            ))

    return calls, cleaned


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
        on_chunk: Any = None,  # Callable[[str], None] | None
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
        on_chunk: Any = None,
    ) -> ProviderResponse:
        self.received.append(list(messages))
        if not self._responses:
            raise PhantomError("ScriptedProvider exhausted")
        response = self._responses.pop(0)
        # Tests that pass on_chunk get a single chunk callback with
        # the full text — keeps streaming-aware code paths exercised.
        if on_chunk is not None and response.text:
            try:
                on_chunk(response.text)
            except Exception:
                pass
        return response


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
        timeout_s: float = 0.0,  # 0 means: read PHANTOM_HTTP_TIMEOUT_S, default 60
        client: Any = None,  # httpx.Client; injected by tests
        tools_supported: bool = True,
    ) -> None:
        if not base_url:
            raise PhantomError("provider requires base_url")
        if not model:
            raise PhantomError("provider requires model")
        import os as _os
        if timeout_s <= 0:
            try:
                timeout_s = float(_os.environ.get("PHANTOM_HTTP_TIMEOUT_S", "60"))
            except ValueError:
                timeout_s = 60.0
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_s
        self._client = client  # lazy-imported below
        self._tools_supported = tools_supported  # latched off on 5xx with tools
        self._tools_warning_sink: Any = None  # callable(str) — set by chat REPL

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
        on_chunk: Any = None,
    ) -> ProviderResponse:
        send_tools = bool(tools) and self._tools_supported
        try:
            return self._post_chat(messages, tools if send_tools else [], on_chunk=on_chunk)
        except _ToolsNotSupported as e:
            self._tools_supported = False
            self._notify(
                f"  ⚠ provider {self.name!r} doesn't accept tools "
                f"(model {self._model!r}); falling back to chat-only mode."
            )
            return self._post_chat(messages, [], on_chunk=on_chunk)

    def _post_chat(
        self,
        messages: list[ProviderMessage],
        tools: list[dict[str, Any]],
        on_chunk: Any = None,
    ) -> ProviderResponse:
        # When tools are off, scrub the orphaned residue of prior
        # tool-call rounds: any role="tool" message, and any empty
        # assistant message (those wrap a tool_calls payload that the
        # encoder drops). Otherwise NVIDIA / OpenAI reject with
        # "tool role with no preceding assistant tool call" — the
        # orphans survive a mid-session tools-fallback latch.
        if not tools:
            messages = [
                m for m in messages
                if m.role != "tool"
                and not (m.role == "assistant" and not m.content.strip())
            ]

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

        # ─── streaming branch ────────────────────────────────────────────────
        # When the caller passed an on_chunk callback, request the server-
        # sent-events stream and dispatch text deltas live. Tool-call deltas
        # are accumulated and surfaced once the stream completes. This is
        # the path the chat REPL uses for token-by-token output.
        if on_chunk is not None:
            payload["stream"] = True
            return self._stream_chat(url, headers, payload, tools, on_chunk)

        # 429 retry with backoff. NVIDIA's free tier throttles aggressively
        # in chat sessions; one short retry recovers ~half the cases.
        attempts = 2
        for attempt_idx in range(attempts):
            try:
                response = self._http().post(url, headers=headers, json=payload)
            except Exception as exc:
                cls = type(exc).__name__
                if "Timeout" in cls or "timeout" in str(exc).lower():
                    raise PhantomError(
                        f"provider {self.name!r} timed out after {self._timeout:.0f}s "
                        f"(model={self._model!r}). The model may be stuck or NVIDIA's "
                        f"gateway is holding the connection. Try /reset and switching "
                        f"to a faster model with /model meta_llama-3.3-70b-instruct, "
                        f"or raise PHANTOM_HTTP_TIMEOUT_S to allow longer waits."
                    ) from exc
                raise PhantomError(f"provider {self.name!r} request failed: {exc}") from exc

            # 429 retry path: one short backoff with jitter.
            if response.status_code == 429 and attempt_idx + 1 < attempts:
                # Honour Retry-After if the server sent one (capped at 10s).
                retry_after = 0.0
                try:
                    retry_after = float(response.headers.get("Retry-After", "0"))
                except (ValueError, TypeError):
                    retry_after = 0.0
                import random as _random
                import time as _time
                wait = max(0.5, min(retry_after, 10.0)) if retry_after else 1.5 + _random.random()
                self._notify(
                    f"  ⚠ provider {self.name!r} rate-limited (429); "
                    f"retrying in {wait:.1f}s…"
                )
                _time.sleep(wait)
                continue
            break

        if response.status_code >= 400:
            body = response.text[:300]
            if (
                tools
                and response.status_code in (400, 422, 500, 502, 503)
                and _looks_like_tool_rejection(body)
            ):
                raise _ToolsNotSupported(body)
            if response.status_code == 429:
                raise PhantomError(
                    f"provider {self.name!r} rate-limited (429) on {self._model!r} "
                    f"after a retry. NVIDIA's free tier throttles bursty traffic — "
                    f"wait ~30 seconds, or switch to a less-loaded model with "
                    f"/model meta/llama-3.3-70b-instruct."
                )
            raise PhantomError(
                f"provider {self.name!r} returned {response.status_code}: {body}"
            )
        data = response.json()
        return self._parse(data)

    def _stream_chat(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        tools: list[dict[str, Any]],
        on_chunk: Any,
    ) -> ProviderResponse:
        """SSE-stream the chat completion and dispatch text deltas live.

        Returns a final ProviderResponse with the full text + any
        tool calls that arrived. Calls ``on_chunk(text)`` for each
        text-content delta. Tool-call args are accumulated string
        fragments and parsed once the stream closes.

        Falls back to non-streaming (recursive call without on_chunk)
        if the server rejects ``stream=true``.
        """
        client = self._http()
        accumulated_text: list[str] = []
        # tool_calls indexed by `index` (the position in the parallel-call
        # list). Each value collects {id, name, arguments_str}.
        tool_call_state: dict[int, dict[str, str]] = {}
        finish_reason = "stop"

        try:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 429:
                    # 429 during stream-open — let the non-streaming path
                    # handle the retry. Read the body so the connection
                    # closes cleanly.
                    try:
                        resp.read()
                    except Exception:
                        pass
                    raise PhantomError(
                        f"provider {self.name!r} rate-limited (429) on "
                        f"{self._model!r}. Wait ~30s or switch model."
                    )
                if resp.status_code >= 400:
                    body = b"".join(resp.iter_bytes(8192))[:300].decode("utf-8", "replace")
                    if (
                        tools
                        and resp.status_code in (400, 422, 500, 502, 503)
                        and _looks_like_tool_rejection(body)
                    ):
                        raise _ToolsNotSupported(body)
                    raise PhantomError(
                        f"provider {self.name!r} returned {resp.status_code}: {body}"
                    )

                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {})

                    # Text content delta
                    text_piece = delta.get("content") or ""
                    if text_piece:
                        accumulated_text.append(text_piece)
                        try:
                            on_chunk(text_piece)
                        except Exception:
                            pass

                    # Tool-call deltas (accumulated by index)
                    for tc_delta in delta.get("tool_calls") or []:
                        idx = tc_delta.get("index", 0)
                        slot = tool_call_state.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc_delta.get("id"):
                            slot["id"] = tc_delta["id"]
                        fn = tc_delta.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
        except _ToolsNotSupported:
            raise
        except PhantomError:
            raise
        except Exception as exc:
            cls = type(exc).__name__
            if "Timeout" in cls or "timeout" in str(exc).lower():
                raise PhantomError(
                    f"provider {self.name!r} stream timed out after "
                    f"{self._timeout:.0f}s (model={self._model!r}). "
                    f"Try /model meta/llama-3.3-70b-instruct or raise "
                    f"PHANTOM_HTTP_TIMEOUT_S."
                ) from exc
            raise PhantomError(f"provider {self.name!r} stream failed: {exc}") from exc

        # Build the final ProviderResponse from accumulated state.
        full_text = "".join(accumulated_text)
        calls: list[ToolCall] = []
        for idx in sorted(tool_call_state.keys()):
            slot = tool_call_state[idx]
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"].strip() else {}
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(
                id=slot["id"] or f"stream-{idx}",
                name=slot["name"] or "",
                arguments=args,
            ))

        # Same inline-tool-call extraction as the non-streaming path:
        # some models emit kimi-style markers in the text content even
        # when streaming.
        if not calls:
            extracted, cleaned_text = _extract_inline_tool_calls(full_text)
            if extracted:
                calls.extend(extracted)
                full_text = cleaned_text

        return ProviderResponse(
            text=full_text,
            tool_calls=tuple(calls),
            finish_reason=finish_reason,
        )

    def set_tools_warning_sink(self, fn: Any) -> None:
        """Register a callable(str) the provider calls when it disables tools.

        The chat REPL passes a small printer here so the user sees the fallback
        notice inline. None / unset is fine — silent fallback.
        """
        self._tools_warning_sink = fn

    def _notify(self, msg: str) -> None:
        if self._tools_warning_sink is not None:
            try:
                self._tools_warning_sink(msg)
            except Exception:
                pass

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

        # Some NVIDIA-hosted models (kimi-k2.6, minimax) emit tool calls in
        # their native delimiter format inside the text content instead of
        # the OpenAI tool_calls array. Extract them so the agent loop can
        # actually run them. The cleaned text (with the markers stripped)
        # is what the user sees as the assistant turn.
        if not calls:
            extracted, cleaned = _extract_inline_tool_calls(text)
            if extracted:
                calls.extend(extracted)
                text = cleaned

        return ProviderResponse(
            text=text,
            tool_calls=tuple(calls),
            finish_reason=finish,
        )
