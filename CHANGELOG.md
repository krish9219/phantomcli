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

## [1.1.33] — 2026-05-10 — `/update` actually works (zip wrapper-dir fix)

User on v1.1.31 ran `phantom update`, saw "installed 601 files" /
"updated to v1.1.32" reported successfully, then `phantom chat` still
showed v1.1.31 in the banner. Across multiple attempts.

Root cause: v1.1.29-v1.1.32 source zips were built with a
`phantomcli-source/` wrapper directory at the root. The `_extract_to`
logic copies each top-level entry from the zip onto the install dir,
so package files landed at `site-packages/phantomcli-source/phantom/`
instead of `site-packages/phantom/`. The "601 files" really were
copied — just to a directory Python never imports from. **Every
`/update` since v1.1.29 has been a silent no-op.**

### Fixed

* **`_detect_source_root` added to `update_cmd.py`** — when a zip has
  a single top-level wrapper directory whose contents look like the
  package (contains `phantom/` or `omnicli/`), the extract logic
  pivots into it. Canonical flat layout still works unchanged.
* **Server-side zip rebuilt** — `phantomcli-source-v1.1.32.zip` was
  republished without the wrapper so v1.1.31 users can recover via
  `phantom update`. Also republishing v1.1.33 with the canonical
  flat layout.

### Tests

* 9 new in `test_v1_1_33_fixes.py`: `_detect_source_root` cases (root,
  wrapper, omnicli-only wrapper, unrecognised, empty wrapper),
  `_extract_to` end-to-end with wrapper layout (the live regression),
  `_extract_to` with flat layout (no regression), user-data preservation
  with wrapper pivot, and a structural assertion that the
  wrapper-pivot logic stays in the source.
* Suite: 2614 passed, 8 skipped, 0 failed.

### Build process note

Canonical zip-build command — files go directly at the root, no wrapper:

```bash
cd <source_dir> && zip -rq /tmp/phantomcli-source-vX.Y.Z.zip . -x "*.zip"
```

The mistake in v1.1.29-v1.1.32 was building from the parent directory
with `zip ... source_dir/`, which prepends the wrapper. The
`_detect_source_root` fix means this can't break `/update` again, but
the canonical build is still flat.

---

## [1.1.32] — 2026-05-10 — Robustness pass after the v1.1.28→v1.1.31 saga

The v1.1.28→v1.1.31 arc exposed a handful of process and UX gaps that
weren't bugs per se but cost real time. v1.1.32 closes them.

### Fixed

* **Orphan-install detection in `phantom update`** — when pip has no
  record of `phantom-cli` (the install was from a stale install.ps1
  run, a deleted venv, or manual zip extraction) the zip-extract update
  path can write to the wrong directory, while PATH keeps resolving
  to the old executable. v1.1.32 detects this case via
  `pip show phantom-cli` and bails with a concrete copy-pasteable
  `pip install --upgrade --force-reinstall <zip>` command instead
  of silently failing. Returns exit 2.
* **Identity post-filter heuristic catch-all** — the static brand
  list was the only defence against novel model-leak strings. v1.1.32
  adds an anchored heuristic: `I am [Capitalised non-name]` at the
  head of a reply gets rewritten to `I'm <assistant_name>`, even if
  the brand isn't in the static list. Skips the rewrite when the
  captured token equals `assistant_name` (no churn) and only applies
  to the first 250 chars of a reply (no false positives in body text).
* **429 rate-limit error suggests concrete switches** — both the
  streaming and retry-exhausted error paths now name three specific
  models the user can `/model` to: NVIDIA's free
  `meta/llama-3.3-70b-instruct`, paid `claude-haiku-4-5`, paid
  `anthropic/claude-sonnet-4-5`. v1.1.31's "wait or switch" was a
  dead-end message.

### CI flakes addressed

* **Import-discipline budget** — bumped `phantom.cli` cold-import cap
  500 → 1000 ms. Local floor is 150 ms; Windows CI runners legitimately
  hit 500-700 ms cold and the suite was redding out. New regression
  catches if import time goes above 1000 ms.
* **`start_server` immediate-crash test** — log file appeared empty on
  Windows CI. Fixed in production: open log with `buffering=0` and
  close the parent's fd after Popen so any lingering buffer state
  releases before the test reads. Test also tolerates Windows
  shell=True path-quoting failures.
* **`run_bash` server-hint timeout test** — was failing on Linux
  docker-tier with "permission denied" on `tmp_path/fake_server.py`.
  Test now `chmod 0o755`'s tmp_path and `0o644`'s the script so the
  sandbox container can read it.
* **`doctor --chat` smoke test** — skips on Windows CI when stdout
  isn't a TTY, since prompt_toolkit can't attach without a real
  console screen buffer there.

### Installers

* `install.ps1` and `install.sh` no longer hardcode `Phantom v1.0.0`.
  Both pull the version from `version.json` at install time and
  fall back to `latest` if the manifest is unreachable. Banner stays
  in sync without a script edit on every release.

### Refactor

* `_build_prompt_label(user_label)` extracted from inline closure to
  module-level helper. Now unit-testable: the v1.1.31 source-inspection
  test is replaced with a behavioural one that asserts the return value
  is a `prompt_toolkit.formatted_text.ANSI` instance, plus a
  fallback-to-plain-string test for environments without prompt_toolkit.
* `is_pip_managed()` and `_orphan_install_hint()` added to
  `phantom/cli/update_cmd.py`.

### Tests

* 15 new in `test_v1_1_32_fixes.py` covering all the above (orphan
  detection, identity heuristic, ANSI wrap behaviour, 429 message
  contract, import budget acknowledgement).
* Suite: 2605 passed, 8 skipped, 0 failed.

---

## [1.1.31] — 2026-05-10 — The REAL Windows `^[[36m` fix (prompt_toolkit ANSI wrap)

User on PowerShell 5.x reported v1.1.30 still showed literal `^[[36m`
in the prompt label. Their transcript revealed the actual root cause:

* The boot banner (`● Welcome back, Arvi Sir.`) and tool-call lines
  rendered colours correctly — proving `enable_ansi()` / SetConsoleMode
  IS working.
* But the input prompt label `^[[36mArvi Sir ›^[[0m` showed literal
  escape codes.

Two distinct rendering pipelines:

* `sys.stdout.write` → Windows console (VT-aware after `enable_ansi()`)
  → colours render. ✓
* `prompt_toolkit.PromptSession.prompt(label)` → prompt_toolkit's own
  renderer, which treats a plain string as **literal text** and
  ignores embedded ANSI codes regardless of the global console state. ✗

v1.1.29 / v1.1.30 fixed the first pipeline. v1.1.31 fixes the second.

### Fixed

* **`_build_prompt_label()` wraps in `prompt_toolkit.formatted_text.ANSI`**
  so prompt_toolkit's renderer interprets the embedded `\033[36m` /
  `\033[0m` codes instead of emitting them as literal text. The
  fallback path (no prompt_toolkit available) still returns a plain
  `you > ` string.

### Tests

* 4 new in `test_v1_1_31_fixes.py`: ANSI helper importable, chat
  module imports it, prompt label is wrapped (regression net), ANSI
  round-trip preserves escapes.
* Suite: 2590 passed, 8 skipped, 0 failed.

---

## [1.1.30] — 2026-05-10 — Windows ANSI ACTUALLY fixed (read-back verification)

v1.1.29 was supposed to fix the literal `^[[36m` Windows rendering bug
but didn't — users on PowerShell 5.x kept seeing it. Root cause:
`_try_os_system_trick()` always returns truthy because `os.system("")`
runs successfully whether or not VT mode actually enables.
v1.1.29 took that as a success signal, set `_ANSI_OK = True`, and never
reached the strip fallback.

### Fixed

* **`enable_ansi()` now verifies via read-back** — after each Windows
  attempt (`os.system("")`, `SetConsoleMode`), the function calls
  `GetConsoleMode` and checks whether the
  `ENABLE_VIRTUAL_TERMINAL_PROCESSING` flag is actually on. If not,
  it falls through to the next strategy. If every strategy fails
  verification AND colorama is unavailable, the strip wrapper
  installs and the user sees plain text instead of `^[[36m` garbage.
* **Pre-flight checks**: honour `NO_COLOR` (https://no-color.org) and
  `PHANTOM_NO_COLOR` env vars, detect non-TTY stdout (piped/redirected
  output), and detect `TERM=dumb`. In any of those cases, install the
  strip wrapper unconditionally — escapes won't render and would leak
  as literal text.
* `_try_os_system_trick()` and `_try_setconsolemode()` no longer
  return bool — the verifier is the only honest signal. They're
  fire-and-forget side-effect calls now.

### Tests

* 11 new in `test_v1_1_30_fixes.py`: NO_COLOR opt-out, PHANTOM_NO_COLOR
  opt-out, redirected-stdout strip, TERM=dumb strip, verifier-on-POSIX
  short-circuit, SetConsoleMode-reached-after-os.system-fails-verify,
  strip-fallback-when-verification-keeps-failing (the user-reported
  v1.1.29 regression), colorama-when-SetConsoleMode-doesnt-stick,
  strategy fns return None, idempotency after strip install.
* v1.1.28 + v1.1.29 ANSI tests updated for the new verifier-driven API.
* Suite: 2586 passed, 8 skipped, 0 failed.

---

## [1.1.29] — 2026-05-10 — Five user-reported bugs from the v1.1.28 transcript

Five concrete fixes after the v1.1.28 user transcript exposed:
ANSI still rendering literally, "I am Ling" still leaking, three
parallel servers picking the same port, paste leaving the first
line visible, spinner not running through tool calls.

### Fixed

* **Windows ANSI for real** — v1.1.28's `enable_ansi()` had a typing
  bug (no `argtypes`/`restype` on ctypes calls) and silently failed
  on at least one user host. v1.1.29 stacks four strategies:
  1. `os.system("")` (cheapest Windows VT init via console-host
     side effect, works on Win10 1607+).
  2. Native `SetConsoleMode` via ctypes with explicit type bindings.
  3. Colorama's `just_fix_windows_console`.
  4. Final fallback: install ANSI-stripping stdout/stderr wrappers
     so output is monochrome but readable instead of `^[[36m` garbage.
* **Identity post-processing** — model still leaks "I am Ling" /
  "developed by Ant Group" under social-engineering framings even
  with the v1.1.22/23 anchor. v1.1.29 adds a regex post-filter that
  rewrites every reply (and streaming chunks) replacing leaked brand
  identities with the user's chosen `assistant_name`. Belt-and-braces
  alongside the system-prompt anchor. The trailing tail is bounded
  to 120 non-period non-newline chars so a periodless reply doesn't
  get its whole tail eaten by a greedy match.
* **Streaming lookback hardened** — the live filter retains a 192-char
  lookback (was 64) so a brand string landing across a force-flush
  boundary stays in the retained tail instead of leaking through the
  cleaned head. Force-flush threshold bumped 256 → 1024 so models
  usually hit a sentence boundary first.
* **Atomic port reservation** — three sequential `start_server` tool
  calls in one turn all probed port 5001 *before any child actually
  bound it*, so all three thought 5001 was free. v1.1.29 adds a
  process-local `_RESERVED_PORTS` table with 15s expiry: when a
  reservation is handed out, the next concurrent call sees that port
  as taken and bumps to the next free one. Atomic via threading lock.
* **Paste placeholder off-by-one** — v1.1.28 erased `n_lines - 1`
  lines, leaving the first line of the pasted block visible above
  the placeholder. v1.1.29 erases `n_lines + 1` and re-prints the
  prompt label cleanly, so all the user sees is the placeholder.
* **Spinner continuity** — tool result printer now does `\\r\\033[K`
  before printing, so the spinner doesn't end up on the same line
  as the result preview. The spinner thread continues to render
  underneath each tool block, giving the Claude-Code-style continuous
  animation feel through the whole turn.

### Tests

* 34 new in `phantom/tests/test_v1_1_29_fixes.py`: ANSI strip wrapper
  (escape codes, plain text, complex sequences), `enable_ansi`
  stacked strategy (POSIX, os.system trick, strip fallback), identity
  post-processing (parametrised brand patterns + "developed by"
  clauses + leaving legitimate text alone + empty input + default
  name + periodless-tail bound + 120-char cap + newline anchor),
  streaming lookback constants + cross-boundary brand simulation,
  atomic port reservation (sequential, already-reserved, expired,
  exhausted, in-use), `_erase_lines_above` math + zero-no-op, paste
  placeholder structural assertion, tool-call/result printer
  spinner-clearing prefix contract. Plus 2 v1.1.28 tests updated.
* New `_reset_port_reservations` autouse fixture in
  `test_start_server.py` so `_RESERVED_PORTS` doesn't leak across
  tests.
* `tests/sandbox/test_no_unsandboxed_subprocess.py` allowlist gains
  `phantom/cli/_terminal.py` for the literal `os.system("")` trick.
* Module-level `_emit_tool_call_line`, `_emit_tool_result_line`,
  `_erase_lines_above` extracted from inline closures so the
  spinner-continuity and paste-placeholder contracts are unit-testable
  without spinning up a full chat session.
* Suite: 2575 passed, 8 skipped, 0 failed.

---

## [1.1.28] — 2026-05-10 — Windows VT mode + Claude-Code paste placeholder + knowledge-vs-tool prompt fix

Three honest fixes after the v1.1.27 user transcript exposed real
TUI bugs I should have caught in v1.1.26.

### Fixed

* **Windows ANSI colours render as literal `^[[36m` garbage** in
  PowerShell 5.x. New `phantom.cli._terminal.enable_ansi()` calls
  `kernel32.SetConsoleMode(handle, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING)`
  on Windows for both stdout and stderr handles. Falls back to
  `colorama.just_fix_windows_console()` if ctypes/Win32 fails. Called
  at the top of `chat()` and the outer `phantom>` REPL — before any
  banner colour writes.
* **Pasted text echoed twice** — once as the user typed/pasted, once
  as the `[Pasted: N lines]` summary. v1.1.28 erases the pasted lines
  from the visible scrollback after submit (using `\033[F\033[K` per
  line) and replaces them with a single `[Pasted text #N +X lines, Y
  chars]` placeholder à la Claude Code. Full text is still sent to
  the model. Falls back to v1.1.27 behaviour on terminals that don't
  support cursor-up.
* **Knowledge questions trigger tool calls** — the v1.1.16 "act, don't
  narrate" rule was too aggressive. Asking "explain async/await"
  caused the model to write `async_explainer.py` and run it (5+ min
  of file rewrites) instead of streaming a markdown answer. Added a
  new section to `DEFAULT_SYSTEM_PROMPT`: *"When to use tools — and
  when NOT to"*. Tools are for filesystem/shell/web/memory operations.
  For pure-knowledge questions ("explain X", "compare A vs B", "what
  is Y", "how does Z work") the model answers directly in markdown.
  The "act, don't narrate" rule still fires for actual build/run/fix
  tasks.

### Tests

* 7 new in `phantom/tests/test_v1_1_28_fixes.py`: POSIX no-op,
  idempotency, Windows SetConsoleMode call (verifies VT flag is OR'd
  in for both stdout + stderr), colorama fallback when kernel32
  fails, returns False when everything fails, system prompt has the
  knowledge-vs-tool clause, "act, don't narrate" rule preserved.
* Suite: 2541 passed, 0 failed.

---

## [1.1.27] — 2026-05-10 — Streaming responses + interactive `/confirm` gate

The two items I deferred in v1.1.26 are now in.

### Added

* **Streaming text responses.** `Provider.complete()` accepts an
  `on_chunk: Callable[[str], None]` kwarg. When set, the provider
  sends `stream=True`, parses SSE deltas, and dispatches each text
  fragment to the callback. Tool calls accumulate across chunks
  (`function.arguments` is a string fragment per delta — provider
  reassembles by `index`). The chat REPL stops the spinner on first
  chunk and prints text inline so you see tokens as they arrive.
* **`/confirm [on|off]`** slash command. Toggles a y/n prompt before
  destructive tool calls (`write_file`, `edit_file`, `run_bash`,
  `start_server`). Persisted to `profile.json`. Works via a new
  `AgentSession.on_tool_call_approve(round, tc) -> bool` hook —
  decline returns a `{"error": "user declined"}` JSON to the model
  so it knows to try a different approach instead of looping.
* **Pre-execution preview** when `/confirm on`:
  - `edit_file`: shows the old/new strings as a red/green diff (8 lines each).
  - `write_file`: shows path + line count + first 8 lines of new content.
  - `run_bash`: shows the command (300 chars).
  - `start_server`: shows command + port.
  Three answers: `y` (proceed), `n` (decline), `always-off` (proceed
  AND disable confirm mode for this and future sessions).

### Tests

* 13 new in `phantom/tests/test_v1_1_27_fixes.py`: streaming SSE
  chunk dispatch ordering, tool_calls accumulated across chunks
  (id+name first, args fragmented), empty-line + `[DONE]` handling,
  malformed-SSE skipping, callback-exception isolation, non-streaming
  path preserved when no `on_chunk` passed, approval hook proceeds
  on True, approval declined returns user-declined marker, hook
  exception fail-open, profile persists `confirm_destructive`,
  `/confirm on` / `off` toggle, status report, `/help` lists
  `/confirm`.
* Suite: 2535 passed, 0 failed.

---

## [1.1.26] — 2026-05-10 — TUI polish: tool icons, diff preview, markdown reply, expanded read-allowlist

Patch release. Closes the gaps from the v1.1.25 self-rating: read
access to `~/.phantom/*` was blocked, replies were raw text, tool calls
were undifferentiated lines, edits had no visible diff, and there was
no smoke test for chat startup.

### Added

* **`~/.phantom/` is now in the read-only allowlist.** `read_file`
  and `list_dir` can pull `profile.json` / `providers.json` /
  `memory.db` even when workspace is set elsewhere. Writes still use
  the strict workspace allowlist — agents can't overwrite the user's
  profile by accident.
* **Per-tool icons in the live progress feed.** ⚡ run_bash,
  📝 write_file, ✏️ edit_file, 🔍 read_file, 📂 list_dir,
  🌐 web_search, 🌍 web_fetch, 💾 memory_add, 🔎 memory_search,
  🚀 start_server. Catch a missing icon: `test_all_default_tools_have_icons`.
* **Result preview after each tool call.** Tool result is parsed and
  a one-line dim summary prints below the call: `✓ wrote 1234 bytes`,
  `exit 1`, listening URL, `× error`, etc. Per-tool field selection.
* **Inline coloured diff for `edit_file`.** `edit_file` now returns
  a `diff` field (compact unified, capped at 40 lines). The chat REPL
  renders adds in green, removes in red, hunk headers in cyan —
  Cursor-style.
* **Markdown rendering of assistant replies.** Code blocks get syntax
  highlighting, lists indent, tables align, **bold** works. Uses
  `rich.markdown.Markdown`. Falls back to plain text on non-TTY.
* **`phantom doctor --chat`** smoke test. Imports the chat module,
  builds a `PromptSession` with the same key-bindings the chat REPL
  uses, renders the boot banner — all non-interactively. Catches the
  v1.1.23-class bug where unit tests passed but the binary crashed on
  first launch. Exits 0 on pass, 1 on fail.

### Tests

* 25 new in `phantom/tests/test_v1_1_26_fixes.py`: read-allowlist
  expansion, write-allowlist unchanged, edit_file diff round-trip,
  diff truncation, parametrised tool-icon table, every default tool
  has an icon, run_bash/write_file/edit_file/read_file/list_dir/
  start_server/error preview shapes, diff colour codes, markdown
  rendering edge cases (empty / non-TTY), doctor --chat smoke test
  passing.
* 1 updated: chat-test reply-output assertion (rendering now goes
  through `write` so test buffer captures it).
* Suite: 2522 passed, 0 failed.

---

## [1.1.25] — 2026-05-10 — Auto-continue mid-task checkpoints + prompt label survives paste

Patch release. Two real issues from the v1.1.24 user run:

1. **Prompt label `Arvi Sir ›` disappeared after multi-line paste.**
   prompt_toolkit's multi-line mode repaints the screen and clobbered
   the label that `run_repl` wrote *before* calling `read_line`.
2. **Agent kept stopping mid-task to wait for confirmation.** The
   model would write *"Re-running pytest now."* / *"Now I'll fix the
   schema."* / *"Installing pytest-asyncio."* and end the turn — the
   user had to type "yeah proceed" between every step.

### Fixed

* **Prompt label survives paste** — the styled `user ›` label is now
  passed *into* `PromptSession.prompt(...)` instead of being written
  before. prompt_toolkit owns the rendering; multi-line repaints
  preserve it. The duplicate label that `run_repl` used to write is
  suppressed when prompt-toolkit is active (via a `__phantom_owns_label__`
  attribute on `read_line`).
* **Auto-continue mid-task** — after each provider call that returns
  text but no tool calls, the agent loop now checks
  `_looks_like_premature_checkpoint(text)`. If the text reads as a
  forward-looking promise (*"I'll run pytest"*, *"Let me start the
  server"*, *"Now I'll fix the schema"*, *"Installing pytest-asyncio"*,
  *"Re-running pytest now"*) AND there were tool calls earlier in the
  turn, the agent injects a continuation user message — *"Continue.
  You said you would do something next — do it now using tools, without
  asking for permission."* — and re-calls the provider. Capped at 3
  auto-continues per turn so a misbehaving model can't burn unlimited
  rounds. Conservative regex: only fires on short messages (<400 chars)
  to avoid false positives on legitimate final summaries.
* **Stronger anti-checkpointing in DEFAULT_SYSTEM_PROMPT.** Added a
  *"Critical rule: do not stop mid-task and wait for confirmation"*
  block with concrete ❌/✓ examples mirroring the user's observed
  pattern. Models follow patterns they've seen demonstrated.

### Tests

* 23 new in `phantom/tests/test_v1_1_25_fixes.py`: parametrised
  positive table (10 user-observed checkpoint phrases), parametrised
  negative table (6 legitimate final summaries / conversational
  replies), long-final-summary not flagged, empty/short text not
  flagged, end-to-end auto-continue kicks in, no-prior-tools doesn't
  auto-continue, three-checkpoint cap, legitimate completion passes
  through, system prompt warns about premature stops with examples.
* Suite: 2497 passed, 0 failed.

---

## [1.1.24] — 2026-05-10 — Hot fix: `phantom chat` startup crash on Windows

Hot fix. v1.1.23 shipped a Ctrl+V key-binding stub with `filter=None`
which prompt_toolkit rejects with `TypeError: Expecting a bool or a
Filter instance.`. The stub was meant as a future hook for terminals
that don't send bracketed-paste — but prompt_toolkit handles paste
natively in multiline mode anyway, so the stub was both broken AND
unnecessary.

### Fixed

* Removed the `@chat_bindings.add("c-v", filter=None)` stub. `phantom
  chat` now starts cleanly on Windows with prompt_toolkit installed.
* Multi-line paste behaviour is unchanged from v1.1.23 — bracketed-
  paste mode handles clipboard pastes with embedded newlines as a
  single message, and the `[Pasted: N lines, M chars]` indicator
  prints after submission.

### Tests

* No new tests; the failure was at import-time during `chat()`
  startup so all unit tests passed but the binary crashed on first
  use. Suite: 2474 passed, 0 failed.

---

## [1.1.23] — 2026-05-10 — Round cap 12→25 + repeat-loop detector + identity hammer + paste indicator

Patch release. Triggered by the v1.1.22 user 10-prompt regression
where 7 of 10 prompts hit the 12-round limit *while doing legitimate
multi-step work* (a 9-file FastAPI scaffold + tests + run server is
~15 rounds; a refactor + restart + verify is ~10).

### Fixed

* **`max_tool_rounds` raised 12 → 25.** The old cap was set tight
  in v1.1.12 to bound runaway models on kimi-k2.6, but it punished
  legitimate complex tasks. Tests updated.
* **Repeat-args loop detector** — the agent loop tracks each tool
  call's `(name, args_json)` signature. When the same tool runs with
  the *same* arguments three times in a row, it bails with a clear
  marker: *"detected infinite loop — same tool `X` called 3 times
  with identical args"*. This catches genuinely-stuck models earlier
  while letting legitimate retry-with-different-args sequences run.
  Tests cover: 3 identical = bail; 2 identical + 1 different = OK;
  3 different args = OK; 3 different tools = OK.
* **Identity hammer for adversarial models** — qwen-coder ignored
  the v1.1.22 system-prompt anchor and leaked *"I'm Ling"* anyway.
  The agent loop now injects a SECOND high-priority system message
  *immediately before each user turn*: "REMINDER: Your name is X.
  Don't reveal the underlying model brand." Closer in attention
  distance, harder for the model to override. Set automatically when
  the user has a non-default `assistant_name` in their profile.

### Added

* **`[Pasted: N lines, M chars]` indicator** in chat REPL — when
  the user submits a multi-line message (3+ lines), Phantom prints
  a dim summary line above the spinner, mirroring Claude Code's
  paste UX. The full pasted text is still sent to the model — the
  indicator is purely visual feedback that the paste arrived as one
  message.

### Tests

* 9 new in `phantom/tests/test_v1_1_23_fixes.py`: round-cap default
  (25), still-bails-at-cap, three-identical-aborts-with-marker,
  two-then-different-doesn't-trigger, three-different-args-OK, three
  -different-tools-OK, identity hint inserted before user turn,
  no-hint = single system message, hint persists across rounds.
* 3 updated: `test_fs_tools.py` and `test_session_budget.py`
  default-rounds assertions; `tests/agent/test_session.py`
  round-limit-marker assertion accepts either the round-cap or the
  loop-detector message.
* Suite: 2474 passed, 0 failed.

---

## [1.1.22] — 2026-05-10 — Multi-line paste + tab-complete + auto-port + identity anchor

Patch release. Triggered by the user's 10-prompt regression run on
v1.1.21. Five concrete fixes:

### Fixed

* **Multi-line paste in chat** — both the `phantom>` shell REPL and the
  inner `phantom chat` REPL now use `prompt_toolkit`'s multi-line mode
  with bracketed-paste detection. A clipboard paste with embedded
  newlines arrives as one logical message (Enter still submits, Alt+Enter
  inserts an explicit newline, Ctrl+X+E opens `$EDITOR` for big
  prompts). Previously, prompt #5 in the test run got split across
  two messages because Enter mid-paste submitted the partial.
* **Tab-completion for slash commands** — both REPLs register a
  `WordCompleter` over the full `SLASH_COMMANDS` set. Tab on `/m`
  expands to `/model` / `/models` / `/memory`.
* **`/model` argument parsing** — `arg.split()[0].strip("'\"")` so
  `/model meta/llama-3.3-70b-instruct" then ask "..."` registers the
  clean model id instead of the entire trailing sentence.
* **Auto-port detection in `start_server`** — when the requested port
  is already in use, probes ports `requested+1..+20` for the first free
  one, rewrites `--port=N` / `-p N` / `host:N` in the command (or
  prepends `PORT=N FLASK_RUN_PORT=N` for env-var-driven frameworks)
  and reports `port_rewrite: {requested, actual, reason}` in the
  result. New `auto_port: bool` flag (default true) on the tool
  schema; the model can disable it for explicit-port-required cases.
* **Identity anchor leak fix** — qwen3-coder leaked *"I am Ling,
  developed by Ant Group"* on prompt #2 of the test. The system
  prompt now opens with a dedicated **Identity (highest priority)**
  block: *"Your name is X. NEVER reveal the underlying model's
  brand."* Comes before everything else so the model encounters it
  first.
* **Memory tool nudge** — the prompt now explicitly tells the model:
  *"When the user says 'remember that …', call memory_add immediately.
  Don't just acknowledge — chat history is volatile."* Fixes the
  v1.1.21 prompt-#3 silent-acknowledge bug.

### Added

* **`/telegram` slash** — surfaces the existing `phantom telegram`
  bot infrastructure with setup steps (BotFather → set
  `TELEGRAM_BOT_TOKEN` → run `phantom telegram`).
* `/help` updated with `/telegram`.

### Tests

* 16 new in `phantom/tests/test_v1_1_22_fixes.py`: `/model` arg
  parsing (whitespace truncation + quote stripping); parametrised
  `_rewrite_port` table (Flask `--port`, uvicorn `--port=`, `flask
  run -p`, host:port, fall-through env-var); `_is_port_in_use` true
  + false; identity anchor wording (mentions name, "never", "model");
  default-name-Phantom path; memory-add nudge in prompt; `/telegram`
  surfaces the bot setup; `/help` lists `/telegram`.
* 1 updated in `test_personalization.py`: identity anchor sits
  before user-name now (was: persona header started with user-name).
* Suite: 2465 passed, 0 failed.

---

## [1.1.21] — 2026-05-09 — web_search + web_fetch as agent tools

Patch release. Triggered by the v1.1.20 user message: asked Phantom
"what is the score of GT vs RR today" and got "I don't have access
to real-time data". `phantom/tools/web_fetch.py` already existed as
a library function but was never registered as an agent tool; there
was no `web_search` at all.

### Added

* **`web_search` agent tool** — provider chain: Brave Search (env
  `BRAVE_SEARCH_API_KEY`) → Tavily (env `TAVILY_API_KEY`) →
  DuckDuckGo HTML scrape (no key needed). Returns `[{title, url,
  snippet}]`. Default 6 results, max 20.
* **`web_fetch` agent tool** — wraps the existing
  `phantom.tools.web_fetch.web_fetch` (HTTPS only, SSRF block on
  private hosts). Returns `{ok, url, status, content_type, text,
  truncated}` with text capped at 8 KB so the agent context stays
  sane. Used for: read the actual page after `web_search`, or fetch
  a known URL directly.
* Both tools are registered in `default_tools` so every chat session
  gets them automatically — the model now answers "today's score"
  type questions by `web_search` → pick best hit → `web_fetch`.

### Tests

* 14 new in `phantom/tests/test_web_tools.py`: HTML strip + entity
  decode utilities, DuckDuckGo URL unwrap, full DDG-result regex
  parse with a fake response, no-results-found PhantomError path,
  empty-query/empty-url JSON hint paths, success-shape JSON output,
  PhantomError surfaced as `error` field, body-truncation at 8 KB,
  private-host fetch error propagated, `default_tools` registration
  + schema sanity.
* Suite: 2449 passed, 0 failed.

---

## [1.1.20] — 2026-05-09 — `start_server` tool — true detached spawn + URL probe

Patch release. Triggered by the v1.1.19 user report: the model called
`run_bash python app.py` which blocked Flask in the foreground until
the 60s timeout killed it. No URL was ever returned and the server
process was always dead by the time the agent loop continued.

### Added

* **`start_server` tool** — first-class agent tool for launching
  long-running web servers. Spawns the process *truly detached*
  (Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; POSIX:
  `start_new_session=True`) so the child outlives the agent loop.
  Redirects stdout/stderr to `$workspace/.phantom_server.log`. Polls
  `127.0.0.1:port` for up to `wait_s` seconds (default 3) to confirm
  the server is listening, then returns:
  `{pid, command, port, url, log, listening, alive, hint}`.
  - When listening: hint = "tell the user to open <URL>".
  - When alive but not listening: hint points the user at the log.
  - When dead at probe time: hint = "exited immediately, check log".
* **Auto-port-guess** in `_guess_port` for common frameworks (Flask
  5000, uvicorn/Django 8000, Next/Rails 3000, plus `--port N` /
  `-p N` / `host:port` parsing).

### Changed

* **`run_bash` description rewritten**: now explicitly tells the
  model "DO NOT use this for long-running servers (`python app.py`,
  `flask run`, `uvicorn`, `npm start`, etc.). Use the start_server
  tool instead." Replaces the old "background it with `start /b`"
  guidance which the model ignored half the time.

### Tests

* 19 new in `phantom/tests/test_start_server.py` covering port
  guessing (parametrised: 11 cases including `--port`, `-p`,
  `host:port`, framework defaults), real-server spawn end-to-end
  (URL returned, log file created, listening probe returns true),
  immediate-crash path (alive=False, hint references log, log
  contains the import error), empty-command JSON-error parity,
  default port fallback, and tool registration in `default_tools`.
* 1 updated in `test_run_bash_timeout.py`: run_bash description
  now asserts on `start_server` mention instead of `nohup`/`start /b`.
* Suite: 2436 passed, 0 failed.

---

## [1.1.19] — 2026-05-09 — Host-OS shell guidance in system prompt

Patch release. Triggered by the v1.1.18 user report: "all bash
commands are not executing properly". The model on Windows kept
emitting POSIX commands like `mkdir -p path`, `cmd 2>&1 | tail -20`
that cmd.exe can't parse, so they failed silently (or with
non-obvious "Invalid switch" errors).

### Fixed

* **`_os_shell_guidance()`** returns a paragraph tailored to the host
  OS, injected into every chat session's system prompt:
  - **Windows:** "run_bash uses cmd.exe — POSIX commands DO NOT work.
    DO NOT use `mkdir -p` (no `-p`); use the write_file tool which
    auto-creates parents, or `python -c "import os; os.makedirs(...)"`.
    DO NOT pipe to tail/head/grep — use findstr. Background servers
    with `start /b CMD`."
  - **Linux / Darwin:** "run_bash uses /bin/sh. Standard POSIX is
    fine. Background long-running servers with `nohup CMD >...&`."
* **OS guidance is now non-negotiable** — even a blank profile gets
  it. Previously `_personalize_system_prompt` only added persona
  lines if `user_name` or `workspace_path` was set; on a clean install
  the model had no idea what OS it was on.

### Tests

* 7 new in `phantom/tests/test_os_shell_guidance.py` covering
  per-OS guidance content (Windows / Linux / Darwin), full
  personalize-prompt integration on Windows + Linux, the specific
  warnings that prevent the v1.1.18 failure modes (no POSIX pipes,
  redirect to write_file for `mkdir -p` cases).
* 1 updated in `test_personalization.py`: blank-profile path now
  asserts OS guidance is appended (was: assert prompt unchanged).
* Suite: 2417 passed, 0 failed.

---

## [1.1.18] — 2026-05-09 — run_bash 60s default + server-start guidance

Patch release. Triggered by the v1.1.17 user report: `python app.py`
started Flask in the foreground and the agent stayed locked for 25
minutes — the old 300s per-call timeout meant each blocked tool ate
5 minutes of the wall-clock budget.

### Fixed

* **`run_bash` default timeout 300s → 60s.** Most legitimate commands
  finish in seconds. 60s lets a hung server die fast so the agent
  moves on. Configurable per-call via `{"timeout": N}` (clamped 1–600).
* **Tool description now warns about backgrounding servers.** The
  model sees: "DO NOT run long-running servers in the foreground —
  they will block until the timeout fires. Background them: on
  Windows `start /b python app.py`, on POSIX
  `nohup python app.py >server.log 2>&1 &`. After starting a server,
  stop calling tools and tell the user the URL."
* **Server-start detector + hint** — when `_looks_like_server_start`
  matches the command (python script, flask run, uvicorn, gunicorn,
  npm/pnpm/yarn start/run/dev, node script, next dev/start, rails
  server) AND the call hits the wall-clock cap, the JSON result
  grows a `hint` field with the same backgrounding guidance, so the
  model corrects on retry.
* **`SandboxTimeoutError` → JSON result**, no longer a turn-killing
  exception. The fabricated result has `exit_code: -1`, the timeout
  message in stderr, and the server hint when applicable. The agent
  loop continues normally instead of bubbling out.
* **`run_bash` empty `command` returns hint JSON** (parity with the
  v1.1.10 file-tool fix), instead of raising PhantomError.

### Tests

* 26 new in `phantom/tests/test_run_bash_timeout.py`: parametrised
  positive table for `_looks_like_server_start` (python app.py,
  flask run, uvicorn, gunicorn, npm/pnpm/yarn start/run/dev, node
  *.js, next dev/start, rails server), negative table (python
  --version, pip install, ls, mkdir, python -c one-liner),
  default-timeout sanity, clamping, empty-command JSON hint,
  server-hint appears on timeout, no-hint for normal commands,
  tool schema reflects the new defaults + warning.
* Suite: 2410 passed, 0 failed.

---

## [1.1.17] — 2026-05-09 — /preset + /voice + /dashboard + /doctor + /plugins

Patch release. Triggered by the v1.1.16 user report: "I don't see any
voice models or anything here that was added in the software" + "let
me load the openrouter model". Five new slash commands surface
already-built features that were unreachable from inside chat.

### Added

* **`/preset <name>`** — register a curated provider in one step. Asks
  for the API key inline (or uses the env var if exported). Skips the
  multi-step `/add` wizard.
* **`/presets`** (or `/preset` with no arg) — list all curated
  presets: nvidia, groq, openrouter, together, fireworks, mistral,
  cerebras, deepseek, perplexity, deepinfra, xai, ollama, lmstudio,
  vllm-local, github. Free-tier and local-only entries are flagged.
* **`/voice` / `/dictate`** — explains how to launch `phantom dictate`
  (Pro voice transcription via Whisper) from a fresh terminal.
* **`/dashboard`** — explains how to launch the web dashboard
  (chat, sessions, plans, costs, plugins on :8000).
* **`/doctor`** — inline host capability report: which sandbox
  backend is selected, which others are available. No need to leave
  chat.
* **`/plugins`** — list discovered plugins with their version,
  enabled state, and capabilities. Discovers from
  `phantom.plugins.loader` so it finds installed first-party plugins
  (clock, code-review, code-search, gh-search, github-pr, todo,
  weather, web-screenshot).
* `/help` is updated with a "tools" group and the preset commands.

### Tests

* 11 new in `phantom/tests/test_v1_1_17_slashes.py` covering
  `/presets` listing all, `/preset` no-arg behaving like `/presets`,
  `/preset ollama` registering without prompting (local-only),
  unknown preset warning, `OPENROUTER_API_KEY` env var skipping the
  prompt, `/voice` + `/dictate` aliasing, `/dashboard` instructions,
  `/doctor` running without crash on this host, `/plugins` listing
  or reporting empty without traceback, `/help` listing every new
  command.
* Suite: 2384 passed, 0 failed.

---

## [1.1.16] — 2026-05-09 — Act-don't-narrate prompts + 429 retry + think-tag stripping

Patch release. Triggered by the v1.1.15 user session: even with dual
mode wired correctly, the executor said "I'll create app.py..."
without ever calling write_file. Plus llama-3.3 leaked `</think>`
tags. Plus NVIDIA started returning 429s.

### Fixed

* **DEFAULT_SYSTEM_PROMPT now demands action.** New "Act, don't
  narrate" section spells out: "Saying 'I will create app.py' without
  calling write_file is a failure. Call write_file first, then report
  what you did." Single-model mode now stops describing instead of
  doing.
* **Executor system prompt rewritten as directive.** Was previously
  injected into the user message (model treated it as content);
  now mutated into `session.system_prompt` for the dual-mode turn
  and restored after. Wraps coder output in `<coder_plan>` tags so
  the model has a clear delimiter, and explicitly forbids paraphrasing
  the plan in chat output.
* **`</think>` artefact stripping** — `_strip_thinking_tags` removes
  `<think>...</think>`, `<thinking>...</thinking>`, `<thought>...</thought>`,
  `<reasoning>...</reasoning>`, plus orphan closing tags llama-3.3
  sometimes leaves at the start of replies. Applied to every assistant
  turn before the user sees it. Empty-after-strip → keep original
  (so an all-thinking reply still shows something).
* **429 retry with backoff.** When the provider returns 429, Phantom
  now retries once with a short jittered backoff (or honours the
  `Retry-After` header, capped at 10s). The user sees an inline
  `⚠ rate-limited; retrying…` notice. If the second call also 429s,
  the error message names the model and suggests switching.

### Tests

* 13 new in `phantom/tests/test_v1_1_16_fixes.py`: prompt assertions
  (act-don't-narrate, executor directive, coder_plan tags), think-tag
  stripping (well-formed, orphan closing, multiple aliases, normal
  text passthrough, never-empty-result, empty input), 429 retry path
  (succeeds after one retry, raises actionable error after two
  failures, honours Retry-After, non-429 doesn't retry).
* Suite: 2373 passed, 0 failed.

---

## [1.1.15] — 2026-05-09 — Dual-model mode (planner + executor)

Patch release. The user proposed: use a strong-but-quirky coder model
(qwen3-coder, kimi, deepseek) to *write* the code, and a reliable
tool-calling model (llama-3.3, llama-4-maverick) to *execute* it —
write the files, run the commands. Same pattern as Aider's architect
mode and several agent frameworks. Implemented.

### Added

* **Profile fields** `coder_provider`, `executor_provider`,
  `dual_mode`. Backwards-compatible loader: profiles saved by older
  versions get the new fields defaulted to empty / False.
* **`/coder <provider|model-id>`** sets the planner/coder model.
  Accepts a registered provider name OR a raw model id (in which
  case it clones the default provider's endpoint+key and registers
  the model id as a new entry, same trick as `/model`).
* **`/executor <provider|model-id>`** sets the executor model.
* **`/dual on|off`** toggles the two-stage flow. Refuses `/dual on`
  when either coder or executor isn't set, with a clear hint.
  `/dual` without args reports the current state.
* **Two-stage agent loop** in `chat`: when dual mode is active, each
  user turn:
  1. Calls the coder model with NO tools and a system prompt
     instructing it to produce complete code with `\`\`\`lang file=PATH`
     fences and `$ ` shell-command lines.
  2. Calls the executor session (the normal one with tools) but
     prepends an "execute the plan below" preamble + the coder's
     output to the user's original prompt.
* **Coder failure falls through gracefully** — if the coder call
  errors (timeout, garbled, rate-limited), Phantom prints a notice
  and runs the executor on the original prompt as a single-model
  turn. No dead session.
* **`/help`** lists the three new commands under a "dual-model" group.

### Tests

* 17 new in `phantom/tests/test_dual_model.py` covering profile
  field serialisation + back-compat, `/coder` / `/executor` with
  registered names + raw model ids, `/dual` validation (refuses
  without both halves) + on/off + status, the resolver helper
  (registered → return, model-id → clone default, no default →
  fail cleanly), and `_run_coder_stage` (tools-stripped payload,
  coder system prompt sent, raw text returned, unknown-provider
  raises).
* Suite: 2360 passed, 0 failed.

---

## [1.1.14] — 2026-05-09 — `/model <model-id>` one-shot switch + garbled-output detector

Patch release. Triggered by the v1.1.13 user report: kimi-k2.6 returned
token soup (pipes, multilingual fragments, broken JSON) and the only
way to switch off it was the multi-step `/add` wizard.

### Added

* **`/model <model-id>` reuses the current endpoint + key.** When the
  arg isn't a registered provider name, Phantom now treats it as a
  raw model id and swaps just the model on the active provider. So
  `/model meta/llama-3.3-70b-instruct` works directly — same NVIDIA
  endpoint, same key, just a different model. The new entry is
  registered automatically (auto-named from the model id, with -2,
  -3 suffixes on collision) so it shows up in `/models` next time.
* **`_looks_garbled` heuristic** — when a reply has high pipe density
  (>4%), high backslash density (>6%), or >25% non-ASCII chars,
  Phantom prints a one-liner after the response:
  `⚠ that reply looks garbled (model X). Try /reset then /model
  meta/llama-3.3-70b-instruct.` The thresholds are conservative —
  legitimate code with backslashes and unicode passes.

### Tests

* 12 new in `phantom/tests/test_quick_model_switch.py` covering
  `_switch_model_only` (keeps endpoint+key, registers new entry,
  drops orphan tool history, suffix-on-collision) and
  `_looks_garbled` (kimi pipe soup, normal English, normal code with
  backslashes, short replies, high CJK density). Plus end-to-end
  through `_handle_slash` (unknown arg falls back to model-id,
  unknown arg with no active provider shows error, registered name
  still works).
* Suite: 2343 passed, 0 failed.

---

## [1.1.13] — 2026-05-09 — HTTP timeout 120s → 60s + actionable timeout error

Patch release. Triggered by the v1.1.12 user report: a single LLM call
hung for 11+ minutes on kimi-k2.6 because the httpx default of 120s
wasn't being hit (NVIDIA's gateway kept the connection alive long
enough that the round-level wall-clock budget couldn't help).

### Fixed

* **Default HTTP timeout 120s → 60s.** A model that takes longer than
  60s to start streaming a response is almost certainly stuck.
* **`PHANTOM_HTTP_TIMEOUT_S` env var** — override the default for
  legitimately slow endpoints or local models.
* **Actionable timeout error.** When httpx raises a Timeout exception,
  the user now sees: `provider 'openai-compat' timed out after 60s
  (model='kimi-k2.6'). The model may be stuck or NVIDIA's gateway is
  holding the connection. Try /reset and switching to a faster model
  with /model meta_llama-3.3-70b-instruct, or raise
  PHANTOM_HTTP_TIMEOUT_S to allow longer waits.` Non-timeout errors
  keep the old "request failed" message.

### Tests

* 6 new in `phantom/tests/test_http_timeout.py` covering default 60s,
  env override, explicit-arg-wins-over-env, invalid-env-falls-back,
  timeout-error contents, non-timeout error preserves old message.
* Suite: 2331 passed, 0 failed.

---

## [1.1.12] — 2026-05-09 — Bounded tool-loop + live tool visibility + Ctrl+C abort

Patch release. Triggered by the v1.1.11 user report: kimi-k2.6 went
into a 14-minute silent tool loop with no visibility and no escape.

### Fixed

* **Wall-clock budget** — `AgentSession` gains
  `wall_clock_budget_s` (default 300s = 5 min). The loop checks
  before every round and after every tool result; on bust it returns
  the last text plus a one-line marker.
* **`max_tool_rounds` lowered 25 → 12** — longer turns were almost
  always the model stuck in a loop. 12 still fits typical multi-step
  coding tasks; the budget catches the rest.
* **Live tool-call visibility** — `AgentSession` gains
  `on_tool_call(round_idx, tool_call)` and
  `on_tool_result(round_idx, tool_call, result_str)` callbacks. The
  chat REPL wires a printer that shows
  `→ run_bash mkdir -p /home/a/Projects/flask-app && cd …` as each
  tool runs, so a long turn no longer looks frozen. Callback errors
  are caught — a broken printer never kills the turn.
* **Ctrl+C aborts the current turn cleanly** — `KeyboardInterrupt`
  during `respond_to()` is caught in the chat REPL, the spinner
  stops with ✗, the partial state is kept (so /reset works), and
  the prompt returns. A second Ctrl+C at the prompt exits the REPL.

### Tests

* 7 new in `phantom/tests/test_session_budget.py` covering default
  rounds = 12, max-rounds returning the marker with the model's last
  text, wall-clock budget tripping mid-loop, generous budget running
  to completion, on_tool_call firing per call with the correct
  round_idx and arguments, on_tool_result firing per result, and
  callback exceptions not killing the turn.
* 2 updated: the older `tests/agent/test_session.py` round-limit
  marker assertion is now substring-based (the marker text expanded);
  `phantom/tests/test_fs_tools.py` reflects the 12-round default.
* Suite: 2325 passed, 0 failed.

---

## [1.1.11] — 2026-05-09 — Identity substitution + kimi tool-call parser

Patch release. Three fixes from the v1.1.10 Ghost / Arvi Sir test.

### Fixed

* **Identity stuck on "Phantom"** — the default system prompt
  hard-coded "You are Phantom, …", so prepending an "answer to the
  name X" persona line made the model see contradictory instructions
  and pick Phantom anyway. `_personalize_system_prompt` now
  substitutes the chosen `assistant_name` directly into the
  "You are X," opener (only the first occurrence; later mentions of
  Phantom in the prompt body are product references and are left
  alone).
* **Kimi/minimax tool calls were ignored** — `moonshotai/kimi-k2.6`
  emits tool calls inside delimited text:
  `<|tool_calls_section_begin|><|tool_call_begin|>functions.run_bash:{...}<|tool_call_end|><|tool_calls_section_end|>`
  instead of the OpenAI `tool_calls` array. The agent saw plain text
  and never invoked the tool. `_extract_inline_tool_calls` now scans
  the response, pulls each call into a `ToolCall`, and strips the
  markers from the text so the user sees a clean assistant turn.
* **REPL still said "you ›" / "phantom ›"** — both prompts now read
  from the saved profile. After onboarding as Arvi Sir / Ghost the
  prompts become `Arvi Sir ›` and `ghost ›`.

### Tests

* 13 new in `phantom/tests/test_personalization.py` covering name
  substitution (assistant name → prompt opener, kept-default,
  workspace + user_name header, blank profile no-op, only-first-
  occurrence) and the kimi parser (single call, multiple calls in
  one block, no markers, malformed JSON, optional `functions.`
  prefix, end-to-end through `OpenAICompatibleProvider._parse`,
  native `tool_calls` preserved when present).
* Suite: 2318 passed, 0 failed.

---

## [1.1.10] — 2026-05-09 — JARVIS boot, profile, and 9 new slash commands

Patch release. Phantom now feels like a real assistant: it asks for
your name and a workspace path on first run, greets you on every boot
with a system snapshot, and exposes a full set of slash commands for
profile, licensing, memory, and uninstall — all from inside chat.

### Added

* **First-run profile** at `~/.phantom/profile.json`. The first time
  you run `phantom chat` it asks three questions:
  1. What should I call myself? (default: Phantom)
  2. What should I call you?
  3. Where should I create projects? (default: `~/Projects`)
  Subsequent boots skip the questions. The profile shapes the agent's
  system prompt — your name, the assistant's name, and the workspace
  are injected so the agent addresses you by name and creates files
  in your chosen directory by default.
* **JARVIS-style boot banner** — cyan ANSI logo + system snapshot
  (host, OS, CPU, RAM free/total, disk free/total, workspace) +
  personalised "Welcome back, <name>" greeting. Animated when stdout
  is a TTY; silent on pipes / CI.
* **Slash commands**:
  - `/name [new]` — show or rename the assistant.
  - `/workspace [path]` — show or change the project root (auto-creates).
  - `/system` — host snapshot on demand.
  - `/memory [query]` — show stored memory; with a query, search.
  - `/buy` — Pro lifetime licence URL + price.
  - `/license` — show current tier.
  - `/install-license <PHC-...>` / `/change-license <PHC-...>` —
    activate or replace a key.
  - `/god-mode [on|off]` — autonomous-action mode (modifies system
    prompt, persisted to profile).
  - `/uninstall` — confirmation flow that removes `~/.phantom/`
    entirely. Without `--yes` it just warns. With `--yes` it
    rmtree's the install dir and prints the platform-specific shim
    removal command.
* `/help` is now grouped (chat / model / you / licence / danger).

### Improved

* **Tool errors return actionable JSON** instead of raising. When the
  model passes a missing or empty `path` to write_file / read_file /
  list_dir / edit_file, the handler now returns
  `{"error": "...", "hint": "Retry with: {...example args...}"}` so
  the model can recover on the next round. Previously a single bad
  tool call killed the turn and produced output-only-no-files (the
  Flask app issue from the v1.1.9 user report).

### Tests

* 22 new in `phantom/tests/test_profile_and_boot.py` covering profile
  load/save/roundtrip, onboarding (skip-when-complete, all-three-
  fields, default-on-blank), sysinfo struct, boot banner with/without
  user_name, every new slash command (no-arg, with-arg, persistence
  to profile, system-prompt mutation for /god-mode), uninstall
  confirm-required + --yes flow, and tool error guidance.
* 1 updated in `phantom/tests/test_fs_tools.py` — bad write_file args
  now return JSON instead of raising.
* `tests/sandbox/test_no_unsandboxed_subprocess.py` allowlist gains
  `phantom/cli/sysinfo.py` (read-only host probes for cpu/mem on
  macOS/Windows; Linux uses /proc directly).
* Suite: 2305 passed, 0 failed.

---

## [1.1.9] — 2026-05-09 — base_url normalization (auto-strip /chat/completions)

Patch release. Triggered by the v1.1.8 user report: pasting
`https://integrate.api.nvidia.com/v1/chat/completions` as the base
URL produced 404s because the provider then built
`…/v1/chat/completions/chat/completions`.

### Fixed

* **`normalize_base_url()`** strips trailing `/chat/completions`,
  `/completions`, `/embeddings`, `/messages`, `/responses`, and
  trailing slashes/whitespace from any base URL before it's saved.
* `ProviderRegistry.add()` runs the normalizer before persisting, so
  pasting the full endpoint URL from a docs page just works.
* `ProviderRegistry.load()` runs the normalizer on every existing
  entry and rewrites the file once if any cleanup happened. Healthy
  files are not touched (no mtime churn).

### Tests

* 14 new in `phantom/tests/test_base_url_normalization.py` covering a
  parametrised table of paste shapes (the exact NVIDIA paste, trailing
  slashes, embeddings/responses/messages variants, localhost,
  whitespace, already-clean), plus auto-strip on `add()`,
  silent-repair on `load()`, and no-write when all entries are clean.

---

## [1.1.8] — 2026-05-09 — /model + /add + /smart slash commands + tool-history scrub

Patch release. Three fixes triggered by the v1.1.7 NVIDIA + minimax test:

1. After tools-fallback latched off, follow-up turns hit a 400
   *"Message has tool role, but there was no previous assistant message
   with a tool call!"* because the orphan tool turns from the prior
   round were still being sent.
2. No way to switch model from inside chat.
3. No prompt-engineering helper.

### Fixed

* **Orphan tool-history scrub** — when a request goes out without
  tools, `OpenAICompatibleProvider` now drops `role="tool"` messages
  *and* empty assistant turns (which wrapped a stripped tool_calls
  payload). Fixes the 400 chain.

### Added

* **`/model`** — show the current model.
* **`/model <name>`** — switch to a registered provider mid-session.
  The session's provider is rebuilt against the saved entry, the
  warning sink is rewired, and orphan tool-role history is dropped so
  the new model doesn't immediately 400.
* **`/models`** / **`/providers`** — list registered providers,
  starring the active one and marking the registry default.
* **`/add`** — launch the setup wizard from inside chat to register a
  new provider without leaving the session.
* **`/smart [on|off]`** — toggle prompt-expansion mode. When on,
  prepends an "expert engineer, restate as a precise spec, then act"
  preamble to the system prompt. Default off (saves cost).
* **Coloured `/help`** with all commands grouped and described.

### Tests

* 17 new in `phantom/tests/test_slash_commands.py` covering tool-
  history scrub (the exact NVIDIA 400 bug), `/help` listing all
  commands, `/reset` clearing history, `/history` length, `/exit`
  sentinel, `/models` empty + populated, `/providers` alias, `/model`
  no-arg / unknown-name / switch / drop-orphans, `/smart` default-off
  / toggle / state-reporting / system-prompt restoration, and
  end-to-end `run_repl` slash-with-arg dispatch.

---

## [1.1.7] — 2026-05-09 — Tools-fallback + thinking spinner

Patch release. Two fixes triggered by the v1.1.6 NVIDIA + minimax test:

1. NVIDIA NIM crashed on `minimaxai/minimax-m2.5` (and other models that
   don't support tool calling) with a 500 *"Object of type Undefined is
   not JSON serializable"* whenever Phantom included tool definitions.
2. The chat REPL was bare — no animation while the model was thinking.
   v3 / v4.0.10 had the Claude-Code-style Braille spinner; v4 had lost it.

### Added

* **Tools-fallback** on 5xx — when the provider returns 4xx/5xx with a
  body matching a known "no tool support" pattern (the NVIDIA Undefined
  error, "tools are not supported", "function calling is not supported",
  etc.) and tools were in the request, Phantom retries the same request
  without tools and latches off tool support for the rest of the
  session. The user sees an inline `⚠ provider … doesn't accept tools`
  notice.
* **Pattern matcher** in `phantom.agent.provider._looks_like_tool_rejection`
  — false positives only cost one harmless retry, true positives keep
  chat working on tool-less models.
* **`PhantomSpinner`** in `phantom/agent/spinner.py` — Braille frames,
  rotating thinking verbs ("Thinking", "Cross-referencing knowledge
  base", "Phantomizing", "Bending spacetime", …), elapsed time and
  token estimate, ✓/✗ on success/failure. Auto-disabled on non-TTY
  streams (CI, pipes) and via `PHANTOM_NO_SPINNER=1`. Context-manager
  and `with_spinner(fn, ...)` wrapper both supported.
* **Spinner wired into chat** — both `phantom chat` and the `phantom>`
  shell's plain-text fall-through wrap the LLM call in a spinner. The
  user sees `⠋ Thinking… (3s · ↑ 36 tokens)` while waiting.
* **Fancier prompts** — `you ›` (cyan) and `phantom ›` (green) replace
  the plain `you>` / `phantom>`.

### Tests

* 10 new in `phantom/tests/test_tools_fallback.py` covering known
  rejection phrases (parametrised), retry without tools on the exact
  NVIDIA Undefined error, latch persistence across calls, unrelated
  5xx not misclassified, no-tools payload not triggering retry,
  and `tools_supported=False` opt-out at init.
* 5 new in `phantom/tests/test_spinner.py` covering non-TTY no-op,
  enabled-stream summary line, return-value propagation, exception
  propagation, ✗ mark on failure.

---

## [1.1.6] — 2026-05-09 — Wizard simplified to direct 3-prompt custom flow

Patch release. Replaces the 16-line preset menu with a direct
3-prompt flow (base URL, model, API key) so the wizard works for any
OpenAI-compatible endpoint without forcing the user to scan a list.

### Changed

* **First-run wizard** now prompts directly for base URL, model id,
  and API key — no preset picker. Phantom works with any OpenAI-
  compatible endpoint, so the wizard reflects that.
* The wizard auto-derives the provider name from the registered
  domain (`integrate.api.nvidia.com` → `nvidia`,
  `api.together.xyz` → `together`, `models.github.ai` → `github`,
  `localhost` → `localhost`). If the derived name is taken, appends
  `-2`, `-3` etc. so re-running never silently overwrites.
* Wizard bails on a blank base URL, a non-http(s) scheme, or a blank
  model id. The API key prompt accepts blank input (for local
  endpoints like Ollama / vLLM that don't need a key).

### Kept

* `phantom config provider preset <name>` — one-line shortcuts to
  curated providers (nvidia, groq, openrouter, together, fireworks,
  mistral, cerebras, deepseek, perplexity, deepinfra, xai, ollama,
  lmstudio, vllm-local, github) — still works exactly as before with
  interactive key prompting.
* `phantom config provider custom <name>` — flag-driven manual entry,
  with prompts when flags are missing (from v1.1.5).
* `phantom config setup` — direct alias for the wizard.

### Tests

* 8 new in `phantom/tests/test_setup_wizard.py` covering the 3-prompt
  flow, blank-input cancellation paths, non-http rejection, blank-key
  acceptance for local endpoints, and the new `derive_name()` helper
  with a parametrised host table.
* 1 updated in `test_provider_cmd_interactive.py` to drive the new
  wizard layout.

---

## [1.1.5] — 2026-05-09 — Interactive provider config

Patch release. Removes the "now what?" moment after `phantom config
provider custom <name>` printed `Missing argument`.

### Added

* **`phantom config setup`** — direct alias for the first-run wizard.
  Same picker `phantom chat` shows on a clean install; useful when the
  user is exploring `phantom config --help` and wants to add a provider
  without going through chat.
* **Interactive prompts** on `phantom config provider custom <name>` —
  if `--base-url`, `--model`, or key flags are missing, the command
  prompts for each. Existing flag-driven invocations keep working
  unchanged.
* **API-key prompt** on `phantom config provider preset <name>` — when
  no `--key` is given and the preset's env var isn't set, prompt for
  the key inline. Skipped for local-only presets (ollama, lmstudio,
  vllm-local).
* Both `custom` and `preset` now print whether the new entry became
  the default, with the `phantom config provider use <name>` hint when
  it didn't.

### Tests

* 8 new in `phantom/tests/test_provider_cmd_interactive.py` covering:
  prompt firing for each missing flag, blank key skipping the prompt,
  explicit flags bypassing all prompts, env-var presence skipping the
  preset prompt, local-only presets skipping the prompt entirely,
  `phantom config setup` running the wizard, and cancel-from-setup
  exiting 2.

---

## [1.1.4] — 2026-05-09 — Real `phantom update` command

Patch release. Adds the missing self-update command. Existing installs
no longer have to rerun `install.ps1` / `install.sh` to pick up new
versions.

### Added

* **`phantom update`** — fetches `version.json` from the official CDN,
  compares with `phantom.__version__`, downloads `phantomcli-source.zip`,
  verifies the SHA-256 against the manifest, and extracts in place over
  the install dir. User data in the install root (`.license`,
  `.machine_key`, `memory.db`, `providers.json`, `oauth/`,
  `.repl_history`) is preserved — only the package directories are
  overwritten. Refuses unsafe zip entries (path traversal). Exit codes:
  0 success / already-current, 1 network/extract failure, 2 SHA mismatch
  or install dir not writable.
* **`phantom update --check`** — compare and report only, don't download
  or write anything.
* **`phantom update --force`** — re-install even when already on the
  latest version (recovery from a half-extracted update).
* **`phantom update --manifest-url <url>`** — point at a self-hosted
  mirror or a test manifest.

### Tests

* 19 new in `phantom/tests/test_update_cmd.py` covering version
  comparison (parametrised), manifest parsing edge cases, extract
  overwrite + user-data preservation + path-traversal rejection, and
  full `perform_update()` flow (no-op match, download+extract, SHA
  mismatch refused, `--force`).

---

## [1.1.3] — 2026-05-09 — First-run setup wizard + REPL chat fall-through

Patch release. Removes the two largest first-run frictions: `phantom
chat` no longer demands `--base-url` / `--model` flags, and the
`phantom>` shell now treats plain text as a chat message instead of
rejecting it as "No such command".

### Added

* **First-run wizard** — running `phantom chat` with nothing configured
  drops into an interactive picker that lists every preset (NVIDIA,
  Groq, OpenRouter, GitHub Models, Together, Fireworks, Mistral,
  Cerebras, DeepSeek, xAI, Ollama, LM Studio, vLLM, …) plus a "custom"
  option for any OpenAI-compatible URL. The user picks once, pastes a
  key (skipped for local endpoints), and the choice is saved as the
  default provider. Subsequent runs skip the wizard.
* **`phantom config provider use <name>`** — set the default provider
  used by `phantom chat`. `provider list` now stars the default.
* **`ProviderRegistry.set_default()` / `.get_default()`** — persists
  the default name in `providers.json` under a top-level `"default"`
  key. Backwards-compatible: existing files without the key continue
  to load. Removing the default provider auto-promotes the first
  remaining one.
* **REPL chat fall-through** — inside `phantom>`, a line whose first
  token isn't a known subcommand is sent to the agent as a chat
  message. The first such line lazily builds an `AgentSession` (using
  the saved default provider) and caches it. So `phantom` → `hi` now
  actually replies.

### Changed

* `phantom chat` flag contract: passing only one of `--base-url` /
  `--model` still exits 2 with the half-configured error; passing
  neither (and having no env vars) now resolves from the saved default
  or runs the wizard.

### Tests

* 25 new tests in `phantom/tests/test_setup_wizard.py` cover registry
  default semantics, wizard preset/custom paths, cancel, local-only
  preset (no key prompt), and `resolve_chat_config()` precedence.
* `test_repl.py` updated: `test_unknown_command_prints_clean_message`
  replaced with `test_unknown_word_falls_through_to_chat` plus a new
  `test_known_subcommand_still_dispatches_first` regression.

---

## [1.1.2] — 2026-05-08 — REPL: clean exits + pretty usage errors

Patch release. Cosmetic but visible REPL polish.

### Fixed

* In click 8+, `click.exceptions.Exit` and `Abort` are `RuntimeError`
  subclasses, not `SystemExit`. The REPL's `except SystemExit` missed
  them, so typing a sub-group name like `config` (which triggers
  `no_args_is_help` → `click.Exit(0)`) fell through to the generic
  Exception branch and printed a bare `error:` line with no message.
  Now we catch both `SystemExit` and click's exit classes silently,
  pretty-print `UsageError` messages via `.format_message()` (so
  `No such command 'foo'.` appears clean), and only emit `error: <msg>`
  when there's actually a message.

### Tests

* 2 new regression tests: `config` sub-group help no longer leaves a
  spurious `error:` line; unknown-command messages print exactly once.
  Local suite: 2188 passed.

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
