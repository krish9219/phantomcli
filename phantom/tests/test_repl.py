"""Tests for the phantom shell REPL (phantom/cli/repl.py)."""

from __future__ import annotations

import io
import sys

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_HOME", str(tmp_path / "phantom_home"))
    import importlib
    import phantom.licensing as licensing
    importlib.reload(licensing)
    return tmp_path


def _run_repl_with_input(monkeypatch, capsys, lines: list[str]) -> tuple[str, str]:
    """Drive run_repl() with a scripted list of lines. Returns (stdout, stderr)."""
    import phantom.cli.repl as repl

    feed = iter(lines)
    def fake_reader():
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(repl, "_make_prompt", lambda _path: fake_reader)
    repl.run_repl()
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_exit_terminates_loop(isolated_home, monkeypatch, capsys):
    out, err = _run_repl_with_input(monkeypatch, capsys, ["exit"])
    assert "Phantom v" in err  # banner went to stderr
    # No error printed; loop exited cleanly.


def test_quit_terminates_loop(isolated_home, monkeypatch, capsys):
    _run_repl_with_input(monkeypatch, capsys, ["quit"])


def test_eof_terminates_loop(isolated_home, monkeypatch, capsys):
    """Empty input list -> EOFError -> clean exit."""
    _run_repl_with_input(monkeypatch, capsys, [])


def test_blank_lines_are_skipped(isolated_home, monkeypatch, capsys):
    out, err = _run_repl_with_input(monkeypatch, capsys, ["", "   ", "version", "exit"])
    assert "1.1.0" in out


def test_version_subcommand_dispatches(isolated_home, monkeypatch, capsys):
    out, err = _run_repl_with_input(monkeypatch, capsys, ["version", "exit"])
    assert "1.1.0" in out


def test_help_does_not_kill_loop(isolated_home, monkeypatch, capsys):
    """`help` invokes --help (which raises SystemExit internally); loop must survive."""
    out, err = _run_repl_with_input(monkeypatch, capsys, ["help", "version", "exit"])
    # version still ran AFTER help — proves the loop didn't die.
    assert "1.1.0" in out


def test_unknown_command_does_not_kill_loop(isolated_home, monkeypatch, capsys):
    out, err = _run_repl_with_input(
        monkeypatch, capsys, ["definitely_not_a_command", "version", "exit"],
    )
    assert "1.1.0" in out


def test_parse_error_is_reported(isolated_home, monkeypatch, capsys):
    """Unbalanced quote -> shlex raises -> reported, loop continues."""
    out, err = _run_repl_with_input(
        monkeypatch, capsys, ['echo "unterminated', "version", "exit"],
    )
    assert "parse error" in err
    assert "1.1.0" in out


def test_pro_gate_inside_repl_does_not_kill_loop(isolated_home, monkeypatch, capsys):
    """A free-tier user invoking a Pro command should see the gate and stay in the loop."""
    from datetime import datetime, timedelta, timezone
    from phantom import licensing

    licensing.license_status()  # initialise trial
    state = licensing._load_state()
    state["trial_start"] = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    licensing._save_state(state)

    out, err = _run_repl_with_input(
        monkeypatch, capsys, ['swarm "test goal" --json', "version", "exit"],
    )
    assert "1.1.0" in out  # version ran after the gate fired
    assert "Pro feature" in err or "Phantom Pro" in err


def test_repl_runs_when_no_subcommand_passed(isolated_home, monkeypatch, capsys):
    """`phantom` (no args) should call run_repl(); we monkeypatch to verify."""
    called = {"v": False}
    def fake_repl():
        called["v"] = True

    import phantom.cli.repl as repl
    monkeypatch.setattr(repl, "run_repl", fake_repl)

    from typer.main import get_command
    from phantom.cli import app
    cmd = get_command(app)
    try:
        cmd(args=[], standalone_mode=False)
    except SystemExit:
        pass
    assert called["v"]
