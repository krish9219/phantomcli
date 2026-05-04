# Show HN: Phantom — sandboxed AI coding agent with cross-harness memory import

I built Phantom over the last several months because every AI coding harness either runs everything as me (Claude Code, OpenCode) or fakes a sandbox that's bypassable in 2 minutes (most of the rest). I also kept losing context every time I switched between Claude Code, Codex, and OpenCode — three transcripts, three different memories.

Phantom is open-core (MIT for the entire CLI + sandbox + plugins + dashboard; Pro tier sells the multi-key pool and hosted mirror — it's how I keep the lights on).

What's notable:

- **4-tier sandbox with bubblewrap → firejail → unshare → docker fallback chain.** Every shell call goes through it. 40+ destructive patterns are permanently blocked. God Mode is always disabled on remote channels (Telegram, Discord, Slack, Matrix).
- **Cross-harness memory import.** `phantom memory import claude-code | codex | opencode` reads other agents' transcripts into Phantom's episodic memory. Continue your Claude Code conversation in Phantom.
- **Transactional multi-file edits with crash recovery.** EditTransaction stages every write, snapshots first, and atomically commits or restores from a write-ahead log. First agent I know that won't half-edit your repo on SIGKILL.
- **AST-aware rename for Python and JS/TS** — scope-aware, shadowing-correct, never touches strings or comments.
- **Plugin mirror with detached Ed25519 signatures.** Tar-slip safe extraction. SHA-256 verified on every install.
- **Daemon mode** (`phantom serve` + `phantom connect`) gives sub-1ms warm round-trips that beat Go/Rust harnesses on the realistic warm path. Cold start is still Python physics (~270ms binary, ~120ms script).
- **Mermaid in dashboard AND TUI.** TUI auto-detects kitty graphics protocol / sixel / ASCII fallback.
- **PWA with offline outbox + Web Push.** Service worker queues your offline POSTs in IndexedDB and flushes on reconnect.

Numbers: 2,070 tests passing, 0 failures, 5 env-gated skips. ~120ms cold start in script mode, 0.6ms via daemon. 41 MB resident.

Honest about what's NOT shipped: WhatsApp/iMessage adapters (channel breadth ceiling without partner integrations), full TypeScript type-checker integration (the AST rename is regex-with-scope, not a real type checker — accurate enough for 90% of refactors).

Repo: https://github.com/krish9219/phantomcli (LICENSE: MIT for the public surface)
Pro tier (hosted mirror + multi-key pool): https://phantom.aravindlabs.tech

I'd love feedback on:
1. The sandbox API — is the 4-tier model the right knob?
2. The cross-harness importer — what other agents should ship transcripts I can ingest?
3. The plugin mirror's signing scheme — detached vs. embedded was a real design decision; happy to discuss.

Built solo. Counting on this for income — buying a Pro license is the most direct way to support development.
