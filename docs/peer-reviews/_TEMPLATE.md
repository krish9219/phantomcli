# Stage <N> Peer Review

* Stage:    <N> — <one-line title>
* Author:   <name>
* Date:     YYYY-MM-DD
* Version:  4.0.0-dev (commit <sha>)
* Files reviewed: <list, or `git log --diff-filter=ACMRTUXB --name-only ...` snippet>

> Written **as if reviewing someone else's PR**. The author of the stage
> writes their own review, but the tone, depth, and standard is what they
> would apply to a stranger's work. No self-congratulation; no excuses for
> known weaknesses.

## 1. Scope reviewed

A short paragraph describing exactly what was inspected. Include any code
that was *not* reviewed (e.g. "the cron entrypoint was changed but the
review focused on the executor — cron is exercised in Stage 7").

## 2. Strengths

What did the author get right? Be specific. "The sandbox tier-selection
logic correctly falls back through bwrap → firejail → unshare and emits
exactly one log line per process lifetime; the test for this asserts
log-call count, not just presence." Two-to-five bullets, no fluff.

## 3. Risks

Ranked, highest first. Each risk gets:

* **What** — concrete description.
* **Why it matters** — what happens if it bites us.
* **Mitigation** — what is in place; what is not.

Three-to-eight bullets. If you cannot find any risks, you are not looking
hard enough — go back and read the code again.

## 4. Required follow-ups (block stage close)

Bugs that **must** be fixed before the next stage starts. Each bullet
links to a code location and a test that should be added or changed.
**An empty section means the stage closes.**

## 5. Suggested follow-ups (do not block)

Smaller items: tidy-ups, missing docstrings on internal helpers, a
fixture that could be deduplicated. These get filed as GitHub issues
referencing this review file. Tag with `stage<N>-followup`.

## 6. Sign-off

> I have reviewed the deliverables listed in `docs/stages/STAGE_<N>.md`
> against the acceptance criteria there. The Required follow-ups list
> above is empty (or has been resolved with commits referenced above).
> I attest that the stage is **closed**.
>
> Reviewer: <name>
> Date:     YYYY-MM-DD
> Codebase commit: <sha>
