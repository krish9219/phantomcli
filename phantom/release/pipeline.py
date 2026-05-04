"""Release-pipeline guards.

These functions inspect the repo and refuse to ship if invariants
break. They run in CI on every release tag; calling them from a local
shell verifies the same gates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from phantom._version import __version__
from phantom.errors import PhantomError

__all__ = [
    "ReleaseError",
    "ReleaseManifest",
    "audit_repo",
    "build_manifest",
]


class ReleaseError(PhantomError):
    code = "phantom.release"


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """What the CDN consumes."""

    version: str
    release_date: str
    closed_stages: tuple[int, ...]
    test_count: int
    repo_sha: str

    def to_json(self) -> str:
        return json.dumps({
            "version": self.version,
            "release_date": self.release_date,
            "closed_stages": list(self.closed_stages),
            "test_count": self.test_count,
            "repo_sha": self.repo_sha,
        }, separators=(",", ":"), sort_keys=True)


# ─── audits ──────────────────────────────────────────────────────────────────


def audit_repo(repo_root: str | Path) -> list[str]:
    """Return a list of release-blocking issues. Empty list = ship.

    Checks:
    1. Every `docs/stages/STAGE_<N>.md` whose Status is CLOSED has a
       matching `docs/peer-reviews/STAGE_<N>.md`.
    2. CHANGELOG.md has an entry for the current `phantom.__version__`
       *or* explicitly says ``[Unreleased]`` (development cycle).
    3. Every closed stage's smoke test exists at
       `phantom/tests/test_stage_<N>_done.py`.
    4. `phantom._version.__version__` is a valid semver-ish string.
    """
    root = Path(repo_root)
    issues: list[str] = []

    stages_dir = root / "docs" / "stages"
    reviews_dir = root / "docs" / "peer-reviews"
    smoke_dir = root / "phantom" / "tests"
    closed: list[int] = []

    if not stages_dir.exists():
        issues.append(f"missing {stages_dir}")
        return issues

    stage_re = re.compile(r"STAGE_(\d+)\.md$")
    for path in sorted(stages_dir.glob("STAGE_*.md")):
        m = stage_re.search(path.name)
        if not m:
            continue
        n = int(m.group(1))
        text = path.read_text()
        is_closed = "Status:  CLOSED" in text or "Status: CLOSED" in text
        if is_closed:
            closed.append(n)
            review = reviews_dir / f"STAGE_{n}.md"
            if not review.exists():
                issues.append(f"closed stage {n} missing peer review at {review}")
            smoke = smoke_dir / f"test_stage_{n}_done.py"
            if not smoke.exists():
                issues.append(f"closed stage {n} missing smoke test at {smoke}")

    changelog = root / "CHANGELOG.md"
    if not changelog.exists():
        issues.append("CHANGELOG.md missing")
    else:
        ctext = changelog.read_text()
        if __version__ not in ctext and "[Unreleased]" not in ctext:
            issues.append(
                f"CHANGELOG.md has no entry for version {__version__!r} "
                "and no [Unreleased] section"
            )

    if not re.match(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$", __version__):
        issues.append(f"phantom.__version__ {__version__!r} is not semver-shaped")

    return issues


def build_manifest(
    repo_root: str | Path,
    *,
    test_count: int,
    repo_sha: str = "unknown",
) -> ReleaseManifest:
    """Build a release manifest. Raises :class:`ReleaseError` if
    :func:`audit_repo` finds blockers.
    """
    issues = audit_repo(repo_root)
    if issues:
        raise ReleaseError("release blocked: " + "; ".join(issues))

    root = Path(repo_root)
    closed: list[int] = []
    for path in sorted((root / "docs" / "stages").glob("STAGE_*.md")):
        m = re.search(r"STAGE_(\d+)\.md$", path.name)
        if not m:
            continue
        if "CLOSED" in path.read_text():
            closed.append(int(m.group(1)))

    return ReleaseManifest(
        version=__version__,
        release_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        closed_stages=tuple(closed),
        test_count=test_count,
        repo_sha=repo_sha,
    )
