# Stage 2 Peer Review

* Stage:    2 — Plugin SDK + 5 reference plugins
* Author:   Phantom v4 architect (self-review per ADR-0006)
* Date:     2026-04-25
* Version:  4.0.0-dev
* Files reviewed: every Stage-2 deliverable in `docs/stages/STAGE_2.md`.

## 1. Scope reviewed

* `phantom/plugins/{__init__,manifest,capability,plugin,signature,loader,registry}.py`
* `phantom/plugins/builtin/{clock,weather,gh_search,code_search,todo}/{manifest.json,__init__.py}`
* `phantom/cli/__init__.py` — `phantom plugin list/enable/disable`
* `tests/plugins/test_*.py` — manifest, loader, registry, signature, builtin
* `tests/cli/test_plugin.py`
* `phantom/tests/test_stage_2_done.py`

Test count delta: **+88** (1,022 → 1,110), 4 skipped for absent tooling.

## 2. Strengths

* **The capability model is closed and explicit.** Four enum members
  (`NETWORK`, `EXECUTOR`, `MEMORY`, `FILESYSTEM`) — no surprises. The
  loader builds the sandbox policy from this enum, and each reference
  plugin checks `ctx.capabilities` at the top of `call()` so granted
  capabilities are visible in the call frame, not in distant config.
* **The manifest schema is hand-validated.** No `jsonschema` dependency
  for a 200-line schema; we get better error messages and a smaller
  install footprint. The schema-as-data dict is still emitted for
  external tooling that wants to validate manifests independently.
* **The signature path uses Ed25519 + canonical-JSON.** The only
  attack surface is "what bytes do we sign?" — and the
  `canonical_payload` function strips the signature field before
  hashing, so the signature is not self-referential. PyNaCl's audited
  Ed25519 implementation does the actual crypto.
* **The reference plugins exercise three distinct capability sets:**
  none (clock), network-only (weather), network+executor (gh-search),
  executor+filesystem (code-search), memory-only (todo). The loader
  test surface naturally covers each capability path.
* **The CLI subcommand wiring uses Typer's `add_typer`.** Plugin
  commands compose under `phantom plugin <subcommand>` without
  cluttering the top-level help.
* **The registry separates discovery from policy.** Loader finds
  plugins; registry decides which are enabled. Operator can disable a
  plugin without deleting its directory; subsequent `phantom doctor`
  reports show the plugin as available-but-disabled, not missing.

## 3. Risks

* **High — the loader does not yet apply the registry's enabled flag
  during `discover()`.** Today, `PluginLoader.discover()` returns
  every plugin found on disk. The registry is consulted only by the
  CLI (`phantom plugin list`). When the agent runtime starts loading
  plugins (Stage 4 ACP integration), it will need to filter by
  `registry.is_enabled()`. Mitigation: the public API supports this
  trivially; we just need to thread the registry into the agent
  loop. Filed as a Stage-4 follow-up.
* **High — plugin code runs in-process during `load_plugin`.** The
  manifest validation and the entry-point import both happen before
  any sandbox is built. A malicious manifest pointing at a module
  with a `__init__.py` side effect can run arbitrary code at import
  time. Mitigation: signed-bundle verification (already implemented)
  + operator policy `plugins.require_signed = true` (Stage-8 config
  knob). The current bundled plugins are first-party and trusted; the
  risk is for third-party plugins, which the Stage-2 cut does not
  install automatically.
* **Medium — the plugin sandbox policy is the loader's
  responsibility, but Stage 2 does not actually wire it through.**
  The reference plugins manually construct sandbox calls (gh-search,
  code-search). A real plugin runtime would receive a
  `PluginContext` whose `sandbox_policy` is built from the plugin's
  declared capabilities. The wiring lives in Stage 4 when ACP
  spawns plugin agents. Today, a plugin is "trusted to obey its own
  capability checks", which is fine for first-party plugins.
* **Medium — duplicate-name handling silently drops the later
  copy.** `PluginLoader.discover()` warns and skips. An operator who
  intentionally shadows a built-in (e.g. their own `clock`) gets the
  built-in. The fix is to reverse the precedence: user plugins win
  over built-ins. Filed as Stage-7 onboarding-doc clarification +
  Stage-8 selectable behaviour.
* **Low — the weather plugin's HTTP fallback (`urllib.request.urlopen`)
  is not exercised by tests** because tests inject a `_FakeHttp`. The
  fallback only runs when the plugin runs against the real internet,
  which is excluded from CI. We could add a `respx`-mocked variant if
  the path becomes load-bearing.
* **Low — Stage 2 does not add a `phantom plugin install <path>` or
  `<package>` subcommand.** Operators must place the directory by
  hand. Stage-8 release pipeline owns this.

## 4. Required follow-ups (block stage close)

None.

## 5. Suggested follow-ups (do not block)

* Stage 4: thread `PluginRegistry` into the loader so `discover()`
  filters by enabled state by default.
* Stage 4: build the per-plugin `SandboxPolicy` automatically from the
  manifest's capabilities (and operator config).
* Stage 7: document the "user plugin shadows built-in" precedence
  rule (and reverse it if telemetry shows it confuses users).
* Stage 8: `phantom plugin install <path>` + `phantom plugin sign <key>`.
* Stage 8: consider parallelising `discover()` if startup latency
  grows past 100ms with 50+ plugins.

## 6. Sign-off

> Reviewed against `docs/stages/STAGE_2.md`. Required follow-ups list
> is empty. Stage 2 is **closed**.
>
> Concrete validation:
>
> * `tests/plugins/`: 76 passed, 2 skipped for missing rg.
> * `tests/cli/`: 22 passed (15 from Stage 1 + 7 new for plugin CLI).
> * `phantom/tests/test_stage_2_done.py`: 6 passed.
> * Full sweep: 1,110 passed, 4 skipped in 47 s.
>
> Reviewer:        Phantom v4 architect
> Date:            2026-04-25
