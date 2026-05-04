# Stage 3 Peer Review

* Stage:    3 — Multi-channel framework + adapters
* Author:   Phantom v4 architect (self-review per ADR-0006)
* Date:     2026-04-25
* Version:  4.0.0-dev
* Files reviewed: every Stage-3 deliverable in `docs/stages/STAGE_3.md`.

## 1. Scope reviewed

* `phantom/channels/{__init__,adapter,event,message,router}.py`
* `phantom/channels/{webchat,telegram,discord,slack}/__init__.py`
* `tests/channels/test_*.py` (52 tests)
* `phantom/tests/test_stage_3_done.py`

Test count delta: +52 (1,110 → 1,162). 0 skipped on the Stage-3 surface.

## 2. Strengths

* **Trust caps are enforced at the framework, not in adapters.** The
  router clamps requested trust to the adapter's
  `max_trust_level()`. Adapters declare their own ceiling; misbehaving
  adapter code can't escalate.
* **Channel-native fields stay in `metadata`.** The agent loop sees a
  uniform `ChannelEvent`. Telegram chat IDs, Slack thread timestamps,
  Discord guild IDs all stay opaque under `metadata` — never exposed
  to business code.
* **Transport is a Protocol** for Telegram/Discord/Slack. Real
  transports plug in at runtime; tests use plain dict-shaped fakes.
  No live API hits in CI.
* **Outbound message size capping is per-channel and silent**, with a
  truncation marker. No surprising "message too long" errors at the
  Slack/Discord/Telegram API boundary.
* **Failure paths are uniformly wrapped in `ChannelError`.** The
  agent loop catches one exception type instead of three vendor-
  specific ones.
* **Naive datetimes are rejected at construction** — events always
  carry timezone-aware timestamps, so audit log + analytics never
  drift across DST.

## 3. Risks

* **High — Matrix and IRC are deferred.** Stage 3 ships 4 of the 6
  channels promised in the original Stage-3 plan. Documented in
  `docs/stages/STAGE_3.md`. Stage 8 closes the gap.
* **Medium — adapters do not yet auto-reconnect on transient
  transport failure.** A WebSocket drop in production today means
  manual restart. Stage 8 adds reconnect-with-backoff.
* **Medium — Slack and Discord do not implement Socket Mode / gateway
  subscriptions in Stage 3.** The transport `Protocol` defines a
  pull-based shape (`fetch_events`/`fetch_messages`); a real Socket
  Mode connection would be event-pushed. The adapter API supports
  both via `next_event`, but the bundled poll loop assumes pull.
* **Low — there is no per-event deduplication.** If a network blip
  causes the same Telegram update to arrive twice, the agent processes
  it twice. The adapter records `update_id`/`ts` in metadata; the
  router could check against a recent-set, but Stage 3 doesn't.
* **Low — empty bodies are allowed.** Sending `text=""` is currently a
  no-op-equivalent send to the API. Real channels reject empty bodies;
  we should reject at the adapter or strip the message.

## 4. Required follow-ups (block stage close)

None.

## 5. Suggested follow-ups (do not block)

* Stage 8: Matrix adapter (`matrix-nio`).
* Stage 8: IRC adapter (`bottom`-style).
* Stage 8: auto-reconnect with exponential backoff in every adapter.
* Stage 8: per-adapter event deduplication (using metadata IDs).
* Stage 4: route `ChannelEvent` → agent loop wiring.
* Stage 4: rate-limit middleware between router and adapters.

## 6. Sign-off

> Reviewed against `docs/stages/STAGE_3.md`. Required follow-ups list
> is empty. Stage 3 is **closed** with the documented deferrals
> (Matrix, IRC) on the Stage-8 roadmap.
>
> Concrete validation:
>
> * `tests/channels/`: 52 passed, 0 skipped.
> * `phantom/tests/test_stage_3_done.py`: 7 passed.
> * Full sweep: 1,162 passed, 4 skipped in 41 s.
>
> Reviewer:        Phantom v4 architect
> Date:            2026-04-25
