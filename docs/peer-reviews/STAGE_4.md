# Stage 4 Peer Review

* Stage: 4 — MCP client + server + ACP multi-agent runtime
* Date: 2026-04-25

## Strengths

* MCP protocol module is hand-rolled JSON-RPC 2.0 (no jsonrpc deps).
  Codec is symmetric (encode is the inverse of decode), validated by
  round-trip tests.
* ACP topological scheduler emits failure events, propagates upstream
  failures, and detects cycles. Single-process today; the public API
  is async-friendly.
* Mass-spawn cap (1024 lifetime, max-concurrent per wave) prevents a
  prompt-injected agent from forking the host.

## Risks

* **High** — ACP runs sync; a long-running child blocks the parent.
  Stage 8 should port to anyio.
* **Medium** — MCP transport is the simplest possible in-memory shape
  for tests; the real stdio adapter (`subprocess.Popen` w/ line
  framing) lives in Stage 8 release pipeline.
* **Medium** — `notifications/initialized` is silent-acked but
  unsequenced. A misbehaving server could send notifications before
  the client's initialize completes; today we'd discard them. Spec-
  compliant but worth surfacing in operator logs.

## Required follow-ups

None.

## Suggested follow-ups

* Stage 8: stdio MCP transport + `phantom mcp serve` CLI.
* Stage 8: anyio-based ACP runtime.
* Stage 8: plugin → ACP integration (auto-build per-plugin SandboxPolicy).
