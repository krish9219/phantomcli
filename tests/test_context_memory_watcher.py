"""Tests for the poll-based CONTEXT.md live-reload watcher."""
from __future__ import annotations

import os
import time

import pytest

from omnicli.context_memory_watcher import Watcher, start


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    (home / ".phantom").mkdir(parents=True)
    (proj / ".phantom").mkdir(parents=True)
    (home / ".phantom" / "CONTEXT.md").write_text("v1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PHANTOM_CONTEXT_USER", str(home / ".phantom" / "CONTEXT.md"))
    return {"home": home, "proj": proj}


class TestFirstPollSeedsState:
    def test_first_poll_fires_nothing(self, fake_tree):
        fires: list[list[str]] = []
        w = Watcher(on_change=fires.append, project_dir=str(fake_tree["proj"]))
        changed = w.poll_once()
        assert changed == []
        assert fires == []


class TestEditTriggersChange:
    def test_edit_existing_file_fires(self, fake_tree):
        fires: list[list[str]] = []
        w = Watcher(on_change=fires.append, project_dir=str(fake_tree["proj"]))
        w.poll_once()  # seed
        # Give the filesystem a moment so mtime will move
        time.sleep(0.02)
        (fake_tree["home"] / ".phantom" / "CONTEXT.md").write_text("v2")
        # Force mtime to advance regardless of FS resolution
        now = time.time() + 1
        os.utime(fake_tree["home"] / ".phantom" / "CONTEXT.md", (now, now))
        changed = w.poll_once()
        assert len(changed) == 1
        assert "CONTEXT.md" in changed[0]
        assert len(fires) == 1


class TestNewFileDetected:
    def test_new_project_context_detected(self, fake_tree):
        fires = []
        w = Watcher(on_change=fires.append, project_dir=str(fake_tree["proj"]))
        w.poll_once()   # seed (only home CONTEXT.md exists)
        # Add a project CONTEXT.md
        (fake_tree["proj"] / ".phantom" / "CONTEXT.md").write_text("project rules")
        changed = w.poll_once()
        assert any("proj" in p for p in changed)


class TestDeleteDetected:
    def test_deleted_file_fires(self, fake_tree):
        fires = []
        w = Watcher(on_change=fires.append, project_dir=str(fake_tree["proj"]))
        w.poll_once()  # seed
        os.remove(fake_tree["home"] / ".phantom" / "CONTEXT.md")
        changed = w.poll_once()
        assert len(changed) == 1


class TestNoFalsePositives:
    def test_no_edit_no_fire(self, fake_tree):
        fires = []
        w = Watcher(on_change=fires.append, project_dir=str(fake_tree["proj"]))
        w.poll_once()
        assert w.poll_once() == []
        assert fires == []


class TestBackgroundThread:
    def test_start_stop_clean(self, fake_tree):
        w = start(on_change=lambda paths: None,
                  project_dir=str(fake_tree["proj"]),
                  interval_s=0.05)
        assert w._thread is not None
        assert w._thread.is_alive()
        w.stop(timeout_s=2.0)
        assert not w._thread.is_alive()

    def test_double_start_is_noop(self, fake_tree):
        w = start(on_change=lambda p: None,
                  project_dir=str(fake_tree["proj"]),
                  interval_s=0.05)
        first = w._thread
        w.start()  # second call shouldn't spawn another thread
        assert w._thread is first
        w.stop()


class TestCallbackException:
    def test_broken_callback_does_not_kill_watcher(self, fake_tree):
        def _bad(paths): raise RuntimeError("oh no")
        w = Watcher(on_change=_bad, project_dir=str(fake_tree["proj"]))
        w.poll_once()   # seed
        time.sleep(0.02)
        (fake_tree["home"] / ".phantom" / "CONTEXT.md").write_text("v2")
        now = time.time() + 1
        os.utime(fake_tree["home"] / ".phantom" / "CONTEXT.md", (now, now))
        # Must NOT raise
        w.poll_once()
