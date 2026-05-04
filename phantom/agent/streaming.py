"""Streaming response support for OpenAI-compatible providers.

Real LLMs return tokens incrementally; the user sees them appear as the
model writes them. The Stage-4 :class:`Provider` API was synchronous
``complete()``-only — fine for tool-calling agent loops where the
final-text turn is short, but unworkable for "explain this to me" turns
that take 20+ seconds.

This module adds a non-breaking ``stream()`` method on
:class:`phantom.agent.provider.OpenAICompatibleProvider`. Existing
callers continue to use ``complete()``; new callers (chat REPL, web
dashboard) opt into ``stream()`` and yield chunks.

Wire format
-----------

OpenAI-compatible servers stream Server-Sent Events. Each event line is
``data: {json}\\n\\n`` with a final ``data: [DONE]`` sentinel. The JSON
body has a ``choices[0].delta`` shape that incrementally builds the
final message: ``delta.content`` for text chunks, ``delta.tool_calls``
for incremental tool-call assembly.

We parse SSE with a tiny line-buffer rather than pulling in
``sseclient`` or ``httpx-sse`` — the format is simple and a third-party
dep would be larger than the parser.

Public types
------------

:class:`StreamChunk` — one decoded event:

* ``text``       — delta text (possibly empty if this chunk is a
                   tool-call delta or a keep-alive).
* ``tool_call``  — :class:`ToolCallDelta` with the cumulative shape so
                   far. Yielded as soon as a delta is observed; the
                   *complete* tool call appears in the *final*
                   :class:`ProviderResponse` returned by ``stream()``.
* ``done``       — True on the final chunk; never True before.

:class:`ToolCallDelta` — incremental tool-call accumulator. Identical
shape to :class:`ToolCall` once complete.

The :func:`stream` method's iterator yields :class:`StreamChunk`
objects until done. Callers reduce them into a
:class:`ProviderResponse` themselves, or use :func:`drain_stream` for
the convenience.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
    ProviderResponse,
    ToolCall,
)
from phantom.errors import PhantomError

__all__ = [
    "StreamChunk",
    "ToolCallDelta",
    "drain_stream",
    "stream",
    "stream_provider",
]


# ─── data types ──────────────────────────────────────────────────────────────


@dataclass
class ToolCallDelta:
    """Cumulative tool-call as the model emits it piece by piece.

    The id + name typically arrive together in the first delta. The
    ``arguments`` JSON string accumulates across many deltas; we
    parse it only when the stream finishes, because partial JSON is
    not parseable.
    """

    id: str = ""
    name: str = ""
    arguments_raw: str = ""

    def merge(self, other: "ToolCallDelta") -> None:
        if other.id and not self.id:
            self.id = other.id
        if other.name and not self.name:
            self.name = other.name
        self.arguments_raw += other.arguments_raw

    def finalize(self) -> ToolCall:
        try:
            args = json.loads(self.arguments_raw) if self.arguments_raw else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}
        return ToolCall(id=self.id, name=self.name, arguments=args)


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One observed event from the SSE stream."""

    text: str = ""
    tool_call: ToolCallDelta | None = None
    done: bool = False
    finish_reason: str = ""


# ─── parser ──────────────────────────────────────────────────────────────────


def _iter_sse_events(line_iter: Iterator[bytes | str]) -> Iterator[str]:
    """Yield each ``data: ...`` payload from an SSE byte stream.

    SSE comments (``: ...``), keep-alives, and unknown event types are
    silently dropped. Lines are joined per the spec: a single event
    may span multiple ``data:`` lines, with ``\\n`` between them.
    """
    buffer: list[str] = []
    for raw in line_iter:
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace")
        else:
            line = raw
        line = line.rstrip("\r\n")

        if line == "":
            if buffer:
                yield "\n".join(buffer)
                buffer = []
            continue
        if line.startswith(":"):
            # SSE comment / keep-alive.
            continue
        if line.startswith("data: "):
            buffer.append(line[6:])
        elif line.startswith("data:"):
            buffer.append(line[5:])
        # Other field types (id:, event:, retry:) are ignored.
    if buffer:
        yield "\n".join(buffer)


def _parse_chunk(payload: str) -> StreamChunk:
    """Parse one SSE data payload into a :class:`StreamChunk`."""
    payload = payload.strip()
    if payload == "[DONE]":
        return StreamChunk(done=True)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return StreamChunk()  # silently drop malformed chunks

    choices = data.get("choices") or []
    if not choices:
        return StreamChunk()
    choice = choices[0]
    finish = choice.get("finish_reason") or ""
    delta = choice.get("delta") or {}
    text = delta.get("content") or ""

    raw_tool_calls = delta.get("tool_calls") or []
    if raw_tool_calls:
        # OpenAI streams one tool_call delta at a time with an
        # ``index`` discriminator; we only care about index 0 for now
        # — most agents emit tools sequentially. Multi-tool parallel
        # streaming is a v4.2 follow-up.
        first = raw_tool_calls[0] if isinstance(raw_tool_calls[0], dict) else {}
        fn = first.get("function") or {}
        return StreamChunk(
            text=text,
            tool_call=ToolCallDelta(
                id=str(first.get("id", "")),
                name=str(fn.get("name", "")),
                arguments_raw=str(fn.get("arguments", "")),
            ),
            finish_reason=finish,
        )

    return StreamChunk(text=text, finish_reason=finish)


# ─── public stream method ───────────────────────────────────────────────────


def stream(
    provider: OpenAICompatibleProvider,
    messages: list[ProviderMessage],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> Iterator[StreamChunk]:
    """Stream chunks from an OpenAI-compatible provider.

    Yields :class:`StreamChunk` objects as the server emits SSE
    events. The final yielded chunk has ``done=True``.

    Network errors raise :class:`phantom.errors.PhantomError`; SSE
    parsing failures are silently dropped (the stream just yields
    fewer events). HTTP 4xx / 5xx wraps to PhantomError on first read.
    """
    payload: dict[str, Any] = {
        "model": provider._model,  # noqa: SLF001 — colocated module
        "messages": [provider._encode_message(m) for m in messages],  # noqa: SLF001
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if provider._api_key:  # noqa: SLF001
        headers["Authorization"] = f"Bearer {provider._api_key}"  # noqa: SLF001

    url = f"{provider._base_url}/chat/completions"  # noqa: SLF001
    client = provider._http()  # noqa: SLF001

    try:
        with client.stream(
            "POST", url, headers=headers, json=payload,
        ) as response:
            if response.status_code >= 400:
                # Pull the body for diagnostics, then raise.
                body = b"".join(response.iter_bytes()).decode("utf-8", errors="replace")
                raise PhantomError(
                    f"provider {provider.name!r} stream returned "
                    f"{response.status_code}: {body[:200]}"
                )
            yielded_done = False
            for payload_str in _iter_sse_events(response.iter_lines()):
                chunk = _parse_chunk(payload_str)
                if chunk.done:
                    yielded_done = True
                yield chunk
                if chunk.done:
                    return
            if not yielded_done:
                # Some servers close the stream without an explicit
                # [DONE]. Surface a synthetic done so callers can
                # rely on the contract.
                yield StreamChunk(done=True)
    except PhantomError:
        raise
    except Exception as exc:
        raise PhantomError(
            f"provider {provider.name!r} stream failed: {exc}"
        ) from exc


def drain_stream(
    chunks: Iterator[StreamChunk],
) -> ProviderResponse:
    """Reduce an iterator of :class:`StreamChunk` into one
    :class:`ProviderResponse`.

    Use this when the caller just wants the final assembled response
    and not the chunk-by-chunk feed (e.g. when piping into the
    standard agent loop).
    """
    text_parts: list[str] = []
    tool_acc: ToolCallDelta | None = None
    finish = "stop"
    for chunk in chunks:
        if chunk.text:
            text_parts.append(chunk.text)
        if chunk.tool_call is not None:
            if tool_acc is None:
                tool_acc = ToolCallDelta()
            tool_acc.merge(chunk.tool_call)
        if chunk.finish_reason:
            finish = chunk.finish_reason
        if chunk.done:
            break
    tool_calls: tuple[ToolCall, ...] = ()
    if tool_acc is not None:
        tool_calls = (tool_acc.finalize(),)
    return ProviderResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        finish_reason=finish,
    )


def stream_provider(
    provider: OpenAICompatibleProvider,
    messages: list[ProviderMessage],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[Iterator[StreamChunk], ProviderResponse]:
    """Convenience: stream + drain in one call.

    Returns the iterator (already consumed once but re-iterable as a
    list snapshot) and the final assembled response. Tests prefer
    :func:`stream` directly so they can observe each chunk.
    """
    chunks = list(stream(provider, messages, tools=tools or []))
    return iter(chunks), drain_stream(iter(chunks))
