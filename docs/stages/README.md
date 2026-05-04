# Phantom v4 Development Stages

> The stage-by-stage record of how Phantom v4 was built, what each stage
> shipped, what was verified, and what was deferred.

This directory is **append-only**. Once a stage closes, its file is frozen.
Bugs found later are tracked in the next stage's file (or as GitHub issues
tagged `stage<N>-followup`), not by editing the closed stage's record.

If you are joining the project and want to understand the codebase, read
the stage files in order. They are designed so a developer who has never
seen Phantom can read them top-to-bottom and arrive at the current state
with full context.

## Index

| Stage | Title                                                                  | Status        | Files                       |
|-------|------------------------------------------------------------------------|---------------|-----------------------------|
| 0     | Foundation: repo, packaging, CI, docs scaffold                         | CLOSED        | [STAGE_0.md](STAGE_0.md)    |
| 1     | Sandbox & Executor v2                                                  | CLOSED        | [STAGE_1.md](STAGE_1.md)    |
| 2     | Plugin SDK + 5 reference plugins                                       | CLOSED        | [STAGE_2.md](STAGE_2.md)    |
| 3     | Multi-channel framework + 4 channel adapters (Matrix/IRC deferred)     | CLOSED        | [STAGE_3.md](STAGE_3.md)    |
| 4     | MCP client/server + ACP multi-agent protocol                           | CLOSED        | [STAGE_4.md](STAGE_4.md)    |
| 5     | Skills system + Memory v2 hybrid retrieval                             | CLOSED        | [STAGE_5.md](STAGE_5.md)    |
| 6     | Realtime voice + Canvas host + PWA assets                              | CLOSED        | [STAGE_6.md](STAGE_6.md)    |
| 7     | i18n + Onboarding wizard + Docs site                                   | CLOSED        | [STAGE_7.md](STAGE_7.md)    |
| 8     | Hardening: auth rotation, observability, release pipeline              | CLOSED        | [STAGE_8.md](STAGE_8.md)    |

## How to read a stage file

Every closed stage follows the structure mandated by ADR-0006:

1. **Goal** — one sentence.
2. **Deliverables** — every file added or changed.
3. **Validation** — exact commands and what they printed.
4. **Acceptance criteria** — the gates that had to clear.
5. **Known limitations** — what was deferred and to where.
6. **Smoke test** — the in-package assertion that the stage is wired.

Pair every stage file with its peer review at
`docs/peer-reviews/STAGE_<N>.md` (template in `docs/peer-reviews/_TEMPLATE.md`).
