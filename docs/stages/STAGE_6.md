# Stage 6 — Realtime voice + Canvas + PWA

* Status: CLOSED
* Date: 2026-04-25

## Deliverables

* `phantom/voice/{__init__,loop}.py` — VoiceFrame, VoiceTurn, STTEngine
  protocol, TTSEngine protocol, VoiceLoop with VAD-driven flush and
  barge-in.
* `phantom/canvas/{__init__,node}.py` — CanvasNode with per-kind
  validation (text, code, table, chart, button, form, container) and
  JSON serialisation.
* `phantom/pwa/{__init__,manifest}.py` — Web App Manifest builder +
  service worker template (stale-while-revalidate + network-first
  for /app/api/ + skipWaiting on activate).
* Tests across `tests/voice/`, `tests/canvas/`, `tests/pwa/` (28 new).
* `phantom/tests/test_stage_6_done.py`.

## Validation

* 28/28 Stage-6 tests pass.
* PWA manifest is JSON-serialisable; SW source is a valid string.
* Voice loop accepts frames and emits transcripts on silence; barge-in
  cancels queued TTS.

## Known limitations

* Real STT/TTS engines (faster-whisper / Piper) are extras; the
  framework runs without them. Stage 8 wires them in via
  `phantom-cli[voice]`.
* Canvas client-side renderer is not in this repo — it lives in the
  dashboard React app served at `phantom.aravindlabs.tech/app`. The
  protocol contract here is what the renderer consumes.
* PWA assets are *generated* by the build target; the build CLI is a
  Stage-8 deliverable.
