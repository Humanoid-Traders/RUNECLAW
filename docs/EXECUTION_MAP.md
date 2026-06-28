# RUNECLAW Execution Map

Companion to `docs/SIGNALS_MAP.md` and `docs/RISK_ENGINE_MAP.md`. How a
risk-approved trade actually reaches Bitget: order placement, SL/TP, monitoring,
reconciliation, slippage accounting, and the per-user routing. Anchors are at
time of writing — trust the function/class names if they drift.

> **Entry point:** `LiveExecutor.execute()` (`bot/core/live_executor.py` ~L1472).
> A `LiveExecutor` is bound to one account — the shared operator account by
> default, or a specific user's own account when per-user live trading is on
> (see `docs/LIVE_TRADING_ENABLEMENT.md`).

## Order placement

- **Exchange client** — `_get_exchange()` (~L335): authenticated ccxt Bitget,
  USDT-M swap, **isolated margin (mandatory)**, per-account credentials.
- **Order types** — market (immediate; slippage tracked) and limit (pending fill;
  2× stale-data timeout). Limit entry price is confluence-based
  (`bot/core/limit_entry.py`: VWAP / EMA / session levels / round numbers / ATR
  bands → tier A–D quality).
- **Leverage & margin** — default 5×; **dynamic leverage** scales down in high
  vol (>4% ATR ⇒ halve) and up in low vol (<1% ATR ⇒ +40%), bounded [2×, 10×].
  Margin mode is verified post-set with retry-once; mismatch ⇒ CRITICAL abort.
- **Sizing at execution** — `notional = margin_usd × leverage`,
  `qty = notional / entry_price`. Balance guard rejects if margin > available
  (fail-closed). Micro-test caps available ($/position, $/total, count).

## Exits & position management

- **SL/TP placement** — `_place_sl_tp()` (~L2691) / `_place_sl_tp_v3()` (~L2952).
- **Trailing stop** (`bot/utils/trailing.py`) — activates after ~50% of TP
  distance; trails at ATR × multiplier; only tightens.
- **Partial take-profit ladder** (`bot/core/partial_tp.py`) — TP1 (1.5R, 50%, SL→BE),
  TP2 (2.5R, 30%, lock 1R), runner (ATR trail).
- **Scale-out ladder** (`bot/risk/scale_out.py`) — +3.5% close 50%, +7% close 25%,
  runner 25% at 1× ATR. Two-tranche entry: 60% then 40% on retest confirmation.
- **Time-based / smart exits** (`bot/core/smart_exits.py`) — stale-position exits
  by strategy type; volume-decay exits; funding-cost warning.

## Monitoring & reconciliation

- **`check_positions()`** (~L3431) — per-tick SL/TP/limit-fill checks; closes on hit.
- **`reconcile_positions()`** (~L5801) — detects exchange-side closes (SL/TP
  triggered server-side) and syncs local state. **Exchange is source of truth.**
- **`close_position()`** (~L4302) — guarded close (per-trade-id lock prevents the
  double-close race across monitor / reconcile / Telegram).
- **Startup** — reconcile + `sync_positions_from_exchange()` + `verify_and_fix_sltp()`
  before accepting signals; orphan adoption catches untracked exchange positions.
- **Per-user (when enabled)** — the engine iterates `_all_live_executors()` so every
  account is monitored/reconciled, and rehydrates linked users' executors at
  startup so their positions resume monitoring after a restart
  (`bot/core/engine.py`).

## Execution quality

- **Slippage** (`bot/core/slippage.py`) — per-symbol signed slippage stats
  (mean/median/p95/p99), volume-adjusted prediction, and **edge-based rejection**
  (skip if predicted slippage > 30% of trade edge). 500 records/symbol, persisted.
- **Commission** — taker fee per side, deferred to close.
- **Order history** — last 200 orders in memory; execution-failure tokens classify
  no-position outcomes vs fills.
- **Graceful degradation** — WS-gap ⇒ pause; API-error accumulation ⇒ reduce-only.

## Persistence

- `data/live_positions.json` / `data/closed_trades.json` (operator);
  `…_<user_id>.json` per user. Atomic temp+fsync+replace; `.bak` fallback on
  corrupt read.

## Key anchors

| Concept | Location |
|---|---|
| `LiveExecutor` | `bot/core/live_executor.py:225` |
| `execute()` | `live_executor.py:1472` |
| `_get_exchange()` | `live_executor.py:335` |
| `_place_sl_tp()` / `_v3` | `live_executor.py:2691` / `2952` |
| `check_positions()` | `live_executor.py:3431` |
| `close_position()` | `live_executor.py:4302` |
| `reconcile_positions()` | `live_executor.py:5801` |
| Per-user routing / monitoring | `bot/core/engine.py` · `_executor_for` / `_all_live_executors` |
| Slippage | `bot/core/slippage.py` |
| Partial TP / scale-out | `bot/core/partial_tp.py` / `bot/risk/scale_out.py` |

## Gaps (see SIGNALS_MAP for the ranked list)

No adaptive maker-first vs market routing; no partial-fill ledger (fills recorded
as all-or-nothing); commission is flat (no maker rebate / volume tiering); no L2/L3
depth-curve fit (slippage uses recent-trade stats); funding/basis is detected but
not executed; per-user live-balance size-clamp still reads the operator balance.
