# Round 7 — Regime / Correlation Stand-Down Gate (Design)

**Status:** design only — no code, non-committing. Nothing here ships until each
phase clears its own A/B on the frozen benchmarks.

**Owner:** Round 7 (structural). Prior rounds tuned *parameters* on existing
gates; this round changes the *shape* of the entry gate.

---

## 1. Problem statement

Two distinct-but-related gaps, both about **correlated entries firing together**:

1. **Same-bar correlated-fill blind spot (backtest + live).** The correlation
   cap (`max_correlation_per_group = 2`) is enforced by counting *already-open*
   positions only. A cluster of correlated symbols that all signal on the same
   bar each see zero open group members at evaluation time, so they **all pass**
   and all fill on the next bar — silently bypassing the cap exactly when
   correlation risk is highest (a synchronized, market-wide move).

2. **The market-wide risk-off signal is live-only and soft.** A cross-asset
   regime/correlation signal already exists, but (a) it is a *soft* confidence/
   size nudge, not a hard stand-down, and (b) it is **not present in the
   backtester at all** — so a stand-down gate built on it cannot be A/B-tested
   on our frozen benchmarks. Closing that parity gap is the real structural
   work of this round.

---

## 2. Mechanism — evidenced, not asserted

### 2.1 Same-bar blindness

`bot/backtest/portfolio_engine.py:133-158` — the merged timeline walks each
timestamp `ts` and loops symbols in dict order. For each symbol the per-bar
pipeline runs independently, and a new entry is **deferred to the next bar's
open** via `eng._pending_entry` (line 149-152). The risk engine is **shared**
across all symbols (`self._risk`, line 65-71), but it is consulted one symbol at
a time.

`bot/risk/risk_engine.py:2135-2157` — `_check_correlation` counts group members
from `self._portfolio.open_positions` **only**:

```python
open_groups = [self._correlation_group(pos.asset)
               for pos in self._portfolio.open_positions]
group_count = open_groups.count(new_group)
if group_count >= max_per_group:  # max_correlation_per_group = 2
    return "CORRELATION: already N positions ..."
```

So on a synchronized bar: symbol A is evaluated → 0 open group members →
approved → pending (fills next bar). Symbol B evaluated → A is **still not
open** → 0 members → approved. Symbols C, D … likewise. The `>= 2` cap never
binds because nothing in the group is *open yet*. Confirmed against fold-3 of
the earlier walk-forward reconstruction.

The rolling-correlation "V2" sub-check (`risk_engine.py:2159-2191`) has the same
limitation: it iterates `self._portfolio._positions` — open only.

### 2.2 Parity gap

- Live path **has** a cross-asset signal:
  `bot/core/cross_asset.py:_classify_regime` produces
  `risk_off / risk_on / rotation / normal` from BTC dominance + `alt_correlation`
  (mean pairwise |corr| of alts vs BTC), and `_compute_adjustments` turns
  `risk_off` into `conf −0.05, size ×0.7` (soft). It is wired into the live
  engine at `bot/core/engine.py:2382` (`get_symbol_adjustment`).
- Backtest path has **none**: `git grep -l cross_asset bot/backtest/**` → empty.
  The backtester has no notion of market-wide correlation or regime beyond the
  per-symbol `analyzer._current_regimes` fed to `risk.set_regime`
  (`bot/backtest/engine.py:393-395`).

**Consequence:** a stand-down gate keyed on market-wide correlation cannot be
measured on the frozen benchmarks today. It would be unfalsifiable. That is why
this is structural, not a parameter sweep.

---

## 3. Design goals

1. **Forward-looking counting.** The correlation cap must count *intended*
   same-bar entries (pending, not-yet-filled), not just open positions.
2. **Hard, regime-aware stand-down.** When market-wide correlation spikes and
   the regime is risk-off, gate *new* entries. Never touch existing positions —
   no forced exits.
3. **Path parity.** The gate lives in the shared risk engine and is fed
   *identical* regime/correlation state by both the live engine and the
   portfolio backtester, so it is A/B-testable on frozen snapshots.
4. **Gated OFF by default, env-tunable, fail-open.** Consistent with every prior
   round; an error in the new path must degrade to today's behaviour, never
   reject spuriously.
5. **Existing checks preserved.** Correlation already binds for manual trades
   (`risk_engine.py:1111-1113`); keep that. The mapped-group vs unmapped-alt
   bucket distinction (`_UNMAPPED_GROUP`) stays.

---

## 4. Proposed architecture

### Phase 1 — Pending-intent ledger (fixes §2.1)

Add a short-lived "intent" ledger to the risk engine keyed by
`(correlation_group, direction)`:

- **Register** an intent when `evaluate()` returns APPROVED (the caller commits
  to filling next bar).
- **Drain** on fill, cancel, or expiry (a bar-count / wall-clock TTL so a
  dropped intent can't latch the gate — same fail-safe pattern as the
  loss-streak probe).
- `_check_correlation` counts `open positions + live intents` in the group.

Path wiring:
- **Backtest:** `portfolio_engine` already defers via `_pending_entry`. Register
  the intent at approval; clear it in `eng._execute_fill`
  (`portfolio_engine.py:152`).
- **Live:** register at confirm-trade time; clear on fill/cancel via the
  existing executor callbacks.

This is the cleanest structural fix, low-risk and mechanical. **A/B it first and
alone** so its effect is isolated.

### Phase 2 — In-backtest correlation/regime signal + hard stand-down (fixes §2.2)

- New setter on the risk engine, mirroring `set_regime`:
  `set_market_state(alt_correlation: float, regime: str, vol_state: str)`.
- New hard gate in `_evaluate_locked`, gated by a flag (default OFF): if
  `alt_correlation > standdown_corr_threshold` **and** regime is risk-off (or
  volatility is EXPANSION), then tighten `max_correlation_per_group` toward 1
  (or reject new entries beyond an already-open cluster). Existing positions are
  untouched.
- **Backtester must compute the signal.** This is the main build item: the
  portfolio engine already holds every symbol's bar panel on the merged
  timeline, so it can compute a rolling pairwise-return correlation across the
  open + candidate symbols and a coarse regime, then call `set_market_state`
  each scan — the backtest analogue of `CrossAssetTracker`. Reuse the same
  correlation math as `cross_asset.py:160-167` so live and backtest agree.

---

## 5. Validation plan

- **Datasets:** `majors_1h` v2 **and** `alts_1h` v2 frozen snapshots, **portfolio
  mode** (multi-symbol — single-symbol runs cannot exercise correlation).
- **A/B structure:** Phase 1 and Phase 2 measured independently; each shipped as
  its own PR, gated OFF until its A/B clears, one PR at a time.
- **Metrics:** PF, Sharpe / Sortino / Calmar, max drawdown, trade count, and —
  specifically — drawdown concentrated in synchronized-move windows.
- **Honest expectation.** This is a **tail-risk / drawdown reducer**, not a PF
  booster. Blocking correlated stacking removes both some winners and some
  losers; headline PF may barely move while max DD and Calmar improve. The
  success criterion is therefore **DD / Calmar improvement at ≤ a small PF
  cost, OOS-validated** — not "PF went up."
- **Overfit guard.** Single threshold, default OFF, validated on *both*
  snapshots and via walk-forward before any default flip. Consistent with the
  Round-5/6 OOS discipline that has already killed several IS-only "wins."

---

## 6. Rollout

| Phase | Change | Risk | A/B gate |
|-------|--------|------|----------|
| 1 | Pending-intent ledger (forward-looking corr cap) | Low, mechanical | Isolate on both v2 snapshots |
| 2 | In-backtest corr/regime signal + hard stand-down | Higher (new signal + parity) | Separate A/B; DD/Calmar criterion |

Each phase is a separate gated PR. Nothing flips to default-ON without an
OOS-clean A/B on both benchmarks.

---

## 7. Risks & open questions

1. **Over-suppression.** Forward-looking counting could block legitimately
   diversified same-bar entries. Mitigation: the mapped-group cap and the looser
   unmapped-alt bucket already differ; keep that split and calibrate separately.
2. **Threshold calibration on thin history.** The stand-down correlation
   threshold is one number fit on limited benchmark history — the classic
   overfit surface. Default OFF + dual-snapshot + walk-forward is the guard.
3. **Live↔backtest intent-lifecycle divergence.** Fill timing differs between
   paths (next-bar-open vs real fills). The existing live↔backtest parity
   harness (#205) must be extended to cover intent register/drain, or the two
   paths will silently disagree on when the cap binds.
4. **Interaction with existing soft cross-asset nudge (live).** Phase 2 adds a
   *hard* gate on top of the live soft adjustment; confirm they compose sanely
   (soft nudge inside the gate, not double-counted).

---

## 8. Why not just tighten `max_correlation_per_group` to 1?

Because it wouldn't bind: §2.1 shows the cap is bypassed on synchronized bars
regardless of its value — a correlated cluster all evaluate against zero open
members. Lowering the number changes nothing until the counting is
forward-looking. Phase 1 is the prerequisite that gives *any* correlation cap
real authority on same-bar clusters.

---

## 9. Empirical results & disposition (what actually happened)

Phase 1 shipped (#310, forward-looking cap, gated OFF). A/B on the majors/alts
v2 snapshots was thin and mixed — and, tellingly, **every rejection logged as
`UNMAPPED_ALT`**, even for mapped symbols. That pointed to the real issue.

### 9.1 The regime stand-down was chasing a bug, not a missing feature

`_correlation_group` never stripped the ccxt perp settle suffix. The bot trades
USDT-perps (`SOL/USDT:USDT`); the `_CORRELATION_GROUPS` keys are spot-style
(`SOL/USDT`). So **every futures symbol missed the map and pooled into one
`_UNMAPPED_GROUP` bucket** — the ALT_L1/MEME/DEFI/BTC/ETH taxonomy has been dead
on the live path, with the pooled `max_unmapped_correlated` (default 3) as the
only correlation limit actually in force. This also explains why Phase 1 kept
measuring as a near no-op: it was making a pooled bucket forward-looking, not
the intended per-group caps.

### 9.2 Fixing the mapping *loosens* aggregate risk (counterintuitive)

Built a dense, group-concentrated benchmark (`corr_dense_1h`: 11 ALT_L1 + 5 MEME
+ 5 DEFI, ~500 days) so the caps actually bind, and A/B'd pooled (bug) vs
per-group (fix):

| dense benchmark | pooled (current) | per-group (fixed) |
|---|---|---|
| Return | +1.40% | +1.53% |
| Profit factor | 1.87 | 1.83 |
| Win rate | 40% | 57% |
| **Max drawdown** | **1.49%** | **3.11%** |
| **Calmar** | **0.94** | **0.49** |
| Sortino | 0.62 | 0.36 |

The pooling bug **accidentally acted as a global correlated-exposure cap** (≤3
correlated positions total). Correct per-group caps (2 ALT_L1 + 2 MEME + 2 DEFI
+ 2 unmapped = up to 8) *loosen* aggregate exposure, roughly **doubling max
drawdown**. So the naïve correctness fix is net-negative for risk.

### 9.3 Disposition

- **Perp-mapping fix: gated OFF** (`correlation_perp_group_mapping_enabled`).
  Default preserves the tighter pooled behaviour on the live account.
- **Forward-looking cap (Phase 1): stays OFF.** Thin/mixed; no evidence to flip.
- **Regime/correlation hard stand-down (original Phase 2): not built.** The
  evidence says the lever isn't a regime gate — it's **aggregate correlated
  exposure**, which the pooling already bounds.
- **Real next step (revised Phase 2):** correct per-group mapping **paired with a
  global concurrent-correlated-position cap**, so exposure is bounded *and*
  correctly attributed. A/B that combination on `corr_dense_1h` before enabling.

All caveats stand: <15 trades per run, single period — directional, not
conclusive. The dense benchmark is committed so this is reproducible and the
revised Phase 2 can be measured rather than assumed.
