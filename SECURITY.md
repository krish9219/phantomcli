# Phantom Security Policy

> Phantom is a local AI agent that executes shell, writes files, browses
> the web, and integrates with chat channels. It is a security-sensitive
> piece of software. We take vulnerability reports seriously and respond
> on a clear timeline.

---

## Reporting a vulnerability

**Do not open a public GitHub issue.** Email
`security@aravindlabs.tech` with:

* A description of the issue.
* The earliest version you can reproduce against.
* A minimal proof-of-concept (or steps to reproduce).
* Your name / handle for credit, or "anonymous" if you prefer.

We will acknowledge within **2 business days**, target a fix within
**14 days** for high-severity issues, and coordinate disclosure with
you. CVE assignment goes through MITRE.

PGP key for encrypted reports:
`https://phantom.aravindlabs.tech/.well-known/security/pgp.asc`.

## Scope

In scope:

* The `phantom/` and `omnicli/` packages.
* The PWA (`https://phantom.aravindlabs.tech/app`) and its service worker.
* The license server API (`/api/license/*`).
* The plugin signature verification path.
* The sandbox tier-selection logic (Stage 1+).
* Any first-party plugin or channel adapter shipped in the wheel.

Out of scope:

* Third-party plugins that have not been vetted by Aravind Labs.
* User-installed MCP servers.
* The model providers themselves (NVIDIA, Anthropic, OpenAI, etc.).
* Bugs in dependencies that we have not pinned to a vulnerable version
  (file those upstream and let us know).

## Threat model

Phantom assumes:

* The user trusts the model they are talking to **with the capabilities
  they have explicitly granted**, but does not trust it to respect the
  trust gate or the blocklist if the model is adversarial or
  prompt-injected.
* The user is the local privileged operator. Anyone else with access to
  their box already has greater capability than Phantom can grant.
* The host kernel is not compromised. (If the kernel is compromised,
  the sandbox cannot defend the user; that is true of every sandbox.)
* The user's network is hostile to the level of "ISP can MITM
  un-pinned TLS". Therefore: TLS pinning where feasible, and the
  license-cache is signed.

Out of threat model:

* A user who deliberately disables the sandbox and runs in God Mode on a
  production server. We document the risk and do not engineer around
  it.
* Side-channel attacks on the host (Spectre, Rowhammer). Phantom relies
  on the kernel and the CPU to defend against these.

## Hardening checklist (per release)

Each release runs the following before tagging:

* `bandit -r phantom -lll` — no high-severity findings.
* `pip-audit` — no known CVEs in pinned dependencies.
* `semgrep --config=p/security-audit phantom/` — no critical findings.
* `mypy --strict phantom/` — clean.
* `pytest -q -m security` — every security-marked test passes.
* The smoke tests for **all** closed stages pass.

The Stage-8 release pipeline (`docs/stages/STAGE_8.md`) makes this
non-negotiable.

## Known limitations

We document, rather than fix, the following:

* **God Mode** (Trust 4) intentionally bypasses the trust gate. It
  cannot bypass the sandbox. It can be set by a prompt injection only
  if the user has previously typed `/trust 4`; the activation has a
  TTL.
* **Plugin signature verification** (Stage 2) protects against
  third-party tampering, not against a compromised signing key. Key
  rotation procedure is documented in `docs/security/key-rotation.md`
  (lands with Stage 2).
* The PWA's service worker cache can serve stale UI for up to 24 hours
  after a security release. Critical fixes ship a new service worker
  with `skipWaiting()` enabled.

## Coordinated disclosure

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure):

1. You report privately.
2. We confirm and triage.
3. We develop a fix and prepare an advisory.
4. We agree a public-disclosure date with you.
5. We release the fix and the advisory at the same time.
6. CVE published.
7. You get public credit (or stay anonymous, your choice).

Hall of fame: `https://phantom.aravindlabs.tech/security/hall-of-fame`.
