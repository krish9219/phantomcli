# Changelog

All notable changes to PhantomCLI / Phantom are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The major version cadence:

* **v3.x** — `omnicli` package, Python 3.9+, single-binary commercial CLI.
  Frozen feature set; receives security patches only.
* **v4.x** — `phantom` package, Python 3.11+, open-core distribution.
  Active development. See `docs/stages/` for the roadmap.

---

## [1.1.1] — 2026-05-08 — Phantom shell (REPL) + installer shim fix

Patch release. Two surface improvements on top of the v1.1.0 licensing
landing:

### Added

* **Phantom shell (REPL).** Running `phantom` with no subcommand now
  drops you into an interactive prompt (`phantom> `) where every
  existing subcommand works without re-typing `phantom`. Banner shows
  current licence tier inline (`Pro`, `Pro · trial · 11d remaining`,
  or `Free`). Built-ins: `help`, `exit`/`quit`/`:q`, `clear`. Each line
  is `shlex.split` and dispatched into the existing Typer app, so
  `SystemExit` from `--help` or command errors no longer kills the
  loop. Uses `prompt_toolkit` when available (history, line editing,
  Ctrl+R search) with `input()` fallback for piped/non-TTY hosts.
* `~/.phantom/.repl_history` — persistent shell history across sessions.

### Fixed

* **Installer shim.** `install.ps1` and `install.sh` were generating a
  shim that ran `python -m phantom.cli` without setting `PYTHONPATH`.
  Outside the install directory the shim failed with
  `ModuleNotFoundError: No module named 'phantom'`. Both shims now bake
  in `PYTHONPATH=$INSTALL_DIR` so `phantom` works from anywhere.

### Tests

* 10 new REPL tests covering: exit/quit/EOF, blank-line skipping,
  subcommand dispatch, `help` survives, unknown command survives,
  parse errors are reported, the Pro gate firing inside the REPL
  doesn't kill the loop, and the no-args entry actually triggers
  `run_repl()`. Full local suite: 2186 passed, 8 skipped, 0 failed.

---

## [1.1.0] — 2026-05-08 — Pro tier gating + 14-day trial + licensing backend

Phantom now ships in two tiers. **Free** (chat, plugins, memory, MCP, bench,
doctor, version) stays free for everyone, forever. **Pro** (daemon mode,
swarm runner, voice dictation, sandboxed self-dev) is gated behind a
₹999 lifetime licence covering up to 3 devices. Every new install gets a
**14-day full-Pro trial** so users can feel the daemon's sub-50ms warm
roundtrip and the swarm runner's parallel diff collection before deciding.
Existing installs from before v1.1.0 are detected by mtime and
**grandfathered as Pro forever** — no friction for early adopters.

### Added

* `phantom/licensing/` — client-side licensing module. Fernet-encrypted
  cache at `~/.phantom/.license`, machine-bound via per-install seed +
  HMAC(seed, MAC). Online validation against
  `phantom.aravindlabs.tech/api/phantomcli/check-license` with 30-day
  cache and 90-day offline grace, so the daemon hot path never depends
  on network.
* `phantom license activate / status / deactivate / devices` —
  user-facing licence management. `activate` validates the key online
  and registers this device; `status` prints Pro / trial / Free; `devices`
  lists every registered machine; `deactivate` frees a slot.
* `require_pro()` — CLI-boundary gate. Wraps `phantom serve`, `phantom
  swarm`, `phantom dictate`, `phantom self-dev`. Free state prints an
  upgrade banner and exits non-zero; trial state prints "N days
  remaining" once and passes through; Pro state passes silently.
* **Licensing backend** — new FastAPI service at
  `127.0.0.1:6020` (proxied via Caddy). Endpoints: `POST
  /api/payment/order`, `POST /api/payment/verify`, `POST
  /api/payment/webhook`, `POST /api/license/resend`, `POST
  /api/phantomcli/check-license`, `POST
  /api/phantomcli/deactivate-device`, `GET /api/phantomcli/devices`.
  Postgres-backed (`licenses`, `devices`, `payments`, `webhook_events`).
  Razorpay HMAC verification on every paid path; idempotent on
  `razorpay_payment_id` and `razorpay_event_id`. SMTP delivery of the
  PHC key on payment success.

### Changed

* Buy page (`/buy.html`) wired to the new endpoints and PHC key format.
  The web "activate" tab now displays the exact `phantom license
  activate PHC-…` command rather than calling a server endpoint —
  activation is a CLI-only operation.
* Hero copy on `index.html` and `landing.html` now describes the Free vs
  Pro split honestly, including the 14-day trial and grandfathering
  policy. The FAQ "no Pro tier, no feature gating" line was removed
  (it was true for v1.0.x but not for v1.1.0).
* `install.ps1` and `install.sh` closing copy lists Free vs Pro commands
  with `[Free]`/`[Pro]` tags, points at `phantom license activate`, and
  reminds users about the 14-day trial.

### Migration

* Existing v1.0.x installs upgrading via `/update`: detected by file
  mtime in `~/.phantom`. Grandfather marker is written on first run and
  Pro stays unlocked forever. No user action required.
* Fresh v1.1.0 installs: 14-day trial starts on first `phantom`
  invocation. After 14 days, Pro features lock until a key is activated.
* Legacy `omnicli/licensing.py` is unchanged but unused by the v1.1.x
  CLI. The new `phantom/licensing/` module is the source of truth.

---

## [1.0.2] — 2026-05-08 — CI matrix green + surgical-fix system prompt

Patch release. CI now passes 100% across Linux/macOS/Windows × Python
3.11/3.12/3.13 (was failing on macOS daemon E2E and across Windows
before). Also wires Phantom's surgical-fix editing philosophy into the
default system prompt so out-of-the-box bug fixes are targeted
`edit_file` calls, not whole-file rewrites — matching how Claude Code,
Cursor, and Aider all behave.

### Fixed

* **Windows binary build** (`phantomcli.spec`): `strip=False`. PyInstaller
  was invoking GNU `strip` (the only one on hosted Windows runners, via
  MinGW) on the bundled `python312.dll`, corrupting the PE so the binary
  died at launch with `Failed to load Python DLL`. Disabling strip costs
  negligible size on POSIX and unblocks the Windows binary entirely.
* **SandboxPolicy path comparison on Windows**
  (`phantom/sandbox/policy.py`): the validator's "workdir is inside
  writable_paths" check hard-coded forward slashes, rejecting legitimate
  Windows policies (`D:\tmp\job\sub` ⊂ `D:\tmp\job`, `D:\` as root mount,
  trailing-backslash normalisation). Switched to `os.path.normpath` and
  `dirname(p) == p` for portable root detection.
* **Docker backend probe**
  (`phantom/sandbox/backends/docker.py`): now requires `OSType=linux`
  from `docker info`. Hosted Windows runners ship Docker Desktop in
  Windows-container mode by default, which rejects our `--read-only`
  flag. WSL2-backed Docker on Windows still works.

### Added

* **Surgical-fix default system prompt** (`phantom/agent/session.py`):
  `DEFAULT_SYSTEM_PROMPT` now teaches the model to read the failing
  code first, find the root cause, and apply the *minimum* change via
  `edit_file` rather than rewriting the whole file with `write_file`.
  Custom callers can still pass their own `system_prompt` to override.
* **Tightened tool descriptions** (`phantom/agent/tools.py`): `edit_file`
  is described as the always-preferred surgical edit primitive;
  `write_file` is reserved for new files / >80% rewrites.

### CI / test infra

* macOS daemon E2E tests now use a short `/tmp`-anchored socket path
  (AF_UNIX 104-byte limit was tripped by pytest's `tmp_path` under
  `/private/var/folders/...`).
* `tests/sandbox/test_policy.py` and `test_audit.py` use
  `os.path.abspath`-built constants so POSIX-style hard-coded paths
  work on Windows too.
* The bash-via-MCP smoke test, the AF_UNIX-only daemon E2E suite, and
  the "select_backend returns a real backend" assertion are
  Windows-skipped with explicit reason strings.
* Windows binary cold-start budget relaxed from 2 s → 5 s (NTFS access
  + Defender first-touch overhead).

---

## [1.0.1] — 2026-05-05 — Windows support

First public release with Windows runtime support.

### Added

* **Cross-platform daemon transport** (`phantom/daemon/transport.py`):
  unix sockets on POSIX, TCP loopback (`127.0.0.1`) on Windows with a
  per-user port hashed from `$USERNAME`. Same newline-delimited JSON
  wire format on both backends.
* **Windows passthrough sandbox** (`phantom/sandbox/backends/passthrough.py`):
  v1.0 fallback that runs commands without isolation, emits a one-shot
  loud audit warning, and disables Trust Level 4 by default. Tier rank
  99 so any real backend is always preferred. ADR-0007 documents the
  AppContainer plan for v1.2.
* **Windows encoding regression net** (`phantom/tests/test_windows_encoding_audit.py`):
  fails any v1.0 module using `open()` / `read_text()` / `write_text()`
  without explicit `encoding="utf-8"` (Windows defaults to cp1252 and
  silently mangles non-ASCII).
* `.github/workflows/tests.yml` — CI workflow for the test suite.

### Changed

* `phantom/daemon/server.py` and `client.py` refactored onto the new
  cross-platform transport.
* `phantom/voice/dictate.py` — Windows-aware audio recorder selection.
* macOS daemon warm-up: retry ping on connect-before-accept race.

### Documented

* `docs/adr/0007-windows-sandbox.md` — accepted: passthrough now,
  AppContainer in v1.2.

## [1.0.0] — 2026-05-04 — UNIFIED RELEASE

PhantomCLI renumbers from 4.0.10 to start the **v1.0** line cleanly.
`omnicli` (legacy v3 surface) and `phantom` (v4-developed features) now
both ship under the v1.0.0 banner — one product, one version.

### Added — competitor-parity sweep

* **Daemon mode** — `phantom serve` + `phantom connect` over a
  unix socket. Sub-50 ms perceived round-trip. Closes the
  cold-start gap against Rust harnesses without a rewrite.
* **Benchmarks** — `phantom bench` prints reproducible numbers:
  cold start, daemon round-trip, RSS, turn-latency p50/p95, scaling
  slope. Methodology baked into the JSON output.
* **Cross-harness importer** — `phantom memory import
  {claude-code,codex,opencode}` reads other agents' transcripts into
  Phantom's episodic memory.
* **MCP auto-import** — `phantom mcp import` slurps
  `~/.claude/mcp.json` and `~/.codex/mcp.json` (and project-local
  variants) with one command.
* **Voice MVP** — `phantom dictate` records a few seconds of audio
  via sox/arecord/parecord and transcribes through Whisper. Stub
  backend ships for offline tests.
* **Custom OpenAI-compatible providers** — `phantom config provider
  custom <name> --base-url --model --key-env` adds vLLM / Ollama /
  any compatible endpoint in one shot.
* **Swarm runner** — `phantom swarm "<goal>" --agents N` fans out
  N subagents into isolated `git worktree`s, collects diffs, and
  flags file-level conflicts.
* **Sandboxed self-dev** — `phantom self-dev "<change>"` applies an
  edit in a worktree, runs the full test suite there, and only
  swaps (with `--swap`) if green.
* **Mermaid in dashboard** — server renders fenced ` ```mermaid `
  blocks via the official renderer with `securityLevel: 'strict'`
  and a MutationObserver for streamed inserts.
* **3 first-party plugins** — `github-pr` (gh CLI inspector),
  `web-screenshot` (Playwright PNG), `code-review` (pure-Python
  static lints over a unified diff). Demonstrates the SDK end-to-end.

### Changed

* `phantom/_version.py` → `1.0.0`, tuple `(1, 0, 0)`.
* `omnicli/__init__.py` → `1.0.0`. Both packages aligned.
* `pyproject.toml` → `1.0.0`.
* `version.json` → `1.0.0`.
* `phantomcli.spec` rewritten: bundles dashboard static, builtin
  plugins, and v1 sub-packages. Builds a single `phantom` binary.
* `phantom.config` is now a package (`config/main.py` + `config/providers.py`)
  with full backwards-compatible re-exports.
* MCP source discovery dedupes by resolved path so `cwd == $HOME`
  doesn't double-count.

### Added — Sessions A/B/C closure (final wave)

* **Single binary actually built** — `pyinstaller --clean
  --noconfirm phantomcli.spec` produces `dist/phantom` (45 MB,
  no UPX so cold-start stays fast). Smoke tests assert
  `phantom version`, `--help`, `bench --json`, and the binary's
  cold-start budget. CI matrix workflow at
  `.github/workflows/binary.yml` builds + smoke-tests on
  ubuntu-latest and macos-latest.
* **Mermaid TUI renderer** (`phantom.render.mermaid`) — terminal-
  capability detection (kitty graphics protocol, sixel, ASCII),
  shell-out to `mmdc` for PNG synthesis, ASCII fallback that wraps
  to terminal width and never raises.
* **Plugin mirror** (`phantom.plugins.mirror`) — FastAPI server +
  index manager + client. Detached Ed25519 signatures stored in the
  index (no chicken-and-egg with bundle SHA-256), tar-slip safe
  extraction with explicit symlink-escape rejection, SHA-256
  verification on every install. CLI: `phantom plugin
  search/install/uninstall/publish`.
* **Transactional multi-file edits** (`phantom.edits`) — atomic
  commit, per-file snapshot, reverse-order rollback on any failure,
  mode-preserving atomic replace via NamedTemporaryFile + os.replace,
  duplicate-path guard, freshly-created files removed on rollback,
  UTF-8 strict, unified-diff preview that shows new files against
  /dev/null.
* **AST-aware Python rename** (`phantom.refactor`) — scope walker
  that respects shadowing (function locals make outer-name reads
  invisible), an explicit `only_in_function_at_line` mode for
  function-local renames, name-conflict detection at module level,
  import-alias support, and a tokenize-verified rewrite path that
  guarantees we never touch strings or comments.
* **TUI polish layer** (`phantom.tui`) — `StreamingResponse`
  (Rich-markup-safe accumulator with stray-bracket escaping),
  `ProgressTracker` (counting + ETA with rich.progress live-display
  context manager), `FileUpdateSidePanel` (LRU-bounded recent edits
  with text + Rich panel rendering).

### Tests

* +85 from the first wave plus +118 from this wave (binary smoke
  +8, mermaid TUI +17, mirror +20, edits +23, Python rename +22,
  TUI +28). **Total suite: 1971 passing, 0 failing, 5 env-gated
  skips.**

---

## [4.0.10] — 2026-04-27

### Fixed — explicit role beats dynamic persona shapeshifter

User report: a 3,500-char data-scientist prompt run via
`phantom chat` shapeshifted to "FRONTEND DEVELOPER" (powered by
z-ai/glm4.7), the model returned an empty response, and an
unrelated IPL Flask dashboard build was triggered. Root cause was a
*third* hijack point I missed in 4.0.9: ``omnicli.engine.
get_dynamic_persona()`` runs a slow-path LLM call ("what title fits
this prompt?") whenever the keyword map doesn't match cleanly. The
LLM saw "Streamlit UI" and returned "Frontend Developer" — overriding
the user's explicit "You are a senior data scientist".

### Changes

* **New helper `_persona_from_explicit_role(prompt)`** — extracts
  the role from the start of a prompt (`"You are a/an X"`,
  `"You're a/an X"`, `"Act as a/an X"`, `"Imagine you are X"`,
  `"Pretend you are X"`) and returns a clean Title-Cased string,
  capped at 4 words. Bounded to the first 300 chars so an offhand
  "your role" buried in instructions doesn't swing the persona.
* **`get_dynamic_persona`** now checks the explicit role FIRST. If
  present, it returns immediately — no keyword map, no router LLM
  call, no main-model LLM call, no API spend. The shapeshift banner
  shows the role the user asked for ("PhantomCLI shapeshifted to:
  SENIOR DATA SCIENTIST") instead of an LLM-invented title.
* The previous fast paths (small-talk, keyword map) and slow path
  (router/main model fallback) still run for prompts that don't
  declare an explicit role — so build prompts like "build a flask
  app" still pick "Full Stack Web Developer" exactly as before.

### Tests — 18 new in `tests/engine/test_persona_role_guard.py`

7 parametric extraction tests (data-scientist / ML / tutor / SQL /
security / kubernetes / backend) and 7 negative-case tests (build
prompts / small-talk / blank / mid-text role mentions). Plus 4
behavioural tests with monkeypatched ``OpenAI`` clients that fail the
test on any API call, proving the role path short-circuits the LLM
router. The exact production prompt (Streamlit + machine learning +
Phase 1/2/3) is pinned as a regression test.

Total: 1722 project tests passing.

## [4.0.9] — 2026-04-27

### Fixed — orchestrator no longer hijacks role-assigned prompts

Real-world bug: a 3,500-char data-science prompt (`"You are a senior
data scientist… inspect these files… Phase 1 — Discover, Phase 2 —
Build, Phase 3 — Execute… If anything errors, use read_file to read
the traceback…"`) was hijacked twice by the v3 orchestrator. The
fix-request heuristic matched 3 fix-pattern words inside instructional
text (`traceback`, `error`, `diagnose`) and routed the conversation
into focused-fix mode against an unrelated active project. The
multi-agent orchestrator then would have shapeshifted into the FULL
STACK WEB DEVELOPER persona based on body keywords. The user's
explicit role assignment never reached the model.

### Changes

* **`_looks_like_fix_request`** now bails when the prompt has an
  explicit role assignment (`"You are a…"`, `"Your job is…"`,
  `"Act as a…"`), 2+ phase markers (`"Phase 1 / Phase 2 / …"`), or is
  longer than 1500 chars without an actual `Traceback (most recent
  call last)` block. A real traceback still wins.
* **`AgentOrchestrator.should_spawn`** honours explicit role
  assignments verbatim — body keywords (flask / dashboard / machine
  learning) don't override `"You are a senior data scientist…"`.
* **New escape-hatch slash commands `/raw <prompt>` and
  `/agent <prompt>`** — bypass both gates and route straight to the
  single-agent path. Use this if a prompt keeps getting hijacked.

### Tests — 25 new in `tests/cli/test_orchestrator_guards.py`

The exact prompt that hit the bug in production is pinned as a
regression test. Plus parametric coverage of role recognition,
phase-marker detection, real tracebacks still triggering fix mode,
short ad-hoc fix asks still triggering, long structured prompts not
triggering, and normal build prompts still spawning the orchestrator.

Total: 1704 project tests passing.

## [4.0.8] — 2026-04-27

### Fixed — agent can finally create and edit files on its own

* **`default_tools()` now exposes `write_file`, `read_file`, `edit_file`,
  and `list_dir` as first-class agent tools.** Previously the agent had
  only `run_bash`, so creating a Python file forced the model to pipe
  source through shell heredocs (`cat > foo.py <<EOF … EOF`). Free-tier
  GitHub Models LLMs (Llama, Phi) routinely botched heredoc escaping —
  quotes, backticks, `$`, and indentation broke, leaving corrupted or
  empty files. Symptom in the wild: "Phantom isn't creating Python
  files accurately." The model now picks `write_file` directly and the
  bytes land on disk verbatim.
* **`edit_file` tool added** — exact-match string replacement
  (`old_string` → `new_string`), refuses non-unique matches unless
  `replace_all=True`. Lets the model do small in-place changes without
  rewriting whole files.
* **`AgentSession.max_tool_rounds` bumped 8 → 25.** Multi-step coding
  flows (read → edit → run tests → fix) and ML workflows hit the old
  cap mid-task. New default fits realistic loops with margin.
* **`default_tools(extra_writable_paths=…)`** lets operators extend
  the file-tool allowlist beyond the session workdir when needed
  (e.g. a Streamlit app dir, a model cache directory).

### Implementation

* `phantom/tools/fs.py` — added `edit_file()` with full unit-level
  validation (missing file, ambiguous match, non-string types,
  identical strings, allowlist enforcement).
* `phantom/agent/tools.py` — registers all five tools in
  `default_tools()` with rich JSON Schemas and explicit guidance in
  the descriptions ("DO NOT use run_bash with heredocs for file
  creation").
* `phantom/agent/session.py` — `max_tool_rounds` default raised to 25.

### Tests — 25 new in `phantom/tests/test_fs_tools.py`

* fs unit: parent-dir creation, allowlist rejection, unicode/special
  chars (the original bug class), missing-file handling.
* edit_file unit: unique replace, non-unique rejection, replace_all,
  not-found, identical strings, allowlist, missing file.
* default_tools: registration assertion (regression for the bug),
  schema validity round-trip, every handler smoke-tested,
  extra_writable_paths extension, allowlist enforcement.
* End-to-end with `ScriptedProvider`: simulated model calls
  `write_file`, then a multi-round flow `write_file` → `edit_file`
  → `read_file` validates tool results feed back correctly across
  rounds.
* Pins the new `max_tool_rounds = 25` default.

Total: 132 phantom tests green (107 + 25); 1709 project tests
collected, 1679 passed, 3 pre-existing unrelated failures
(`test_status_json` expects 3 OAuth providers but code now returns
4 since GitHub OAuth was added; `test_blocklist_blocks` Click stderr
capture infra issue; `test_spans_emitted_per_tool_call` requires
opentelemetry which isn't installed in this env).

## [4.0.7] — 2026-04-27

### Fixed — `phantom update` now self-cleanses

* **Stale `__pycache__` no longer shadows new code.** After
  extracting a new zip, ``_do_update`` walks the install tree and
  removes every ``__pycache__/`` directory it finds, then touches each
  ``.py`` file forward to current mtime. Previously, an updated
  ``omnicli/cli.py`` whose extracted-from-zip mtime predated an
  existing ``cli.cpython-311.pyc`` got ignored — Python preferred the
  cached bytecode and the user kept seeing the old menus until
  something else triggered a recompile (or a manual cache wipe).
  Symptom in the wild: 4.0.6 shipped with a new "M — Change model"
  entry that several users reported as missing after `phantom update`.
* **Update report now verifies on-disk version.** After the
  download + extract + dependency sync, `phantom update` reads
  ``INSTALL_DIR/omnicli/__init__.py`` from disk and parses out
  ``__version__`` directly, then compares it against the version it
  thought it just installed. If they differ, it prints a loud
  warning instead of the green success message and points the user
  at the clean-reinstall docs. New helper:
  ``_read_on_disk_omnicli_version()``.

### Tests

* 2 new regression tests in `phantom/tests/test_oauth_github.py`:
  on-disk version parse from synthetic init.py, missing-init no-op.
  Total OAuth tests now 30, plus 77 stage-closure smoke tests = 107
  green.

## [4.0.6] — 2026-04-27

### Fixed — OAuth client_ids actually persist now

* **`~/.phantom/.env` is loaded at startup.** Before this release the
  setup walkthroughs (W → G for GitHub, etc.) *wrote* the file but
  nothing ever *read* it back. After every `phantom update` (or any
  fresh shell), `PHANTOM_OAUTH_GITHUB_CLIENT_ID` was empty again, so
  the menu showed "not set — see option G first" even though the
  client_id was sitting on disk. Now the dotfile is loaded:
    * On `_run_setup()` entry — keeps the menu badges accurate.
    * On every `_phantom_subprocess_env()` call — child processes
      (login, doctor, plugin manager) inherit the right env even if
      the parent shell didn't have the var.
    * Standard dotenv precedence: shell `export` wins over the file.
    * Comments (`#`) and blank lines ignored. Surrounding quotes
      stripped from values.

### Fixed — Google green-dot reflects reality

* The login submenu's `●` for Google now lights up when the engine
  is wired to Gemini via the AI Studio API key path, not only when
  an OAuth token is present (which never happens for Gemini — see
  4.0.5 changelog). Same for GitHub Models: the dot now also reads
  the engine config so users who pasted a manual GitHub PAT get
  proper feedback.

### Added — model picker

* New menu entry **W → M (Change model)**. Detects the active
  provider from `main_url`, shows a curated catalog with the current
  selection marked, and saves the chosen model to `main_model`.
    * **GitHub Models catalog**: gpt-4o, gpt-4o-mini, o1-mini,
      Llama-3.3-70B-Instruct, Llama-3.2-90B-Vision-Instruct, Phi-4,
      Mistral-Large-2411, Codestral-2501, DeepSeek-V3,
      Cohere-command-r-plus-08-2024.
    * **Gemini catalog**: gemini-2.0-flash, gemini-2.0-flash-thinking-exp,
      gemini-1.5-pro, gemini-1.5-flash, gemini-1.5-flash-8b.
    * Free-form **Other** option for models not in the catalog.

### Tests

* 5 new tests in `phantom/tests/test_oauth_github.py`:
  `_load_phantom_env` happy path, shell-precedence rule, no-file
  no-op, GitHub catalog shape, Gemini catalog shape. Total OAuth
  tests now 28, plus 77 stage-closure smoke tests = 105 green.

## [4.0.5] — 2026-04-26

### Changed — honest about Google OAuth

* Removed the Google OAuth device-flow path from the setup-menu —
  it cannot work for Gemini today and shipping a path that fails
  with `400 invalid_scope` after the user does the GCP setup is worse
  than not shipping it. Two compounding reasons:
    1. Google's device-flow allowlist excludes the
       `…/auth/generative-language` scope. POST returns
       `invalid_scope: 'Invalid device flow scope'`.
    2. Even if scope worked, the OpenAI-compat shim at
       `generativelanguage.googleapis.com/v1beta/openai/` validates
       AI Studio API keys (`AIza…`), not OAuth bearer tokens (`ya29…`).
* Setup menu **W → 2** now offers:
    * **a** — paste API key from AI Studio (recommended, ~30 sec)
    * **b** — explanation panel covering both blockers + what a real
      OAuth implementation would need (Desktop OAuth client +
      loopback redirect + PKCE + native Gemini client, ~250 LoC)
* Hid the **Y — Google setup** menu entry (and stopped saving the
  client_id env var), since the path it set up couldn't complete.
  The helper function `_login_setup_google_client_id` is preserved
  in code for if/when a proper Gemini OAuth flow gets built.
* `GoogleOAuthFlow` class kept (still used for `whoami` + future
  loopback flow) — the device-flow methods remain functional for
  scopes Google does allow.

## [4.0.4] — 2026-04-26

### Added — Gemini login (two paths)

* **Google AI Studio API key paste** — easy path. `phantom setup` →
  `W` → `2` → `a` opens https://aistudio.google.com/app/apikey in the
  browser and prompts for the pasted key. Saves it to the engine
  config wired to https://generativelanguage.googleapis.com/v1beta/openai
  with default model `gemini-2.0-flash`. No GCP project, no consent
  screen, no OAuth client. ~30 seconds end to end.
* **Google OAuth Device Flow** — advanced path. `phantom auth login
  --provider google` (or setup-menu `W → 2 → b`) drives RFC 8628
  device-code against `oauth2.googleapis.com`. Now actually finishes
  the round-trip:
    * Required client_id check raises a friendly LicenseError pointing
      at the Cloud Console URL, the consent-screen step, and the
      mandatory "TVs and Limited Input devices" application type.
    * Legacy `verification_url` field is normalised to RFC's
      `verification_uri` so the polling code doesn't break on Google's
      slightly older device responses.
    * `whoami()` calls `https://www.googleapis.com/oauth2/v2/userinfo`
      and returns email + name + verified status + locale.
    * `models_base_url` and `default_model` populated → reuses the
      same auto-wiring path as GitHub login (now generalised in
      `phantom/cli/auth.py:_wire_oauth_engine`).
* `phantom auth whoami --provider google` — supported (was
  github-only before this release).
* New setup-menu entry **Y — Google setup** mirrors the existing
  **G — GitHub setup**: walks through console.cloud.google.com,
  enables Generative Language API, creates the OAuth client of the
  correct type, saves the client_id to `~/.phantom/.env`.

### Tests

* 8 new unit tests in `phantom/tests/test_oauth_github.py` covering:
  client_id validation, legacy verification_url normalisation, modern
  verification_uri parsing, models_base_url/default_model defaults,
  whoami round-trip, blank-token rejection, 401 surface, FLOWS dict
  registration. Total OAuth tests now 23, plus the 77 stage-closure
  smoke tests = 100 green.

## [4.0.3] — 2026-04-26

### Fixed

* **Windows OAuth save crashed with AttributeError.** After a user
  authorised the GitHub device code, `TokenStore.save()` called
  `_machine_key()` which tried to derive a Fernet key from
  `socket.gethostname() + str(os.getuid())` — but `os.getuid` only
  exists on POSIX. On Windows the call raised `AttributeError`,
  losing the just-acquired access token. Fix: detect missing
  `os.getuid` with `hasattr` and fall back to `getpass.getuser()`,
  which is portable. Final fallback to `USERNAME`/`USER` env var if
  even getpass fails on a locked-down host. Added a regression test
  that monkeypatches `os.getuid` away and verifies key derivation
  still produces a valid 44-byte Fernet key.

## [4.0.2] — 2026-04-26

### Fixed

* **Setup-menu OAuth invocation:** when `phantom setup` → `W` → `1`
  spawned `python -m phantom.cli auth login --provider github`, the
  child process used `-m` mode which adds *cwd* to `sys.path`. The
  user's shell cwd is typically their home dir, not the install dir,
  so the child died with `ModuleNotFoundError: No module named
  'phantom'`. Fix: pass `cwd=install_dir` and `PYTHONPATH=install_dir`
  to the four subprocess sites in `omnicli/cli.py`
  (`_login_run_phantom_auth`, `_login_run_phantom_auth_subcmd`,
  `_v4_run_doctor`, `_v4_plugin_manager`). Two new helpers expose the
  install dir / env from `omnicli.cli.__file__` so this fix doesn't
  hardcode any path.

## [4.0.1] — 2026-04-26

### Added

* **GitHub OAuth login** — `phantom login github` (and the long form
  `phantom auth login --provider github`) runs the RFC 8628 device-code
  flow, fetches a free GitHub Models access token, and auto-wires the
  engine config (`main_api_key`, `main_url`, `main_model`). End result:
  free GPT-4o + Claude 3.5 Sonnet + Llama 3.3 70B + Phi 4 + o1-mini
  with no API key billing — both `phantom chat` and the legacy
  `python run.py chat` immediately use it.
* `phantom whoami --provider github` — calls `/user`, prints login,
  display name, public email, plan, and public-repo count. Useful to
  confirm token health without launching chat.
* Setup-menu entry **W — Login with Account** in `phantom setup`,
  with an embedded **G — GitHub setup** walkthrough that registers the
  OAuth client_id into `~/.phantom/.env` and exports it into the
  current process.
* 14 mocked-network tests in `phantom/tests/test_oauth_github.py`
  (begin / poll / authorization_pending / slow_down / success /
  explicit error / refresh-unsupported / whoami / blank-token reject /
  403 surface / FLOWS dict registration / top-level shortcut commands).

### Fixed

* `/update` self-checker now distinguishes 4.0.0 → 4.0.1 cleanly.
  Previously a same-version material content change (e.g. a feature
  added without bumping) silently said "Already on the latest version".
  The fix is procedural — bump the patch level on every shipped change.

### Notes

* OAuth flows for `openai` and `anthropic` are still listed but
  flagged EXPERIMENTAL in the setup menu — neither provider currently
  exposes API access via OAuth, so the flow will not yield a usable
  token. They remain in `_FLOWS` for forward-compat.

## [4.0.0] — 2026-04-26

First open-core release of the v4 line. All nine development stages closed
with passing tests, peer reviews, and stage-closure smoke tests. Release
audit (`phantom.release.audit_repo`) returned 0 issues. Test suite: 1,654
collected, 77 stage-closure tests green at cut.

### Highlights

* **Tiered sandbox** — bubblewrap → firejail → unshare → docker, auto-selected per host.
* **Plugin SDK** — Ed25519-signed plugins with five reference plugins shipped.
* **Channels** — WebChat + Telegram + Discord + Slack adapters with trust-cap enforcement.
* **MCP + ACP** — JSON-RPC 2.0 MCP client/server + child-agent runtime with topological waves.
* **Skills + Memory v2** — SKILL.md bundles + SQLite/FTS5 hashing-trick TF-IDF rerank.
* **Voice + Canvas + PWA** — VAD-driven STT/TTS, typed UI canvas, web app manifest + service worker.
* **i18n + Onboarding wizard + Docs site** — five locales (en/hi/te/es/zh), pure-data wizard, MkDocs Material.
* **Hardening** — KeyPool auth rotation, dependency-free metrics, release-pipeline guards.

The v3.x `omnicli` line remains shipped, frozen, and security-patched in
parallel — pyproject installs both packages side-by-side.

### Stage 8 — Hardening: auth rotation, observability, release pipeline _(CLOSED 2026-04-25)_

* `phantom.auth.KeyPool` — round-robin key rotation w/ cooldown;
  `stats()` exposes only the last 4 chars of any key.
* `phantom.observability.{Counter, Histogram, Registry}` — dependency-
  free metrics, OTel-export-compatible shape.
* `phantom.release.{audit_repo, build_manifest}` — release-pipeline
  guards. Audit refuses to ship if any closed stage lacks its peer
  review or smoke test.
* +29 tests (1,278 → 1,307).

### Stage 7 — i18n + Onboarding wizard + Docs site _(CLOSED 2026-04-25)_

* `phantom.i18n` with five locales (en, hi, te, es, zh). Locale
  parity is enforced by test.
* `phantom.onboarding.Wizard` — pure-data state machine; tests don't
  need a TTY.
* `mkdocs.yml` + `docs_site/index.md` — Material-themed docs site
  config + landing page.
* +26 tests.

### Stage 6 — Realtime voice + Canvas host + PWA _(CLOSED 2026-04-25)_

* `phantom.voice.VoiceLoop` — VAD-driven STT flush + TTS playback
  queue + barge-in cancellation.
* `phantom.canvas.CanvasNode` — typed UI tree (text/code/table/chart/
  button/form/container) with per-kind validation, JSON-serialisable.
* `phantom.pwa.{build_manifest, build_service_worker}` — Web App
  Manifest + service worker (stale-while-revalidate + network-first
  for `/app/api/`, skipWaiting on activate).
* +28 tests.

### Stage 5 — Skills system + Memory v2 _(CLOSED 2026-04-25)_

* `phantom.skills.{SkillBundle, SkillLoader}` — Anthropic-style
  SKILL.md bundles with trigger-based activation; bundled
  `git_workflow` skill.
* `phantom.memory.MemoryStore` — SQLite + FTS5 + hashing-trick TF-IDF
  cosine reranker; namespaced by `(user, project, session)`.
* +24 tests.

### Stage 4 — MCP client + server + ACP runtime _(CLOSED 2026-04-25)_

* `phantom.mcp` — JSON-RPC 2.0 MCP client + server. Initialize,
  tools/list, tools/call, resources/list. Hand-rolled, no jsonrpc dep.
* `phantom.acp` — single-process child-agent runtime with topological
  dependency waves, error isolation, mass-spawn cap (1024 lifetime,
  configurable per-wave).
* +41 tests.

### Stage 3 — Multi-channel framework + 4 channel adapters _(CLOSED 2026-04-25)_

#### Added

* **`phantom.channels`** — channel-adapter framework: `ChannelAdapter`
  ABC, `ChannelEvent`, `ChannelMessage`, `ChannelRouter` with
  trust-cap enforcement.
* **WebChat adapter** — embedded WebSocket chat for the dashboard;
  trust cap 3 (local user).
* **Telegram, Discord, Slack adapters** — mock-friendly transports;
  trust cap 2; per-channel size capping with truncation marker.
* **+52 tests** (1,110 → 1,162).

#### Notes

* Matrix and IRC adapters deferred to Stage 8 (need real
  homeserver / IRC server for end-to-end verification). The framework
  fully supports them — Stage 8 just adds the transport classes.

### Stage 2 — Plugin SDK + 5 reference plugins _(CLOSED 2026-04-25)_

#### Added

* **`phantom.plugins`** — full plugin SDK: manifest schema, capability
  enum, plugin ABC, loader, registry, Ed25519 signature verification.
* **Five reference plugins**: `clock` (no caps), `weather`
  (`network`), `gh-search` (`network` + `executor`), `code-search`
  (`executor` + `filesystem`), `todo` (`memory`).
* **`phantom plugin {list,enable,disable}`** CLI under `phantom`.
* **88 new tests** (1,022 → 1,110), 4 skipped on hosts without
  ripgrep / docker / firejail.

#### Notes

* Plugin code currently runs in-process during loader-time import.
  Stage-4 ACP integration moves plugin execution into per-plugin
  sandbox slots.
* Built-in plugins shadow user plugins on name collision; documented
  in `docs/stages/STAGE_2.md`.

### Stage 1 — Sandbox & Executor v2 _(CLOSED 2026-04-25)_

#### Added

* **`phantom.sandbox`** — full tiered sandbox: bubblewrap → firejail →
  unshare → docker, selected automatically per host. ADR-0003 has the
  rationale.
* **`phantom.sandbox.run`** — the public entry point. Every shell call
  in Phantom v4 routes through it; the grep-style test
  `tests/sandbox/test_no_unsandboxed_subprocess.py` enforces this on
  every CI build.
* **`phantom.engine.execute_bash`** — the v4 sandbox-mediated executor.
  Preserves the v3 permanent-blocklist as defence-in-depth on top of
  the kernel-enforced sandbox.
* **`phantom.errors`** — typed exception hierarchy: `PhantomError` ←
  `SandboxError` ← {`SandboxUnavailableError`, `SandboxLaunchError`,
  `SandboxTimeoutError`, `SandboxResourceError`, `SandboxBlockedError`,
  `SandboxOutputTruncatedError`}, `PermissionDeniedError`,
  `PluginError`, `ChannelError`, `ProtocolError`, `LicenseError`.
* **`phantom doctor`** — host capability report. Plain text or
  `--json`; fails with exit 1 when no backend is available.
* **`phantom run -- <cmd>`** — direct sandbox round-trip without the
  agent loop. Useful for smoke-testing and CI.
* **`phantom version`** — print the version and exit.
* **`phantom.config`** — typed `~/.phantom/config.json` loader with
  schema validation.
* **Audit log** — append-only JSON-line log at
  `~/.phantom/sandbox-audit.log`. One record per call, mode 0600.
* **226 new tests** across `tests/sandbox/` (197), `tests/cli/` (15),
  `phantom/tests/test_stage_1_done.py` (9), `tests/test_compat_no_growth.py`
  (existing baseline assertion), bringing the total to **1,022 passed
  + 2 skipped** (skipped tests run when their backend is installed).

#### Changed

* The legacy `omnicli.executor` is **unchanged** by Stage 1. v3
  consumers continue to use the in-process trust gate. v4 consumers
  use `phantom.engine.execute_bash`. Migration is opt-in.

#### Notes

* The deny-list of secret paths (`DEFAULT_DENY_PATHS`) is enforced by
  bind-mount on the bwrap backend. The unshare backend relies on host
  filesystem permissions for the same paths; `docs/security/sandbox.md`
  is honest about this.
* The Stage-1 peer review (`docs/peer-reviews/STAGE_1.md`) flags
  unshare-only filesystem isolation as a **High** risk that Stage 2
  must address before plugin sandboxing ships.

### Stage 0 — Foundation _(CLOSED 2026-04-25)_

#### Added

* `phantom/` package with strict mypy + ruff + branch-coverage gating.
* `pyproject.toml` as the single source of truth for build, lint, type-check,
  and test configuration. Replaces ad-hoc `requirements.txt` (kept as a thin
  alias for legacy installs).
* Architecture Decision Records under `docs/adr/`. Every irreversible call
  (license model, sandbox tier, mobile strategy, backwards-compat policy) is
  written down with the trade-off explicitly named.
* `docs/stages/STAGE_0.md` — the canonical record of what Stage 0 delivered,
  how to verify it, and what unblocks for Stage 1.
* Peer-review template (`docs/peer-reviews/_TEMPLATE.md`). Every stage closes
  with a written review against this template; reviews are committed.
* Stage-tracking smoke tests (`phantom/tests/test_stage_*_done.py`) that fail
  loudly if a stage's deliverables regress.

#### Changed

* `omnicli` package version bumped to `3.0.12` to reflect the new co-habiting
  layout. No public API changes; every existing import path resolves.

#### Notes

* The 796-test baseline (`tests/` + `test_phantom.py`) continues to pass
  unmodified. v4 work is purely additive at this stage.
* No runtime behaviour ships in Stage 0. The deliverable is the production
  scaffolding that every subsequent stage relies on.

---

## [3.0.11] — 2026-04-21

(See `version.json` for the full v3 changelog.)
