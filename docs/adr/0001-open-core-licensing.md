# ADR-0001 — Open-core licensing instead of closed-commercial or pure-MIT

* Status:  Accepted
* Date:    2026-04-25
* Authors: Aravind Labs (with Phantom v4 architect)

## Context

PhantomCLI v3 ships fully closed-source under a commercial license. The
license server is Razorpay-backed, devices are Fernet-bound, and the
dashboard is gated by license-presence middleware. Revenue: predictable.
Distribution: limited — every user who tries Phantom is a user we have
already convinced to pay before they have used it.

We are about to ship Phantom v4, which is going to compete with OpenClaw —
an MIT-licensed multi-channel agent platform that is openly extensible and
already has sponsors (OpenAI, GitHub, NVIDIA). On the dimensions we measured,
v4 needs to win on:

* Channel reach (we are adding 6 channels)
* Plugin extensibility (we are shipping a plugin SDK)
* Sandboxing depth (we are shipping bubblewrap-tier isolation)
* Multi-agent / MCP (we are shipping ACP + MCP server-mode)

For each of those, the OpenClaw market expects to be able to **read the
source**. A closed product that promises a sandbox cannot win the trust of a
sysadmin who is about to give it `sudo`. A closed plugin SDK gets zero
third-party plugins.

## Decision

The Phantom v4 core ships under **MIT**. A commercial **Pro tier** ships under
the existing closed commercial license, gating only the surfaces where
proprietary value clearly accrues to a deployment, not to the product's
reach.

### What is in the MIT-licensed core

* The `phantom` CLI and REPL.
* The sandbox tier (Stage 1) and the executor.
* The plugin SDK and loader (Stage 2).
* The channel adapter framework and the 6 first-party channels (Stage 3).
* MCP client + server (Stage 4).
* ACP multi-agent runtime (Stage 4).
* Skills bundle format and loader (Stage 5).
* The on-device memory layer (FTS5 + sqlite-vss vectors) (Stage 5).
* Local realtime voice (Whisper + Piper) (Stage 6).
* The PWA shell (Stage 6).
* The i18n catalogues (Stage 7).

### What is in the commercial Pro tier

* The hosted **license server** at `phantom.aravindlabs.tech` and the
  device-binding it enables.
* The **multi-tenant Web Dashboard** with team workspaces, audit export,
  and OpenTelemetry-export integrations.
* The **license-managed API key pool** for >2 keys (the open core supports
  two so individual users do not feel hand-cuffed; teams that need pooled
  keys with rotation pay).
* **Priority support** with an SLA.
* **Hosted plugin index mirror** (the static index format is open; the
  hosted, vetted, signed mirror is Pro).

## Alternatives considered

### Pure-MIT (everything open)

Wins on community and trust. Loses on revenue: there is no clear paid
surface. We have already shipped a v3 with paying customers; we cannot
ethically pull the rug on them. Rejected.

### Pure closed-commercial (status quo)

Wins on revenue predictability. Loses on every distribution dimension we
need v4 to win. The OpenClaw playbook works precisely because their core is
free; a closed product cannot replicate it. Rejected.

### "Source-available, non-commercial" (BSL / Elastic v2 / SSPL)

Lets us publish the code without losing revenue, but it is not OSI-approved
and the plugin / contributor community treats it as closed. Distribution
gain ≈ zero. Rejected.

### Dual-license (AGPL core + commercial Pro)

A defensible middle ground but introduces friction for hobbyists who run
Phantom inside a private repo or company. The lawyer-overhead cost is
disproportionate to the value we capture. Rejected.

## Consequences

**We get:**

* Permission to talk about Phantom in places that reject closed-source agents
  (HN, r/selfhosted, security forums).
* A surface where third-party plugin authors can plausibly contribute.
* The ability to claim "audit our sandbox yourself" with a straight face.
* Continued v3 license revenue migrated to v4 via the Pro tier.

**We pay:**

* The marketing site, license server, and dashboard now have to be
  unambiguously the things being paid for. They have to be good.
* We accept that some users will run an "almost as good" core for free
  forever. That is fine; they were never going to pay for v3 anyway.
* Each new core feature must be evaluated against the question: "is this
  Pro or core?" The default answer is **core**. Anything that touches
  multi-tenancy, hosted services, or team workflows is Pro.

## Stakes

If this decision is wrong:

* **Worst case** — we lose paid Pro conversions because the open core is
  "good enough". Mitigation: the Pro feature set is intentionally chosen
  around team workflows that single users do not need.
* **Reversal cost** — re-closing the source after release is socially
  expensive but legally trivial (we own the copyright). We can stop
  releasing core under MIT at any time; releases already shipped stay MIT
  forever, which is acceptable.
* **Probability of regret** — low. Open-core is the dominant model for
  developer-tooling startups in 2026 (HashiCorp v1, GitLab, Sentry, Posthog
  all run versions of it).
