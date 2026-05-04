# Phantom — local AI agent

> The AI assistant that runs on your laptop, answers on your phone via
> PWA, and lives in the channels you already use — without uploading
> your data anywhere.

* **Sandbox-first**: every shell call goes through bubblewrap →
  firejail → unshare → docker. ADR-0003.
* **Plugin SDK**: third-party developers ship Phantom plugins as
  Python packages with declared capabilities and Ed25519 signatures.
* **Multi-channel**: WebChat, Telegram, Discord, Slack at v4 launch;
  Matrix, IRC, WhatsApp on the Stage-8 roadmap.
* **MCP + ACP**: speak the open Model Context Protocol; coordinate
  multi-agent waves with the Agent Communication Protocol.
* **Memory v2**: SQLite + FTS5 + TF-IDF hybrid retrieval, namespaced
  per `(user, project, session)`.
* **Realtime voice**: local STT / TTS pipeline with VAD-driven flush
  and barge-in.
* **PWA installable** from `phantom.aravindlabs.tech/app` — service
  worker offline cache, push notifications, no app-store gate.
* **i18n**: en / hi / te / es / zh out of the box.
* **Open-core MIT**: Pro tier (multi-tenant dashboard, license-managed
  key pool, hosted plugin signing) is commercial. ADR-0001.

## Quick start

```bash
git clone https://github.com/krish9219/phantomcli
cd phantomcli
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
phantom doctor
phantom run -- echo "hello phantom"
```

## Roadmap

The v4 rebuild ships in nine stages. See the
[stage index](../docs/stages/README.md) for what's done and what's
next. Each stage has a deliverables file, a peer-review file, and an
in-package smoke test.

## Why Phantom over OpenClaw?

OpenClaw is an excellent multi-channel AI gateway. Phantom v4 wins on:

* **Sandbox depth** — 4-tier fallback vs Docker-only.
* **Test coverage on security paths** — 100 % branch on
  `phantom.sandbox.*`.
* **Lean codebase** — every concept fits in one file.
* **Documentation discipline** — ADRs, stage docs, peer reviews on
  every change.

OpenClaw still wins on channel breadth (Matrix, IRC, WhatsApp,
iMessage, …) and on native-app reach (we ship a PWA instead). The
v4 plan trades some surface area for a tighter, more auditable core.

## License

[Open-core](../LICENSE): MIT for the CLI / sandbox / plugins / channels
/ MCP / ACP / skills / memory / voice / canvas / PWA / i18n.
Commercial Pro tier for hosted dashboard, license-managed API key pool
>2 keys, and the plugin signer.
