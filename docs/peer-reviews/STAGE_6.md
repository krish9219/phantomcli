# Stage 6 Peer Review

* Stage: 6 — Voice + Canvas + PWA
* Date: 2026-04-25

## Strengths

* Voice loop is engine-agnostic via Protocols; tests use stubs and the
  framework is fully exercised without faster-whisper / Piper.
* Barge-in is a single counter we can monitor in production.
* Canvas validation rejects malformed agent output at construction
  time, not at render time. The dashboard cannot crash on bad agent
  JSON.
* PWA manifest + service worker are *generated* by Python — the build
  pipeline can re-stamp the cache version on every release without
  manual edits.

## Risks

* **Medium** — real-time voice in a single thread will block on TTS
  rendering for long utterances. Stage 8 should chunk the TTS render
  call.
* **Medium** — service worker uses stale-while-revalidate for the app
  shell; a security release that needs to invalidate cached UI relies
  on the cache version bump. Document the policy in Stage-8 release
  pipeline.

## Required follow-ups

None.

## Suggested follow-ups

* Stage 8: bundle real engine adapters for STT (faster-whisper) and
  TTS (Piper) under the `[voice]` extras.
* Stage 8: dashboard React app source under `/web/dashboard-pro/` (Pro
  tier).
* Stage 8: PWA build CLI: `phantom pwa build --out dist/pwa`.
