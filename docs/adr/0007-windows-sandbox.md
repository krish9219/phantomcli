# ADR 0007 — Windows sandbox: passthrough in v1.0, AppContainer in v1.2

*Status*: accepted, 2026-05-05
*Supersedes*: nothing
*Superseded by*: nothing

## Context

ADR-0003 specifies a 4-tier sandbox fallback chain
(`bubblewrap → firejail → unshare → docker`). Every tier is **Linux
kernel-level**:

* `bubblewrap` and `unshare` use Linux user/PID/mount namespaces.
* `firejail` wraps `unshare` with a curated profile DSL.
* `docker` runs Linux containers (on Windows it requires WSL2 or
  Hyper-V isolation, both of which sit a layer above Phantom).

None of these tools have a native Windows analogue. Windows offers
its own isolation primitives:

* **AppContainer** — process-level isolation via Win32 SIDs, capability
  filtering, and broker services. Supported since Windows 8.
* **Job Objects** — process-group resource limits + UI restrictions.
* **Hyper-V isolation** — full VM isolation (Windows 10 Enterprise+).
* **Windows Sandbox** — the OS feature that runs a one-shot disposable
  VM. Requires Pro+ SKU.

Each of these requires Win32 API integration that's substantial — at
minimum 2–3 weeks of focused engineering plus testing across SKUs.

## Decision

In **v1.0** we ship a `PassthroughBackend` on Windows that:

1. Runs the requested command in a normal `subprocess.run()` with
   **no isolation** beyond Phantom's existing in-process trust gate
   and resource limits (wall-clock timeout, output truncation).
2. Emits a **loud one-shot warning** to the structured log on first
   use, naming the lack of isolation explicitly.
3. **Never selects on POSIX** — the probe returns `False` on Linux/
   macOS unless the operator opts in via `PHANTOM_ALLOW_PASSTHROUGH=1`.
4. Has tier rank **99** so any real backend is always preferred.
5. Strips God Mode (Trust Level 4) on Windows by default — operators
   can override with explicit config.

In **v1.2** we ship a real Windows backend using **AppContainer** as
the primary isolation primitive, with **Job Objects** for resource
limits. ADR-0008 (forthcoming) will specify the design.

## Consequences

### Positive

* Phantom **runs** on Windows in v1.0 — every feature except real
  isolation works. We don't gate the Windows audience on a v1.2
  date.
* The honest passthrough is auditable: the warning fires every
  process restart, and the audit log marks each call's
  `tier=passthrough` so reviewers see the lack of isolation.
* By tying Trust Level 4 to a non-passthrough backend, we keep the
  worst-case behaviour (no-prompt destructive commands) off the
  table on Windows by default.

### Negative

* The "4-tier sandbox" pitch in our marketing is **POSIX-only** in
  v1.0. We document this in the README's platform-support table.
* We must follow through on AppContainer for v1.2; Windows users
  who buy the security pitch are entitled to the real thing.

### Neutral

* Operators who want isolation on Windows in v1.0 can run Phantom
  inside WSL2 — the Linux path with bubblewrap/firejail then works
  unchanged. We document this as an interim recommendation.

## Alternatives considered

### A. Block Phantom on Windows entirely until v1.2

Rejected. It cuts ~30% of the developer market for a feature that
most Windows operators don't enable anyway (Trust Level 4 is
opt-in). Better to ship a working agent with clearly-marked limits
than no agent at all.

### B. Ship a thin Job-Object-only backend in v1.0 and call it
"sandboxed"

Rejected as misleading. Job Objects gate resource use but don't
restrict filesystem access, registry access, or network — the things
operators expect from a sandbox. Calling it sandboxed when it isn't
loses us trust the moment a security researcher scrutinises it.

### C. Require WSL2 on Windows

Rejected as a hard requirement. WSL2 is the right answer for power
users who want isolation today, but mandating it would block every
Windows dev who hasn't enabled WSL2. We document it as the
recommended path in the README; we don't enforce it.

### D. Ship AppContainer in v1.0

Rejected on schedule. AppContainer integration with our policy DSL
needs proper design + testing across Windows 10 Home / 10 Pro /
11 Home / 11 Pro / Server 2019 / Server 2022. Doing it well takes a
sprint. We commit to v1.2.

## Validation

* `phantom doctor` on Windows reports `sandbox: passthrough (no
  isolation)` so operators see the state at install time.
* Audit log entries written by `PassthroughBackend` carry
  `tier=passthrough` — searchable.
* Trust Level 4 on a passthrough host requires explicit config
  override; default config refuses.

## Implementation

* `phantom/sandbox/backends/passthrough.py` — the backend.
* `phantom/tests/test_sandbox_passthrough.py` — 17 tests covering
  probe gating, env handling, timeouts, truncation, warning
  semantics.
* `tests/sandbox/test_no_unsandboxed_subprocess.py` — allowlist now
  permits `passthrough.py` to call subprocess.run directly.

## Reviewers

Reviewed by: Aravind Labs (sole maintainer).
