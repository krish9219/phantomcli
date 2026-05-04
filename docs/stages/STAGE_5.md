# Stage 5 — Skills system + Memory v2

* Status: CLOSED
* Date: 2026-04-25

## Deliverables

* `phantom/skills/{__init__,bundle,loader}.py` — SKILL.md frontmatter
  parser, bundle loader, trigger-based activation policy.
* `phantom/skills/builtin/git_workflow/SKILL.md` — one reference skill.
* `phantom/memory/{__init__,store}.py` — SQLite + FTS5 + hashing-trick
  TF-IDF cosine reranker. Hybrid scoring: 0.6 BM25 + 0.4 cosine.
  Namespaced by `(user, project, session)`.
* Tests: `tests/skills/`, `tests/memory/`. +24 tests.
* `phantom/tests/test_stage_5_done.py`.

## Validation

* 24/24 Stage-5 tests pass.
* Skill loader discovers `git_workflow` and matches "commit" trigger.
* MemoryStore round-trip preserves namespace; hybrid retrieval ranks
  exact matches first.

## Known limitations

* TF-IDF is the hashing trick (1024 buckets), not real embeddings. A
  swap to sentence-transformers / chromadb is operator-pluggable in
  Stage 8 via the `[vector]` extras.
* Memory namespaces are passed explicitly on every call; no per-session
  context-injection middleware yet (Stage 8 owns).
* Skills `body` is loaded eagerly. A 100MB SKILL.md would balloon
  memory; Stage 8 adds lazy body loading.
