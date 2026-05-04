"""Tests for project_memory — summary writer, run-history append, relatedness match."""
from __future__ import annotations

import os
import time

import pytest

from omnicli.project_memory import (
    write_summary, append_run, find_related_projects,
    format_related_prompt, ProjectSummary, SUMMARY_FILENAME,
    _parse_existing, _tokenize, _score,
)


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "project_abc123"
    d.mkdir()
    return d


class TestWriteSummary:
    def test_creates_summary_file(self, project_dir):
        path = write_summary(str(project_dir),
                             directive="build an IPL dashboard",
                             refined="refined spec")
        assert os.path.isfile(path)
        assert path.endswith(SUMMARY_FILENAME)

    def test_includes_core_fields(self, project_dir):
        write_summary(str(project_dir),
                      directive="build cricket dashboard",
                      refined="IPL live refresh")
        raw = (project_dir / SUMMARY_FILENAME).read_text()
        assert "build cricket dashboard" in raw
        assert "IPL live refresh" in raw
        assert "abc123" in raw  # session_id extracted from dirname

    def test_files_and_agents_rendered(self, project_dir):
        write_summary(str(project_dir),
                      directive="x", refined="y",
                      files=[{"path": "app.py", "size": 1200,
                              "purpose": "Flask entry"}],
                      agents=[{"name": "Fetcher Agent",
                               "role": "Backend",
                               "status": "done",
                               "elapsed_s": 40}])
        raw = (project_dir / SUMMARY_FILENAME).read_text()
        assert "`app.py`" in raw
        assert "1,200 bytes" in raw
        assert "Fetcher Agent" in raw
        assert "done" in raw


class TestRunHistory:
    def test_append_run_adds_line(self, project_dir):
        write_summary(str(project_dir), directive="x", refined="")
        append_run(str(project_dir), "relaunch", note="after fix")
        raw = (project_dir / SUMMARY_FILENAME).read_text()
        assert "relaunch" in raw
        assert "after fix" in raw

    def test_append_preserves_previous_runs(self, project_dir):
        write_summary(str(project_dir), directive="x", refined="",
                      extra_runs=[{"ts": "2026-01-01T00:00:00",
                                   "action": "initial_build",
                                   "note": "ok"}])
        append_run(str(project_dir), "fix_applied", note="typo in models.py")
        parsed = _parse_existing(str(project_dir / SUMMARY_FILENAME))
        actions = [r["action"] for r in parsed.runs]
        assert "initial_build" in actions
        assert "fix_applied" in actions

    def test_rewrite_keeps_created_timestamp(self, project_dir):
        import time as _t
        write_summary(str(project_dir), directive="x", refined="")
        _t.sleep(0.05)
        write_summary(str(project_dir), directive="x2", refined="y2")
        parsed = _parse_existing(str(project_dir / SUMMARY_FILENAME))
        # created_at is preserved; last_updated advances
        assert parsed.created_at
        assert parsed.last_updated >= parsed.created_at


class TestParseExisting:
    def test_extracts_directive_and_refined(self, project_dir):
        write_summary(str(project_dir),
                      directive="build IPL cricket dashboard live refresh",
                      refined="with Bootstrap and Chart.js")
        parsed = _parse_existing(str(project_dir / SUMMARY_FILENAME))
        assert "IPL cricket" in parsed.directive
        assert "Chart.js" in parsed.refined

    def test_missing_file_returns_empty_summary(self, tmp_path):
        parsed = _parse_existing(str(tmp_path / "nosuch" / SUMMARY_FILENAME))
        assert parsed.directive == ""


class TestRelatednessScoring:
    def test_identical_token_sets_score_one(self):
        a = {"ipl", "cricket", "dashboard"}
        assert _score(a, a) == 1.0

    def test_disjoint_sets_score_zero(self):
        assert _score({"ipl", "cricket"}, {"weather", "map"}) == 0.0

    def test_half_overlap_above_zero_under_one(self):
        s = _score({"ipl", "cricket", "dashboard"},
                   {"ipl", "cricket", "analytics"})
        assert 0 < s < 1

    def test_tokenize_strips_stopwords(self):
        tokens = _tokenize("create a cricket dashboard with live refresh")
        # 'create', 'a', 'with' must be dropped (English + Phantom stopwords)
        # 'dashboard' is also stopped — it appears in nearly every directive,
        # so matching on it wouldn't discriminate between projects.
        assert "create" not in tokens
        assert "a" not in tokens
        assert "with" not in tokens
        assert "dashboard" not in tokens
        # Content words remain — these ARE good for discriminating
        assert "cricket" in tokens
        assert "refresh" in tokens
        assert "live" in tokens

    def test_tokenize_drops_short(self):
        tokens = _tokenize("a b c abc def ghi")
        # short (<3 char) tokens dropped
        assert "a" not in tokens
        assert "b" not in tokens
        assert "abc" in tokens


class TestFindRelatedProjects:
    def test_empty_work_dir(self, tmp_path):
        r = find_related_projects("ipl dashboard", work_dir=str(tmp_path))
        assert r == []

    def test_matches_above_threshold(self, tmp_path):
        # Build two projects — one related, one not
        p1 = tmp_path / "project_ipl111"; p1.mkdir()
        write_summary(str(p1), directive="IPL cricket live dashboard with stats", refined="")
        p2 = tmp_path / "project_weather222"; p2.mkdir()
        write_summary(str(p2), directive="weather forecast for coastal cities", refined="")

        rows = find_related_projects("build an IPL cricket dashboard",
                                      work_dir=str(tmp_path))
        names = [os.path.basename(r.project_dir) for r in rows]
        assert "project_ipl111" in names
        assert "project_weather222" not in names

    def test_sorted_by_relatedness(self, tmp_path):
        # Two related, one more than the other
        for name, directive in [
            ("project_aaa", "IPL cricket dashboard live"),
            ("project_bbb", "IPL cricket stats viewer"),
            ("project_ccc", "dashboard basic layout"),
        ]:
            d = tmp_path / name; d.mkdir()
            write_summary(str(d), directive=directive, refined="")
        rows = find_related_projects("IPL cricket real-time dashboard",
                                      work_dir=str(tmp_path), min_score=0.05)
        # Most related should be first
        assert rows[0].relatedness >= rows[-1].relatedness
        assert rows[0].relatedness > 0


class TestFormatRelatedPrompt:
    def test_renders_numbered_entries(self, tmp_path):
        d = tmp_path / "project_x"; d.mkdir()
        write_summary(str(d), directive="IPL cricket dashboard", refined="")
        rows = find_related_projects("IPL cricket", work_dir=str(tmp_path),
                                      min_score=0.05)
        txt = format_related_prompt(rows)
        assert "[1]" in txt
        assert "project_x" in txt
        assert "match" in txt
