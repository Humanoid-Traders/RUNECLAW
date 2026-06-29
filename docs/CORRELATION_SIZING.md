# Portfolio-Aware Correlation Sizing

> **Status: BUILT, default OFF** (`CORRELATION_SIZING_ENABLED=false`). Only ever
> *reduces* position size; until enabled, behaviour is byte-identical.

## Why (and why not "just the existing correlation check")

RUNECLAW already has a correlation control: `_check_correlation` is a **count-cap
concentration gate** — it *rejects* a new trade once its correlation group already
holds `max_correlation_per_group` (or `max_unmapped_correlated`) open positions.
But that gate is binary: every trade **below** the cap is admitted at **full
size**. So the second and third correlated, same-direction bet each go on at 100%
even though the marginal portfolio risk they add is larger than the first — three
ALT-L1 longs are much closer to one 3× SOL long than to three independent trades.

This module fills exactly that gap: a graduated **size reduction** (not a
reject) for a new trade that stacks on the **same correlation group AND the same
direction** as positions already open. The count-cap still backstops the extreme;
this just makes the trades it lets through appropriately smaller.

## How it works

`bot/risk/risk_engine.py`:

- `_correlation_size_factor(idea)` counts open positions that share the new
  trade's correlation group **and** direction, and returns a multiplier:

  ```
  factor = max(correlation_sizing_floor, 1 - correlation_sizing_step * count)
  ```

  Defaults: `step = 0.20` (−20% per correlated same-side position), `floor = 0.5`.
  So one prior correlated long → 0.80×, two → 0.60×, three → floored at 0.50×.

- The **unmapped-alt bucket is excluded**: its members share one pooled group but
  are not all mutually correlated, so co-membership there is not a concentrated
  directional bet and is left at full size.

- **Opposite-direction** positions don't count — a same-group hedge *lowers*
  portfolio risk, so it never triggers a cut.

Hook: applied in `_evaluate_locked`, in the same pre-cap reduction chain as the
regime / session / equity-curve / drawdown-recovery / macro multipliers, right
before the notional cap — so the cap and check #2 retain final authority.

## Safety

- **Default OFF**; byte-identical behaviour until enabled.
- **Only reduces** size (multiplier in `[floor, 1.0]`) — never grows a position.
- **Bounded** by `correlation_sizing_floor` and **fail-open**: any error returns
  `1.0` (no reduction), so it can never block a trade or bypass the risk engine.

## Enable

```
CORRELATION_SIZING_ENABLED=true
# optional tuning:
CORRELATION_SIZING_STEP=0.20
CORRELATION_SIZING_FLOOR=0.5
```
