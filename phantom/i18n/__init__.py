"""Phantom i18n — message catalogues, no gettext dependency.

We ship a tiny in-process catalogue (dict-of-dicts) so end users get
translated UX without pulling in babel/gettext compile chains. The
operator picks a locale via ``PHANTOM_LOCALE`` or
``~/.phantom/config.json``; messages fall back to English when a
translation is missing.

Stage 7 ships catalogues for: en (default), hi, te, es, zh.
"""

from __future__ import annotations

from phantom.i18n.catalog import (
    AVAILABLE_LOCALES,
    DEFAULT_LOCALE,
    Translator,
    available_locales,
    set_locale,
    t,
)

__all__ = [
    "AVAILABLE_LOCALES",
    "DEFAULT_LOCALE",
    "Translator",
    "available_locales",
    "set_locale",
    "t",
]
