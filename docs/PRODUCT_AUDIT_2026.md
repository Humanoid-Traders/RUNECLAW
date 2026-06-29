# RUNECLAW — Final Product Audit & Roadmap (2026-06)

A full-system audit of signal generation, analysis/LLM, technical/confluence
patterns, strategy/regime, risk/execution, and learning/backtest. Findings were
produced by per-domain review and the high-impact ones verified against code.

## Overall verdict

A genuinely deep, well-engineered system — 35+ confluence voters with correct
indicator math, a fail-closed risk engine, honest backtest↔live parity, an
isotonic confidence calibrator, and a hardened multi-user live stack. **The
dominant theme is that much of the best machinery is built but not switched on.**
The highest-ROI work is *activating and correcting what already exists*, not new
features.

## Verified high-impact findings

| # | Finding | Status | Where |
|---|---|---|---|
| 1 | Market regime is computed but never wired to the risk engine — `set_regime()` is never called in `bot/`; `_current_regime` is permanently `UNKNOWN`, so every per-regime size multiplier is 1.0×. | verified | `risk_engine.py:1394` (no callers) |
| 2 | Strategy router runs each cycle then its output is discarded (`pass`). | verified | engine scan path |
| 3 | Repaint / intrabar bias — the in-progress candle is not dropped before TA; indicators & patterns read `closes[-1]`. | verified | `engine.py:1381`, analyzer/patterns |
| 4 | De-correlation (`family_cap`), voter-weight learning, and calibration all default OFF. | verified | `config.py:783/591/580` |
| 5 | Two divergent scan systems (`/scan` hardcoded 67-symbol + own scoring, with a volume-calc bug, vs the autonomous full-market scanner). | verified | `scan_skill.py:376/411` |
| 6 | Live WS prices drive SL/TP monitoring with no per-tick staleness guard. | flagged (cited) | `engine.py:2857`, `ws_feed.py:199` |
| 7 | Partial-fill SL sizing + no SL/TP price-side assertion at placement. | flagged (cited) | `live_executor.py:2254/2956` |

## Confirmed strengths (do not regress)

- Risk engine: fail-closed contract, fixed-fractional sizing, leverage-as-margin
  unit handling, slippage/drift guards, the per-trade notional cap as final
  authority.
- Backtest: shared Analyzer/RiskEngine/Portfolio, gap-aware stop fills, embargo,
  causal `as_of` session sizing — no lookahead.
- LLM safety: the model never sets SL/TP geometry; parse-failures block rather
  than defaulting to LONG.
- Multi-user live (this session): per-user isolation, own-equity sizing,
  kill-switch, caps, observability.

## Roadmap (prioritized)

### P0 — Activate what's already built (highest ROI)
1. **Bridge analyzer regime → `risk.set_regime()` before `evaluate()`** [S] —
   unlocks the dead regime-multiplier stack. *Changes live sizing → gated +
   backtest-validated.* **(In progress — first build.)**
2. ~~**Wire strategy-router outputs**~~ — **WITHDRAWN on verification.** The
   analyzer already owns strategy selection: `_classify_strategy_type` (a richer,
   multi-factor classifier) sets `idea.strategy_type`, which already feeds
   per-strategy risk sizing. The engine's `strategy_router.select_strategy` is a
   *cruder, redundant* regime→map used only by a dead `pass` block and a broken
   `/strategy` admin display. Wiring it would **downgrade** strategy selection.
   Action: optional cleanup (remove the dead block, fix the `/strategy` display) —
   *not* activation.
3. **Enable + validate calibration, family-cap, voter-weight learning** [M] —
   backtest each, then default-on with a readiness report.

### P1 — Signal-quality correctness
4. **Drop the in-progress candle before all TA** [S] — removes systemic repaint
   and aligns live with the (bar-closed) backtest. **(Done — gated
   `DROP_UNCLOSED_CANDLE_ENABLED`.)**
5. **Unify the two scan paths** [M] — `/scan` calls the real scanner + analyzer.
6. **Use the full framework prompt on all LLM tiers** [S] — non-admin scans
   currently get a degraded one-liner.

### P2 — Live-money safety hardening
7. **WS tick-staleness guard + reconnect watchdog** [S] — biggest live-exit risk.
   **(Staleness guard done — `WS_MAX_TICK_AGE_SEC`; stale WS ticks excluded from
   SL/TP monitoring → REST fallback. Dead-socket reconnect is handled by the
   websockets ping/pong timeout.)**
8. **SL/TP side-sanity assertion + post-placement "stop is open" verify** [S].
   **(Side-sanity done — `_place_sl_tp`/`_place_sl_tp_v3` refuse an inverted pair
   → unprotected-position escalation handles it. Post-placement re-verify is
   already covered by the periodic SL/TP self-heal.)**
9. **Reconcile SL/TP qty to exchange-verified fill size; unify
   unprotected→flatten across all entry paths** [M].

## Notes
- P0.1 (regime bridge) and P0.3 (enabling learners) change real-money behaviour
  and must be shipped **gated + backtest-validated**, never flipped on blindly —
  the same default-OFF pattern used throughout the codebase.
- The analyzer `Regime` values (`TREND_UP/TREND_DOWN/EXPANSION/RANGE/CHOP/UNKNOWN`)
  are already a subset of the risk engine's `_REGIME_MULTIPLIERS` keys, so the
  bridge needs no translation layer — only wiring + a gate.
