# Guardian — Market-Integrity & Manipulation Veto (Phase 4, veto-only)

> The AI proposes. Deterministic controls authorize. This layer can only ever
> say **NO** — it never proposes, sizes, or signals a trade.

## What this is

The defensive market/social intelligence layer. It scores external red-flag
features (social + market + on-chain shapes commonly seen around pump-and-dump /
manipulated tokens) into a single deterministic verdict — `clear` / `caution` /
`veto` — that the risk gate can consume as an **additional rejection input**.

Two hard properties, by construction:

1. **Veto-only.** The output has exactly two effects: it can *reject* a candidate
   (`veto`), or *warn* (`caution`), or do nothing (`clear`). There is no code path
   by which it can approve, up-vote, size up, or originate a trade. It only ever
   tightens.
2. **Detection, never generation.** It flags manipulation *shapes* — sybil-looking
   mention networks, wash-trade-shaped volume, coordinated-uniform sentiment — as
   reasons to stand down. It never produces any of them (per the Non-Goals: sybil
   detection yes, sybil *generation* never).

## Features (all detection signals; each optional → a missing one is skipped)

| feature | shape it catches | direction |
|---|---|---|
| `social_spike_ratio` | mentions vs baseline — a sudden coordinated pump | high = risk |
| `new_account_ratio` | fraction of mentioning accounts freshly created — sybil shape | high = risk |
| `sentiment_uniformity` | near-identical messaging — bot-network shape | high = risk |
| `price_liquidity_divergence` | price move large vs on-chain liquidity — thin-book pump | high = risk |
| `wash_volume_ratio` | volume concentrated in self-crossing / round-trips | high = risk |
| `holder_concentration` | top-holder share — rug/exit risk | high = risk |
| `listing_age_hours` | brand-new token, no track record | low = risk |

A **hard flag** (an individually disqualifying reading, e.g. holder concentration
above the rug threshold) forces `veto` on its own. Otherwise a weighted count of
soft flags maps to `caution` / `veto`. A missing feature is skipped (fail-open per
feature) — never fabricated, never counted.

## Staged enforcement (default OFF)

Like the Intent Compiler and Authority Envelope, this ships as a **pure core** with
three modes (`off` / `shadow` / `enforce`) for a later, separately-gated wiring:

- `off` — not consulted (default).
- `shadow` — the verdict is recorded (`INTEGRITY: shadow — would veto …`) but does
  not block.
- `enforce` — a `veto` blocks the candidate at the risk gate (tighten-only).

This PR ships the pure core + tests only; it blocks nothing.

## Pre-registered predictions (before the tests)

- **V1 — veto-only.** No input to `assess()` ever yields anything but
  `clear`/`caution`/`veto`; there is no "approve"/positive output. *Falsifier:* any
  input producing an up-vote or a positive size signal.
- **V2 — hard flag forces veto.** A single hard flag (e.g. holder concentration
  past the rug threshold) yields `veto` regardless of the other (clean) features.
  *Falsifier:* a hard flag returning `clear`/`caution`.
- **V3 — clean features clear.** A feature bundle with every reading in the benign
  range returns `clear` with no flags. *Falsifier:* a clean bundle vetoing.
- **V4 — fail-open per feature + determinism.** Missing features are skipped (not
  counted, not fabricated); the same bundle yields the same verdict + score every
  time. *Falsifier:* a missing feature raising, being counted, or a non-deterministic
  verdict.

Results: `tests/test_guardian_integrity_veto.py`.
