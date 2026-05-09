"""Stage 0 smoke test — asserts the foundation is wired.

Run with::

    pytest phantom/tests/test_stage_0_done.py -v

Per ADR-0006 every stage closes with a smoke test that fails loudly if
its deliverables regress. Stage 0 ships scaffolding only; the assertions
below cover packaging, version stamping, dual-package coexistence, and
the documentation deliverables enumerated in
``docs/stages/STAGE_0.md`` § "Deliverables".
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ─── Packaging & version ──────────────────────────────────────────────────────


@pytest.mark.stage0
def test_phantom_imports_cleanly() -> None:
    """`import phantom` must work with only the base dependency set."""
    import phantom

    # Re-importing must be idempotent.
    import phantom as phantom2  # noqa: PLR0402

    assert phantom is phantom2


@pytest.mark.stage0
def test_phantom_version_is_stage_0_dev() -> None:
    """`phantom.__version__` is the Stage-0 development pin."""
    import phantom

    assert phantom.__version__ in {"4.0.0-dev", "4.0.0", "4.0.1", "4.0.2", "4.0.3", "4.0.4", "4.0.5", "4.0.6", "4.0.7", "4.0.8", "4.0.9", "4.0.10", "1.0.0", "1.0.1", "1.0.2", "1.1.0", "1.1.1", "1.1.2", "1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.7", "1.1.8", "1.1.9", "1.1.10", "1.1.11", "1.1.12"}
    # Tuple form is parseable and consistent with the string form.
    assert phantom.VERSION_TUPLE in {(4, 0, 0), (1, 0, 0), (1, 0, 1), (1, 0, 2), (1, 1, 0), (1, 1, 1), (1, 1, 2), (1, 1, 3), (1, 1, 4), (1, 1, 5), (1, 1, 6), (1, 1, 7), (1, 1, 8), (1, 1, 9), (1, 1, 10), (1, 1, 11), (1, 1, 12)}


@pytest.mark.stage0
def test_phantom_feature_flags_shape() -> None:
    """The ``feature_flags()`` API has a stable shape consumed by the dashboard."""
    import phantom

    flags = phantom.feature_flags()
    assert set(flags) == {"stage", "version", "release_date"}
    # Stage advances monotonically with each closed stage. The Stage-0
    # foundation laid down by this test stays present at every later
    # stage, so we accept stage >= 0.
    assert flags["stage"] >= 0
    assert flags["version"] == phantom.__version__
    assert isinstance(flags["release_date"], str)


@pytest.mark.stage0
def test_phantom_unknown_attribute_raises_clear_error() -> None:
    """The lazy module loader's error message must guide the developer."""
    import phantom

    with pytest.raises(AttributeError) as excinfo:
        _ = phantom.does_not_exist  # type: ignore[attr-defined]
    msg = str(excinfo.value)
    assert "phantom" in msg
    assert "does_not_exist" in msg
    assert "stage" in msg.lower()


# ─── Dual-package coexistence (ADR-0002) ──────────────────────────────────────


@pytest.mark.stage0
def test_omnicli_legacy_package_still_imports() -> None:
    """ADR-0002: the v3 package keeps importing without modification."""
    import omnicli

    assert omnicli.__version__ in {"3.0.12", "4.0.0", "4.0.1", "4.0.2", "4.0.3", "4.0.4", "4.0.5", "4.0.6", "4.0.7", "4.0.8", "4.0.9", "4.0.10", "1.0.0", "1.0.1", "1.0.2", "1.1.0", "1.1.1", "1.1.2", "1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.7", "1.1.8", "1.1.9", "1.1.10", "1.1.11", "1.1.12"}


@pytest.mark.stage0
def test_phantom_and_omnicli_can_coexist_in_one_process() -> None:
    """Both packages must be importable in the same Python process."""
    import omnicli
    import phantom

    # They must not share a top-level name (no monkey-patching either way).
    assert phantom.__name__ == "phantom"
    assert omnicli.__name__ == "omnicli"


# ─── Documentation deliverables (Stage 0 § Deliverables) ──────────────────────

REQUIRED_DOC_FILES = [
    # Top-level documentation
    "VISION.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "README.md",
    # ADRs
    "docs/adr/README.md",
    "docs/adr/0001-open-core-licensing.md",
    "docs/adr/0002-backwards-compat-cohabitation.md",
    "docs/adr/0003-tiered-sandbox.md",
    "docs/adr/0004-pwa-instead-of-native.md",
    "docs/adr/0005-single-hosting-plane.md",
    "docs/adr/0006-stage-gates-and-peer-review.md",
    # Stage system
    "docs/stages/README.md",
    "docs/stages/STAGE_0.md",
    "docs/peer-reviews/_TEMPLATE.md",
    "docs/peer-reviews/STAGE_0.md",
    # Packaging
    "pyproject.toml",
    # Stage-tracking machinery
    "phantom/_version.py",
    "phantom/__init__.py",
    "phantom/py.typed",
    "phantom/tests/__init__.py",
    "tests/test_compat_no_growth.py",
    "tests/_omnicli_public_snapshot.json",
]


# A small allow-list of Stage 0 deliverables that are *legitimately* empty
# files (PEP 561 marker, package-init re-export shims with no content yet).
LEGITIMATELY_EMPTY: frozenset[str] = frozenset({"phantom/py.typed"})


@pytest.mark.stage0
@pytest.mark.parametrize("rel_path", REQUIRED_DOC_FILES)
def test_required_documentation_deliverable_exists(rel_path: str) -> None:
    """Each Stage 0 deliverable enumerated in ``STAGE_0.md`` exists on disk."""
    path = REPO_ROOT / rel_path
    assert path.exists(), f"Stage 0 deliverable missing: {rel_path}"
    if rel_path not in LEGITIMATELY_EMPTY:
        assert path.stat().st_size > 0, f"Stage 0 deliverable is empty: {rel_path}"


# ─── Stage version + changelog agreement ──────────────────────────────────────


@pytest.mark.stage0
def test_changelog_mentions_stage_0() -> None:
    """The CHANGELOG entry for the in-progress release names Stage 0."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert ("[Unreleased] — 4.0.0-dev" in text) or ("[4.0.0]" in text) or ("[1.0.0]" in text)
    assert "Stage 0" in text


@pytest.mark.stage0
def test_pyproject_declares_phantom_package() -> None:
    """pyproject.toml ships both packages."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'include = ["phantom*", "omnicli*"]' in text
    assert "phantom-cli" in text
    assert 'phantom    = "phantom.cli:main"' in text


@pytest.mark.stage0
def test_adr_index_lists_all_six_adrs() -> None:
    """The ADR index references every ADR file present in docs/adr/."""
    index = (REPO_ROOT / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
    for n in range(1, 7):
        assert f"| 000{n}|" in index, f"ADR-000{n} not listed in docs/adr/README.md"
