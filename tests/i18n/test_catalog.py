"""Tests for :mod:`phantom.i18n.catalog`."""

from __future__ import annotations

import pytest

from phantom.i18n import (
    AVAILABLE_LOCALES,
    DEFAULT_LOCALE,
    Translator,
    available_locales,
    set_locale,
    t,
)


@pytest.fixture(autouse=True)
def _reset_locale():
    set_locale(DEFAULT_LOCALE)
    yield
    set_locale(DEFAULT_LOCALE)


class TestCatalog:
    def test_default_locale_is_english(self):
        assert DEFAULT_LOCALE == "en"
        assert "en" in AVAILABLE_LOCALES

    def test_five_locales_shipped(self):
        assert set(AVAILABLE_LOCALES) >= {"en", "hi", "te", "es", "zh"}

    def test_known_key_translates(self):
        assert "Phantom" in t("doctor.title")

    def test_t_with_locale_argument(self):
        assert "डॉक्टर" in t("doctor.title", locale="hi")
        assert "医生" in t("doctor.title", locale="zh")

    def test_unknown_key_returns_key_itself(self):
        assert t("nonexistent.key") == "nonexistent.key"

    def test_unknown_locale_falls_back_to_english(self):
        # Even if we ask for klingon, we get English (not a crash).
        assert "Phantom" in t("doctor.title", locale="klingon")

    def test_set_locale_persists(self):
        set_locale("es")
        assert "Bienvenido" in t("wizard.welcome")

    def test_set_unknown_locale_falls_back(self):
        actual = set_locale("klingon")
        assert actual == DEFAULT_LOCALE


class TestTranslator:
    def test_per_instance_locale(self):
        es = Translator("es")
        hi = Translator("hi")
        assert "Bienvenido" in es.t("wizard.welcome")
        assert "स्वागत"   in hi.t("wizard.welcome")

    def test_unknown_locale_normalised(self):
        tr = Translator("klingon")
        assert tr.locale == DEFAULT_LOCALE


class TestAllKeysHaveAllLocales:
    def test_locale_coverage(self):
        # Every English key must have a translation in every other
        # locale; otherwise we'd ship invisible English fallback in
        # the middle of a translated UX.
        from phantom.i18n.catalog import _CATALOGS
        en_keys = set(_CATALOGS["en"])
        for loc, catalog in _CATALOGS.items():
            assert set(catalog) == en_keys, f"locale {loc!r} key set diverges from English"


class TestEnvOverride:
    def test_phantom_locale_env_picks_initial(self, monkeypatch):
        # The module-level _current is set at import time. We can't
        # easily re-import here, so we drive it via set_locale + a
        # direct check that the env path is implemented.
        from phantom.i18n.catalog import _resolve_initial_locale
        monkeypatch.setenv("PHANTOM_LOCALE", "zh")
        assert _resolve_initial_locale() == "zh"

    def test_unknown_env_locale_falls_back(self, monkeypatch):
        from phantom.i18n.catalog import _resolve_initial_locale
        monkeypatch.setenv("PHANTOM_LOCALE", "klingon")
        assert _resolve_initial_locale() == DEFAULT_LOCALE
