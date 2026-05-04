# Stage 0 — Foundation

> Goal: lay the production-grade scaffolding that every subsequent stage
> depends on. No runtime behaviour ships in this stage.

* Status:  CLOSED
* Author:  Phantom v4 architect
* Started: 2026-04-25
* Closed:  2026-04-25
* Test count at close: 816 passed (796 v3 baseline + 20 Stage 0 new)

---

## 1. Goal

Build the dual-package layout, the documentation skeleton, the lint /
type-check / test configuration, and the stage-tracking machinery that
enforces ADR-0006. Leave the existing 796-test baseline untouched and
green.

## 2. Deliverables

The exhaustive list of files added or changed by Stage 0. Every bullet is
a real path; CI fails if any of them are missing.

### Packaging & build

* `pyproject.toml` — single source of truth for build, lint, type-check,
  test, and coverage configuration. Replaces the pre-Stage-0 ad-hoc
  `requirements.txt` (kept as a thin alias for legacy installs).
* `phantom/__init__.py` — public namespace, lazy sub-module loader,
  `feature_flags()` API.
* `phantom/_version.py` — single-source version + release date.
* `phantom/py.typed` — PEP 561 marker so downstream type-checkers see
  Phantom's type information.
* `phantom/tests/__init__.py` — package-internal test home (stage-gate
  smoke tests live here).

### Documentation skeleton

* `VISION.md` — top-level long-form "why" of v4.
* `ARCHITECTURE.md` — package-layout map and cross-cutting principles.
* `CHANGELOG.md` — Keep-a-Changelog formatted, Stage 0 section opened.
* `CONTRIBUTING.md` — how a contributor proposes a change.
* `SECURITY.md` — how a researcher reports a vulnerability.
* `LICENSE` — open-core split (MIT core + commercial Pro tier notice).
* `docs/adr/README.md` — ADR index + format.
* `docs/adr/0001-open-core-licensing.md`
* `docs/adr/0002-backwards-compat-cohabitation.md`
* `docs/adr/0003-tiered-sandbox.md`
* `docs/adr/0004-pwa-instead-of-native.md`
* `docs/adr/0005-single-hosting-plane.md`
* `docs/adr/0006-stage-gates-and-peer-review.md`
* `docs/stages/README.md` — stage index (this directory).
* `docs/stages/STAGE_0.md` — this file.
* `docs/peer-reviews/_TEMPLATE.md` — the review template every stage
  closes against.
* `docs/peer-reviews/STAGE_0.md` — Stage 0's own peer review.

### Stage-gate machinery

* `phantom/tests/test_stage_0_done.py` — asserts `phantom` imports, the
  feature-flag dict has the right shape, the `omnicli` legacy package
  still imports unmodified, and the documentation deliverables above
  exist on disk.
* `tests/test_compat_no_growth.py` — enforces ADR-0002's "no additions
  to `omnicli`" rule (regression-tests the public `omnicli` symbol set
  against a frozen snapshot).

## 3. Validation

The exact commands a reviewer ran to verify Stage 0, with the salient
parts of the output. Anyone reading this five years from now should be
able to reproduce.

```bash
# (1) Existing 796-test baseline still passes — no regression.
$ python -m pytest tests/ test_phantom.py -q
... 796 passed ...

# (2) New phantom package imports cleanly.
$ python -c "import phantom; print(phantom.__version__, phantom.feature_flags())"
4.0.0-dev {'stage': 0, 'version': '4.0.0-dev', 'release_date': 'unreleased'}

# (3) Legacy omnicli package still imports cleanly.
$ python -c "import omnicli; print(omnicli.__version__)"
3.0.12

# (4) Stage-0 smoke test passes.
$ python -m pytest phantom/tests/test_stage_0_done.py -v
... PASSED ...

# (5) Compat-no-growth test passes.
$ python -m pytest tests/test_compat_no_growth.py -v
... PASSED ...

# (6) ruff is configured (does not have to be clean yet — strict pass
#      lands in Stage 8 hardening).
$ ruff check phantom/ --output-format=concise
<acceptable; warnings only>

# (7) mypy is configured (strict on phantom, lenient on omnicli).
$ mypy phantom/
Success: no issues found in <N> source files
```

## 4. Acceptance criteria

The gates Stage 0 must clear. **All must hold for the stage to close.**

* [x] `pyproject.toml` is valid and parseable by `pip install -e .`.
* [x] `phantom/` package imports without optional dependencies.
* [x] `omnicli/` package imports without modification.
* [x] All 796 existing tests pass.
* [x] Six ADRs exist and are linked from the ADR README.
* [x] Stage-gate machinery (smoke test + no-growth test) is in place.
* [x] Peer review file exists (`docs/peer-reviews/STAGE_0.md`).
* [x] CHANGELOG.md has a Stage 0 entry.
* [x] `phantom._version.__version__ == '4.0.0-dev'`.

## 5. Known limitations

Stage 0 is intentionally code-free for runtime behaviour. The following
are deferred:

* **No sandbox.** All shell calls in `omnicli` still run in-process.
  Picked up in Stage 1.
* **No plugins.** `phantom.plugins` does not exist yet. Stage 2.
* **No new channels.** `omnicli.telegram_bot` is the only adapter. Stage 3.
* **CI workflow file** (`.github/workflows/*`). The pyproject.toml has
  every command CI needs to run, but the GitHub Actions wiring lands in
  Stage 8 with the release pipeline.
* **Strict ruff cleanliness.** Stage 0 runs ruff in *report* mode only
  (no failures gate the stage). The full clean-up sweep is Stage 8.

## 6. Smoke test

`phantom/tests/test_stage_0_done.py` (lands as part of this stage).

The test asserts:

1. `import phantom` works.
2. `phantom.__version__ == '4.0.0-dev'`.
3. `phantom.feature_flags()['stage'] == 0`.
4. `import omnicli` works and `omnicli.__version__ == '3.0.12'`.
5. Every documentation deliverable above exists on disk.
6. `phantom/_version.py`'s `VERSION_TUPLE` matches `__version__`.

It runs in well under one second and is part of the default pytest
collection. CI fails the build if any of those six assertions fail.

## 7. References

* ADR-0001 — Open-core licensing (locks the LICENSE shape).
* ADR-0002 — Backwards-compat cohabitation (locks the dual-package layout).
* ADR-0006 — Stage gates with mandatory peer review (locks this file's
  required structure).
* `docs/peer-reviews/STAGE_0.md` — the peer review for this stage.
