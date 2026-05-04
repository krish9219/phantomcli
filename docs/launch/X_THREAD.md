# X / Twitter launch thread

A 10-tweet thread for launch day. Replace `[link]` with the GitHub repo URL on send.

---

**1/** Phantom v1.0 is out. Open-source AI coding agent with a real sandbox, atomic edits, AST-aware refactor, signed plugin mirror, and a daemon mode that beats Go/Rust harnesses on warm-path latency.

MIT core. Hosted Pro tier sells multi-key pool + mirror. Single dev. → [link]

---

**2/** Sandboxing isn't a permission prompt. It's bubblewrap → firejail → unshare → docker fallback chain + 40+ permanently blocked destructive patterns + 4 trust tiers. God Mode is always blocked on remote channels (Telegram/Discord/Slack/Matrix).

The first AI agent I trust on God Mode.

---

**3/** Cross-harness memory import. Already use Claude Code? Codex? OpenCode?

`phantom memory import claude-code` absorbs every transcript into Phantom's episodic memory. Continue conversations from where the other harness left off. No context loss.

---

**4/** Edit transactions you can't half-finish.

EditTransaction stages every file change, snapshots first, generates a unified-diff preview, then atomically commits — or restores from snapshot on any failure. SIGKILL-safe via on-disk write-ahead log.

23 dedicated tests. 0 known bugs.

---

**5/** AST-aware rename — Python AND JS/TS.

`phantom.refactor.rename_python_symbol` walks the AST, respects shadowing, handles nested scopes, and never touches strings or comments. JS/TS variant understands template literals, regex literals, block scopes.

46 tests. Real refactors, not regex.

---

**6/** Plugin mirror with detached Ed25519 signatures + tar-slip safe extract.

```
phantom plugin search github
phantom plugin install github-pr --require-signed
phantom plugin publish ./my-plugin
```

Run your own mirror in 3 commands: Caddyfile + systemd unit + Dockerfile in deploy/.

---

**7/** Daemon mode hides Python's cold-start physics.

`phantom serve` starts once. `phantom connect` is a 0.6 ms unix-socket round-trip. The cold-start gap vs Rust/Go disappears for actual usage.

Cold-start (binary): 268 ms.
Warm round-trip (daemon): 0.6 ms.

---

**8/** Channels: CLI, WebChat, Telegram, Discord, Slack, Matrix. PWA installable on any phone with offline POST queueing via IndexedDB + Web Push subscriptions.

Channel ABC is small (5 methods). Adding IRC / Element / Mattermost is a couple hundred lines.

---

**9/** 16 provider presets out of the box (Together, Fireworks, DeepInfra, Mistral, Groq, NVIDIA, OpenRouter, DeepSeek, Perplexity, Cerebras, xAI, Ollama, LM Studio, vLLM, GitHub Models). Custom OpenAI-compat in one command.

```
phantom config provider preset together
```

Done.

---

**10/** Tests: 2,070 passing, 0 failing.
Single binary: 45 MB, no UPX (start time matters more than size).
License: MIT core + Razorpay-backed Pro tier (₹999 lifetime, 3 devices).

I'm Aravind, sole dev. Buying a Pro license is the most direct way to support this. → [pro link]
