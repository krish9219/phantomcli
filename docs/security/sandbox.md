# Phantom Sandbox — Operator Guide

> Plain-English tour of the sandbox: what it does, how to install it,
> how to read its audit log, what it protects against, and what it does
> not.

The sandbox is the security-critical core of Phantom v4. Every shell
command and every tool call routes through it. If you operate Phantom in
production, you should understand at minimum the contents of this file.

If you are a developer working on the sandbox itself, also read
[`phantom/sandbox/README.md`](../../phantom/sandbox/README.md) and
[ADR-0003](../adr/0003-tiered-sandbox.md).

---

## Quick start

```bash
$ phantom doctor
Phantom doctor                          v4.0.0-dev
  ✓ python 3.11+               (3.13.12)
  ✓ phantom package            (importable)
  ✓ omnicli legacy package     (importable)

  Sandbox backends:
    ✓ bwrap                (tier 1)
    ✗ firejail             (tier 2)
    ✓ unshare              (tier 3)
    ✗ docker               (tier 4)

  Selected sandbox: bwrap
```

If the line above says `Selected sandbox: <name>`, you're good. If it
says `No sandbox available`, install one (see below) before running
shell-using tools.

## Installing a backend

Pick the strongest one your distro ships:

| Distro family    | Recommended backend | Install                         |
|------------------|---------------------|---------------------------------|
| Debian / Ubuntu  | bwrap (tier 1)      | `sudo apt install bubblewrap`   |
| Fedora / RHEL    | bwrap (tier 1)      | `sudo dnf install bubblewrap`   |
| Arch             | bwrap (tier 1)      | `sudo pacman -S bubblewrap`     |
| Alpine           | bwrap (tier 1)      | `sudo apk add bubblewrap`       |
| macOS            | docker (tier 4)     | Docker Desktop                  |
| Windows + WSL2   | bwrap inside WSL    | Same as your WSL distro         |

Phantom always falls back to `unshare` (tier 3) on Linux, even with
none of the above installed. That gives you kernel-namespace isolation
out of the box. Installing bwrap on top is a strict upgrade.

## Pinning a tier

If you have multiple backends installed and want to force one:

```bash
# Pin via env var (one-shot):
PHANTOM_SANDBOX_TIER=docker phantom run -- echo hi

# Pin permanently in config:
cat > ~/.phantom/config.json <<EOF
{
  "sandbox": {
    "preferred": "bwrap",
    "disabled": ["docker"]
  }
}
EOF
```

The env var takes precedence over the config file. Setting both
`preferred` and `disabled` for the same name is a no-op (the disable
wins).

## Reading the audit log

The log lives at `~/.phantom/sandbox-audit.log` (or `$PHANTOM_HOME/sandbox-audit.log`).
Each line is a self-contained JSON object:

```json
{"ts":"2026-04-25T17:30:01.234567Z","code":"ok","tier":"bwrap",
 "cmd_sha256":"a1b2c3...","argv_len":3,"policy_hash":"abc123",
 "deadline_s":300.0,"duration_s":0.0123,"exit_code":0,
 "truncated":false,"pid_actual":null,"phantom_ver":"4.0.0-dev"}
```

The schema is stable across releases. Every key in the example is
guaranteed to be present. New keys may be added (new tooling can rely on
their presence; old tooling can ignore them).

| Key            | Meaning                                                     |
|----------------|-------------------------------------------------------------|
| `ts`           | ISO-8601 UTC timestamp.                                     |
| `code`         | Outcome short ID. `ok` on success; one of the              |
|                | `phantom.errors.SandboxError.code` values otherwise.        |
| `tier`         | Backend that ran the command.                               |
| `cmd_sha256`   | SHA-256 of the argv joined by `\0`. Stable across calls.    |
| `argv_len`     | Number of argv tokens (`cmd_sha256` pre-image cardinality). |
| `policy_hash`  | Short hash of the policy's security envelope.               |
| `deadline_s`   | Wall-clock deadline used.                                   |
| `duration_s`   | Wall-clock duration (4-decimal precision).                  |
| `exit_code`    | Process exit code. `null` on launch failure.                |
| `truncated`    | True iff stdout/stderr were capped.                         |
| `phantom_ver`  | Phantom version that wrote the record.                      |

We deliberately do NOT log:

* The actual command line (only its hash).
* Stdout or stderr.
* The user's environment variables.

If you want richer logging, run with `PHANTOM_AUDIT_VERBOSE=1` (lands
in Stage 8); the local default protects user privacy.

## Rotating the log

The log is append-only by design. Rotate it with logrotate:

```
# /etc/logrotate.d/phantom
/home/*/.phantom/sandbox-audit.log {
    weekly
    rotate 14
    compress
    missingok
    notifempty
    create 0600
}
```

Phantom does not lock the log; logrotate's `copytruncate` (or simple
rotate) is safe.

## What the sandbox protects against

* **A prompt-injection that asks the model to `rm -rf /` your laptop.**
  Two layers stop it: the permanent blocklist (defence in depth) catches
  the obvious patterns; the sandbox's filesystem deny-list and read-only
  roots make even a creative variant unable to touch SSH keys, cloud
  credentials, or your browser profile.
* **A model that wants to exfiltrate your secrets over the network.**
  By default the sandbox has no network. The agent has to ask
  explicitly for a network-enabled tool, and the operator can deny it.
* **A model that wants to fork-bomb your machine.** The blocklist + the
  fd-count + RSS + CPU-time ceilings catch this.
* **A model that wants to read `/etc/shadow`.** Bind-mount of
  `/dev/null` over `/etc/shadow` (bwrap), or host filesystem
  permissions (unshare) — the file is unreadable.
* **A model that wants to overwrite your Phantom license or memory DB.**
  `~/.phantom` is on the deny list. The agent can't even see it from
  inside a sandboxed tool.

## What the sandbox does NOT protect against

* **A kernel-level CVE.** If the host kernel is compromised, no sandbox
  can defend you. Keep your kernel updated.
* **A user who explicitly disables the sandbox** (Stage 8 will document
  the `--unsafe-no-sandbox` flag for the rare case where it makes
  sense; it requires an operator-set env var to even be available).
* **Side-channel attacks** like Spectre / Rowhammer.
* **A tool that produces a network egress through DNS-over-something
  on a host where `network=True`.** The sandbox is binary on network:
  on or off. There is no fine-grained egress filter today.

## Common questions

**Q: What if I want a hermetic build (no system tools)?** Pass
`read_only_paths=()` when constructing the policy. The sandbox is then
fully sealed; even `/usr/bin/ls` is invisible. You'll have to mount
your own toolchain via `writable_paths`.

**Q: Can I get a shell inside the sandbox to debug?** Yes:
`phantom run -- /bin/sh`. You'll be in a sandboxed shell with the
default policy.

**Q: Can the sandbox call `setuid` programs?** No — every backend drops
all capabilities. `sudo` etc. silently lose their privilege escalation
inside the sandbox. This is intentional.

**Q: Why does my command see only `/usr`, `/bin`, `/lib`, `/lib64`,
`/etc`?** Those are the default `read_only_paths`. Most CLI tools are
under one of those. If you need something outside (e.g. `/opt/myapp`),
add it to the policy.

**Q: How fast is it?** On Linux with bwrap, cold start is sub-50 ms for
a trivial command. On macOS with docker, expect 1-2 seconds for the
first call (image-pull); subsequent calls reuse the cached image.

## Reporting issues

Sandbox bugs are security-critical. Report via `SECURITY.md`, not the
public issue tracker.
