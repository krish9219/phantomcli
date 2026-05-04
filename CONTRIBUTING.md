# Contributing to Phantom

> Phantom v4 is open-core. The CLI, sandbox, plugin SDK, channels, MCP/ACP,
> skills, memory, voice, canvas, and PWA are MIT. The Pro-tier dashboard,
> license server, and hosted plugin index are commercial. Contributions are
> welcome to the open core; the commercial surface is closed by policy.

This document tells you how a contribution is reviewed and what we expect
from a pull request. Read `ARCHITECTURE.md`, `VISION.md`, and the relevant
ADR(s) before opening a PR that touches non-trivial code.

---

## Ground rules

1. **Open an issue first** for anything bigger than a typo. Describe the
   problem, the proposed solution, and the alternatives you considered. A
   PR that lands without an issue is allowed but reviewed last.
2. **One PR = one topic.** Do not bundle a refactor with a feature with a
   bug fix. Each gets its own PR.
3. **Tests come with code.** New behaviour without tests is not reviewed.
   Existing behaviour that lacks tests gets tests *added* in the same PR.
4. **No silent breaking changes.** Every behaviour change in a public API
   is called out in `CHANGELOG.md` and, where appropriate, in the
   relevant `docs/stages/` file.
5. **Strict typing on new code.** `phantom/*` is mypy-strict. Adding a
   `# type: ignore` requires a comment explaining why.

## What "production grade" means here

Phantom is a security-sensitive agent that ships on real users' laptops.
"Production grade" is not a vibe; it has a checklist:

* Every public function has a docstring with at least one `Examples`
  block.
* Every error path is reachable from a test.
* Every external input is validated at the trust boundary.
* No `print` for diagnostics — use the logger.
* No `time.sleep` to dodge a race — use the proper synchronisation.
* No `subprocess.run(... shell=True)` outside the sandbox.
* Configuration goes through `phantom.config`; no `os.getenv` reads
  scattered through business code.

If your PR violates any of the above, the reviewer will say so. It is not
personal.

---

## Setting up a dev environment

```bash
git clone https://github.com/krish9219/phantomcli.git
cd phantomcli
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Before opening a PR:

```bash
ruff check .
ruff format --check .
mypy phantom/
pytest -q
```

All four must be green. CI runs the same commands.

## Running the existing baseline

```bash
# Full v3 + v4 test surface (fast):
pytest -q

# v3 baseline only (the historical 796 tests):
pytest -q tests/ test_phantom.py

# v4 stage-gate smoke tests only:
pytest -q phantom/tests/

# Coverage report:
pytest --cov=phantom --cov=omnicli --cov-branch
```

## Architecture decisions

If your PR proposes a different way of doing something the project already
decided on, **write an ADR**. Don't rewrite the code first. Rewriting code
to argue an architectural point is much slower than writing 300 words and
having the conversation against a written proposal.

ADR template + index: `docs/adr/README.md`.

## Stage gates

Phantom v4 ships in 9 stages. If your PR touches a stage's deliverables,
read that stage's `docs/stages/STAGE_<N>.md` first — the deliverables
list is exhaustive, and PRs that violate it (e.g. by adding a new
public symbol that the stage explicitly deferred) will be redirected to
a later stage.

A stage closes when its peer review (`docs/peer-reviews/STAGE_<N>.md`)
has its sign-off section filled in. Until then, the stage is in
progress; PRs against an in-progress stage are encouraged.

## What we do not accept

* Auto-formatted re-indents over the whole codebase.
* Mass renames not part of an architectural decision.
* "Cleanups" that touch >50 files in one PR.
* Vendored dependencies (use `pyproject.toml`).
* Test-disabling commits without an issue explaining why.

## Reporting security issues

See `SECURITY.md`. Do **not** open a public issue for security bugs.

## License of contributions

By submitting a contribution to the open-core surface (anything outside
`phantom/pro/` and `infra/license-server/`), you agree your contribution
is licensed under MIT. By submitting a contribution to the closed
commercial surface, you agree your contribution is licensed under the
project's commercial license; see `LICENSE` for the boundary.
