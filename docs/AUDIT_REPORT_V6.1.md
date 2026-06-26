# RUNECLAW Audit Report V6.1 — Follow-up Sweep

**Date:** 2026-06-26
**Branch:** `claude/complete-audit-test-report-3hw8kt`
**Predecessor:** `docs/AUDIT_REPORT_V6.md` (same session). V6 covered risk / security /
execution / learning+compliance. This V6.1 sweep covers the subsystems V6 did
**not** reach — the signal-generation core, the backtest engine, NLP/intent
routing, the MCP server, config parsing, and the LLM provider — and runs the deep
backtest harness.
**Method:** Three independent subsystem passes (backtest / analyzer / NLP-MCP-config-LLM),
each finding re-verified at exact `file:line`, plus a 500-run deep backtest
(20 symbols × 5 regimes × 5 seeds).

---

## Summary

| Severity | Found | Fixed (this PR) | Documented |
|----------|-------|-----------------|------------|
| HIGH | 3 | 1 | 2 |
| MEDIUM | 8 | 5 | 3 |
| LOW | 8 | 2 | 6 |
| **Total** | **19** | **8** | **11** |

Two of the MEDIUM fixes (BT-CRASH-1/2) are crashes the **deep backtest run itself
surfaced** — all 6 of its hard errors — and are now fixed (see "Deep backtest run").

Two V6 open questions are now **resolved**:
- The *"intent router returns `''` for `help`"* observation was a **genuine product
  bug**, not a stale test — now **fixed** (NLP-1).
- The *"regime detection returns `UNKNOWN` where a test expected `CHOPPY`"*
  observation is **not a bug in the analyzer core**: `_detect_regime` never emits
  `UNKNOWN` and is consistent with its test. The `UNKNOWN`/`CHOPPY` enum mismatch
  lives in the **separate** `bot/skills/quant_skill.py` regime system (different
  enum `MarketRegime.CHOPPY` vs the analyzer's `Regime.CHOP`). No analyzer change
  needed; flagged for the quant-skill module owner.

---

## Fixed in this PR

### AN-1 — `analyzer.py` used an undefined `logger` (HIGH, latent crash)
**`bot/core/analyzer.py`** — the module imported `audit, system_log, trade_log,
scan_log` but **no `logger`**, yet seven `except` handlers called `logger.debug/
warning(...)` (LLM-calibration writer at :659, order-flow :711, funding :731,
volume-profile :1736, sentiment :1905, supply-demand :1937). The LLM-calibration
block runs on **every** `analyze()` call; if its inner `try` ever threw
(disk/permission/serialization), the handler itself raised
`NameError: name 'logger' is not defined`, aborting trade-idea generation for that
symbol. **Fix:** added `import logging` + `logger = logging.getLogger(__name__)`.
Verified the module imports and `logger` resolves.

### NLP-1 — bare `help` / `commands` misrouted to social chat (MEDIUM)
**`bot/nlp/intent_router.py:386`** — the help rule required `show help` /
`list commands`; the bare tokens `help`, `help me`, `commands`, `menu` matched no
rule and weren't in `trading_words`, so `_is_social_message` returned them as
social chat and `classify_rules` never reached the help skill. The single most
common discovery command silently failed. **Fix:** broadened the help rule with a
**tightly anchored** alternative `^\s*(help|commands?|menu)(\s+me)?\s*$` so the
bare forms route to `help` while longer queries ("help me set a stop on BTC")
are unaffected. Verified across 8 inputs; `tests/test_intent_and_monitor.py::
test_help` now passes.

### LLM-1 — OpenAI-compatible path could return `None` content (MEDIUM)
**`bot/llm/provider.py:521`** — the Anthropic branch returns `raw_text or ""`, but
the OpenAI-compat branch returned `response.choices[0].message.content` directly,
which the SDK can set to `None` (content-filter finish, tool-call-only, empty
completion). Callers that `.strip()` the result (e.g. the intent LLM classifier)
hit `AttributeError`. **Fix:** normalize to `... or ""`, mirroring the Anthropic
branch.

### CFG-1 — non-finite env floats silently disabled risk guards (MEDIUM)
**`bot/config.py:107`** — `_env_float` caught only `ValueError`; `float("nan")` /
`float("inf")` parse without error, and a non-finite risk limit silently disables
its guard (`x > nan` and `x < nan` are both `False`). **Fix:** added an
`math.isfinite` check that logs and falls back to the safe default for any
non-finite value, protecting **every** float field (including the un-bounded
ones — see CFG-2).

### MCP-1 — bad numeric argument bypassed the structured error envelope (LOW)
**`bot/mcp/server.py:347`** — argument coercion (`int()`/`float()`) ran *before*
the execute `try/except`, so a call like `runeclaw_backtest {"bars":"abc"}` raised
an unhandled `ValueError` out of `call_tool`, bypassing the `MCPResponse` error
envelope and the secret-redaction path. **Fix:** wrapped coercion in
`try/except (ValueError, TypeError)` returning a structured error.

### BT-CRASH-1 — `generate_synthetic` divides by zero on underflowed price (MEDIUM)
**`bot/backtest/data_loader.py:148`** — the mean-reversion overlay computes
`(price - start_price) / start_price`. On a very-low-priced asset (PEPE at
1.3e-5) under a sustained downtrend, the −10%/bar cap geometrically decays the
price until it **underflows to 0.0**; that 0.0 becomes the next segment's
`start_price`, so the division raises `ZeroDivisionError` and aborts the run. This
caused **5 of the 6** deep-backtest errors (all PEPE "Crash Recovery" seeds).
**Fix:** skip the pull-back when `start_price` is zero (no reference band exists).
Verified PEPE Crash Recovery now completes (0 trades, degenerate candles correctly
rejected by the analyzer).

### BT-CRASH-2 — analyzer emits an invalid `TradeIdea` on collapsed SL/TP (MEDIUM)
**`bot/core/analyzer.py:943`** — for a low-priced/high-vol asset the ATR-derived
stop/target distance can fall below tick precision, so rounding collapses
`stop_loss`/`take_profit` onto `entry`. The `TradeIdea` directional-sanity
validator then raises (`stop_loss must be below entry`), and the exception
propagates out of `analyze()` → aborts the whole run. This caused the 6th
deep-backtest error (DOGE "High Volatility"). **Fix:** validate the rounded
levels before constructing the `TradeIdea` and skip the degenerate idea (no-trade
is the safe outcome) instead of raising. Verified DOGE High-Vol now completes
(3 trades; degenerate ideas skipped, valid ones proceed). Regression tests in
`tests/test_backtest_edge_cases.py`.

### BT-DISP — deep-backtest printed win rate 100× too low (LOW, reporting)
**`run_deep_backtest.py:193,247,268,295`** — `win_rate` is a fraction in `[0,1]`
(0.8 = 80%) but was printed with a `%` suffix, so the console report showed
`WR=0.8%` instead of `80.0%` (per-run, per-symbol, per-regime, and global lines).
The JSON results were always correct (fraction). **Fix:** multiply by 100 at the
four display sites only; stored fields left unchanged so downstream consumers
(`is_validated`, strategy eval) still receive the fraction.

---

## Documented — backtest validity (need plumbing changes; not fixed here)

### BT-H1 — configured commission is silently ignored (HIGH)
**`bot/backtest/engine.py:227,478` + `bot/risk/portfolio.py:204` + `bot/config.py:161`** —
`BacktestConfig.commission_pct` (default 0.1), the `--commission` CLI flag, and
`run_deep_backtest.py`'s `commission_pct=0.1` are **never applied to PnL**. The
only commission charged is `CONFIG.risk.commission_pct` (0.06%) inside
`PortfolioTracker`, while the result header *reports* 0.1%. Net: every published
backtest understates costs ~40% vs what it claims, and the cost knob is
non-functional (cost-sensitivity analysis is invalid). **Fix:** thread
`config.commission_pct` into the portfolio/risk engine (or recompute commission in
the engine), and stop reporting a `commission_pct` that isn't the one charged.

### BT-H2 — wall-clock "session" adjustments leak into backtest decisions (HIGH)
**`bot/core/analyzer.py:763` + `bot/risk/risk_engine.py:529` → `session_aware.py`** —
both confidence (±0.02–0.03) and position sizing (×0.75–1.10) call
`get_current_session()`, which defaults to `datetime.now(UTC)` — the **real
wall-clock hour the backtest is launched**, not the simulated `bar.timestamp`.
This (a) breaks reproducibility (same seed → different trades depending on the
hour you run it) and (b) is non-causal (adjusts by a factor unrelated to the
simulated market). **Fix:** thread `bar.timestamp` through analysis/sizing so
`get_current_session(now=bar.timestamp)` is used in backtest (the function already
accepts a `now` arg).

### BT-L — metric conventions (LOW)
- **Calmar not annualized** (`engine.py:459`): `total_return / max_dd_pct` should
  annualize the numerator for comparability across run lengths.
- **Sharpe/Sortino use population stddev** (`engine.py:553-585`, `ddof=0`): slight
  upward bias; use `ddof=1` (guard `len < 2`).
- **Breakeven trades counted as losses** (`engine.py:411`): `net_pnl <= 0` treats
  exact-0 as a loss, depressing win rate / inflating max-consecutive-losses
  (conservative bias; the risk engine treats breakeven as neutral).

**Verified clean in backtest:** no same-bar SL/TP look-ahead (stops checked on
`i+1`), SHORT PnL signs, slippage applied in the adverse direction both sides,
drawdown peak tracking, timestamp-derived annualization, per-run state isolation
(fresh analyzer/portfolio/risk with isolated temp state file), and reasonable
synthetic-data realism (GARCH vol clustering, Student-t fat tails, OHLC
consistency). Cross-run seed determinism holds **except** for the BT-H2 wall-clock
leak.

---

## Documented — config / LLM / analyzer / MCP / NLP (not fixed here)

| ID | Sev | Location | Issue |
|----|-----|----------|-------|
| **CFG-2** | Med | `config.py:131,138,147-178` | Many risk limits use raw `_env_float` (no clamp): `max_drawdown_pct`, `min_risk_reward`, `max_portfolio_exposure_pct`, `max_symbol_exposure_pct`, `max_margin_risk_pct`, `max_portfolio_var_pct`, `volatility_guard_atr_pct`. A typo'd `MAX_PORTFOLIO_EXPOSURE_PCT=500` loads verbatim and widens risk. Route every risk-limit field through `_env_float_bounded` with sane ranges. (CFG-1 already blocks the inf/nan case.) |
| **LLM-2** | Med | `provider.py:267-303` | Non-admin tier override returns a **keyless** `LLMConfig` when `LLM_TIER_*_PROVIDER` is set but no key is discoverable, instead of falling back to the primary config — silently runs that tier with no LLM. Guard the override return with `if tier_key:` (the default-routing branch already does). Fails safe (→ rules), but surprising. |
| **LLM-3** | Low | `provider.py:494` | Adaptive-thinking gated on substring `"opus"` in model name — brittle; effectively dead under current tier routing. |
| **AN-2** | Low | `divergence.py:164,192,220,248` | Divergence strength `Δ/(abs(i1)+1e-10)` saturates to the cap when the pivot value is near zero (OBV cumulative, MACD-hist zero-crossings), over-stating OBV/MACD divergence confidence. Normalize by the indicator's recent range instead. |
| **AN-3** | Low | `analyzer.py:1063` | `_classify_strategy_type` reads `indicators["close"]` which is never populated → always 0, relies on the `signal.price` fallback; the ATR factor silently disables if `signal is None`. Store `close` or use `signal.price` directly. |
| **MCP-2** | Low | `server.py:185,440` | `runeclaw_backtest` documents "max 5000" bars but never enforces it; `runeclaw_fullscan` treats any non-`quick` mode as a full 67-symbol scan. Authenticated resource-amplification hardening: clamp `bars`, validate `mode`. |
| **NLP-2** | Low | `intent_router.py:529` | `whynot` is an accepted LLM intent with no rule/registered skill; `trade_journal` is listed twice in `valid_skills`. Confirm/register `whynot` or drop it. |

**Verified clean:** MCP auth is fail-closed (refuses to start without
`MCP_AUTH_TOKEN`; `hmac.compare_digest` on every call), `runeclaw_execute` is not
exposed, and `_shield_evaluate` is read-only w.r.t. risk state (counters mutate
only in `record_trade_result`, not `evaluate`) — the advisory-only invariant
holds. Config safety-switch parsing (`_env_bool`, `is_live()`) is fail-closed.
Indicator math (RSI/MACD/ATR/EMA/Bollinger/VWAP/ADX/Stochastic), confluence
scoring, confidence clamping `[0,1]`, and LONG/SHORT direction labeling are
correct with proper zero/empty-series guards and **no look-ahead bias**.

---

## Deep backtest run

The 500-run deep backtest harness (`run_deep_backtest.py`) was executed on this
branch (20 symbols × 5 regimes × 5 seeds, 1500 1H bars each, ~28 min). It
exercises the full analyze → risk → portfolio pipeline per bar across all regimes,
and surfaced two crash bugs (BT-CRASH-1/2 above), now fixed.

**Global summary (this run):**

| Metric | Value |
|--------|-------|
| Valid runs | 494 / 500 (6 errors — all now fixed by BT-CRASH-1/2) |
| Total trades | 3,239 |
| Avg return | +3.86% (best +32.93%, worst −1.46%) |
| Avg max drawdown | 1.12% (worst 2.87%) |
| Crashed runs (DD>20%) | 0 |
| Avg win rate | ~70% |
| Avg Sharpe / Sortino | +2.34 / +2.86 |
| Avg profit factor | 29.35 |

**Read these numbers with strong caveats.** The avg profit factor (~29) and Sharpe
(~2.3) are implausibly high for a real strategy and are artifacts of: (a) synthetic
data the rule engine can exploit, (b) BT-H1 — commission understated ~40% (the
config knob is non-functional), and (c) BT-H2 — wall-clock session leakage makes
the run non-reproducible. The results validate that the **pipeline runs end-to-end
without crashing** and that the **risk gates fire** (cooldown, loss-streak, regime
filter, VaR, confidence), but they are **not** evidence of live edge. With the two
crash fixes, a re-run should produce 500/500 valid runs.

---

*Fixes in this PR are limited to the six contained, verified defects above. The 11
documented items are recommended for focused follow-ups: BT-H1/BT-H2 and CFG-2 are
the highest-value (they affect the validity of published backtest numbers and the
robustness of operator-supplied risk limits).*
