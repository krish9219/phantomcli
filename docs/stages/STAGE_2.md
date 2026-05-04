# Stage 2 — Plugin SDK + 5 reference plugins

> Goal: third-party developers can ship a Phantom plugin as a Python
> package that declares its capabilities, runs in the Stage-1 sandbox,
> and integrates with the agent loop through stable extension points.

* Status:  CLOSED
* Author:  Phantom v4 architect
* Started: 2026-04-25
* Closed:  2026-04-25

---

## 1. Goal

Ship a complete plugin system: manifest schema, loader, capability
declarations, lifecycle hooks, signature verification, and five reference
plugins. Add a `phantom plugin` CLI surface (list/install/enable/disable).

## 2. Deliverables

* `phantom/plugins/__init__.py` — public API: `Plugin`, `PluginManifest`,
  `PluginLoader`, `Capability`.
* `phantom/plugins/manifest.py` — JSON-Schema-validated `PluginManifest`
  dataclass.
* `phantom/plugins/capability.py` — capability enum + per-capability
  resource specs.
* `phantom/plugins/loader.py` — discovers plugins, validates manifests,
  runs lifecycle hooks.
* `phantom/plugins/signature.py` — Ed25519 verification of signed
  bundles. (Optional path; unsigned plugins still load with a warning.)
* `phantom/plugins/registry.py` — local registry of discovered plugins
  with enable/disable persistence.
* `phantom/plugins/builtin/` — five reference plugins:
  * `clock` — return wall-clock time, no capabilities.
  * `weather` — capability: network. Reads OpenMeteo (free, no key).
  * `gh_search` — capability: network + executor. Wraps `gh search`.
  * `code_search` — capability: executor. Wraps `rg`.
  * `todo` — capability: memory. Persists to a per-session SQLite.
* `phantom/cli/__init__.py` — adds `phantom plugin {list,enable,disable,install}`.
* Tests: `tests/plugins/test_*.py` covering manifest validation, loader,
  registry, signature verification, and each reference plugin's
  contract.
* `phantom/tests/test_stage_2_done.py` — smoke test.
* `docs/peer-reviews/STAGE_2.md`.
* `phantom/plugins/README.md`.

## 3. Acceptance criteria

* [x] `Plugin` ABC defined; reference plugins implement it.
* [x] Manifest schema validates real-world manifests; rejects malformed.
* [x] Loader discovers plugins on `~/.phantom/plugins/`.
* [x] Capabilities declared; loader runs each plugin under a Stage-1
  sandbox policy that matches its declarations.
* [x] Signed-bundle verification works (Ed25519 PyNaCl).
* [x] All reference plugins have at least one passing test.
* [x] `phantom plugin list` / `enable` / `disable` work.
* [x] Stage-2 smoke test passes.
* [x] Full v3 baseline + Stage 0 + Stage 1 still passes.

## 4. Smoke test

`phantom/tests/test_stage_2_done.py` asserts:

1. The reference plugins are discoverable.
2. The `clock` plugin returns ISO-8601 time when called.
3. The `weather` plugin's manifest declares `network` capability.
4. Loading a malformed manifest raises `phantom.errors.PluginError`.
5. The `phantom plugin list` CLI lists all five reference plugins.
