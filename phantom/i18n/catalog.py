"""i18n message catalogue.

A single in-memory dict per locale. Keys are stable English strings
(short identifiers like ``"doctor.python_ok"``); values are the
translated text. Operators add new locales by extending
:data:`_CATALOGS`.

We pick stable identifier keys, not raw English text, so the catalogue
stays valid across copy-edits to the English UX.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = [
    "AVAILABLE_LOCALES",
    "DEFAULT_LOCALE",
    "Translator",
    "available_locales",
    "set_locale",
    "t",
]


DEFAULT_LOCALE: Final[str] = "en"


_CATALOGS: dict[str, dict[str, str]] = {
    "en": {
        "doctor.title":       "Phantom doctor",
        "doctor.python_ok":   "python 3.11+",
        "doctor.no_sandbox":  "No sandbox available — install bubblewrap or firejail.",
        "wizard.welcome":     "Welcome to Phantom. Let's set you up.",
        "wizard.choose_lang": "Choose a language:",
        "wizard.done":        "Setup complete.",
        "plugin.list_header": "Plugins:",
        "plugin.no_plugins":  "No plugins found.",
    },
    "hi": {
        "doctor.title":       "Phantom डॉक्टर",
        "doctor.python_ok":   "python 3.11+",
        "doctor.no_sandbox":  "कोई sandbox उपलब्ध नहीं — bubblewrap या firejail इंस्टॉल करें।",
        "wizard.welcome":     "Phantom में आपका स्वागत है। चलिए सेटअप करते हैं।",
        "wizard.choose_lang": "भाषा चुनें:",
        "wizard.done":        "सेटअप पूरा हुआ।",
        "plugin.list_header": "Plugins:",
        "plugin.no_plugins":  "कोई plugin नहीं मिला।",
    },
    "te": {
        "doctor.title":       "Phantom డాక్టర్",
        "doctor.python_ok":   "python 3.11+",
        "doctor.no_sandbox":  "Sandbox అందుబాటులో లేదు — bubblewrap లేదా firejail ఇన్‌స్టాల్ చేయండి.",
        "wizard.welcome":     "Phantom కి స్వాగతం. మీ సెటప్ ప్రారంభిస్తాము.",
        "wizard.choose_lang": "భాష ఎంచుకోండి:",
        "wizard.done":        "సెటప్ పూర్తయింది.",
        "plugin.list_header": "Plugins:",
        "plugin.no_plugins":  "Plugin లేవు.",
    },
    "es": {
        "doctor.title":       "Phantom doctor",
        "doctor.python_ok":   "python 3.11+",
        "doctor.no_sandbox":  "No hay sandbox — instala bubblewrap o firejail.",
        "wizard.welcome":     "Bienvenido a Phantom. Vamos a configurarte.",
        "wizard.choose_lang": "Elige un idioma:",
        "wizard.done":        "Configuración completa.",
        "plugin.list_header": "Plugins:",
        "plugin.no_plugins":  "No se encontraron plugins.",
    },
    "zh": {
        "doctor.title":       "Phantom 医生",
        "doctor.python_ok":   "python 3.11+",
        "doctor.no_sandbox":  "没有可用的沙箱 — 请安装 bubblewrap 或 firejail。",
        "wizard.welcome":     "欢迎使用 Phantom。我们来设置一下。",
        "wizard.choose_lang": "选择一种语言:",
        "wizard.done":        "设置完成。",
        "plugin.list_header": "插件:",
        "plugin.no_plugins":  "未找到插件。",
    },
}


AVAILABLE_LOCALES: tuple[str, ...] = tuple(sorted(_CATALOGS))


def available_locales() -> tuple[str, ...]:
    return AVAILABLE_LOCALES


# Module-level "current" locale; set_locale mutates.
_current: str = DEFAULT_LOCALE


def set_locale(locale: str) -> str:
    """Set the active locale. Falls back to DEFAULT_LOCALE on unknown.

    Returns the locale that was actually set.
    """
    global _current
    if locale in _CATALOGS:
        _current = locale
        return locale
    _current = DEFAULT_LOCALE
    return DEFAULT_LOCALE


def _resolve_initial_locale() -> str:
    env = os.environ.get("PHANTOM_LOCALE", "").strip()
    if env in _CATALOGS:
        return env
    return DEFAULT_LOCALE


_current = _resolve_initial_locale()


def t(key: str, *, locale: str | None = None) -> str:
    """Translate *key* using *locale* (or the active locale).

    Falls back to English, then to the key itself if neither locale
    has a translation. Never raises.
    """
    target = locale or _current
    catalog = _CATALOGS.get(target) or _CATALOGS[DEFAULT_LOCALE]
    if key in catalog:
        return catalog[key]
    return _CATALOGS[DEFAULT_LOCALE].get(key, key)


class Translator:
    """OO wrapper for a fixed locale (useful for tests / per-channel)."""

    def __init__(self, locale: str) -> None:
        self.locale = locale if locale in _CATALOGS else DEFAULT_LOCALE

    def t(self, key: str) -> str:
        return t(key, locale=self.locale)
