# Stage 5 Peer Review

* Stage: 5 — Skills + Memory v2
* Date: 2026-04-25

## Strengths

* SKILL.md frontmatter parser is hand-rolled (no PyYAML); it accepts
  the common case and rejects malformed input with clear errors.
* MemoryStore is dependency-free for the lexical+TF-IDF blend. Runs
  anywhere SQLite runs. The `[vector]` optional dep stays optional.
* Hybrid retrieval is a single function with documented blend weights;
  operators can A/B different weights without code changes.
* Namespacing by `(user, project, session)` is enforced at every
  read/write. There is no "global" memory.

## Risks

* **Medium** — Hashing TF-IDF collisions are a real thing; on a
  corpus of >10k records two unrelated documents will sometimes share
  buckets. The 0.6 BM25 weight masks this in practice but a
  pathological input could regress.
* **Medium** — `_hashing_tfidf` is recomputed at every search call
  for every candidate. A cache (per-record) is a Stage-8 perf
  follow-up.
* **Low** — Skill body loading is eager; large bodies cost memory at
  discover time. Practically every skill is < 4 KiB so this is fine
  today.
* **Low** — FTS5 is enabled implicitly by SQLite ≥ 3.9; older Linux
  distros (RHEL 7) ship 3.7. Document the requirement in onboarding.

## Required follow-ups

None.

## Suggested follow-ups

* Stage 8: per-record TF-IDF vector cached in a sidecar table.
* Stage 8: pluggable embedding backend (chromadb / qdrant) via the
  `[vector]` extras.
* Stage 7: document SQLite ≥ 3.9 requirement in onboarding.
