# `phantom.sandbox`

> Tiered process isolation for shell and tool execution. Every `subprocess`
> call in Phantom flows through this module.

If you are looking for *why* this module exists, read
[`docs/adr/0003-tiered-sandbox.md`](../../docs/adr/0003-tiered-sandbox.md).
This README is the working developer's tour: what the module exposes,
how the pieces fit, and how to extend it.

---

## Public surface

```python
from phantom.sandbox import (
    run,                # the main entry point
    SandboxPolicy,      # declarative policy
    SandboxResult,      # return shape
    ResourceLimits,     # ceiling fields
    select_backend,     # which tier will run?
    available_backends, # which tiers probe ok?
)
```

Everything else is private.

## The four-tier fallback chain

| Rank | Backend     | When picked                                              |
|------|-------------|----------------------------------------------------------|
| 1    | `bwrap`     | Linux + bubblewrap installed (fastest cold start).       |
| 2    | `firejail`  | Linux + firejail installed (alternative isolation).      |
| 3    | `unshare`   | Linux ≥ 3.8 with user namespaces (always available).     |
| 4    | `docker`    | Daemon reachable (only practical option on macOS / WSL). |

Selection probes each backend in rank order; the first available one
wins. Operators can pin a tier with `PHANTOM_SANDBOX_TIER=bwrap` or
disable a tier with `~/.phantom/config.json` `sandbox.disabled`.

## The launch flow

```
caller                     phantom.sandbox.run()
                                 │
                                 ▼
                       select_backend()  ── cached after first call
                                 │
                                 ▼
                          backend.launch()  ── tier-specific subprocess
                                 │
                                 ▼
                         SandboxResult
                                 │
                                 ▼
                       AuditWriter.write()  ── one JSON line per call
```

## Files

| File                          | Purpose                                                  |
|-------------------------------|----------------------------------------------------------|
| `__init__.py`                 | Public API: `run`, types, exception re-exports.          |
| `_backend.py`                 | `SandboxBackend` ABC. Every tier implements it.          |
| `policy.py`                   | `SandboxPolicy`, `ResourceLimits`, deny-list defaults.   |
| `result.py`                   | `SandboxResult`.                                         |
| `select.py`                   | Backend selection + caching + override.                  |
| `limits.py`                   | Translates `ResourceLimits` to per-backend argv.         |
| `audit.py`                    | Append-only JSON-line audit-log writer.                  |
| `backends/__init__.py`        | Registry + ordered listing.                              |
| `backends/bwrap.py`           | bubblewrap wrapper.                                      |
| `backends/firejail.py`        | firejail wrapper.                                        |
| `backends/unshare.py`         | unshare + prlimit (always-available Linux fallback).     |
| `backends/docker.py`          | docker wrapper.                                          |

## Adding a new backend

1. Implement a subclass of `SandboxBackend` in `phantom/sandbox/backends/<name>.py`.
2. Set `name` and `tier_rank` (lower = preferred).
3. Implement `probe()` (return True iff the backend is usable on the
   current host; never raise).
4. Implement `launch(argv, policy)` (run the command and return a
   `SandboxResult`; raise the appropriate `SandboxError` subclass on
   failure).
5. Add the class to the registry in `backends/__init__.py`.
6. Add backend-specific tests in `tests/sandbox/test_<name>.py`.
7. The contract test in `tests/sandbox/test_run_contract.py` runs
   automatically against your backend when its `probe()` returns True
   on the test host.

## What the sandbox guarantees

* **No host network** by default. Operators opt in with
  `policy.network=True`.
* **No host filesystem** outside what `policy.read_only_paths` and
  `policy.writable_paths` mount. Default: `/usr`, `/bin`, `/lib`, `/lib64`,
  `/etc` read-only; nothing writable.
* **No host secrets.** `DEFAULT_DENY_PATHS` hides SSH keys, cloud
  credentials, browser profiles, password managers, and Phantom's own
  config. Backends that support filesystem masking (bwrap) bind
  `/dev/null` over them; backends that don't (unshare) rely on host
  filesystem permissions, which already deny ordinary users access to
  these targets.
* **Resource ceilings.** CPU time, RSS, FD count, output bytes — all
  capped via `prlimit` (or docker `--ulimit`) before the command runs.
  Wall-clock deadline enforced by the Python `subprocess.run(timeout=…)`
  in the parent.
* **Audit trail.** One JSON line per call to
  `~/.phantom/sandbox-audit.log`. Mode 0600. The log records the SHA-256
  of the argv, *not* the argv itself — what was run, not what it
  said.

## What the sandbox does NOT guarantee

* **Defence against a kernel-level CVE.** The sandbox relies on the host
  kernel's namespace and seccomp implementations. If the kernel is
  compromised, the sandbox cannot defend the user.
* **Side-channel resistance.** Spectre, Rowhammer, etc. are not in
  scope. The sandbox is an integrity boundary, not a confidentiality
  boundary.
* **Network isolation across `--no-network` boundaries on the same
  host.** Two simultaneous Phantom sandboxes on the same host with
  `network=False` see each other's namespace activity through `/proc`
  on backends that don't isolate `/proc` aggressively. Use docker for
  multi-tenant scenarios.
