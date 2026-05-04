# ADR-0002 — Backwards-compatible coexistence: `omnicli` v3 alongside `phantom` v4

* Status:  Accepted
* Date:    2026-04-25
* Authors: Aravind Labs

## Context

PhantomCLI v3 ships as the `omnicli` Python package. There are 796 tests
across `tests/` and `test_phantom.py`, and an unknown number of external
scripts, dashboards, and webhooks that import `omnicli.*` directly.

Phantom v4 introduces a new package layout (`phantom`) with strict typing,
sandboxing, plugins, channels, MCP, ACP, skills, vector memory, voice,
canvas, PWA, and i18n. Trying to land all of this *inside* the existing
`omnicli` package would mean either:

1. Breaking imports for v3 consumers, or
2. Leaving `omnicli` as a frozen alias that re-exports everything from
   `phantom`, with a slow deprecation cycle.

## Decision

We **co-habit**. Both packages ship from the same wheel. `omnicli` is frozen
at v3.0.12; new behaviour lands in `phantom`. Where v4 supersedes a v3
module 1:1, `omnicli` re-exports from `phantom` in `omnicli/_compat.py` so
old import paths keep working. Where v4 fundamentally changes a contract
(e.g. the executor moves from in-process to sandboxed), `omnicli` retains
the v3 implementation; users who want the new behaviour switch to
`phantom`.

Concrete rules:

* **Adding** to `omnicli` is forbidden. New features go in `phantom`.
* **Removing** from `omnicli` is forbidden. Bugs get fixed; APIs do not
  shrink.
* **Renaming** in `omnicli` is forbidden. New names go in `phantom`.
* The 796-test baseline runs on every CI build and must stay green.
* The wheel installs both top-level packages; `import omnicli` and
  `import phantom` both work in the same process.
* `phantomcli` (the legacy command-line entry point) stays bound to
  `omnicli.cli:main`; the new `phantom` entry point binds to
  `phantom.cli:main`. Users opt in by running the new command.

The legacy package gets retired in a future v5 release. We do not commit to
a date.

## Alternatives considered

### Big-bang rename: `omnicli` → `phantom`

Forces every existing user to update their imports. Breaks scripts,
dashboards, webhooks, license-validation flows in the wild. Saves us
~30 minutes of test plumbing. Rejected.

### Pure-alias `omnicli` that just re-exports `phantom`

Simpler to explain, but the v3 sandbox, executor, and trust-gate semantics
are intentionally different from v4. A v3 user who relies on the old
behaviour would silently get the new behaviour after upgrading. That is the
worst kind of breaking change — silent. Rejected.

### Two separate wheels, two separate installs

Maximum isolation but doubles the release pipeline cost and forces users to
choose at install time. Rejected for this stage; revisited in Stage 8 if the
combined wheel grows uncomfortably large.

## Consequences

**We get:**

* No customer-visible breakage on upgrade.
* Freedom to redesign internals in `phantom` without dragging legacy code
  into the new world.
* A clean migration story: "Stop importing from `omnicli`. The replacement
  is here in `phantom`. The old import keeps working, but the new one is
  better. Here is a mechanical migration script."

**We pay:**

* Two packages to keep installed, tested, and documented.
* A small import-time cost (both top-level `__init__.py` files run on
  install).
* Cognitive overhead for new contributors who must understand which
  package "owns" which module.

## Stakes

If this decision is wrong:

* **Worst case** — `omnicli` becomes a bottomless legacy sink that
  consumes review attention. Mitigation: the "no additions" rule above is
  enforced by a CI check (`tests/test_compat_no_growth.py`, lands in
  Stage 0).
* **Reversal cost** — collapsing the two packages later is mechanical: a
  rename + an import rewrite. The hard part is the social commitment to
  the old import paths, not the code.
* **Probability of regret** — low. The cohabitation model is the same one
  Python itself used for `urllib` → `urllib3`, `optparse` → `argparse`,
  and `imp` → `importlib`.
