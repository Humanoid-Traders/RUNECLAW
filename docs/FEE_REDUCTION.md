# Fee Reduction — Lever Inventory & Runbook

RUNECLAW trades Bitget USDT perpetuals. Fees are a first-order drag on a small
account: maker **0.02%**, taker **0.06%** per side, so a round trip costs
**0.04%–0.12%** of notional before slippage. This doc is the canonical map of
every fee lever — what's shipped, what's net-neutral, what's off-code — and how
to enable each.

> **TL;DR — the single biggest win is off-code: enable the BGB fee discount on
> your Bitget account (~20% off every trade, both sides, immediately).** The
> code-side levers below are all shipped but individually marginal / A/B-neutral
> on the swing-heavy benchmark; they bite hardest on scalp-heavy churn.

---

## 1. BGB fee discount (off-code, highest value) — RECOMMENDED

Bitget gives a standing **~20% discount on trading fees when you pay fees in
BGB** (Bitget's exchange token). This applies to **every** order, maker and
taker, on **all** symbols, and stacks with VIP tiers. There is **no code
change** — it is an account setting.

**Effect:** maker 0.02% → ~0.016%, taker 0.06% → ~0.048%. On the live account's
observed ~$11–12 of commission per benchmark run, that is a direct ~20% haircut
off the largest real cost the bot pays — larger than any single code lever below.

**How to enable (operator, one-time):**

1. Buy a small amount of **BGB** on the Bitget spot market (enough to cover a
   few weeks of fees; fees are auto-deducted from the BGB balance).
2. In the Bitget app/web: **Account → Settings → Fee Settings** (or the
   "Deduct fees with BGB" / "BGB fee deduction" toggle in the trading
   preferences) → **turn it ON**.
3. Keep a non-zero BGB balance. When BGB runs out, Bitget silently falls back to
   charging fees in USDT at the full (undiscounted) rate — so **monitor the BGB
   balance** and top up. (A depleted BGB balance is the usual reason the
   discount "stops working".)

**Verify:** after enabling, a filled order's fee line in the trade history shows
the deduction in BGB at the reduced rate. No bot restart needed — this is
entirely exchange-side.

> This lever is intentionally **not** automated in code: it requires holding a
> token and toggling an account setting, both of which are operator decisions
> with their own (small) exposure to BGB price. The bot never buys BGB for you.

---

## 2. Maker-only entries (shipped, ON) — already in force

Entries are placed as **post-only maker limit orders** (`LIMIT_POST_ONLY=1`),
so the entry leg pays the 0.02% maker rate instead of 0.06% taker. This is the
default and already active — no action needed. It is the reason entry fees are
already at the floor.

---

## 3. Fee-aware entry gate (shipped, gated OFF) — `FEE_AWARE_ENTRY_GATE_ENABLED`

Rejects an entry unless the reward to its take-profit clears the round-trip cost
(2×fee + 2×slippage) by `FEE_AWARE_MIN_MULTIPLE` (default 2.0×). The plain
min-RR check is a *ratio* and can pass a tight-stop scalp whose *absolute* TP
distance barely exceeds fees; this gate kills those fee-losers directly.

**A/B (21-symbol `corr_dense_1h`, `--honest`):** OFF and ON produced an
**identical** net result (+1.40%, $11.65 commission, 15 trades, PF 1.87) — but
ON logged **14,938 pre-fill rejections**. On this swing-heavy benchmark the
surviving trades were the same profitable set, so net was unchanged. **The
gate's value is conditional: it bites on scalp-heavy / choppy conditions** where
small-R fillers actually enter and bleed fees. Enable it when the universe skews
short-target:

```dotenv
FEE_AWARE_ENTRY_GATE_ENABLED=1
FEE_AWARE_MIN_MULTIPLE=2.0     # TP reward must be ≥ 2× round-trip cost
FEE_AWARE_SLIPPAGE_PCT=0.05    # per-side slippage estimate used in the cost
```

Skips manual trades (the operator chose the levels).

---

## 4. Re-entry cooldown (shipped, gated OFF) — `REENTRY_COOLDOWN_ENABLED`

The existing cooldown-after-loss (risk check #13) only fires after a **loss** —
it does nothing to stop rapid re-entry churn on the **same symbol** after a win
or flat close, and each such round trip pays 2×(fee+slip). This gate throttles a
fresh entry on a symbol within `REENTRY_COOLDOWN_SECONDS` of the last **real
fill** on that symbol, measured on the same simulated/live clock as the loss
cooldown. Skips manual trades.

Implementation: the stamp happens at the **actual fill** (`note_symbol_entry`,
called from the backtest `_execute_fill` and the live post-execute success
branch) — **not** at evaluation, because `evaluate()` runs twice per trade
(scan + confirm-recheck) and stamping there would self-trip the cooldown. The
check in `_evaluate_locked` is read-only, so `/whynot` reports it as a reason.
In-memory only (a restart clears it — fail-open for a short-horizon guard).

**A/B (21-symbol `corr_dense_1h`, `--honest`, 16-month span, 4h cooldown):**
**byte-identical** to OFF (+1.40%, 15 trades, $11.65 commission, PF 1.87,
max-DD 1.49%) — the cooldown was **inert** because on this swing-heavy cadence
(15 trades over 16 months across 21 symbols) no same-symbol re-entry occurs
within 4h, so there is nothing to throttle. Like the fee-aware gate (§3), its
value is conditional: it bites on **scalp-heavy / high-churn** universes where
the bot re-enters the same symbol in quick succession. The mechanism itself is
covered by `tests/test_reentry_cooldown.py` (blocks immediate re-entry, releases
after the window, stamps only real fills, skips manual). Opt-in, not a default.

```dotenv
REENTRY_COOLDOWN_ENABLED=1
REENTRY_COOLDOWN_SECONDS=14400   # e.g. 4h; 0 or flag OFF = no-op
```

---

## 5. Drift → market fallback (LIVE-only behaviour) — `LIMIT_DRIFT_MARKET_FALLBACK`

When a resting post-only entry limit drifts and price runs **past** it in the
trade's favour **with strong aligned momentum** (ADX ≥ `LIMIT_DRIFT_MARKET_MIN_ADX`,
DI aligned), the executor **cancels the limit and places a MARKET order** to
catch the breakout. That market fill pays the **taker** 0.06% instead of maker
0.02%. Default **ON**.

**Why we do NOT "reprice-not-cross" here (investigated, rejected):** the fallback
*only* fires when price has already moved past the limit. A post-only reprice
cannot fill a runaway that has already left the limit behind — so repricing would
simply **miss the only case this path exists for**. It is net-negative for its
own use case, not a fee win.

**The genuine fee-safe choice** (skip those taker breakouts entirely) already
exists as a flag — no taker fee, but you forgo the momentum breakouts:

```dotenv
LIMIT_DRIFT_MARKET_FALLBACK=0    # skip drifted-past-limit breakout entries (live-only)
```

> This is a **live-executor-only** behaviour. The backtest does **not** model the
> drift/market fallback, so a backtest A/B of this flag is a no-op and would be
> misleading — validate it in live if you want to trade it off. We left the
> default ON: flipping a live money-path default deserves live observation, not a
> backtest that can't see it.

---

## 6. Maker take-profits (backtest fee-accounting only) — `MAKER_TAKE_PROFIT_ENABLED`

The partial-TP ladder closes via taker market orders by design (guaranteed
exit). This flag, in the **backtest only**, charges the TP-exit leg at the maker
rate to *quantify* the potential saving (~6% of total commission on the
benchmark). It does **not** change live order placement — routing TPs as resting
reduce-only maker limits is a separate, carefully-scoped executor change that is
not worth the fill-risk for the modelled saving. Default OFF; byte-identical when
OFF.

---

## Bottom line / recommended order

1. **Enable BGB fee deduction** on the account (§1) — biggest win, zero code, do
   this first.
2. Leave maker entries ON (§2) — already the default.
3. Enable the **fee-aware gate** (§3) and/or the **re-entry cooldown** (§4) *if*
   you run scalp-heavy / choppy universes where churn is the problem; both are
   A/B-neutral on swing-heavy conditions, so they're opt-in, not defaults.
4. Consider `LIMIT_DRIFT_MARKET_FALLBACK=0` (§5) only after observing how often
   the taker breakout path actually fires live.
