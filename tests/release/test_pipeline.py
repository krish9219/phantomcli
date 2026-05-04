"""Tests for :mod:`phantom.release.pipeline`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.release import audit_repo, build_manifest
from phantom.release.pipeline import ReleaseError


REPO_ROOT = Path(__file__).resolve().parents[2]


class TestAuditAgainstRepo:
    def test_repo_passes_audit(self):
        # The actual repo should pass — every closed stage has its
        # peer review and smoke test.
        issues = audit_repo(REPO_ROOT)
        assert issues == [], f"unexpected audit issues: {issues}"


class TestAuditMissingPieces:
    def test_missing_peer_review_flagged(self, tmp_path):
        # Build a tiny fake repo with one closed stage but no review.
        (tmp_path / "docs" / "stages").mkdir(parents=True)
        (tmp_path / "docs" / "peer-reviews").mkdir(parents=True)
        (tmp_path / "phantom" / "tests").mkdir(parents=True)
        (tmp_path / "docs" / "stages" / "STAGE_0.md").write_text(
            "# Stage 0\n* Status: CLOSED\n"
        )
        # Smoke test exists; review does NOT.
        (tmp_path / "phantom" / "tests" / "test_stage_0_done.py").write_text("# ok\n")
        (tmp_path / "CHANGELOG.md").write_text("[Unreleased]\n")
        issues = audit_repo(tmp_path)
        assert any("peer review" in i for i in issues)

    def test_missing_smoke_test_flagged(self, tmp_path):
        (tmp_path / "docs" / "stages").mkdir(parents=True)
        (tmp_path / "docs" / "peer-reviews").mkdir(parents=True)
        (tmp_path / "phantom" / "tests").mkdir(parents=True)
        (tmp_path / "docs" / "stages" / "STAGE_0.md").write_text(
            "# Stage 0\n* Status: CLOSED\n"
        )
        (tmp_path / "docs" / "peer-reviews" / "STAGE_0.md").write_text("ok\n")
        (tmp_path / "CHANGELOG.md").write_text("[Unreleased]\n")
        issues = audit_repo(tmp_path)
        assert any("smoke test" in i for i in issues)

    def test_missing_changelog_flagged(self, tmp_path):
        (tmp_path / "docs" / "stages").mkdir(parents=True)
        issues = audit_repo(tmp_path)
        assert any("CHANGELOG" in i for i in issues)


class TestBuildManifest:
    def test_real_repo(self):
        manifest = build_manifest(REPO_ROOT, test_count=1162, repo_sha="abc123")
        assert manifest.test_count == 1162
        # We've closed all 9 stages by the time this test runs.
        assert 0 in manifest.closed_stages
        # Manifest is JSON-serialisable.
        json.loads(manifest.to_json())

    def test_blocked_when_audit_fails(self, tmp_path):
        # Empty fake repo → audit fails → build_manifest raises.
        with pytest.raises(ReleaseError, match="release blocked"):
            build_manifest(tmp_path, test_count=0)
