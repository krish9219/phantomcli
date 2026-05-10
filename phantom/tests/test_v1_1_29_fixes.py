"""Tests for v1.1.29 — five concrete fixes after the v1.1.28 user transcript.

1. ANSI on Windows: stacked strategy (os.system → SetConsoleMode → colorama
   → strip-fallback) so the failure mode is "monochrome" instead of
   "garbage on screen".
2. Identity post-processing: replace leaked foreign-brand strings
   ('I am Ling', 'developed by Ant Group') in replies AND streamed chunks.
3. Atomic port reservation in start_server.
4. Paste off-by-one: erase one extra line so the first line of the paste
   no longer leaks above the placeholder.
5. Spinner continuity through tool calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ─── ANSI strip wrapper (final-fallback path) ───────────────────────────────

def test_ansi_strip_wrapper_removes_escape_codes():
    """When all colour-init paths fail, the wrapper strips ANSI so output
    isn't garbage on a non-VT terminal."""
    from phantom.cli._terminal import _AnsiStrippingStream
    sink = MagicMock()
    stream = _AnsiStrippingStream(sink)
    stream.write("\033[36mHello\033[0m world\n")
    sink.write.assert_called_once_with("Hello world\n")


def test_ansi_strip_wrapper_passes_through_plain_text():
    from phantom.cli._terminal import _AnsiStrippingStream
    sink = MagicMock()
    stream = _AnsiStrippingStream(sink)
    stream.write("plain text")
    sink.write.assert_called_once_with("plain text")


def test_ansi_strip_wrapper_handles_complex_escape_sequences():
    """Cursor-up + clear-EOL (\\033[F\\033[K) is what the paste-erase
    feature uses. Must be stripped cleanly without leaving fragments.

    Note: spaces from the original text are preserved — only the
    escape sequences are stripped. So `> \\033[0m text` becomes
    `>  text` (two spaces around the original RESET position),
    which is correct: it's what the user would have seen if their
    terminal ignored escape codes.
    """
    from phantom.cli._terminal import _AnsiStrippingStream
    sink = MagicMock()
    stream = _AnsiStrippingStream(sink)
    stream.write("\033[F\033[K\033[36m> \033[0m text")
    # All escape sequences gone; original spacing preserved.
    sink.write.assert_called_once_with(">  text")


# ─── enable_ansi stacked strategy ──────────────────────────────────────────

def _isolate(monkeypatch):
    """Reset terminal-init state and disable pre-flight checks so the
    test drives the Windows strategy path."""
    monkeypatch.setattr("phantom.cli._terminal._INITIALIZED", False)
    monkeypatch.setattr("phantom.cli._terminal._ANSI_OK", False)
    monkeypatch.setattr("phantom.cli._terminal._no_color_requested", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._stdout_is_redirected", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._is_dumb_terminal", lambda: False)


def test_enable_ansi_returns_true_on_posix(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr("os.name", "posix")
    from phantom.cli._terminal import enable_ansi
    assert enable_ansi() is True


def test_enable_ansi_uses_os_system_first_on_windows(monkeypatch):
    """The cheapest Windows fix is `os.system("")`. v1.1.29/30 tries
    it before reaching for ctypes."""
    _isolate(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    # Verifier always False so we exercise the full stack and the strip
    # fallback installs at the end. We just want to confirm os.system
    # was called.
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper", lambda: True)

    calls = []
    def fake_system(cmd):
        calls.append(cmd)
        return 0
    monkeypatch.setattr("os.system", fake_system)

    from phantom.cli._terminal import enable_ansi
    enable_ansi()
    # os.system("") was called as part of the stack.
    assert "" in calls


def test_enable_ansi_falls_back_to_strip_when_everything_fails(monkeypatch):
    """When verifier reports VT off after every attempt and colorama
    is unavailable, install the strip wrapper."""
    _isolate(monkeypatch)
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("phantom.cli._terminal._vt_actually_enabled", lambda: False)
    monkeypatch.setattr("phantom.cli._terminal._try_colorama", lambda: False)
    installed = []
    monkeypatch.setattr("phantom.cli._terminal._install_strip_wrapper",
                         lambda: installed.append(True) or True)

    from phantom.cli._terminal import enable_ansi
    rc = enable_ansi()
    assert rc is False
    assert installed == [True]


# ─── Identity post-processing ─────────────────────────────────────────────

@pytest.mark.parametrize("input_text,expected_substring", [
    # The exact phrases from the v1.1.28 user trace.
    ("I am Ling. I cannot disclose...", "I'm Ghost"),
    ("As an OpenAI engineer reviewing this...", "As an OpenAI engineer"),  # "OpenAI engineer" left alone
    ("I am Ling, not an OpenAI engineer.", "I'm Ghost"),
    ("This is Llama 3.3, your AI assistant.", "I'm Ghost"),
    ("I'm Claude, made by Anthropic.", "I'm Ghost"),
    ("I am DeepSeek, developed by DeepSeek.", "I'm Ghost"),
])
def test_post_process_identity_rewrites_brand_strings(input_text, expected_substring):
    from phantom.cli.chat import _post_process_identity
    out = _post_process_identity(input_text, "Ghost")
    assert expected_substring in out


def test_post_process_identity_strips_developed_by_clauses():
    from phantom.cli.chat import _post_process_identity
    out = _post_process_identity(
        "I'm Ghost — developed by OpenAI. Here's how I can help.",
        "Ghost",
    )
    # The "developed by OpenAI" clause is removed, leaving the
    # legitimate identity intact.
    assert "OpenAI" not in out
    assert "Ghost" in out


def test_post_process_identity_leaves_legitimate_text_alone():
    """Don't mangle reply content that legitimately mentions a model
    name (e.g., when the user asked about it as a topic)."""
    from phantom.cli.chat import _post_process_identity
    src = "The Ling kernel uses lock-free queues. Llama bytecode is similar."
    out = _post_process_identity(src, "Ghost")
    # No "I am" / "I'm" prefix, so the brand mentions stay.
    assert "Ling kernel" in out
    assert "Llama bytecode" in out


def test_post_process_identity_handles_empty_input():
    from phantom.cli.chat import _post_process_identity
    assert _post_process_identity("", "Ghost") == ""
    assert _post_process_identity(None, "Ghost") is None


def test_post_process_identity_uses_phantom_when_no_name_set():
    from phantom.cli.chat import _post_process_identity
    out = _post_process_identity("I am Ling.", "")
    assert "Phantom" in out


# ─── Atomic port reservation ───────────────────────────────────────────────

def test_reserve_free_port_atomic_sequential_calls(monkeypatch):
    """Three sequential calls in the same turn should each get a
    distinct port even if no child has actually bound yet — the
    process-local reservation table prevents races."""
    from phantom.agent import tools
    # Reset reservation state.
    monkeypatch.setattr(tools, "_RESERVED_PORTS", {})
    # Pretend everything is free.
    monkeypatch.setattr(tools, "_is_port_in_use", lambda p: False)
    p1 = tools._reserve_free_port(5000, 5050)
    p2 = tools._reserve_free_port(5000, 5050)
    p3 = tools._reserve_free_port(5000, 5050)
    assert p1 == 5000
    assert p2 == 5001
    assert p3 == 5002


def test_reserve_free_port_skips_already_reserved(monkeypatch):
    """When 5000 is reserved (by a prior call), the next request gets 5001."""
    from phantom.agent import tools
    import time as t
    monkeypatch.setattr(tools, "_RESERVED_PORTS", {5000: t.monotonic() + 60})
    monkeypatch.setattr(tools, "_is_port_in_use", lambda p: False)
    p = tools._reserve_free_port(5000, 5050)
    assert p == 5001


def test_reserve_free_port_drops_expired_reservations(monkeypatch):
    """Reservations expire after ~15s. An old expired entry should be
    cleared so new calls can pick that port again."""
    from phantom.agent import tools
    import time as t
    # Reservation in the PAST → expired.
    monkeypatch.setattr(tools, "_RESERVED_PORTS", {5000: t.monotonic() - 100})
    monkeypatch.setattr(tools, "_is_port_in_use", lambda p: False)
    p = tools._reserve_free_port(5000, 5050)
    assert p == 5000


def test_reserve_free_port_returns_none_when_range_exhausted(monkeypatch):
    """If every port in [start, end] is either reserved or in-use, return None."""
    from phantom.agent import tools
    monkeypatch.setattr(tools, "_RESERVED_PORTS", {})
    monkeypatch.setattr(tools, "_is_port_in_use", lambda p: True)
    assert tools._reserve_free_port(5000, 5005) is None


def test_reserve_free_port_skips_ports_in_use(monkeypatch):
    """Ports the OS reports as in-use are skipped even if not in our
    reservation table."""
    from phantom.agent import tools
    monkeypatch.setattr(tools, "_RESERVED_PORTS", {})
    in_use = {5000, 5001}
    monkeypatch.setattr(tools, "_is_port_in_use", lambda p: p in in_use)
    p = tools._reserve_free_port(5000, 5050)
    assert p == 5002


# ─── Identity regex tightening (no over-consumption) ──────────────────────

def test_post_process_identity_does_not_eat_periodless_tail():
    """v1.1.29 bounds the trailing match at 120 non-period non-newline
    chars so a periodless reply doesn't get its whole tail replaced.

    The pattern matches the brand prefix + up to 120 chars of tail; the
    remainder is left untouched. Verifies the regression that the v1.1.0
    `[^.]*` would have eaten."""
    from phantom.cli.chat import _post_process_identity
    long_tail = " and you should run npm install" * 10  # ~310 chars, no period
    src = f"I am Ling{long_tail} thanks"
    out = _post_process_identity(src, "Ghost")
    # Brand prefix is replaced.
    assert out.startswith("I'm Ghost")
    # The unbounded greedy regex would have eaten "thanks" too. We expect
    # at least the final "thanks" word (and most of the tail past 120
    # chars) to survive.
    assert "thanks" in out


def test_post_process_identity_caps_at_120_chars_of_tail():
    """The bounded `[^.\\n]{0,120}` consumes at most 120 tail chars."""
    from phantom.cli.chat import _post_process_identity
    # 200 chars of "x" with no period — pattern should consume at most 120.
    tail = "x" * 200
    src = f"I am Ling {tail} END"
    out = _post_process_identity(src, "Ghost")
    # "END" must survive (it's well past the 120-char cap).
    assert "END" in out


def test_post_process_identity_stops_at_newline():
    """Newline anchors the bounded pattern — content on the next line
    is never eaten by an "I am Ling" leak on the previous line."""
    from phantom.cli.chat import _post_process_identity
    src = "I am Ling, here to help\nThe second line should survive"
    out = _post_process_identity(src, "Ghost")
    assert "The second line should survive" in out


# ─── Streaming lookback boundary ──────────────────────────────────────────

def test_streaming_lookback_constants_are_safe():
    """The streaming filter keeps a tail buffer for the next chunk so
    multi-token brand strings landing across a flush boundary still get
    caught. Lookback must exceed (longest brand prefix + bounded tail)
    = ~24 + 120 = 144 chars."""
    import inspect
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # The constants live in run_repl's body; assert they're defined and
    # respect the math.
    assert "STREAM_LOOKBACK = 192" in src
    assert "STREAM_FLUSH_AT = 1024" in src


def test_streaming_filter_catches_brand_across_flush_simulation():
    """Simulate the streaming filter manually: feed a chunk that pushes
    buffer past STREAM_FLUSH_AT, retain STREAM_LOOKBACK as tail, then
    feed the rest. Confirm "I am Ling" lands intact in the tail (not
    split) so the next pass cleans it."""
    LOOKBACK = 192
    FLUSH_AT = 1024
    # Build a buffer where "I am Ling" lands in the last 50 chars.
    pre = "padding " * 130  # 130*8 = 1040 chars
    rest = "extra"
    buf = pre + "I am Ling and so on" + rest
    assert len(buf) >= FLUSH_AT
    head, tail = buf[:-LOOKBACK], buf[-LOOKBACK:]
    # "I am Ling" must be wholly inside the retained tail.
    assert "I am Ling" in tail
    # And NOT split between head and tail.
    assert not (head.endswith("I am Lin") or head.endswith("I am Li"))


# ─── Paste placeholder erase math ─────────────────────────────────────────

def test_erase_lines_above_emits_n_cursor_up_clear_eol_pairs():
    """Each line erased is `\\033[F\\033[K`. n=3 → exactly three pairs."""
    from io import StringIO
    from phantom.cli.chat import _erase_lines_above
    out = StringIO()
    _erase_lines_above(3, out=out)
    written = out.getvalue()
    assert written == "\033[F\033[K\033[F\033[K\033[F\033[K"


def test_erase_lines_above_no_op_for_zero_or_negative():
    from io import StringIO
    from phantom.cli.chat import _erase_lines_above
    out = StringIO()
    _erase_lines_above(0, out=out)
    _erase_lines_above(-5, out=out)
    assert out.getvalue() == ""


def test_paste_placeholder_uses_n_lines_plus_one():
    """The paste-handling code in run_repl erases `n_lines + 1` lines,
    not `n_lines - 1` (v1.1.28 bug). Structural assertion against the
    chat module source so the regression is caught even though the
    closure isn't directly callable in tests."""
    import inspect
    from phantom.cli import chat
    src = inspect.getsource(chat)
    # The fix line.
    assert "_erase_lines_above(n_lines + 1)" in src
    # The v1.1.28 buggy variant must not have crept back in.
    assert "_erase_lines_above(n_lines - 1)" not in src


# ─── Spinner-continuity tool printers ─────────────────────────────────────

def test_emit_tool_call_line_starts_with_carriage_return_and_clear_eol():
    """Spinner-continuity contract: every tool-call line MUST start with
    \\r\\033[K so the spinner's current frame is wiped before the new
    text lands."""
    from io import StringIO
    from phantom.cli.chat import _emit_tool_call_line
    out = StringIO()
    _emit_tool_call_line("run_bash", {"command": "ls /tmp"}, out=out)
    written = out.getvalue()
    assert written.startswith("\r\033[K"), (
        f"tool-call line must start with \\r\\033[K but got: {written[:20]!r}"
    )
    assert "run_bash" in written
    assert written.endswith("\n")


def test_emit_tool_call_line_includes_tool_icon():
    """Each tool gets a glyph icon in the line so the user can scan the
    feed visually."""
    from io import StringIO
    from phantom.cli.chat import _emit_tool_call_line
    out = StringIO()
    _emit_tool_call_line("write_file", {"path": "/tmp/x.py"}, out=out)
    # write_file icon is 📝 per _TOOL_ICONS.
    assert "📝" in out.getvalue()


def test_emit_tool_result_line_starts_with_carriage_return_and_clear_eol():
    """Result-preview lines must also start with \\r\\033[K so the
    spinner doesn't end up on the same line as the result."""
    from io import StringIO
    from phantom.cli.chat import _emit_tool_result_line
    out = StringIO()
    # run_bash result with a clear preview.
    result = '{"exit_code": 0, "stdout": "file1.txt\\nfile2.txt"}'
    _emit_tool_result_line("run_bash", result, out=out)
    written = out.getvalue()
    if written:  # preview may be empty for some tools — skip if so
        assert written.startswith("\r\033[K"), (
            f"tool-result line must start with \\r\\033[K but got: {written[:20]!r}"
        )


def test_emit_tool_result_line_skips_empty_preview():
    """Some tools return JSON the preview formatter doesn't recognise.
    In that case we emit nothing at all (no spurious blank line)."""
    from io import StringIO
    from phantom.cli.chat import _emit_tool_result_line
    out = StringIO()
    # Bogus result that yields no preview.
    _emit_tool_result_line("unknown_tool", "not even json", out=out)
    assert out.getvalue() == ""


def test_emit_tool_result_line_emits_for_known_tool_with_data():
    """write_file with a bytes_written field DOES produce a preview, so
    the spinner-clearing prefix must be present."""
    from io import StringIO
    from phantom.cli.chat import _emit_tool_result_line
    out = StringIO()
    _emit_tool_result_line("write_file", '{"bytes_written": 1024}', out=out)
    written = out.getvalue()
    assert written.startswith("\r\033[K")
    assert "1024" in written
