# Architecture Decision Records

> Every irreversible choice this project has made — what we picked, what we
> rejected, and why. Read these before you suggest changing the architecture.

ADRs are append-only. If a decision needs to change, you write a new ADR that
**supersedes** the old one — you do not edit the old one in place. That keeps
the history of how we thought about a problem readable five years from now.

## Format

We use a trimmed Michael Nygard format with one extra section ("Stakes") that
forces the author to write down the cost of getting the decision wrong.

```
# ADR-NNNN — Short title

* Status:  Accepted | Proposed | Superseded by ADR-XXXX
* Date:    YYYY-MM-DD
* Authors: <name(s)>

## Context
What problem are we solving? What constraints are we under?

## Decision
What did we decide?

## Alternatives considered
Each alternative gets a paragraph. Be honest about why we rejected it.

## Consequences
What does this decision cost us? What does it buy us?

## Stakes
What breaks if this decision turns out to be wrong? How reversible is it?
```

## Index

| ID  | Title                                                               | Status   |
|-----|---------------------------------------------------------------------|----------|
| 0001| Open-core licensing instead of closed-commercial or pure-MIT        | Accepted |
| 0002| Backwards-compatible coexistence: `omnicli` v3 alongside `phantom` v4 | Accepted |
| 0003| Tiered sandbox: bubblewrap → firejail → unshare → docker            | Accepted |
| 0004| PWA instead of native iOS/Android apps                              | Accepted |
| 0005| `phantom.aravindlabs.tech` as the single hosting plane              | Accepted |
| 0006| Stage gates with mandatory peer review                              | Accepted |
