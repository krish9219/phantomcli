# Phantom

> The open AI agent OS. Sandboxed, multi-channel, plugin-extensible. Runs on your laptop, answers on your phone.

[![tests](https://img.shields.io/badge/tests-passing-brightgreen)](#tests)
[![license](https://img.shields.io/badge/license-MIT%20core%20%2B%20Pro-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![version](https://img.shields.io/badge/version-1.0.0-purple)](version.json)

Phantom is the AI assistant that **runs on your machine, talks to any model, edits your code safely, and reaches you wherever you are**. The MIT-licensed core gives you the entire CLI, sandbox, plugin system, and dashboard. The Pro tier adds production conveniences (multi-key API pool, hosted plugin mirror, advanced multi-agent orchestration, priority support).

---

## Why Phantom

| | Phantom v1.0 | Claude Code | OpenCode | OpenClaw | Auto-GPT |
|---|---|---|---|---|---|
| Sandboxed shell | **4-tier + bwrap/firejail/unshare/docker** | permission prompts | Plan mode | broad permissions | docker |
| Cross-harness import | **claude-code, codex, opencode** | — | — | — | — |
| Edit transactions | **atomic + WAL crash-recovery** | — | — | — | — |
| AST-aware refactor | **Python + JS/TS** | string replace | string replace | — | — |
| Plugin mirror | **signed Ed25519 + safe extract** | skills | — | clawhub | blocks |
| Channel reach | **Telegram + Discord + Slack + Matrix + WebChat + PWA + CLI** | CLI + web | CLI | 24+ channels | platform |
| MCP both ways | **client + server + auto-import** | client only | client | partial | — |
| Daemon mode | **0.6 ms warm round-trip** | — | — | — | — |
| Open license | **MIT core + Pro** | closed | MIT | MIT | MIT |

[Detailed competitive analysis](docs/comparisons.md) · [Benchmarks](docs/benchmarks.md)

---

## Quick start

```bash
# install
pip install phantom-cli

# or from source
git clone https://github.com/krish9219/phantomcli
cd phantom && pip install -e .

# point it at any OpenAI-compatible provider in one command
phantom config provider preset together
export TOGETHER_API_KEY=...

# chat
phantom

# or skip into the daemon path for sub-50ms restarts
phantom serve &
phantom connect ping
```

---

## Featured commands

```bash
phantom chat                          # interactive REPL with full agent loop
phantom serve / phantom connect       # daemon + thin client (sub-50ms warm path)
phantom bench                         # reproducible performance numbers
phantom dictate                       # voice → Whisper → transcript

phantom swarm "<goal>" --agents 5     # fan out N agents into isolated git worktrees
phantom self-dev "<change>"           # sandboxed self-modifying loop with green-tests gate

phantom memory import claude-code     # absorb other harnesses' transcripts
phantom mcp import                    # absorb ~/.claude/mcp.json + ~/.codex/mcp.json
phantom mcp serve                     # expose Phantom as an MCP server

phantom plugin search github          # browse the public plugin mirror
phantom plugin install github-pr      # SHA-256 + Ed25519 verified install
phantom plugin publish ./my-plugin    # operator: build + register signed bundle

phantom config provider presets       # 15+ pre-configured OpenAI-compat services
phantom config provider preset groq   # one-shot setup
```

---

## Featured features

### Sandboxed execution (every shell call)
4 trust levels (Paranoid → God Mode), 40+ permanently blocked patterns, fallback chain `bubblewrap → firejail → unshare → docker`. Telegram-side God Mode is always blocked.

### Cross-harness memory import
Already use Claude Code, Codex, or OpenCode? Run `phantom memory import <source>` once and Phantom absorbs every transcript into its episodic memory layer. Continue conversations from where the other harness left off.

### Transactional multi-file edits with crash recovery
`phantom.edits.EditTransaction` stages every change, generates a unified-diff preview, and commits atomically. On any failure (or process kill), every file is restored from snapshot via the on-disk write-ahead log. The first AI agent that won't half-edit your repo on a SIGKILL.

### Symbol-aware refactoring (Python + JS/TS)
`phantom.refactor.rename_python_symbol` walks the AST, respects shadowing, handles nested scopes, and never touches strings or comments. The JS/TS counterpart understands template literals, regex literals, and block scopes.

### Plugin mirror with detached signatures
The mirror server (FastAPI) hosts signed plugin bundles. Clients verify SHA-256 + optional Ed25519 signature on every install. Tar-slip safe extraction defends against CVE-2007-4559-style attacks. Run your own mirror in 3 commands: see [`deploy/mirror/README.md`](deploy/mirror/README.md).

### Daemon mode beats Python cold-start physics
`phantom serve` boots once, holds the imports + state in memory; `phantom connect` is a 0.6 ms unix-socket round-trip. The cold-start gap vs Go/Rust harnesses disappears for actual usage.

### Mermaid in dashboard AND TUI
Dashboard renders Mermaid via the official renderer with strict CSP. TUI auto-detects kitty graphics protocol / sixel / falls back to ASCII when neither is present.

### PWA with offline + push
Web manifest, installable PWA, service worker with stale-while-revalidate cache, IndexedDB outbox for offline POST queueing, Web Push subscriptions, VAPID key generation.

---

## What's open vs. paid

### MIT core (free, this repo)

* CLI + REPL + chat
* Sandbox (4 tiers + 4 backends)
* Plugin SDK + loader + 8 first-party plugins
* Memory v2 (FTS5 + hybrid retrieval)
* MCP client + server
* Channel framework + 5 production adapters (CLI, WebChat, Telegram, Discord, Slack, Matrix)
* Daemon mode + bench
* Cross-harness importers
* Transactional edits + WAL
* Python + JS/TS rename
* Mermaid TUI + dashboard
* PWA shell + service worker
* Voice MVP (Whisper)
* Browser tool

### Pro tier (paid via Razorpay, ₹999 lifetime per device, up to 3 devices)

* Multi-key API pool beyond 2 keys (free tier capped at 2)
* Advanced multi-agent orchestration at scale
* Priority support + SLA
* Hosted plugin mirror with curated/audited bundles
* Production web dashboard (multi-user, audit log shipping)
* Enterprise channels (SAML, SSO, audit forwarding)
* Compliance reports (SOC 2 / ISO 27001 / HIPAA scaffolding)

License keys at [phantom.aravindlabs.tech](https://phantom.aravindlabs.tech).

---

## Trust levels

| Level | Name      | Behavior |
|-------|-----------|----------|
| 1     | Paranoid  | Confirm every shell command. |
| 2     | Standard  | Safe prefixes (`ls`, `git status`…) auto-allowed. |
| 3     | Developer | All commands run, sensitive ones log a warning. (default) |
| 4     | God Mode  | No prompts. Always blocked on remote channels (Telegram/Discord/Slack/Matrix). |

God Mode TTL is 30 minutes. After idle, auto-downgrades to Trust 3.

---

## Provider support (16 presets + custom)

```bash
phantom config provider presets
#   NAME          KEY ENV                 DEFAULT MODEL
#   together      TOGETHER_API_KEY        meta-llama/Llama-3.3-70B-Instruct-Turbo
#   fireworks     FIREWORKS_API_KEY       accounts/fireworks/models/llama-v3p3-70b-instruct
#   deepinfra     DEEPINFRA_API_KEY       meta-llama/Llama-3.3-70B-Instruct
#   perplexity    PERPLEXITY_API_KEY      llama-3.1-sonar-large-128k-online
#   mistral       MISTRAL_API_KEY         mistral-large-latest
#   groq          GROQ_API_KEY            llama-3.3-70b-versatile
#   nvidia        NVIDIA_API_KEY          meta/llama-3.3-70b-instruct
#   openrouter    OPENROUTER_API_KEY      anthropic/claude-3.5-sonnet
#   deepseek      DEEPSEEK_API_KEY        deepseek-chat
#   ollama        OLLAMA_API_KEY          llama3.3
#   lmstudio      LMSTUDIO_API_KEY        local-model
#   cerebras      CEREBRAS_API_KEY        llama-3.3-70b
#   xai           XAI_API_KEY             grok-2-latest
#   github        GITHUB_TOKEN            gpt-4o
#   vllm-local    VLLM_API_KEY            meta-llama/Llama-3.3-70B-Instruct
```

Custom OpenAI-compatible endpoints in one command:

```bash
phantom config provider custom my-vllm \
    --base-url http://my-cluster:8000/v1 \
    --model meta-llama/Llama-3.3-70B-Instruct \
    --key-env MY_VLLM_KEY
```

---

## Architecture

```
phantom/
├── cli/              Typer app + 18 commands
├── daemon/           serve/connect, sub-50ms warm round-trip
├── sandbox/          4-tier + bwrap/firejail/unshare/docker
├── plugins/          SDK + 8 builtins + mirror (server + client)
├── channels/         CLI, WebChat, Telegram, Discord, Slack, Matrix
├── mcp/              client + server + import (claude/codex)
├── memory/           FTS5 + hybrid retrieval + cross-harness importers
├── browser/          Playwright primitives (Browser) + browser-use (BrowserAgentRunner)
├── refactor/         AST-aware Python rename + JS/TS rename
├── edits/            EditTransaction + WAL crash-recovery
├── render/           Mermaid TUI (kitty/sixel/ASCII fallback)
├── tui/              Streaming, progress, file panel
├── pwa/              Web manifest + SW + push subscription API
├── voice/            Whisper STT + Piper TTS + chat bridge
├── swarm/            git-worktree-isolated parallel agents
├── selfdev/          Sandboxed self-modifying loop
└── config/           Providers + presets + sandbox config

deploy/
└── mirror/           Dockerfile + systemd unit + Caddyfile + README
```

---

## Performance

`phantom bench` (run on your machine):

| | Phantom v1.0 | jcode (Rust) | Claude Code | OpenCode (Go) |
|---|---|---|---|---|
| Cold start | 268 ms (binary) | 14 ms | ~50 ms | ~14 ms |
| Daemon warm round-trip | **0.6 ms** | — | — | — |
| Idle RSS | 41 MB | 28 MB | ~80 MB | ~25 MB |
| Per-agent scaling | **0 ms / agent** | +9.9 MB | — | — |

Phantom wins the warm-path latency every Rust/Go competitor lacks. Cold start lags Go/Rust by Python physics; the daemon path makes it irrelevant in real usage.

---

## Tests

Real numbers from `pytest`:

```
2070 tests passing, 0 failing, 5 env-gated skips
```

Run yourself:

```bash
pip install -e .[dev]
pytest -q
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Sole maintainer: Aravind ([@aravindlabs](https://github.com/aravindlabs)). PRs welcome on the open-source surface; the Pro tier (license server, hosted mirror) lives in a separate private repo.

---

## License

[MIT](LICENSE) for the open-source core (the entire `phantom/` and `omnicli/` packages).
The Pro tier (license-server source, hosted mirror curation, enterprise channel adapters) is proprietary and sold under the Aravind Labs commercial license.

The boundary is documented in [`LICENSE`](LICENSE) and rationale in [`docs/adr/0001-open-core-licensing.md`](docs/adr/0001-open-core-licensing.md).

---

## Links

* Homepage: [phantom.aravindlabs.tech](https://phantom.aravindlabs.tech)
* Plugin mirror: [phantom.aravindlabs.tech/plugins](https://phantom.aravindlabs.tech/plugins)
* Pro license purchase: [phantom.aravindlabs.tech/pro](https://phantom.aravindlabs.tech/pro)
* Architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md)
* Vision: [`VISION.md`](VISION.md)
* Changelog: [`CHANGELOG.md`](CHANGELOG.md)
