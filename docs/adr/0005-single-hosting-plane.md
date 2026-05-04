# ADR-0005 — `phantom.aravindlabs.tech` as the single hosting plane

* Status:  Accepted
* Date:    2026-04-25
* Authors: Aravind Labs

## Context

A v4 release pipeline needs to ship multiple kinds of artefacts:

1. The marketing / sales site (`/`).
2. The license activation API (`/api/license/*`).
3. The download endpoints for source zips and Linux binaries
   (`/phantomcli/downloads/*`).
4. The hosted PWA (`/app`).
5. The static plugin registry index (`/plugins/index.json`).
6. The mkdocs documentation site (`docs.phantom.aravindlabs.tech`).
7. The version manifest (`/phantomcli/version.json`).
8. CDN-cached release artefacts.

We already operate `phantom.aravindlabs.tech` behind Caddy with Postgres
and the Razorpay-backed license server. The infrastructure is paid-for and
running.

## Decision

We consolidate **all** Phantom-facing services on the existing
`phantom.aravindlabs.tech` hosting plane. The route map:

| Path                                  | Served by             | Cached    |
|---------------------------------------|-----------------------|-----------|
| `/`                                   | Marketing static SPA  | yes       |
| `/app/*`                              | PWA static + SW       | yes (SW)  |
| `/phantomcli/version.json`            | Static, behind CDN    | 60 s TTL  |
| `/phantomcli/downloads/*.zip`         | Static                | immutable |
| `/api/license/*`                      | FastAPI license server| no-cache  |
| `/api/plugins/sign`                   | FastAPI signer (Pro)  | no-cache  |
| `/plugins/index.json`                 | Static, behind CDN    | 5 min TTL |
| `docs.phantom.aravindlabs.tech/*`     | mkdocs, Caddy vhost   | yes       |

Hard rules:

* The PWA, marketing site, plugin index, and version manifest are
  **deployable as static files**. No backend dependency for the read path.
* The license server, signer, and Razorpay integration are the **only**
  dynamic surfaces. They live behind FastAPI and can be deployed
  independently of the static surface.
* TLS, HSTS, OCSP stapling, and CSP headers are owned by Caddy. The
  applications do not implement transport security.
* Every static response carries `Cache-Control` and `ETag` consistent with
  its TTL row above. The release pipeline asserts this.

## Alternatives considered

### Split across multiple subdomains (`api.phantom`, `app.phantom`, etc.)

Cleaner for a large engineering team. Adds DNS, certificate, and
deployment friction for a single-engineer project. Rejected.

### Use Vercel / Cloudflare Pages for the static surface

Faster CDN. Adds another vendor to the bill and another moving piece for
the licensing flow to integrate against. The user already has Caddy and a
running pipeline; doubling vendors for a marginal latency gain is bad
trade.

### Host the docs on Read the Docs

Same vendor problem. mkdocs builds locally and uploads as static files;
that is the simplest possible flow.

## Consequences

**We get:**

* One TLS certificate, one DNS record, one deploy target for the static
  surface.
* A clear separation between the static read-path (cacheable, infinite
  scaling for free) and the dynamic write-path (license + signer).
* Reuse of the existing Caddy/Postgres/Razorpay infrastructure with no
  new vendors.

**We pay:**

* `phantom.aravindlabs.tech` becomes a single point of failure for
  download / activation / docs / PWA. Mitigation: the static surface is
  static — a CDN failover can serve the last successful build from
  anywhere with five minutes of work.
* The CDN cache rules are non-trivial. We keep them in
  `infra/caddy/Caddyfile` and assert them in `tests/test_release_assets.py`
  (Stage 8).

## Stakes

If this decision is wrong:

* **Worst case** — outage takes down activation + downloads + docs at the
  same time. Mitigation: the activation server is the only one that
  matters for paying customers, and it is independently deployable. The
  rest can be served from a backup origin within minutes.
* **Reversal cost** — low. Splitting onto subdomains later is a DNS and
  CDN configuration change, not a code change.
* **Probability of regret** — low at current scale. Revisit when paid
  Pro-tier traffic crosses 1k req/s sustained.
