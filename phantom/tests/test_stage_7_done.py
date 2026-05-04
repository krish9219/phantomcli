"""Stage 7 smoke test."""

from __future__ import annotations

from pathlib import Path

import pytest

from phantom.i18n import AVAILABLE_LOCALES, set_locale, t
from phantom.onboarding import Wizard, default_steps


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.stage7
def test_five_locales_shipped():
    assert set(AVAILABLE_LOCALES) >= {"en", "hi", "te", "es", "zh"}


@pytest.mark.stage7
def test_translate_to_hindi():
    set_locale("hi")
    try:
        assert "स्वागत" in t("wizard.welcome")
    finally:
        set_locale("en")


@pytest.mark.stage7
def test_default_wizard_walks_to_completion():
    w = Wizard(default_steps())
    w.submit("en")
    w.submit("claude-opus-4-5")
    w.submit("sk-test")
    w.submit("yes")
    assert w.done


@pytest.mark.stage7
def test_mkdocs_config_exists():
    assert (REPO_ROOT / "mkdocs.yml").exists()


@pytest.mark.stage7
def test_docs_site_index_exists():
    assert (REPO_ROOT / "docs_site" / "index.md").exists()


@pytest.mark.stage7
def test_phantom_stage_advanced_to_7_or_higher():
    import phantom
    assert phantom.feature_flags()["stage"] >= 7
