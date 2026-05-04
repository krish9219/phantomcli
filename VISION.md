# Phantom Vision

> Phantom is the AI assistant that **runs on your laptop, answers on your
> phone, and lives in the channels you already use** — without uploading your
> data to anybody else's cloud.

This document is the long-form "why" of the project. The README tells you how
to install and use Phantom; this file tells you what we are building toward
and what trade-offs we have already made.

---

## Who Phantom is for

* **Senior individual contributors** — engineers, ops, founders — who want a
  single agent that holds context across projects, executes shell, browses
  the web, and is reachable from terminal _and_ phone _and_ chat.
* **Small teams** that want to deploy a private AI gateway behind a single
  Razorpay-licensed instance, instead of paying per-seat for a SaaS that
  ships their data to a third-party.
* **Developers who build on top of agents** — Phantom's plugin SDK, MCP
  surface, and ACP multi-agent runtime are first-class extension points.

Phantom is explicitly **not** trying to be:

* A consumer chatbot. (No avatar customisation, no roleplay scaffolding.)
* A SaaS. (We sell licenses, not seats.)
* A model lab. (We use models; we do not train them.)

---

## Why we forked the architecture from PhantomCLI v3 → Phantom v4

PhantomCLI v3 (`omnicli` package) is a tight, sellable single-user CLI. It
wins on focus, security, and commercial polish. It loses on:

| Department          | v3 weakness                  | v4 strategy                                                   |
|---------------------|------------------------------|---------------------------------------------------------------|
| Channel reach       | Telegram only                | Adapter framework + 6 first-party channels (Stage 3)          |
| Sandboxing          | Trust gate runs in-process   | bubblewrap → firejail → unshare → docker fallback (Stage 1)   |
| Extensibility       | Closed monolith              | Plugin SDK + signed bundles + static registry (Stage 2)       |
| Multi-modal         | One-shot TTS / image         | Realtime voice loop + canvas host (Stage 6)                   |
| Mobile              | None                         | PWA installable from `phantom.aravindlabs.tech` (Stage 6)     |
| Multi-agent         | Internal `subagents.py`      | ACP-conformant runtime + MCP server-mode (Stage 4)            |
| Memory              | FTS5 lexical only            | Hybrid BM25 + vector with swappable backend (Stage 5)         |
| Skills              | None                         | Anthropic-style skills bundles (Stage 5)                      |
| i18n                | English only                 | gettext + en/hi/te/es/zh (Stage 7)                            |

The v4 plan does not ship as a single big-bang release. Each stage is a
separately reviewed, separately documented increment that can be cut at any
time. If we have to stop after Stage 3, Phantom is still strictly better than
v3.

---

## Open-core licensing

The Phantom core (CLI, sandbox, plugin loader, channel framework, memory v2,
skills, MCP) is **MIT-licensed open-source**. The Pro tier (web dashboard,
license-managed API key pool >2 keys, advanced multi-agent orchestration,
priority support, hosted plugin index mirror) is **commercial**, sold via
[phantom.aravindlabs.tech](https://phantom.aravindlabs.tech) under the
existing Razorpay-backed license server.

Why open-core, not pure-commercial?

1. **Distribution.** Open-core removes the friction that kept v3 stuck behind
   a license-gate the dashboard couldn't show off.
2. **Plugin ecosystem.** Third-party developers will not write plugins for a
   closed product they cannot read or fork.
3. **Trust.** Anyone running a security-sensitive agent on their own box wants
   to be able to read the sandbox's source.
4. **Revenue is preserved.** The Pro tier targets the same users who already
   buy v3 licenses (teams, agencies, power users) — they pay for the
   dashboard and the multi-agent orchestration, not for the CLI binary.

ADR-0001 has the full reasoning and the alternatives we rejected.

---

## Non-goals (what we will deliberately not do)

* **Native iOS/Android apps.** A PWA installable from the browser covers the
  use case at a fraction of the maintenance cost. ADR-0004.
* **A SaaS-hosted Phantom.** The product runs on the user's machine. We host
  the license server, the docs, the plugin index, and the marketing site —
  not the agent.
* **Model training / fine-tuning.** We are downstream consumers of model APIs
  and on-device models (Whisper / Piper). Anything model-side is out of
  scope.
* **Closed extensions.** Every extension point we add to core is documented
  and stable. If we add a hook, it gets a public ABI test.

---

## How we know we are winning

A few measurable signals tracked by the release pipeline:

| Signal                               | Stage 0 baseline | v4.0.0 target    |
|--------------------------------------|------------------|------------------|
| Test count                           | 796              | ≥ 1,800          |
| Branch coverage on security modules  | not measured     | 100%             |
| Channels supported                   | 1 (Telegram)     | 7 (incl. WebChat)|
| Plugins in reference index           | 0                | 5+ first-party   |
| Stages closed with peer review       | 0                | 9                |
| Mean turn latency (local Llama 70B)  | not measured     | ≤ existing v3    |

If any of these regress for a release, the release is blocked.
