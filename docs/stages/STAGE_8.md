# Stage 8 — Hardening: auth rotation, observability, release pipeline

* Status: CLOSED
* Date: 2026-04-25

## Deliverables

* `phantom/auth/{__init__,pool}.py` — thread-safe API key pool with
  cooldown rotation, per-key failure counters, and a privacy-
  preserving `stats()` (only the last 4 chars of any key are exposed).
* `phantom/observability/{__init__,metrics}.py` — in-process Counter +
  Histogram primitives + a global `REGISTRY` with an `export()` shape
  compatible with OpenTelemetry exporters in the optional `[otel]`
  extras.
* `phantom/release/{__init__,pipeline}.py` — `audit_repo()` and
  `build_manifest()`. Audit checks: every closed stage has a peer
  review + smoke test; CHANGELOG has a current entry; version is
  semver-shaped.
* Tests: `tests/auth/`, `tests/observability/`, `tests/release/`. +29 new.
* `phantom/tests/test_stage_8_done.py`.

## Validation

* 29/29 Stage-8 tests pass.
* `audit_repo(REPO_ROOT)` returns `[]` — every previous stage has its
  paperwork in order.
* `build_manifest(REPO_ROOT, test_count=N)` produces a JSON-serialisable
  release manifest naming all closed stages.

## Stage-8 cleanups carried over from earlier reviews

This stage is also where deferred work from earlier peer reviews
lands. Items listed for completeness; items marked **shipped** are in
this stage; items marked **deferred** moved to a v4.1 backlog.

* Plugin → ACP wiring with auto-built sandbox policy — **deferred**
  (needs a v4-port of the agent loop, which is itself deferred).
* Stdio MCP transport + `phantom mcp serve` — **deferred** (the
  in-memory transport is sufficient for tests; deployment glue lands
  with the agent-loop port).
* Matrix and IRC channel adapters — **deferred** (need real homeserver
  + IRC server in CI; the framework supports them).
* Real STT/TTS engines under `[voice]` extras — **deferred** (large
  optional deps, want a separate optional install path).
* Reverse "user plugin shadows built-in" precedence — **deferred**
  pending operator telemetry on confusion.
