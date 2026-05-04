# Go-to-market plan — Year 1, ₹1 crore target

Sole founder + maintainer. Single repo. Open-core monetization.

## Revenue model recap

| Channel | Year-1 target | Conversion math |
|---|---|---|
| Lifetime licenses (₹999) | **₹50 lakh** | 50,000 paying users × 1% conversion of 5M reachable users |
| Annual team licenses (₹5k/seat) | **₹30 lakh** | 60 teams × avg 10 seats |
| Enterprise (₹2.5L+) | **₹20 lakh** | 8 enterprise deals avg ₹2.5L |
| **Total** | **₹1 crore** | |

Reachable user math: OpenCode hit 6.5M monthly devs in ~6 months at similar tech-quality. Conservative 5M reach × 1% conversion = 50K paying users at the lifetime tier. That's the **base case**, not the bull case.

## Quarter-by-quarter

### Q1 (Months 1-3) — Launch + traction

**Month 1: Public launch**
- [ ] Make repo public on GitHub (already prepped: README, CONTRIBUTING, LICENSE)
- [ ] Deploy mirror at phantom.aravindlabs.tech/plugins (Dockerfile + Caddyfile shipped)
- [ ] Stand up Pro purchase page (Razorpay already wired in code)
- [ ] Stand up phantom.aravindlabs.tech landing page
- [ ] Show HN post — Saturday morning PT for max engagement
- [ ] Twitter/X thread same day
- [ ] Submit to Product Hunt the following Tuesday

**Month 2: Content + community**
- [ ] One technical blog post per week on the differentiators (sandbox, edit transactions, AST rename, plugin mirror, daemon mode)
- [ ] 5-minute demo video for each major feature
- [ ] Discord server for community (free tier support)
- [ ] DEV.to / Hashnode cross-posts
- [ ] First 3 sponsored-issue bounties posted (€100-500 each)

**Month 3: First paying customers**
- [ ] First Pro lifetime conversions (target: 100 by end of Q1)
- [ ] First sales call (annual team license)
- [ ] Newsletter launches with weekly release notes

**Q1 target: 5,000 GitHub stars, 100 Pro lifetime customers, 1 team annual deal**
**Q1 revenue: ₹3 lakh**

### Q2 (Months 4-6) — Scale + first enterprise

**Month 4: Conferences + partnerships**
- [ ] Submit talks to PyCon, ChaiCode, FOSDEM
- [ ] Outreach to OpenAI / Anthropic / Mistral DevRel for joint blog posts
- [ ] Launch the v1.1 release with: real-time voice loop, full TypeScript type checker, IRC adapter

**Month 5: Enterprise prep**
- [ ] First enterprise sales conversations (DM warm intros from launch)
- [ ] SOC 2 Type 1 work begins (Drata or Vanta)
- [ ] SAML/SSO Pro feature ships

**Month 6: First enterprise close**
- [ ] First enterprise deal (₹2.5-5L)
- [ ] Mid-year retrospective + pricing recalibration

**Q2 target: 25,000 GitHub stars, 1,000 Pro customers, 5 team deals, 1 enterprise**
**Q2 revenue: ₹15 lakh cumulative**

### Q3 (Months 7-9) — Plugin ecosystem + partnerships

**Month 7: Marketplace economics**
- [ ] Open the plugin marketplace to third-party paid plugins (Aravind Labs takes 20%)
- [ ] First 3 paid third-party plugins launch
- [ ] Bug bounty program ($100 - $5,000 tiers)

**Month 8: Strategic partnerships**
- [ ] Reseller deal with one Indian system integrator
- [ ] Listed on AWS Marketplace + Azure Marketplace as customer-deployed

**Month 9: Mid-funnel optimization**
- [ ] Analyze which features convert free → Pro best
- [ ] Tune the dashboard UX based on conversion data

**Q3 target: 50,000 GitHub stars, 5,000 Pro customers, 30 team deals, 4 enterprise**
**Q3 revenue: ₹50 lakh cumulative**

### Q4 (Months 10-12) — Scale to ₹1 crore

**Month 10: International push**
- [ ] EU pricing (€19 lifetime)
- [ ] US pricing ($25 lifetime)
- [ ] Stripe + Paddle alongside Razorpay

**Month 11: Compliance closes**
- [ ] SOC 2 Type 2 cert closes → unblocks larger enterprise deals
- [ ] HIPAA BAA template ready

**Month 12: Year-end push**
- [ ] Black Friday lifetime deal (₹699 for 1 week)
- [ ] Annual report blog post with public revenue numbers

**Q4 target: 100,000 GitHub stars, 30,000 Pro customers, 60 team deals, 8 enterprise**
**Q4 revenue: ₹1 crore cumulative**

## Defensive moats

These are what keeps competitors from copying the model:

1. **Trademark "Phantom" / "PhantomCLI"** — file in India + US within Q1.
2. **Curated mirror as a value-add** — anyone can run a mirror, but ours is vetted/audited (operator pays for trust, not for code).
3. **License-server source stays closed** — the OSS code can't validate Pro licenses without phoning home. Forks don't get the Razorpay flow.
4. **Brand + domain + community** — phantom.aravindlabs.tech is yours; a fork can't claim it.

## Risks + mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Anthropic / OpenAI launches identical product | High | High | Faster iteration; community moat; specific differentiators (sandbox depth, cross-harness import) are non-trivial to copy |
| OpenCode adds matching sandboxing | Medium | Medium | First-mover; security feature is hard to retrofit cleanly |
| Solo founder burnout | High | Critical | Hire first contractor at month 6 from Pro revenue |
| Major security incident | Low | Critical | Bug bounty program from month 7; SOC 2 cert; quarterly audits |
| Razorpay outage delays revenue | Low | Medium | Add Stripe + Paddle as backups in month 10 |

## Metrics to track weekly

- GitHub stars
- New Pro purchases (count + revenue)
- Active users (telemetry-free; use mirror download counts as proxy)
- GitHub issues filed (community health)
- PRs opened by external contributors
- Newsletter subscribers
- Discord member count
- Bounce rate on Pro purchase page

## What "winning" looks like at year 1

- ₹1 crore in license revenue
- 100k GitHub stars
- 30k paying lifetime customers
- 60 team contracts
- 8 enterprise deals
- 1 full-time hire from revenue
- Phantom is the answer when someone asks "what's the open-source AI coding agent?"
