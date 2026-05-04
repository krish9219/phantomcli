# Stage 7 Peer Review

* Stage: 7 — i18n + Onboarding + Docs site
* Date: 2026-04-25

## Strengths

* i18n coverage test (`TestAllKeysHaveAllLocales`) is real teeth — a
  contributor who adds a new English string but forgets a translation
  gets a red CI build.
* Wizard is a pure data structure; tests don't need a TTY. The CLI
  shell is the (Stage-8) UI veneer over a tested core.
* Docs site reuses every existing ADR / stage / peer-review doc by
  reference, no duplication.

## Risks

* **Medium** — translations are hand-written. An English copy edit
  silently breaks the parity guarantee until the test catches it.
  Stage 8 should add a CI gate that warns on PRs touching only the
  English catalogue.
* **Low** — the wizard re-prompts on invalid input via exception,
  which is fine for the CLI but awkward for a future PWA wizard. The
  state machine returns the current step; PWA can poll.

## Required follow-ups

None.

## Suggested follow-ups

* Stage 8: `phantom onboard` CLI subcommand that drives the wizard
  in a TTY.
* Stage 8: mkdocs build target in the release pipeline.
* Stage 8: weblate / Crowdin integration for community translations.
