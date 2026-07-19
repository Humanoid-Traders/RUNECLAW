# Idle-Asset Yield Optimizer (cross-venue, cross-source)

> Idle capital earning $0 that could be earning. Scan every connected wallet and
> account, match each idle asset to the best available rate — and **recommend,
> never auto-deploy**.

## What this is

The cross-source brain on top of the existing single-venue Yield Radar. The Radar
(`bot/core/yield_radar.py`) scans idle spot on ONE exchange and matches it to that
exchange's Earn rates. This optimizer is source-agnostic: it takes idle holdings
aggregated from *all* connected accounts + wallets and yield offers from *all*
sources (CEX Earn across venues, on-chain staking, DeFi lending), and picks the
best rate per asset.

## Discipline (matches the session's non-custodial + honest rules)

- **Recommendation-only.** The optimizer never moves funds. The action path stays
  the existing confirm-gated `/stake` (flexible-only, reserve-clamped). Its output
  is advice.
- **Custodial vs non-custodial is surfaced, always.** CEX Earn is custodial (the
  venue holds the coin); on-chain staking / DeFi lending is non-custodial. A higher
  custodial APY is shown *with its trust cost stated* — never silently preferred.
  This mirrors the whole session's non-custodial spine (an option can carry a
  `prefer_noncustodial` bias so a marginally-lower non-custodial rate can win).
- **No fabricated rates.** An asset with no known yield option returns
  `no_option` — never a made-up APY (the Proof-of-PnL `UNVERIFIED` discipline).
- **Lockup + risk surfaced.** Each option carries `lockup_days` and a `risk_tier`;
  a `max_lockup_days` filter drops options a user can't commit to.

## Inputs (the caller fetches; the optimizer is pure)

- **holdings**: `[{asset, free_amount, usd_value, location}]` — idle balances from
  the unified cross-venue portfolio (CEXs + Hyperliquid + SIWE wallets).
- **options**: `[{asset, source, kind, apy, lockup_days, custodial, risk_tier}]`
  where `kind ∈ {cex_earn, staking, defi_lending}`. Sources plug in independently:
  the Bitget Earn catalog is one; Lido ETH staking APR, Aave supply APY, other CEX
  Earn are additional feeders.

## Output

`optimize(holdings, options, *, min_usd, max_lockup_days, prefer_noncustodial)`:

    {recommendations: [{asset, idle_usd, best: {...}, alternatives: [...],
                        est_year_usd, status}],
     total_idle_usd, total_deployable_usd, total_est_year_usd,
     unmatched: [asset, ...]}

`status ∈ {recommended, no_option, below_min}`. `est_year_usd = idle_usd * apy/100`.
Recommendations are ranked by `est_year_usd` (most incremental income first).

## Pre-registered predictions (before the tests)

- **Y1 — best rate wins, ties broken honestly.** For an asset with several options,
  `best` is the highest APY passing the lockup filter; with `prefer_noncustodial`,
  a non-custodial option within a small APY margin of a higher custodial one wins,
  and the tradeoff is stated. *Falsifier:* a dominated option chosen, or the
  custodial flag hidden.
- **Y2 — no fabricated yield.** An idle asset with no option → `status=no_option`,
  no APY, and it appears in `unmatched`. *Falsifier:* any invented rate.
- **Y3 — filters + dust.** Holdings below `min_usd` → `below_min` (not counted in
  deployable); options above `max_lockup_days` are excluded from `best`.
  *Falsifier:* a sub-dust holding recommended, or an over-lockup option chosen.
- **Y4 — accounting + determinism.** `total_est_year_usd` equals the sum of
  recommended `est_year_usd`; the same inputs yield the same output every time.
  *Falsifier:* a mismatched total or non-deterministic ranking.

Results: `tests/test_idle_yield.py`.
