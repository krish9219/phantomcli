"""Tests for the TUI polish layer."""

from __future__ import annotations

import time

import pytest

from phantom.tui import (
    FileUpdate,
    FileUpdateSidePanel,
    ProgressTracker,
    StreamingResponse,
    render_token,
)
from phantom.tui.progress import ProgressSnapshot


# ─── render_token (markup escaping) ─────────────────────────────────────────


def test_render_token_passes_through_valid_markup():
    assert render_token("[bold]hi[/bold]") == "[bold]hi[/bold]"


def test_render_token_escapes_stray_brackets():
    # `[1]` is not valid Rich markup — must be escaped.
    out = render_token("see [1] for details")
    assert "\\[1]" in out


def test_render_token_handles_empty_string():
    assert render_token("") == ""


def test_render_token_preserves_non_bracket_text():
    assert render_token("plain text 123") == "plain text 123"


# ─── StreamingResponse ──────────────────────────────────────────────────────


def test_streaming_response_accumulates_tokens():
    sr = StreamingResponse()
    sr.feed("hello ")
    sr.feed("world")
    assert sr.rendered_text() == "hello world"


def test_streaming_response_feed_many():
    sr = StreamingResponse()
    sr.feed_many(["a", "b", "c"])
    assert sr.rendered_text() == "abc"


def test_streaming_response_finalize_blocks_further_feeds():
    sr = StreamingResponse()
    sr.feed("x")
    sr.finalize()
    with pytest.raises(RuntimeError, match="finalized"):
        sr.feed("y")


def test_streaming_response_has_new_content_tracks_flushes():
    sr = StreamingResponse()
    sr.feed("hello")
    assert sr.has_new_content()
    sr.mark_flushed()
    assert not sr.has_new_content()
    sr.feed(" world")
    assert sr.has_new_content()


def test_streaming_response_finalize_idempotent_state():
    sr = StreamingResponse()
    sr.feed("x")
    sr.finalize()
    assert sr.is_finalized()


# ─── ProgressTracker ────────────────────────────────────────────────────────


def test_progress_tracker_basic_advance():
    pt = ProgressTracker(description="t", total=10)
    pt.advance(3)
    snap = pt.snapshot()
    assert snap.completed == 3
    assert snap.total == 10
    assert snap.fraction == 0.3
    assert snap.percent == 30.0


def test_progress_tracker_caps_at_total():
    pt = ProgressTracker(description="t", total=5)
    pt.advance(100)
    assert pt.snapshot().completed == 5


def test_progress_tracker_advance_negative_rejected():
    pt = ProgressTracker(description="t", total=5)
    with pytest.raises(ValueError):
        pt.advance(-1)


def test_progress_tracker_eta_computed_when_partial():
    pt = ProgressTracker(description="t", total=10)
    time.sleep(0.05)
    pt.advance(5)
    snap = pt.snapshot()
    assert snap.eta_s is not None
    assert snap.eta_s > 0


def test_progress_tracker_eta_none_when_complete():
    pt = ProgressTracker(description="t", total=5)
    pt.advance(5)
    assert pt.snapshot().eta_s is None


def test_progress_tracker_eta_none_when_zero_total():
    pt = ProgressTracker(description="t", total=0)
    snap = pt.snapshot()
    assert snap.fraction == 0.0
    assert snap.percent == 0.0


def test_progress_tracker_set_description():
    pt = ProgressTracker(description="initial", total=5)
    pt.set_description("changed")
    assert pt.snapshot().description == "changed"


def test_progress_tracker_context_manager_smoke():
    """Just make sure ContextManager doesn't crash even without a TTY."""
    with ProgressTracker(description="ctx", total=3) as pt:
        pt.advance(1)
        pt.advance(2)
    assert pt.snapshot().completed == 3


# ─── FileUpdateSidePanel ────────────────────────────────────────────────────


def test_panel_records_and_iterates():
    p = FileUpdateSidePanel()
    p.record(FileUpdate(path="/a/b/c.py", action="modified", delta_added=10, delta_removed=2))
    p.record(FileUpdate(path="/a/b/d.py", action="created", delta_added=5))
    assert len(p) == 2
    paths = [u.path for u in p]
    assert paths == ["/a/b/c.py", "/a/b/d.py"]


def test_panel_dedupes_by_path_with_lru_semantics():
    p = FileUpdateSidePanel()
    p.record(FileUpdate(path="/a/x", action="modified"))
    p.record(FileUpdate(path="/a/y", action="modified"))
    p.record(FileUpdate(path="/a/x", action="modified"))  # bumps to end
    paths = [u.path for u in p]
    assert paths == ["/a/y", "/a/x"]


def test_panel_caps_at_max_entries():
    p = FileUpdateSidePanel(max_entries=3)
    for i in range(5):
        p.record(FileUpdate(path=f"/f{i}", action="modified"))
    assert len(p) == 3
    assert [u.path for u in p] == ["/f2", "/f3", "/f4"]


def test_panel_max_entries_validation():
    with pytest.raises(ValueError):
        FileUpdateSidePanel(max_entries=0)


def test_panel_clear():
    p = FileUpdateSidePanel()
    p.record(FileUpdate(path="/a", action="created"))
    p.clear()
    assert len(p) == 0


def test_panel_by_action_filter():
    p = FileUpdateSidePanel()
    p.record(FileUpdate(path="/a", action="created"))
    p.record(FileUpdate(path="/b", action="deleted"))
    p.record(FileUpdate(path="/c", action="created"))
    assert len(p.by_action("created")) == 2
    assert len(p.by_action("deleted")) == 1


def test_panel_short_path_keeps_last_two():
    u = FileUpdate(path="/very/deep/path/to/file.py", action="modified")
    assert u.short_path == "…/to/file.py"


def test_panel_render_text_empty():
    p = FileUpdateSidePanel()
    assert "no file updates" in p.render_text()


def test_panel_render_text_includes_glyphs_and_deltas():
    p = FileUpdateSidePanel()
    p.record(FileUpdate(path="/x.py", action="modified", delta_added=10, delta_removed=3))
    p.record(FileUpdate(path="/y.py", action="created"))
    p.record(FileUpdate(path="/z.py", action="deleted"))
    text = p.render_text()
    assert "~" in text  # modified glyph
    assert "+" in text  # created glyph
    assert "-" in text  # deleted glyph
    assert "+10/-3" in text


def test_panel_render_text_max_rows():
    p = FileUpdateSidePanel()
    for i in range(10):
        p.record(FileUpdate(path=f"/f{i}", action="modified"))
    text = p.render_text(max_rows=3)
    assert text.count("\n") == 2  # 3 rows = 2 newlines


def test_panel_render_panel_returns_rich_panel():
    rich = pytest.importorskip("rich")
    from rich.panel import Panel
    p = FileUpdateSidePanel()
    p.record(FileUpdate(path="/a", action="created"))
    panel = p.render_panel()
    assert isinstance(panel, Panel)
