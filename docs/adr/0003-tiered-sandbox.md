# ADR-0003 — Tiered sandbox: bubblewrap → firejail → unshare → docker

* Status:  Accepted
* Date:    2026-04-25
* Authors: Aravind Labs

## Context

PhantomCLI v3's `executor.py` ships a 4-level trust gate, a permanent
blocklist of destructive commands, and an audit log. All three are useful.
None of them are a sandbox. A clever prompt injection that constructs a
non-blocklisted destructive command (e.g. `python -c "import os; os.system(...)"`)
runs against the host with the user's full privileges.

OpenClaw uses Docker / Podman containers as the isolation boundary. That is
a real sandbox. It is also heavy: the daemon must be installed, images
must be pulled, and on a fresh laptop the first invocation has multi-second
cold-start latency. For a single-user CLI that wants to feel local, that is
unacceptable as the *only* option.

We need a sandbox that is:

1. **Real** — kernel-enforced isolation, not advisory.
2. **Cheap to start** — sub-100 ms cold latency for short commands.
3. **Available** — works without a daemon and without root.
4. **Falls back** — if the preferred mechanism is missing, the next-best
   one is used silently, with the chosen tier logged.
5. **Configurable** — operators can pin a tier or disable a tier.

## Decision

A four-tier sandbox, picked at runtime based on what the host offers. From
strongest-and-cheapest to strongest-and-heaviest:

| Rank | Backend       | Why                                                            |
|------|---------------|----------------------------------------------------------------|
| 1    | **bubblewrap**| Lightweight, no daemon, ships in apt/dnf/brew, <50 ms cold.    |
| 2    | **firejail**  | Widely available alternative to bwrap; older but well-tested.  |
| 3    | **unshare**   | Pure kernel namespaces. Always available on Linux ≥ 3.8.      |
| 4    | **docker**    | Heavy, daemon, but works on macOS and Windows-WSL where the    |
|      |               | other three are awkward. Required tier on non-Linux hosts.     |

Selection algorithm (`phantom/sandbox/select.py`, lands in Stage 1):

1. Read `~/.phantom/config.json` → `sandbox.preferred` and
   `sandbox.disabled` lists. Operator override always wins.
2. Probe each backend with a 50 ms `--version` check.
3. Pick the highest-ranked available backend that is not on the disabled
   list.
4. Cache the choice for the lifetime of the process; re-probe on next
   start.
5. If **none** are available, the agent refuses to run shell tools and
   exits with a setup hint pointing at the install instructions.

Resource limits applied at every tier:

* CPU time:    operator-configurable, default 60 s.
* Wall time:   operator-configurable, default 300 s.
* RSS:         512 MiB default, 4 GiB ceiling.
* FD count:    256 default.
* Output:      capped at 1 MiB stdout + 1 MiB stderr; truncation announced.
* Network:     **off by default**; enabled per-call via explicit capability.

Filesystem layout inside the sandbox:

* `/`           — read-only bind of host root (excluding /home, /root).
* `/workspace`  — read-write bind of the agent's working directory.
* `/tmp`        — fresh tmpfs, 256 MiB, wiped on exit.
* No access to `/etc/shadow`, SSH keys, AWS creds, GPG keyring, browser
  profiles, etc. Implemented as an explicit deny-list bound over `/dev/null`.

## Alternatives considered

### Pure-Python sandbox (seccomp-bpf via libseccomp bindings)

Tempting because it is dependency-free, but the surface is enormous and
write-protecting paths from inside the same process is a known-hard
problem. Rejected.

### Always-Docker

Universal but slow and requires a daemon. Forces every user to install
Docker before they can chat. Rejected as the only option; kept as the
fallback for non-Linux hosts.

### Single-tier (bwrap only)

Cleanest. But on macOS bwrap has no native port and on hardened
distributions firejail is sometimes the only one available. Rejected.

### gVisor / nsjail / Firecracker

Strongest isolation per VM-byte, but distribution headaches: gVisor is
Linux-amd64 only and large; Firecracker is hypervisor-level and
inappropriate for a CLI. Filed as a future possibility for the Pro tier.

## Consequences

**We get:**

* A sandbox that is **objectively stronger** than v3's in-process trust gate
  and **at parity** with OpenClaw's Docker-based isolation on Linux —
  while being faster on cold start.
* A clean configuration story: one config key, one fallback chain.
* Auditability: every sandboxed call logs the chosen tier, the duration,
  the exit code, and a hash of the command line.

**We pay:**

* Four backends to maintain. Each one has its own argument format and
  edge cases. Each gets its own contract test (Stage 1).
* On hosts that have **none** of the four installed, Phantom refuses to
  run shell tools. We compensate with a clear setup hint and a one-line
  install command in the README.

## Stakes

If this decision is wrong:

* **Worst case** — a backend has a CVE and the others do not pick up the
  slack. Mitigation: the disabled-list config lets operators turn off a
  vulnerable tier without code changes.
* **Reversal cost** — adding or removing a tier is a self-contained
  change in `phantom/sandbox/`. We can deprecate firejail in a future
  release if upstream goes unmaintained without breaking callers.
* **Probability of regret** — low. The selection-with-fallback pattern is
  already used by `pip` (build-system selection), `git` (credential
  helper), and `python` itself (random / urandom).
