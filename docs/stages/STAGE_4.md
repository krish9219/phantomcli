# Stage 4 — MCP client + server + ACP multi-agent runtime

* Status: CLOSED
* Date: 2026-04-25

## Deliverables

* `phantom/mcp/{__init__,protocol,client,server}.py` — JSON-RPC 2.0
  request/response types, codec, client, server. Initialization,
  `tools/list`, `tools/call`, `resources/list` round-trip.
* `phantom/acp/{__init__,runtime}.py` — single-process child-agent
  coordinator with topological dependency waves, mass-spawn cap, error
  isolation, and an event log.
* Tests: `tests/mcp/test_protocol.py`, `tests/mcp/test_client_server.py`,
  `tests/acp/test_runtime.py`.
* `phantom/tests/test_stage_4_done.py`.

## Validation

* +41 tests (1,162 → 1,203).
* MCP round-trip via in-memory transport works in 0.1s.
* ACP detects cycles, propagates upstream failures, enforces concurrency cap.

## Known limitations

* MCP transport is in-process / stdio only; SSE is spec-supported but
  not bundled here. Stage 8 follow-up.
* ACP runtime is sync; async (anyio) port is a Stage-8 follow-up.
* Plugin → ACP wiring (run a plugin as an ACP child with auto-built
  sandbox policy) is documented in Stage 2 as a Stage-4 follow-up; the
  wiring is not yet implemented because the executor agent loop has
  not been v4-ported.
