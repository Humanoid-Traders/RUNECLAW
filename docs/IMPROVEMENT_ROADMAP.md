# RUNECLAW — Improvement & Upgrade Roadmap

**Date:** 2026-06-27
**Method:** Four parallel code-grounded surveys (strategy/alpha/backtest, risk/execution, testing/observability/CI, architecture/quality/features), each finding cited to `file:line`. The highest-impact concrete claims were re-verified by hand. This is a plan — no code was changed.

**Baseline:** `main` after the V7 audit arc (PRs #20–#24): CI wired, 15 audit findings fixed, F-3 units reconciled.

The recurring theme across all four surveys: **several safety/quality features are configured or advertised but not actually wired up** — a "looks done but isn't" class that gives false confidence. Those lead the list.

---

## P0 — Correctness/safety bugs (small effort, high impact) — do first

| # | Item | Evidence | Effort |
|---|------|----------|--------|
| P0-1 | **Alert dedup suppresses the FIRST alert for ~5 min after startup.** The "flaky" `test_dedup_prevents_repeat` is masking a real bug: `last_sent = _dedup_cache.get(key, 0)` compared against `time.monotonic()` (arbitrary small epoch). `monotonic() - 0 < 300` is true on a fresh process, so circuit-breaker/SL-proximity alerts are dropped exactly when a freshly-deployed bot is most fragile. | `bot/core/proactive_monitor.py:478-480` (`DEDUP_COOLDOWN=300` `:77`) | S |
| P0-2 | **Slippage guard is a no-op despite defaulting ON.** `slippage_guard_enabled=True` / `max_slippage_edge_ratio=0.30` are referenced *only* in config — never read. Slippage is recorded post-fill but never aborts. A market order into a thin book can fill far from the approved entry, invalidating the SL distance/R:R the risk engine signed off on. | `bot/config.py:528-529` (no other refs); fill path `bot/core/live_executor.py:2351-2363` | S–M |
| P0-3 | **Order splitting logs "SPLITTING" but executes a single market fill.** The tranche loop is a stub ("future refinement"), so a large order takes full market impact while the audit log claims it was sliced. Worse than absent. | `bot/core/live_executor.py:2009-2026` | S to hard-block; M to implement |
| P0-4 | **`.env.example` diverges from code on RISK defaults.** `MAX_POSITION_PCT` 2.0 (docs) vs 13.0 (code), `MIN_CONFIDENCE` 0.60 vs 0.55, `LLM_PROVIDER` anthropic vs openai. An operator trusting the example gets different risk behavior than the code. | `bot/config.py:140,155,443`; `.env.example` | S |
| P0-5 | **Two real ruff errors in `tests/` (CI only lints `bot/`).** Unreachable code after a `return` (`Undefined name 'chat_id'`) and a duplicate `class TestAuditFixes` that silently shadows the first — so the first class's tests **never run** (coverage loss in a financial app). | `tests/test_core.py:2570-2572`, `:4298` vs `:1654` | S |

---

## P1 — High-value (risk/execution + alpha integrity + ops)

### Risk & execution robustness
- **Main-loop error backoff.** `_tick()` exceptions retry every scan interval forever with no backoff — during an exchange outage this hammers the API (ban risk) and masks an unmonitored state. Add consecutive-failure tracking → exponential sleep + CRITICAL audit. `bot/core/engine.py:555-565`. **[S]**
- **Grace-window first-tick exposure.** F-4 added local monitoring for stop-less positions, but it only kicks in on the *next* scan tick (~10–60s later). On 5× leverage that's a real blind window. Enter a tight (~1s) local sub-loop immediately when entry fills but SL placement returns None. `bot/core/live_executor.py:3194-3244`. **[M]**
- **Escalate persistently-`unprotected` adopted positions.** Orphan-adopted positions whose SL can't be placed are logged CRITICAL but never auto-retried or auto-closed (asymmetric with the entry-fill path, which flattens). Retry `_place_sl_tp` each tick + push alert until protected. `bot/core/live_executor.py:1209-1268`. **[M]**
- **Covariance-based portfolio VaR.** VaR #21 uses a per-trade-return proxy and sums notionals with no correlation matrix (documented H-05 limitation) — blind to the real risk: many correlated alt-longs dumping together. The `_price_history` buffer for a real covariance matrix already exists. `bot/risk/risk_engine.py` (H-05 docstring). **[M–L]**
- **Concentration check on live holdings + unknown-symbol deny.** The PCA concentration check runs on *closed-trade* history (no-op early in a run, when concentration risk is highest); an unmapped symbol becomes "its own group" and dodges the correlation limit entirely. Drive it off open positions' price series; default-deny unknown symbols. `bot/risk/risk_engine.py:1357-1389,1483`. **[M]**

### Alpha / backtest integrity
- **Backtest on REAL market data, not synthetic GBM.** The flagship deep-backtest (1675 runs) feeds synthetic GBM+GARCH data clamped to ±10%/bar — no microstructure, no fat tails, no gaps. Real-data harnesses already exist; make them the default and relabel synthetic as smoke tests. Single biggest backtest-vs-live divergence risk. `run_deep_backtest.py:105-111`, `data_loader.py:159-160`. **[S]**
- **Gap-aware stop fills.** Backtest fills stops exactly at the SL price; on a gap-through the realistic fill is the worse open/low. Underestimates loss tails, overstates win rate. Fill at `min(sl, bar.open)` on gaps. `bot/backtest/engine.py:273-289`. **[S]**
- **Close the learning loop.** `get_learning_context()` (similar setups, win-rates, human feedback) is computed but has **zero callers** — adaptation is cosmetic; indicator weights are static literals. Feed a small capped confidence nudge from similar-setup outcomes and measure via walk-forward. `bot/.../orchestrator.py:127-170`, `analyzer.py:1636+`. **[M]**
- **Fix confluence double-counting.** `_score_confluence` returns an unweighted magnitude average; correlated mean-reversion voters (RSI, %B, Stoch, Fib) co-fire and inflate false "confluence." Switch to a true weighted average with per-family caps. `bot/core/analyzer.py:1598-1674`. **[M]**

### Testing / observability / CI
- **Harden the CI gate.** It currently warns (not fails) when a baseline test starts passing and drops order-dependent failures as "flaky" — designed to go green. Make now-passing-baseline a hard failure (forces trimming) and require explicit `@flaky` markers. `scripts/ci_test_gate.py:82-109`. **[S]**
- **Coverage threshold on money paths.** `pytest-cov` is already a dev dep but CI never runs it. Add `--cov=bot/risk --cov=bot/core/live_executor --cov=bot/compliance --cov-fail-under=85`. **[S]**
- **Dependency + secret scanning.** `pip-audit` and `bandit` are declared dev deps but never installed/run. A bot holding exchange keys needs SCA/SAST. Add CI jobs + GitHub secret scanning. `pyproject.toml:44-45`. **[S]**
- **Type-check the money modules.** mypy is fully configured but unused; CI only runs `ruff E9,F821`. Gate `mypy bot/risk bot/core` first. **[M]**
- **Observability beyond Telegram.** `MetricsEngine` computes rich metrics but there's no `/metrics` scrape endpoint, and `/health` returns `ok` unconditionally (no exchange/Redis check). Add Prometheus `/metrics` (hooks already exist) + a real `/ready`. `bot/core/metrics.py`, `api_bridge.py:375`. **[M]**
- **Property/fuzz tests for risk & order math.** No `hypothesis` tests exist. Assert invariants ("size never exceeds max risk %", "stop always on loss side", "rejected order never executes") over generated inputs. **[M]**

---

## P2 — Quality / architecture / features

### Tech debt
- **Ruff backlog: ~600 auto-fixable + ratchet CI.** ~189 unused imports, ~153 empty f-strings, ~81 unused vars. Auto-fix the mechanical ones, then **manually triage hot-path F841 in `live_executor.py`** (`sltp_triggered`, `margin_mode_set`, `dir_up` look like *dropped logic*, not noise), then expand the CI select. **[S + M triage]**
- **Standardize error handling.** 509 broad `except Exception` (telegram_handler ×113, live_executor ×104). ~10 dangerous hot-path swallows (e.g. `except: est_exit = 0` feeding a zeroed price into close logic). Add `@best_effort`/`@critical` decorators; ban silent swallows in the execution path. `bot/core/live_executor.py:4793,5677`. **[M]**
- **Extract `BitgetV3Client` from `LiveExecutor` (5,685-line god-class).** Raw signed-HTTP + routing + risk preflight + persistence + a ~1,140-line `execute()` are intertwined; the riskiest file is the least testable. Extract the transport layer behind an interface so `execute()` can be tested against a fake. **[M slice of an L]**
- **Decompose `TelegramHandler` (6,440 lines, ~75 commands).** `_guard` is copy-pasted 54× → convert to a decorator/middleware (single highest-value cut); move command groups into modules. Also add a checked skill-dispatch helper (`registry.get(name).execute()` is called ~30× without the `Optional` check → `AttributeError` risk). `bot/skills/skill_registry.py:180`. **[M–L]**
- **Centralize config literals.** `10_000` paper balance hardcoded in 8+ places; ATR `*0.02` in 3. Grep-replace with `CONFIG` refs; regenerate `.env.example` from the dataclasses (ties to P0-4). **[S–M]**
- **Split `tests/test_core.py` (5,440 lines, 33% of all tests) by subsystem.** It's exactly where the P0-5 bugs hid. Fold the per-audit-round files into module-mirrored files. **[M]**
- **Quarantine `ollama/` (46 finetuning scripts) + root one-off scripts.** Ship out of the production package; relocate root `*_test.py` into `tests/`; collapse v2/v3/v4 duplication. **[S]**
- **Consolidate three web servers** (aiohttp + stdlib + FastAPI, three auth tokens) onto FastAPI. **[M]**

### Features
- **Restore opt-in paper/sim mode for onboarding.** Highest product-leverage item. Paper is hard-blocked, yet paper wallets are still instantiated and `_check_paper_positions` runs every tick — contradictory half-wired code. Gate the block behind a per-user `sim_opt_in` flag so new users can trade risk-free first (and the contradictory paper code gets reconciled). `bot/core/engine.py:1622-1626,1932`. **[M]**
- **Web dashboard analytics (equity curve / P&L / attribution).** The metrics + reports exist but only as Telegram text; the web dashboard is a state viewer. Add `/api/performance` + `/api/equitycurve` and a charting page — table-stakes for a trading product. **[M]**
- **Unify the two parallel user systems** (SQLite `users` + JSON `UserStore` roles/tiers) — two sources of truth for "can this user trade live" is an authz hazard. Pick SQLite as canonical. **[M–L]**
- **Complete i18n** (currently a 47-key shim wired only into onboarding; some `zh` entries are Simplified not Traditional). Route high-traffic trade/scan/portfolio strings through `t()`. **[M]**
- **Wire up or delete Kelly sizing.** `kelly_position_size`/`get_recommended_size` are implemented but never called — dead safety-adjacent code. Decide: integrate behind a flag (notional cap stays authoritative) or remove. **[S delete / M integrate]**

---

## What's already strong (don't touch)
- **Risk engine** is the reference-quality module: fail-closed contract, clean `evaluate()→RiskCheck` boundary, fsync+atomic persisted circuit breaker, fail-closed on corrupt state.
- **Idempotent order submission** (clientOid + timeout recovery), partial-fill reconciliation, entry-fill-SL-failure → flatten, trailing place-before-cancel.
- **Same code path live vs backtest** at the decision layer; causal `as_of` handling; correctly-annualized Sharpe/Sortino/Calmar; walk-forward with embargo.
- **Tamper-evident hash-chained audit log** with secret redaction; **fail-closed dashboard/JWT auth**; structured frozen config with bounded validators.
- **No look-ahead bias** found in the backtest bar loop.

---

## Recommended execution sequence

1. **Quick-win safety sweep (1 PR, ~all S):** P0-1 dedup, P0-2 slippage guard, P0-3 split hard-block, P0-4 `.env.example`, P0-5 test bugs, plus main-loop backoff. These are the verified "false safety / real bug" items — highest impact-per-effort in the whole list.
2. **CI hardening (1 PR, S):** coverage threshold + pip-audit/bandit + gate hardening. Locks in everything after.
3. **Backtest integrity (1 PR, S):** real-data default + gap-aware stops + remove the ±10% clamp — makes every future strategy claim trustworthy.
4. **Risk depth (sequential, M):** grace-window sub-loop, unprotected-adoption escalation, covariance VaR, live-holdings concentration.
5. **Alpha (M):** close the learning loop, fix confluence weighting — measure each via walk-forward on real data.
6. **Architecture (ongoing, M–L):** extract `BitgetV3Client`, `_guard` decorator, split `test_core.py`, opt-in paper mode.

Each lands with regression tests and the baseline CI gate green.
