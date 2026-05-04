# Phantom Architecture

> A working developer's tour of the codebase. Read this before you change
> anything non-trivial.

This document describes the **post-v4** architecture. The legacy v3 layout
(`omnicli/`) is documented separately in `docs/architecture/v3-legacy.md`
once Stage 0 closes.

---

## 30-second overview

```
                          ┌────────────────────────────┐
                          │   Channels (Stage 3)       │
                          │   Telegram · Discord ·     │
                          │   Slack · Matrix · IRC ·   │
                          │   WebChat · CLI · PWA      │
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │      Routing layer          │
                          │  (channel-agnostic events)  │
                          └─────────────┬──────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
                  │              Engine (core)                 │
                  │  prompt builder · model router · stream    │
                  │  assembler · tool dispatch · agent loop    │
                  └──┬─────────┬─────────┬─────────┬──────────┘
                     │         │         │         │
              ┌──────▼──┐ ┌────▼────┐ ┌──▼────┐ ┌──▼─────┐
              │ Tools   │ │ Memory  │ │ Skills│ │ Plugins│
              │ (S1)    │ │ (S5)    │ │ (S5)  │ │ (S2)   │
              └─────────┘ └─────────┘ └───────┘ └────────┘
                     │
              ┌──────▼──────┐
              │  Sandbox    │  ← bwrap → firejail → unshare → docker
              │  (Stage 1)  │
              └─────────────┘

                 (out-of-band)
              ┌────────────────┐
              │ MCP / ACP      │  Stage 4 — multi-agent + 3rd-party tool
              │ (Stage 4)      │  servers exchange messages over stdio/SSE
              └────────────────┘
```

The numbers in parentheses (`S1`, `S2`, …) are the development stages that
deliver each box. See `docs/stages/` for details.

---

## Package layout

```
phantom/
├── __init__.py            Public namespace (lazy sub-module loading)
├── _version.py            Single-source version + release date
├── _compat.py             Re-exports from omnicli for legacy callers
├── cli/                   Typer entry point + subcommands
├── engine/                Prompt build, stream assembly, tool dispatch
├── sandbox/               Stage 1 — bwrap/firejail/unshare/docker tiers
├── plugins/               Stage 2 — manifest, loader, signature checks
├── channels/              Stage 3 — adapter ABC + per-channel modules
├── mcp/                   Stage 4 — MCP client + server
├── acp/                   Stage 4 — Agent Communication Protocol runtime
├── memory/                Stage 5 — vector + FTS5 hybrid retrieval
├── skills/                Stage 5 — skill bundle format + loader
├── voice/                 Stage 6 — Whisper STT + Piper TTS realtime loop
├── canvas/                Stage 6 — server-side rendering host
├── pwa/                   Stage 6 — service worker + manifest assets
├── i18n/                  Stage 7 — gettext catalogues
├── observability/         Stage 8 — OpenTelemetry tracing + metrics
└── tests/                 Stage-gate smoke tests (in-package)

omnicli/                   (frozen v3 package — bug fixes only)
docs/
├── adr/                   Architecture Decision Records
├── stages/                Per-stage deliverable + validation records
├── architecture/          Deeper dives (this file links into them)
└── peer-reviews/          Peer review write-ups, one per stage
tests/                     Behaviour tests (existing 796-test baseline)
```

Every sub-package contains its own `README.md` with the module-level "why"
and an `__init__.py` that exports a stable public API. Internal helpers stay
private (leading underscore) and are not part of the API contract.

---

## Cross-cutting principles

These rules apply everywhere. Reviewers reject changes that violate them.

1. **No tool runs unsandboxed.** Every shell call, file write, network
   request, and plugin entrypoint goes through `phantom.sandbox`. The
   in-process trust gate from v3 is retained as a *second* line of defence
   inside the sandbox; it is no longer the only line.
2. **Capability declarations are explicit.** Plugins, skills, and MCP servers
   declare what they need (network, filesystem paths, executor) up-front.
   The user sees these declarations at install time and can revoke them
   without touching code.
3. **Memory is namespaced.** Every memory write carries a `(user, project,
   session)` tuple. No global write. Migration tooling enforces this on the
   v3 → v4 transition.
4. **Channels are dumb.** A channel adapter only translates between Phantom
   events and the channel's protocol. All policy (trust, rate limits,
   command gating) lives in the routing layer above.
5. **Failures are typed.** Every public function that can fail returns
   either a value or raises a typed exception derived from
   `phantom.errors.PhantomError`. No bare `Exception`.
6. **Tests are the spec.** If behaviour is not covered by a test, it is not
   considered defined. Reviewers can rip out untested code without notice.
7. **Strict typing on new code.** `phantom/*` is mypy-strict; `omnicli/*`
   stays opt-in until Stage 8 retires it.

---

## Stage gates

A stage is "done" when **all** of these hold. Anything less and the stage
stays in-progress.

* Every public function has a docstring with at least one `Examples` block.
* Every module has a `README.md` describing its surface.
* Branch coverage on security-critical modules is **100 %**. Line coverage
  globally is **≥ 95 %** by Stage 8 (raised gradually stage by stage).
* `ruff check`, `ruff format --check`, `mypy phantom`, `bandit -r phantom`,
  and `pytest` are all green.
* The stage's `STAGE_<N>.md` contains a "Validation" section reproducing the
  exact commands a reviewer ran and what they observed.
* A peer-review write-up at `docs/peer-reviews/STAGE_<N>.md` exists.
* `phantom._version.__version__` and `CHANGELOG.md` agree.
* The stage smoke test (`phantom/tests/test_stage_<N>_done.py`) passes.

---

## Where to look next

* **What does each stage ship?** → `docs/stages/STAGE_<N>.md`
* **Why did we choose X over Y?** → `docs/adr/`
* **How do I write a plugin?** → `phantom/plugins/README.md` (Stage 2)
* **How do I add a channel?** → `phantom/channels/README.md` (Stage 3)
* **How is memory laid out?** → `phantom/memory/README.md` (Stage 5)
