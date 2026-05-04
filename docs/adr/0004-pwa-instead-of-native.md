# ADR-0004 — PWA instead of native iOS / Android apps

* Status:  Accepted
* Date:    2026-04-25
* Authors: Aravind Labs

## Context

OpenClaw ships native iOS, Android, and macOS apps. They look the part on
a phone and they integrate with platform features (push notifications, OS
keyring, share sheet) in a way a website cannot.

Phantom does not have those apps. To match OpenClaw on mobile reach we have
three options: ship native apps, ship cross-platform apps (React Native /
Flutter), or ship a Progressive Web App.

The constraints we live under:

* **No Apple Developer account** at the time of writing ($99/yr blocker).
* **No Google Play console account** ($25 one-time blocker).
* **No iOS / Android device** in the build environment.
* **Single-engineer development cadence** for the foreseeable future.
* The agent is **single-user** by design — there is no SaaS Phantom that a
  mobile app would talk to. Each user runs their own gateway.

The user experience we need to deliver on mobile:

1. Reach the agent from the lock screen — installable, with an icon.
2. Receive notifications when the agent finishes long-running work.
3. Continue a conversation while offline (read-only) when on a flaky
   connection.
4. Speak / listen (voice loop from Stage 6).
5. Never re-enter the gateway URL after the first install.

## Decision

We ship a **Progressive Web App** at
`https://phantom.aravindlabs.tech/app`. Concrete deliverables (Stage 6):

* `manifest.webmanifest` declaring name, icons (192/512), theme colour,
  display mode `standalone`, scope, start URL.
* A service worker (Workbox-based) implementing:
  * Stale-while-revalidate caching for the app shell.
  * Cache-first for static assets (icons, fonts).
  * Network-first with offline fallback for chat history reads.
  * Background sync queue for messages composed while offline.
* Web Push notifications via VAPID. Subscriptions stored on the user's own
  gateway, not centrally.
* `display_override: ["window-controls-overlay", "minimal-ui"]` so the
  installed PWA looks app-shaped on Chromium-family browsers and macOS.
* iOS-Safari "Add to Home Screen" support with the matching apple-touch
  icons.
* Microphone / speaker permission gating for the Stage 6 voice loop.
* Local-only auth tokens — the PWA never sees the license key, only a
  short-lived signed cookie issued by the user's own gateway.

## Alternatives considered

### Native iOS + Android apps

Best UX, worst engineering economics for a single-engineer project with no
existing developer accounts. Filed as "future, when revenue justifies a
mobile contractor".

### Cross-platform (React Native / Flutter / Capacitor wrapper)

Capacitor would essentially wrap the same web app inside a WebView and ship
it to stores. We would still need the developer accounts and signing
infrastructure. The PWA gets us 90% of the value at 0% of the cost; we can
add Capacitor later without touching the underlying web app. Rejected for
now, but the PWA is built so that this conversion is mechanical.

### Telegram / Discord-only mobile

Acceptable as a stopgap (and we ship it via Stage 3 anyway), but it
requires the user to give the agent control over their messaging
account. Many users will not. Insufficient on its own.

## Consequences

**We get:**

* A mobile experience users can install in two taps without opening an app
  store.
* No app review cycle, no signing keys, no platform fees.
* The same codebase serves desktop browsers, mobile browsers, and the
  installed PWA. One bug fix, three platforms.
* Updates are instant (refresh the service worker), not gated by store
  review.

**We pay:**

* The mobile UX is bound by what browsers expose. iOS Safari is the
  weakest link: Web Push only landed in iOS 16.4, and background sync is
  not supported.
* No deep OS integrations: no Siri shortcuts, no widget on the lock
  screen, no NFC.
* The "is this a real app?" perception barrier. We compensate with a
  polished install flow and a screenshot tour on the marketing site.

## Stakes

If this decision is wrong:

* **Worst case** — paying customers ask for a real app and we lose deals
  to OpenClaw. Mitigation: we already plan a Capacitor escape hatch.
  Switching from PWA to Capacitor is a 2–3 week project, not a 6-month
  rewrite.
* **Reversal cost** — low. The PWA's UI code carries over verbatim into a
  Capacitor wrapper. The native shell is the only new code.
* **Probability of regret** — moderate. Mobile is a real hole we are
  papering over. The decision is correct *for the current resourcing*; if
  resourcing changes, we revisit.
