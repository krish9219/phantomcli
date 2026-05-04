# ADR-0006 — Stage gates with mandatory peer review

* Status:  Accepted
* Date:    2026-04-25
* Authors: Aravind Labs

## Context

Phantom v4 is a 9-stage rebuild (`STAGE_0` through `STAGE_8`). Each stage
adds a substantial subsystem (sandbox, plugins, channels, …). The stages
build on each other — Stage 2 plugins use Stage 1 sandbox, Stage 5 memory
plugs into Stage 2's slot, and so on.

The risk: a late-stage bug discovered in Stage 5 turns out to be rooted in
a Stage 1 design flaw nobody flagged at the time. Re-doing Stage 1 means
re-doing everything that built on top of it.

The mitigation: every stage closes with a written, dated, version-pinned
peer review that says "these are the deliverables; here is exactly how I
verified them; here is what I think is fragile and why; here are the open
follow-ups."

## Decision

Every stage closes with **all of** the following before the next stage
starts:

### 1. The deliverables file — `docs/stages/STAGE_<N>.md`

Sections, in order:

1. **Goal** — one sentence.
2. **Deliverables** — a checklist of every concrete artefact added or
   changed by this stage. Bullet must reference a file path.
3. **Validation** — the exact shell commands a reviewer ran, with their
   observed output stripped to the bits that prove the deliverable. No
   hand-waving.
4. **Acceptance criteria** — the gates this stage had to clear (test
   counts, coverage thresholds, lint cleanliness, etc.).
5. **Known limitations** — anything we are deferring, with the stage that
   will pick it up.
6. **Smoke test** — the in-package test that asserts the stage is wired
   (`phantom/tests/test_stage_<N>_done.py`).

### 2. The peer review file — `docs/peer-reviews/STAGE_<N>.md`

Independent of the deliverables file. The author writes it as if reviewing
someone else's pull request. Sections:

1. **Scope reviewed** — the diff range or the file list inspected.
2. **Strengths** — what the author got right.
3. **Risks** — what might bite us later, ranked.
4. **Required follow-ups** — bugs found that **must** be fixed before
   shipping. Anything here blocks the stage.
5. **Suggested follow-ups** — bugs found that can wait. Filed as issues.
6. **Sign-off** — author, date, version of the codebase reviewed.

The peer review is written from the perspective of a 25-year-veteran
engineer with no prior context. It is allowed to be harsh.

### 3. The smoke test — `phantom/tests/test_stage_<N>_done.py`

A small, fast, pytest-driven assertion that the stage's public surface is
present and working. Examples (lands per stage):

* Stage 0: `phantom.feature_flags()['stage'] == 0`.
* Stage 1: a sandboxed `echo hi` returns `"hi"` and the chosen tier is
  recorded in the audit log.
* Stage 2: an empty plugin loads without error and contributes its
  declared capabilities.
* Stage 5: a hybrid retrieval call against a fixture corpus returns the
  expected document at rank 1.

These tests are marked with the `stage<N>` pytest marker and run on every
CI build. They fail loudly on regression.

### 4. The version + changelog bump

`phantom._version.__version__` and `CHANGELOG.md` agree on the stage
boundary. The CHANGELOG entry references the deliverables file and the
peer-review file by path.

## Alternatives considered

### Single end-of-project review

Cheapest. Highest risk: a bad early decision compounds for months before
anyone notices. Rejected.

### Trunk-based "review every PR, no stage gates"

Works for a team of five with shared context. We do not have that. Stage
gates give a single-author project the structure that PR review gives a
team. Rejected on its own; kept in addition (every commit is reviewable).

### External review (paid contractor)

Maybe later. Today we capture the same value in writing and audit it on
release.

## Consequences

**We get:**

* A paper trail any developer can read in five years and understand why
  the codebase looks the way it does, what worked, what did not, and what
  was deliberately deferred.
* A forcing function against scope creep — "is this Stage 3 work, or is
  this Stage 5? If 5, file it."
* Earlier detection of design flaws.

**We pay:**

* Two extra files per stage (~500 lines of writing each). Acceptable cost
  for the read-back value.
* A psychological tax on the author, who now has to write a critical
  review of their own work. Worth it.

## Stakes

If this decision is wrong:

* **Worst case** — the reviews become rubber-stamps and the structure is
  ceremony, not signal. Mitigation: the smoke tests are real code; even
  if the review prose is weak, the test catches regressions.
* **Reversal cost** — zero. We can stop writing them at any point.
* **Probability of regret** — very low. Engineering teams that drop this
  practice almost always wish they had not.
