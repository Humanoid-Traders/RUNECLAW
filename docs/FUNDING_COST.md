# Funding Carry-Cost Awareness

> **Status: BUILT, default OFF** (`FUNDING_COST_AWARE_ENABLED=false`). Only ever
> *reduces* confidence; until enabled, behaviour is unchanged.

## Why (and why not "just another funding voter")

RUNECLAW already reads the **directional** funding signal three ways:

- `of_funding` — a bounded contrarian funding **confluence voter** (order-flow).
- `funding_arb` — a directional **confidence nudge** (`+0.03` favourable / `−0.02`
  adverse) in the analyzer.
- smart-money — funding-driven **cascade / squeeze risk**.

All three look at the **instantaneous** funding rate. A *new* funding voter would
triple-count that. What none of them capture is the **carry cost over the holding
period** — and that is what actually erodes net edge: a **swing** held two days
pays funding every 8h interval; a **scalp** pays ~none. This module adds exactly
that missing dimension.

> This bot is **USDT-M perps only**, so true delta-neutral funding *carry*
> (collect funding hedged with spot) isn't possible — that needs a spot leg. This
> is the safe, fitting piece: cost-awareness, not a carry strategy.

## How it works

`bot/core/funding.py`:

- `expected_intervals(strategy_type)` — funding intervals over the expected hold
  (8h Bitget interval): scalp ≈ 0.25, intraday ≈ 1, swing ≈ 6, position ≈ 15.
- `adverse_funding_cost(rate, direction, strategy_type)` — the fraction of notional
  the trade would **pay** over its hold, and only on the **crowded side** you pay
  (positive funding → longs pay; negative → shorts pay). Earning funding → 0.
- `funding_cost_haircut(...)` — a **bounded, non-positive** confidence adjustment
  in `[-0.05, 0]`, scaling with the adverse cost; **0** when funding is favourable,
  mild, or the hold is short.

Hook: `bot/core/analyzer.py`, right after the `funding_arb` block, gated by
`CONFIG.analyzer.funding_cost_aware_enabled`; fail-open.

## Safety

- **Default OFF**; identical behaviour until enabled.
- **Only reduces** confidence (carry is a drag) — favourable funding is already
  rewarded by `funding_arb`, so this never double-counts a boost.
- **Bounded** (`≥ -0.05`) and **fail-open**. Never bypasses the risk engine.

## Enable

`FUNDING_COST_AWARE_ENABLED=true`.
