# Pricing — phantom.aravindlabs.tech/pro

The page copy below is what to put on the Pro purchase page. Pricing converted to operator's notes; copy is reader-facing.

---

# Phantom Pro

The free MIT core gives you the entire CLI, sandbox, plugin system, and dashboard. **Pro adds the production conveniences that turn Phantom into a tool you can run a business on.**

## What's in Pro

| Feature | Free (MIT core) | Pro |
|---|---|---|
| CLI, sandbox, plugins | ✅ | ✅ |
| Up to 2 API keys | ✅ | — |
| **Unlimited rotating API keys** | — | ✅ |
| Single-user dashboard | ✅ | ✅ |
| **Multi-user dashboard with audit log** | — | ✅ |
| **Multi-agent orchestration at scale** | basic (3 agents) | unlimited |
| **Hosted plugin mirror** with curated/audited plugins | self-host only | ✅ |
| **Priority support** (email, 24h SLA) | community-best-effort | ✅ |
| **Razorpay license server** with device binding | — | ✅ |
| **Compliance reports** (SOC 2 / ISO 27001 / HIPAA) | — | ✅ on Enterprise |
| Source code access | ✅ (MIT) | ✅ |

## Plans

### Phantom Pro — Lifetime

**₹999 one-time.** Up to 3 devices. All Pro features. Lifetime updates.

[Buy Phantom Pro →]

### Phantom Pro — Annual (for teams)

**₹4,999 / user / year.** Unlimited devices per user. Pro features + multi-user dashboard. Includes 24h-SLA email support.

Minimum 5 seats.

[Contact sales →]

### Phantom Enterprise

For teams running Phantom in regulated environments (HIPAA, PCI, SOC 2):

- Everything in Pro Annual
- SAML / SSO / SCIM
- Audit log forwarding (Splunk / Datadog / New Relic)
- Compliance package (SOC 2 mapping, HIPAA BAA)
- Dedicated success engineer
- 4h SLA

**Starts at ₹2,50,000 / year for 25 seats.** Volume discounts above 100 seats.

[Contact sales →]

## FAQ

**Is the open-source version usable in production?**
Yes. The MIT core ships every feature you need to run Phantom on your laptop or your team's laptops. The 2-API-key cap is the only gating; if you don't need to rotate >2 keys, you don't need Pro.

**What does "lifetime" mean?**
You get all Pro features forever, including new Pro features added in future v1.x releases. Major version bumps (v2.0+) are a separate purchase if they ship before the heat death of the universe.

**Can I run my own mirror?**
Yes — see `deploy/mirror/README.md` in the open-source repo. Pro just gives you access to ours, which is curated (we vet every plugin before publishing).

**What if I don't pay?**
You keep using the open-source CLI forever. No nags, no upgrade prompts, no telemetry. We sell licenses by being useful, not by interrupting you.

**Refunds?**
30-day money-back. Email aravind.engineer001@gmail.com.

**How do I support the project without Pro?**
Star the repo. Tell your team. File good bug reports. Send PRs. Sponsor specific issues via the GitHub Sponsors-style bounty system.

---

## Why this pricing

Phantom is built by one person (Aravind). Open-source is the marketing channel. Pro pays for development. The lifetime tier is priced low enough that individual developers can buy without thinking; the annual + enterprise tiers are how teams contribute to keeping Phantom alive.

**Year-1 target: ₹1 crore in license revenue.**

That gets us:
- 6 months of full-time development
- Shipping the v1.x roadmap (E2EE Matrix, full TypeScript type checker, on-device Whisper, native iMessage bridge via BlueBubbles, real-time voice loop)
- A part-time community manager + dedicated security audit
- Hosting + CDN for the mirror

Every license keeps the project open. Thank you.
