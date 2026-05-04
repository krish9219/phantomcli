# Stage 3 — Multi-channel framework + channel adapters

> Goal: Phantom answers on whatever channel the user prefers. The
> agent loop sees a single uniform `ChannelEvent`/`ChannelMessage`
> shape; channel-specific protocol details live in adapters.

* Status:  CLOSED
* Author:  Phantom v4 architect
* Started: 2026-04-25
* Closed:  2026-04-25

---

## 1. Goal

Ship the channel-adapter framework + adapters for the four channels we
can fully test with mocked transports: **WebChat** (own WebSocket
server), **Telegram**, **Discord**, **Slack**. Matrix and IRC are
deferred to Stage 8 (they require live homeservers / IRC servers
to verify end-to-end; the abstraction supports them and Stage 8 fills
in the wire code).

The framework includes the trust-cap policy (per ADR `Stage 1`
section): Telegram is capped at trust level 3 by policy; even God Mode
on the local CLI cannot escalate over Telegram.

## 2. Deliverables

* `phantom/channels/{__init__,adapter,event,message,router,policy}.py`
* `phantom/channels/{webchat,telegram,discord,slack}/{__init__,adapter}.py`
* `tests/channels/test_*.py`
* `phantom/tests/test_stage_3_done.py`
* `docs/peer-reviews/STAGE_3.md`

## 3. Smoke test

`phantom/tests/test_stage_3_done.py`:

1. The `ChannelAdapter` ABC is importable and abstract.
2. WebChat adapter accepts an inbound JSON event and produces a
   `ChannelEvent`.
3. The router dispatches an outbound message to the registered adapter.
4. Telegram adapter caps trust at 3 by policy.
5. `phantom.feature_flags()['stage'] >= 3`.
