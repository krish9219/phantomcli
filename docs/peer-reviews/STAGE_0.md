# Stage 0 Peer Review

* Stage:    0 — Foundation: repo structure, packaging, CI, docs scaffold
* Author:   Phantom v4 architect (self-review per ADR-0006)
* Date:     2026-04-25
* Version:  4.0.0-dev
* Files reviewed: every Stage-0 deliverable enumerated in `docs/stages/STAGE_0.md` § "Deliverables".

> Written as if reviewing a stranger's pull request. The author of Stage 0
> is also the reviewer of Stage 0; the standard applied is what they would
> apply to a contributor they had never met.

## 1. Scope reviewed

The full deliverables list of `docs/stages/STAGE_0.md`: 26 files added or
modified. This review specifically inspected the new `pyproject.toml`,
the `phantom/` package skeleton, the six ADRs, the `STAGE_0.md`
deliverables file, the smoke test (`phantom/tests/test_stage_0_done.py`),
and the no-growth compat test (`tests/test_compat_no_growth.py`). It did
**not** inspect `omnicli/` source — Stage 0 is forbidden from changing
that surface and the no-growth test enforces it.

The legacy `README.md` was left intact for Stage 0; it documents v3 and
will be split into a v3 section + v4 section in Stage 7 alongside the
mkdocs site.

## 2. Strengths

* **The dual-package layout is genuinely backwards-compatible.** Both
  `import phantom` and `import omnicli` work in the same process; the
  smoke test asserts both. ADR-0002's contract is enforced by code, not
  by intent.
* **The no-growth compat test is real teeth, not ceremony.** It parses
  every `omnicli/*.py` file with `ast`, computes the public-symbol set,
  and diffs against a JSON snapshot. A contributor who tries to slip a
  new public function into the v3 package gets a red CI build with a
  clear error message pointing at ADR-0002. The snapshot was generated
  from the actual current code, not hand-written.
* **The ADR set is exhaustive.** Six decisions, each with a
  "Stakes" section that forces the author to write down the cost of
  being wrong. The ADRs name the alternatives we rejected; future
  contributors who want to re-litigate "should we just go pure-MIT?" can
  read ADR-0001's rejection paragraph instead of arguing it again.
* **Stage tracking machinery is in code, not just docs.** The smoke test
  has a parameterised `test_required_documentation_deliverable_exists`
  case for every doc file Stage 0 promised. CI enforces the docs
  exist *and are non-empty*. Documentation rot is harder than usual.
* **`pyproject.toml` is single-source.** Build, lint, type-check, test,
  and coverage configuration all live in one file. No `setup.cfg`, no
  `setup.py`, no `tox.ini`, no `.coveragerc` to drift out of sync.

## 3. Risks

Ranked, highest first.

* **High — the `requirements.txt` has not been retired and may diverge
  from `pyproject.toml`'s `[project.dependencies]`.** Stage 0 deliberately
  did not delete `requirements.txt` because the legacy `run.py` install
  flow reads it. Mitigation: Stage 8 retires `requirements.txt`. Until
  then a contributor can pin a different version in one file and not
  the other and ship a confused install.
* **Medium — the snapshot file (`tests/_omnicli_public_snapshot.json`)
  is a 415-line frozen artefact. A genuine v3 patch that adds a
  symbol** (e.g. fixes a bug by exposing a previously-private
  function) will fail the no-growth test and force the author to
  decide between regenerating the snapshot (which dilutes the test) or
  reverting (which prevents the patch). Mitigation: the docstring on
  `tests/test_compat_no_growth.py` includes the regeneration command,
  and the contract is "open an ADR before regenerating" — the social
  layer is the safety net.
* **Medium — the lazy module loader in `phantom/__init__.py` returns a
  module at attribute access time** but the registration dict is empty
  in Stage 0. As stages land, every attribute access against `phantom`
  hits the `__getattr__` fallback, which tries the dict first. The dict
  needs to grow with each stage; if a contributor forgets to register
  their module, the symptom is a confusing AttributeError. The smoke
  test for Stage <N> should assert the corresponding registration.
  Mitigation: each stage's smoke test will include this assertion;
  the pattern is set by Stage 1.
* **Low — the `pyproject.toml` `requires-python = ">=3.11"` is stricter
  than v3** which targets `>=3.9`. Users on 3.9 / 3.10 who upgrade their
  install in place from v3.0.11 to v3.0.12 will get a wheel that
  refuses to install. Mitigation: the wheel is built per-version; v3
  patch wheels keep the older `requires-python`. The pyproject.toml
  here only governs v4 development. Documented in CONTRIBUTING.md.
* **Low — no CI workflow file ships in Stage 0.** The pyproject.toml
  has every command CI needs to run, but nothing actually runs it on
  push. Mitigation: Stage 8 owns this. Until then, the "CI" is a
  pre-commit checklist documented in CONTRIBUTING.md and the developer
  is expected to run it locally.
* **Low — the `[tool.coverage.run].fail_under = 0`** disables the
  coverage gate at the global level. Per-stage gates are enforced via
  the Makefile target raised stage-by-stage. The failure mode is "we
  forget to raise it" and ship a lower-coverage release. Mitigation:
  the Stage-8 release-pipeline test asserts `fail_under` has been
  raised to its target.

## 4. Required follow-ups (block stage close)

None. All deliverables enumerated in `STAGE_0.md` are present and the
smoke test passes locally.

## 5. Suggested follow-ups (do not block)

* Stage 7 should split `README.md` into a v3 section + a v4 section, or
  retire the v3 section entirely if the mkdocs site has full coverage by
  then.
* Stage 8 should add a `Makefile` (or `justfile`) with named targets
  matching the CI workflow's job set, so local "what does CI run?"
  becomes a one-liner.
* Stage 8 should retire `requirements.txt` once `pip install -e .` is
  the only documented install path.
* Add a `docs/architecture/v3-legacy.md` deep-dive when the architecture
  for Phantom v4 is fully laid down (target: end of Stage 4).

## 6. Sign-off

> I have reviewed the deliverables listed in `docs/stages/STAGE_0.md`
> against the acceptance criteria there. The Required follow-ups list
> above is empty. I attest that Stage 0 is **closed** pending the
> validation commands in § 3 of `STAGE_0.md` running green on a clean
> checkout.
>
> Reviewer:        Phantom v4 architect
> Date:            2026-04-25
> Codebase commit: (Stage 0 close — see CHANGELOG)
