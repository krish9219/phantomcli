# Stage 8 Peer Review

* Stage: 8 — Hardening
* Date: 2026-04-25

## Strengths

* `KeyPool.stats()` deliberately exposes only the **last 4 chars** of
  any key. Even an operator who screen-shares their dashboard cannot
  leak full keys.
* `observability` is dependency-free; the registry's `export()` shape
  is OpenTelemetry-compatible without forcing every install to carry
  the OTel dependency.
* `release.audit_repo()` is the rule that protects the rule: it
  enforces ADR-0006's "every closed stage has a peer review and a
  smoke test." A future contributor cannot mark a stage CLOSED in
  `STAGE_<N>.md` without also producing the artefacts.
* `build_manifest()` raises on audit failure, so the release pipeline
  fails closed.

## Risks

* **Medium** — the audit recognises closed stages by string match
  (``"Status:  CLOSED"`` / ``"Status: CLOSED"``). A creative hand-edit
  of the file (different whitespace, "closed" lowercase) might bypass
  it. Stage 9 (if there ever is one) should switch to a fenced
  frontmatter parser.
* **Low** — `KeyPool` is in-memory; restarting the process loses the
  cooldown state. Operators who care will plug a persistent backend
  via the Pro-tier hosted store.
* **Low** — observability registry is module-global. Tests reset it
  via `reset_for_tests()`; real deployments never reset it. Adequate.

## Required follow-ups

None.

## Suggested follow-ups (v4.1)

* Plugin → ACP integration; agent-loop v4 port.
* Real STT/TTS engine adapters under `[voice]`.
* Matrix and IRC channel adapters.
* Stdio MCP transport + `phantom mcp serve`.
* Persistent KeyPool backend (Pro tier).
* Async ACP runtime (anyio).
