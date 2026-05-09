"""Tests for ``phantom chat`` REPL.

The REPL is driven through :func:`phantom.cli.chat.run_repl` with
deterministic ``read_line`` / ``write`` callables, so we never need a
TTY. These verify the end-to-end loop: input → agent → output, plus
all four slash commands.
"""

from __future__ import annotations

from collections import deque

import pytest
from typer.testing import CliRunner

from phantom.agent import AgentSession, ScriptedProvider
from phantom.agent.provider import ProviderResponse
from phantom.cli import app
from phantom.cli.chat import run_repl


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / ".phantom"))
    yield


def _scripted_session(responses: list[ProviderResponse]) -> AgentSession:
    return AgentSession(
        provider=ScriptedProvider.from_responses(responses),
        tools=[],
    )


# ─── core REPL loop ──────────────────────────────────────────────────────────


class TestRunRepl:
    def test_one_turn_one_reply(self):
        session = _scripted_session([ProviderResponse(text="hi back")])
        inputs = deque(["hello\n", "/exit\n"])
        out: list[str] = []
        rc = run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        assert rc == 0
        joined = "".join(out)
        # The reply line uses a fancier prompt now (cyan/green ›) — assert on
        # the content, not the literal prompt format.
        assert "phantom" in joined and "hi back" in joined

    def test_eof_returns_zero(self):
        session = _scripted_session([])
        out: list[str] = []
        rc = run_repl(
            session,
            read_line=lambda: "",  # immediate EOF
            write=out.append,
        )
        assert rc == 0

    def test_empty_line_skipped(self):
        session = _scripted_session([ProviderResponse(text="ok")])
        inputs = deque(["\n", "hello\n", "/exit\n"])
        out: list[str] = []
        rc = run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        assert rc == 0
        # Provider should have seen exactly one user turn ("hello").
        assert session.provider.received  # type: ignore[attr-defined]
        assert len(session.provider.received) == 1  # type: ignore[attr-defined]

    def test_provider_error_does_not_crash_repl(self):
        session = _scripted_session([])  # exhausted
        inputs = deque(["hello\n", "/exit\n"])
        out: list[str] = []
        rc = run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        assert rc == 0
        # The error was surfaced.
        assert "error:" in "".join(out)


class TestSlashCommands:
    def test_help(self):
        session = _scripted_session([])
        inputs = deque(["/help\n", "/exit\n"])
        out: list[str] = []
        run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        joined = "".join(out)
        for cmd in ("/exit", "/quit", "/reset", "/history", "/help"):
            assert cmd in joined

    def test_history(self):
        session = _scripted_session([ProviderResponse(text="x")])
        inputs = deque(["hi\n", "/history\n", "/exit\n"])
        out: list[str] = []
        run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        # After one turn the history has 2 messages (user + assistant).
        assert "history length: 2" in "".join(out)

    def test_reset(self):
        session = _scripted_session([ProviderResponse(text="x")])
        inputs = deque(["hi\n", "/reset\n", "/history\n", "/exit\n"])
        out: list[str] = []
        run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        assert "history cleared" in "".join(out)
        assert "history length: 0" in "".join(out)

    def test_quit_alias(self):
        session = _scripted_session([])
        inputs = deque(["/quit\n"])
        out: list[str] = []
        rc = run_repl(
            session,
            read_line=lambda: inputs.popleft() if inputs else "",
            write=out.append,
        )
        assert rc == 0


# ─── Typer-level integration ────────────────────────────────────────────────


class TestChatCommandDispatch:
    def test_missing_base_url_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(app, ["chat", "--model", "x"])
        assert result.exit_code == 2

    def test_missing_model_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(app, ["chat", "--base-url", "https://x"])
        assert result.exit_code == 2
