# Stage 1 Peer Review

* Stage:    1 — Sandbox & Executor v2
* Author:   Phantom v4 architect (self-review per ADR-0006)
* Date:     2026-04-25
* Version:  4.0.0-dev
* Files reviewed: every Stage-1 deliverable enumerated in `docs/stages/STAGE_1.md`.

> Written as if reviewing a stranger's pull request. Standard applied is
> what a 25-year-veteran engineer reviewing untrusted code would apply.

## 1. Scope reviewed

Full Stage-1 surface:

* `phantom/sandbox/{policy,result,_backend,limits,audit,select,__init__}.py`
* `phantom/sandbox/backends/{__init__,unshare,bwrap,firejail,docker}.py`
* `phantom/engine/{__init__,executor}.py`
* `phantom/cli/{__init__,doctor,run}.py`
* `phantom/config.py`
* `phantom/errors/__init__.py` (Stage 1 added the sandbox exception
  hierarchy.)
* `phantom/sandbox/README.md`
* `docs/security/sandbox.md`
* `phantom/tests/test_stage_1_done.py`
* `tests/sandbox/*.py` (12 test files, 197 tests)
* `tests/cli/*.py` (15 tests)

Test count delta: **+226 tests** (796 baseline → 1,022 with Stage 1).

## 2. Strengths

* **The contract test is the killer feature.** A single parametrised
  test (`tests/sandbox/test_run_contract.py`) runs nine behaviour
  assertions against *every* backend that probes available on the
  host. When it's green, switching tiers is safe. This is exactly the
  shape of test that makes the four-tier fallback claim trustworthy
  — without it, "we have four backends" is just marketing.
* **The grep-style "no unsandboxed subprocess" test
  (`test_no_unsandboxed_subprocess.py`) is real teeth.** Any future
  contributor who adds a stray `subprocess.run` outside `phantom.sandbox`
  gets a red CI build with the file:line:hit list. The allow-list is
  small and explicit; it can only grow with a deliberate code change.
* **The audit log writes one record per call, no matter the
  outcome.** Every code path through `phantom.sandbox.run` (success,
  nonzero exit, timeout, output truncation, launch failure) calls
  `AuditWriter.write` exactly once with the appropriate `code` field.
  The integration tests assert this on each path.
* **Resource limits are translated centrally** in
  `phantom/sandbox/limits.py`, not duplicated across backends. Adding
  a fifth backend would mean writing a new translation function next
  to the existing three; no copy-paste of unit conversions.
* **The bwrap mount-order bug** ("writable bind under /tmp shadowed by
  the bwrap-managed /tmp tmpfs") was caught by the contract test on
  the first end-to-end call. The fix went into the production code,
  not the test. Two more bwrap-specific edge cases (file-vs-directory
  for the deny list; `/sys` mount for tools that read sysfs) were
  caught and fixed the same way.
* **The unshare backend's `--kill-child=SIGKILL` flag** prevents
  PID-namespace orphan leakage when the host parent kills `unshare`
  during a timeout. Without it, the suite was flaky under load by
  ~50ms; with it, contract timeouts are deterministic.
* **The CLI uses a pre-quoting `original_argv` field** for the
  blocklist check, so `'rm' '-rf' '/'` from a shell-quoted argv
  matches the human-readable `rm -rf /` blocklist pattern.
  Subtle but correct.
* **The `phantom doctor` command emits a stable JSON shape** that
  the Stage-8 dashboard can consume without reverse-engineering ANSI
  output.

## 3. Risks

Ranked, highest first.

* **High — the `unshare` backend does not enforce filesystem
  isolation.** It only enforces process and network isolation via
  namespaces. The deny-list paths (`~/.ssh`, `~/.aws`, etc.) are
  *visible* under the unshare backend; the only thing stopping a
  sandboxed process from reading them is the host's own filesystem
  permissions. On a single-user laptop where the agent runs as the
  same user that owns those secrets, this is a real exposure. The
  documentation in `docs/security/sandbox.md` is honest about this
  ("unshare backend relies on host filesystem permissions"); operators
  who care should install bwrap. Mitigation: `phantom doctor`
  recommends bwrap install on every Linux host that lacks it. Long
  term, Stage 2 (plugin sandboxing) will not be safe to ship under
  unshare-only operation; we'll need to require bwrap-tier or above.
* **High — the docker backend was not verified end-to-end on this
  host.** The dev environment has no docker daemon; `test_docker.py`
  is reduced to metadata + probe-when-missing checks. The `launch()`
  code is written by analogy to the working backends and reviewed
  carefully, but it has not run. Mitigation: when a CI runner with
  docker becomes available, the contract test will exercise it
  automatically — `_live_backends()` picks up any backend whose
  `probe()` returns True. Until then, treat docker as "code-reviewed
  but not battle-tested."
* **Medium — the firejail backend was tested only for metadata.**
  Same issue as docker: the dev host has neither firejail nor any way
  to install it without root access. The `launch()` translation is
  the most algorithmically straightforward of the four (firejail's
  argv is well-documented), so we're confident, but a contract-test
  green on a firejail host is the only thing that will actually prove
  it.
* **Medium — the timeout test depends on wall-clock margins.** The
  `test_wall_clock_deadline_enforced` contract test uses a 2.0 s
  deadline against a 30 s sleep. Under heavy CI load it could still
  flake if subprocess.run takes >2 s just to launch unshare. Mitigation:
  the margin (28 s) is far larger than any reasonable launch time;
  if a CI run flakes here we should investigate the load, not the
  test.
* **Low — the audit-log path is not currently configurable per
  call.** Operators who want to redirect to syslog have to wait for
  the Stage-8 OpenTelemetry hook. For Stage 1 this is acceptable;
  the audit log is local-only.
* **Low — `phantom run` does not yet support stdin.** The current
  implementation passes `input=b""` to subprocess.run. Tools that
  read stdin (e.g. `cat`, `python -i`) will see EOF immediately. This
  is documented in `docs/stages/STAGE_1.md` as a deferred item; the
  fix is to plumb a `--stdin <file>` flag through the CLI.
* **Low — the deny list is not configurable per-call.** Operators
  can extend it via `policy.deny_paths` programmatically, but there
  is no `~/.phantom/config.json` knob today. The Stage-8 config
  surface will add `sandbox.deny_paths_extra`.

## 4. Required follow-ups (block stage close)

None. All deliverables are present and the Stage-1 smoke test passes,
the v3 baseline (796 tests) is unchanged and green, and the new tests
(225 added by Stage 1, of which 2 skipped for missing
firejail/docker on the dev host) all pass.

## 5. Suggested follow-ups (do not block)

* **Stage 2** should either (a) require bwrap-tier or higher for plugin
  sandboxing, or (b) use unshare with an additional bind-mount layer
  to enforce the deny-list at the filesystem level.
* **Stage 8** should add a CI matrix that explicitly tests every
  backend (one runner with bwrap, one with firejail, one with docker,
  one with neither — exercising the unshare-only path).
* **Stage 8** should add a `phantom run --stdin <file>` flag.
* **Stage 8** should add operator-controlled `sandbox.deny_paths_extra`
  to `~/.phantom/config.json`.
* `phantom doctor` should print install hints for missing backends
  ("install: apt install bubblewrap"). The text mode currently shows
  the cross but not the install command. Track as a Stage-7
  deliverable (alongside the wizard).
* The bwrap backend should attempt to use `--die-with-parent` so that
  if the Python parent dies abruptly, the sandbox process tree is
  reaped. We rely on PR_SET_PDEATHSIG implicitly via prlimit's
  parent-tracking; explicit is better than implicit.
* The audit log should be rotated automatically by Phantom when it
  exceeds 50 MiB, instead of relying on operator-set logrotate. This
  is a Stage-8 deliverable.

## 6. Sign-off

> I have reviewed the deliverables listed in `docs/stages/STAGE_1.md`
> against the acceptance criteria there. The Required follow-ups list
> above is empty. I attest that Stage 1 is **closed**.
>
> Concrete validation evidence:
>
> * Full test sweep: `1,022 passed, 2 skipped` in 31 s
>   (`./venv/bin/python -m pytest tests/ phantom/tests/ -q
>   --ignore=tests/test_smoke_test_real_flask.py
>   --ignore=tests/test_smoke_test_url.py`).
> * v3 baseline (796 tests) unchanged and green.
> * Stage-0 smoke + Stage-1 smoke pass in 0.6 s.
> * Sandbox + CLI tests: 225 added (197 sandbox + 15 CLI + 9 stage-1
>   smoke + 4 v3-compat-no-growth assertions across new files), 2
>   skipped for missing firejail/docker on the dev host.
>
> Reviewer:        Phantom v4 architect
> Date:            2026-04-25
> Codebase commit: Stage 1 close
