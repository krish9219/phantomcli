# Stage 7 — i18n + Onboarding wizard + Docs site

* Status: CLOSED
* Date: 2026-04-25

## Deliverables

* `phantom/i18n/{__init__,catalog}.py` — five locales (en, hi, te, es,
  zh), stable identifier keys, fallback chain, env-var override.
* `phantom/onboarding/{__init__,wizard}.py` — pure-data state machine
  for the setup wizard; default steps cover locale, model, key,
  channel choice.
* `mkdocs.yml` — Material-themed docs site config; nav references
  every ADR + stage + peer review + operator guide.
* `docs_site/index.md` — landing page.
* +26 tests across `tests/i18n/` and `tests/onboarding/`.

## Validation

* All five locales have parity with the English key set (enforced by
  `tests/i18n/test_catalog.py::TestAllKeysHaveAllLocales`).
* Wizard walks the four default steps and rejects invalid input
  without advancing.

## Known limitations

* Catalogues are in-process Python dicts. A larger localisation surface
  may want a real gettext compile chain; we can swap the backend without
  changing call sites because everything goes through `t()`.
* The mkdocs site is configured but not yet built into the release
  pipeline; Stage 8 adds the build target.
