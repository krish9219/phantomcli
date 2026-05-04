# Stage 1 — Sandbox & Executor v2

> Goal: every shell-style tool call in Phantom runs inside a kernel-enforced
> sandbox with declared resource ceilings, network capability, and a
> filesystem deny-list. The v3 trust gate stays in place as a *second*
> defence inside the sandbox; it is no longer the only line.

* Status:  CLOSED
* Author:  Phantom v4 architect
* Started: 2026-04-25
* Closed:  2026-04-25
* Test count at close: 1,022 passed (796 v3 baseline + 226 new in Stage 0+1).

---

## 1. Goal

Replace `omnicli.executor.run_safe`'s direct `subprocess.run` with a
sandboxed call. Implement four backends (bubblewrap → firejail → unshare
→ docker) behind a single `phantom.sandbox.run()` API. Provide the
selection logic, the resource-limit translation, the filesystem mount
plan, and a complete audit trail. Achieve **100 % branch coverage** on
the sandbox module — it is the security-critical core of v4.

ADR-0003 has the architectural rationale; this file has the deliverables.

## 2. Deliverables

Every bullet references a real file path. The Stage-1 smoke test asserts
each is present.

### Core sandbox package

* `phantom/sandbox/__init__.py` — public API: `run`, `SandboxPolicy`,
  `SandboxResult`, `select_backend`, `available_backends`. Fully typed.
* `phantom/sandbox/_backend.py` — :class:`SandboxBackend` ABC: every
  tier conforms to this. Methods: `probe()`, `launch()`, `name`,
  `tier_rank`.
* `phantom/sandbox/policy.py` — :class:`SandboxPolicy` (frozen
  dataclass): mounts, env allow-list, network on/off, resource limits,
  deadlines.
* `phantom/sandbox/result.py` — :class:`SandboxResult`: stdout, stderr,
  exit code, tier name, wall-clock, truncation flag.
* `phantom/sandbox/select.py` — backend selection with caching,
  configuration override, and a probe-cache invalidation hook.
* `phantom/sandbox/limits.py` — translates abstract resource ceilings
  (CPU s, RSS MiB, FD count, output bytes) into per-backend invocation
  flags.
* `phantom/sandbox/audit.py` — append-only audit log writer at
  `~/.phantom/sandbox-audit.log` (mode 0600). Records SHA-256 command
  hash, chosen tier, deadline, outcome, duration, exit code.

### Backend implementations

* `phantom/sandbox/backends/bwrap.py` — bubblewrap wrapper.
* `phantom/sandbox/backends/firejail.py` — firejail wrapper.
* `phantom/sandbox/backends/unshare.py` — Linux namespaces + prlimit
  pure-kernel fallback.
* `phantom/sandbox/backends/docker.py` — docker wrapper for non-Linux
  hosts and operator preference.
* `phantom/sandbox/backends/__init__.py` — registry of all backends in
  rank order.

### Executor v2

* `phantom/engine/executor.py` — new sandboxed `execute_bash`. Routes
  through `phantom.sandbox.run`; preserves the v3 trust gate, blocklist,
  and audit-log behaviour as the *second* layer.
* `phantom/engine/__init__.py` — public engine surface.
* `omnicli/executor.py` — **unchanged**; it remains the v3 implementation
  for backwards compat.

### CLI surface (Stage 1 slice)

* `phantom/cli/__init__.py` — Typer app skeleton.
* `phantom/cli/doctor.py` — `phantom doctor` command. Reports installed
  sandbox tiers, Python version, optional dependencies, and points at
  install hints for missing tiers. This is the user-facing entry point
  for "why doesn't shell work?".
* `phantom/cli/run.py` — `phantom run -- <cmd>` for direct sandbox
  testing. Bypasses the agent loop; runs the command in a fresh
  sandbox using the current selection. Useful for debugging.

### Configuration

* `phantom/config.py` — typed config loader for `~/.phantom/config.json`.
  Schema-validated (jsonschema). Env-var overrides per ADR-0005.
* `phantom/config_schema.py` — auto-generated jsonschema document
  consumed by the dashboard's settings UI.

### Tests (Stage 1)

* `phantom/tests/test_stage_1_done.py` — Stage-1 smoke test.
* `tests/sandbox/test_policy.py` — `SandboxPolicy` validation.
* `tests/sandbox/test_select.py` — backend selection: probe, fallback,
  override, caching.
* `tests/sandbox/test_audit.py` — audit-log format, file mode, atomicity.
* `tests/sandbox/test_limits.py` — limit translation per backend.
* `tests/sandbox/test_bwrap.py` — bwrap launch (skipped if not installed).
* `tests/sandbox/test_firejail.py` — firejail launch (skipped if not
  installed).
* `tests/sandbox/test_unshare.py` — unshare launch (always runnable on
  Linux ≥ 3.8).
* `tests/sandbox/test_docker.py` — docker launch (skipped if no daemon).
* `tests/sandbox/test_run_contract.py` — backend-agnostic contract:
  every backend that probes available must satisfy the same set of
  behavioural assertions (stdout capture, exit code propagation,
  network deny default, deadline enforcement, resource cap enforcement,
  filesystem mount semantics).
* `tests/sandbox/test_executor_v2.py` — executor v2 round-trip with
  trust-gate + sandbox + audit log.
* `tests/sandbox/test_no_unsandboxed_subprocess.py` — grep-style test
  that asserts no `phantom/*.py` file (outside `phantom/sandbox/`) calls
  `subprocess.run`, `subprocess.Popen`, `os.execvp`, or `os.system`
  directly.
* `tests/cli/test_doctor.py` — `phantom doctor` output shape.

### Documentation

* `phantom/sandbox/README.md` — module-level "why" + a sequence diagram
  of the selection-and-launch path.
* `docs/security/sandbox.md` — operator-facing tour: how to install
  bwrap/firejail, how to pin a tier, how to read the audit log, what
  the sandbox does and does not protect against.
* `docs/security/key-rotation.md` — placeholder pointing at Stage 2.
* `docs/peer-reviews/STAGE_1.md` — peer review.

## 3. Validation

```bash
# (1) Stage-1 smoke test passes.
$ ./venv/bin/python -m pytest phantom/tests/test_stage_1_done.py -v
... PASSED ...

# (2) Sandbox unit + contract tests pass.
$ ./venv/bin/python -m pytest tests/sandbox/ -v
... <N> passed ...

# (3) The "no unsandboxed subprocess" test passes.
$ ./venv/bin/python -m pytest tests/sandbox/test_no_unsandboxed_subprocess.py -v
... PASSED ...

# (4) Branch coverage on phantom/sandbox/ is 100 %.
$ ./venv/bin/python -m pytest --cov=phantom.sandbox --cov-branch \
                              --cov-report=term-missing tests/sandbox/
TOTAL                                          XXX     0     0   100.00%

# (5) The legacy v3 baseline still passes (regression gate).
$ ./venv/bin/python -m pytest tests/ test_phantom.py -q
... 796 passed ...

# (6) phantom doctor reports the local sandbox state.
$ ./venv/bin/phantom doctor
Phantom doctor                            v4.0.0-dev (stage 1)
  ✓ python 3.11+               (3.13.12)
  ✓ phantom package            (importable)
  ✓ omnicli legacy package     (importable)
  Sandbox backends:
    ✗ bubblewrap               (not found — install: apt install bubblewrap)
    ✗ firejail                 (not found — install: apt install firejail)
    ✓ unshare                  (kernel 6.16, namespaces ok)
    ✗ docker                   (daemon not reachable)
  Selected sandbox: unshare    (tier 3)

# (7) phantom run -- echo OK works.
$ ./venv/bin/phantom run -- echo OK
OK
```

## 4. Acceptance criteria

* [ ] `phantom.sandbox.run` exists, is typed, and is the only place that
  calls `subprocess.*` in `phantom/*` (enforced by
  `tests/sandbox/test_no_unsandboxed_subprocess.py`).
* [ ] All four backends implement the `SandboxBackend` ABC.
* [ ] Branch coverage on `phantom.sandbox.*` is 100 %.
* [ ] The Stage-1 smoke test passes.
* [ ] The legacy 796-test baseline still passes.
* [ ] `phantom doctor` reports the local state.
* [ ] `phantom run -- <cmd>` round-trips a command through the sandbox.
* [ ] `docs/security/sandbox.md` is reviewer-readable in under 10 minutes.
* [ ] The peer-review file is signed off with an empty Required
  follow-ups list (per ADR-0006).

## 5. Known limitations

* The audit log is local-only. Stage 8 wires it into the
  OpenTelemetry exporter for users who want centralised auditing.
* Plugin sandboxing (re-using this same machinery from a plugin's
  perspective) lands in Stage 2.
* The Pro-tier dashboard's "live sandbox events" feed is a Stage-8
  deliverable. Stage 1 only writes the audit log; Stage 8 ships the
  reader.

## 6. Smoke test

`phantom/tests/test_stage_1_done.py` (lands as part of this stage).

The test asserts:

1. `phantom.sandbox.run` is importable.
2. `phantom.sandbox.select_backend()` returns *some* backend on Linux
   (unshare always available).
3. A trivial `["echo", "stage-1-ok"]` round-trips and returns
   `stdout == "stage-1-ok\n"`, `exit_code == 0`, and the chosen tier
   matches `select_backend()`'s answer.
4. A bogus deadline (`deadline_s=0.001`) raises
   `phantom.errors.SandboxTimeoutError`.
5. The audit log received exactly one entry for the round-trip above
   and the entry has the expected JSON keys.
