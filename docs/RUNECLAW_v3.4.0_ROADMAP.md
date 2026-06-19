# RUNECLAW v3.4.0 Refactor Roadmap

**Scope:** 52 remaining findings from Deep Audit v2 (all MEDIUM/LOW severity after v3.3.1 patched the 9 account-ending items)  
**Baseline:** v3.3.1 — all 368 tests passing, 9 critical/high fixes deployed  
**Forward walk status:** Active — sprint 1 and 2 should land before significant RT accumulation if possible  
**Document status:** Living — update finding rows as PRs merge

---

## Superseded claims

> None at time of writing. Add here if any finding below is invalidated by a PR before it is officially closed.

---

## Quick reference — all 52 findings

| ID | Severity | File | Sprint | Status |
|----|----------|------|--------|--------|
| C2-11 | HIGH | risk_engine.py | 1 | **Closed** — 2026-06-19 |
| C2-29 | HIGH | risk_engine.py | 1 | **Closed** — 2026-06-19 |
| C2-15 | MEDIUM | portfolio.py | 1 | **Closed** — 2026-06-19 |
| C2-12 | HIGH | live_executor.py | 1 | **Closed** — 2026-06-19 |
| C2-53 | LOW | engine.py | 1 | **Closed** — 2026-06-19 |
| C2-34 | MEDIUM | risk_engine.py + portfolio.py | 2 | **Closed** — 2026-06-19 |
| C2-33 | MEDIUM | portfolio.py | 2 | **Closed** — 2026-06-19 |
| C2-14 | HIGH | live_executor.py | 2 | **Closed** — 2026-06-19 |
| C2-13 | HIGH | live_executor.py | 2 | **Closed** — 2026-06-19 |
| C2-23 | CRITICAL | risk_engine.py + portfolio.py | 3 | **Closed** — 2026-06-19 |
| C2-26 | HIGH | engine.py | 3 | **Closed** — 2026-06-19 |
| C2-27 | HIGH | live_executor.py | 3 | **Closed** — 2026-06-19 |
| C2-30 | MEDIUM | engine.py | 3 | **Closed** — 2026-06-19 |
| C2-31 | MEDIUM | engine.py | 3 | **Closed** — 2026-06-19 |
| C2-36 | MEDIUM | risk_engine.py | 3 | **Closed** — 2026-06-19 |
| C2-05 | MEDIUM | config.py | 4 | **Closed** — 2026-06-19 |
| C2-06 | MEDIUM | config.py | 4 | **Closed** — 2026-06-19 |
| C2-07 | MEDIUM | config.py | 4 | **Closed** — 2026-06-19 |
| C2-08 | MEDIUM | config.py | 4 | **Closed** — 2026-06-19 |
| C2-35 | MEDIUM | risk_engine.py | 4 | **Closed** — 2026-06-19 |
| C2-44 | LOW | risk_engine.py | 4 | **Closed** — 2026-06-19 |
| C2-47 | LOW | portfolio.py | 4 | **Closed** — 2026-06-19 |
| C2-59 | LOW | models.py | 4 | **Closed** — 2026-06-19 |
| C2-60 | LOW | models.py | 4 | **Closed** — 2026-06-19 |
| C2-61 | LOW | config.py | 4 | **Closed** — 2026-06-19 |
| C2-19 | MEDIUM | analyzer.py | 5 | **Closed** — 2026-06-19 |
| C2-20 | MEDIUM | analyzer.py | 5 | **Closed** — 2026-06-19 |
| C2-21 | MEDIUM | skill_registry.py | 5 | **Closed** — 2026-06-19 |
| C2-22 | MEDIUM | skill_registry.py | 5 | **Closed** — 2026-06-19 |
| C2-28 | HIGH | risk_engine.py | 5 | **Closed** — 2026-06-19 |
| C2-32 | MEDIUM | engine.py | 5 | **Closed** — 2026-06-19 |
| C2-37 | MEDIUM | portfolio.py | 5 | **Closed** — 2026-06-19 |
| C2-41 | LOW | risk_engine.py | 6 | **Closed** — 2026-06-19 |
| C2-42 | LOW | risk_engine.py | 6 | **Closed** — 2026-06-19 |
| C2-43 | LOW | risk_engine.py | 6 | **Closed** — 2026-06-19 |
| C2-45 | LOW | risk_engine.py | 6 | **Closed** — 2026-06-19 |
| C2-46 | LOW | risk_engine.py | 6 | **Closed** — 2026-06-19 |
| C2-48 | LOW | portfolio.py | 6 | **Closed** — 2026-06-19 |
| C2-49 | LOW | portfolio.py | 6 | **Closed** — 2026-06-19 |
| C2-50 | LOW | portfolio.py | 6 | **Closed** — 2026-06-19 |
| C2-51 | LOW | portfolio.py | 6 | **Closed** — 2026-06-19 |
| C2-52 | LOW | engine.py | 6 | **Closed** — 2026-06-19 |
| C2-54 | LOW | engine.py | 6 | **Closed** — 2026-06-19 |
| C2-55 | LOW | engine.py | 6 | **Closed** — 2026-06-19 |
| C2-56 | LOW | live_executor.py | 6 | **Closed** — 2026-06-19 |
| C2-57 | LOW | live_executor.py | 6 | **Closed** — 2026-06-19 |
| C2-58 | LOW | live_executor.py | 6 | **Closed** — 2026-06-19 |
| C2-16 | MEDIUM | live_executor.py | 6 | **Closed** — 2026-06-19 (done early in Sprint 2) |
| C2-38 | HIGH | backtest/engine.py | 6 | **Closed** — 2026-06-19 |
| C2-39 | MEDIUM | backtest/engine.py | 6 | **Closed** — 2026-06-19 |
| C2-40 | MEDIUM | backtest/engine.py | 6 | **Closed** — 2026-06-19 |
| C2-08 | MEDIUM | config.py | 4 | **Closed** — 2026-06-19 (done in Sprint 4) |

---

## Sequencing rationale

Sprints are ordered by **impact on live data quality first, structural correctness second, hygiene last.**

- **Sprint 1** targets sizing math that affects every trade the walk accumulates between now and evaluation.
- **Sprint 2** targets state persistence — a crash between sprints should not leave inconsistent books.
- **Sprint 3** targets concurrency and FSM correctness — these are structural but not acutely live-trade-affecting.
- **Sprints 4–6** are safe to run in any order and can be parallelized.

The one exception: **C2-23** is marked CRITICAL in the audit and placed in Sprint 3 rather than Sprint 1 because the deadlock is latent (safe under current single-threaded asyncio), not acute. If any threading refactor is planned before v3.4.0 ships, C2-23 must move to Sprint 1 immediately.

---

## Sprint 1 — Position sizing correctness

**Goal:** Ensure every trade the walk accumulates from here uses correct sizing and fill quantities.  
**Files:** `risk_engine.py`, `portfolio.py`, `live_executor.py`, `engine.py`  
**Suggested PR:** `fix/v340-sizing-correctness`

---

### C2-11 [HIGH] Macro size multiplier applied after check #2

**File:** `risk_engine.py:527`

**Problem:** `position_usd *= ctx.size_multiplier` happens after check #2 (POSITION_SIZE) has already validated the pre-multiplied value. If `size_multiplier > 1.0`, the returned position exceeds what the risk gate approved. The risk engine approves X, executes X * multiplier.

**Fix:**
```python
# BEFORE (wrong):
# ... run check #2 against position_usd ...
position_usd *= ctx.size_multiplier   # ← happens after approval

# AFTER (correct):
position_usd *= ctx.size_multiplier   # ← move before check #2
# ... now run check #2 against post-multiplied value ...
```

**Test:** Assert that a `size_multiplier=2.0` with a position that would pass at 1× but fail at 2× (exceeds POSITION_SIZE limit) is rejected, not silently downsized or passed through.

---

### C2-29 [HIGH] Regime multiplier can push position above notional cap

**File:** `risk_engine.py:289-293`

**Problem:** The notional cap at line 284–285 limits position to `max_position_pct * balance`. The regime multiplier (`1.5` for `STRONG_TREND_UP`) is then applied after the cap, pushing the position 50% above it. Check #2 evaluates this inflated value against `max_symbol_exposure_pct` (20%), which is wider, so it often passes.

**Fix:**
```python
# Apply regime multiplier BEFORE the cap:
position_usd *= regime_multiplier
position_usd = min(position_usd, balance * max_position_pct)  # cap applies post-multiply
```

**Note:** This changes behaviour — strong-trend positions will be smaller than current. That is correct behaviour. Document the change in the commit message explicitly so it is visible in the forward walk audit log.

**Test:** Assert that `STRONG_TREND_UP` with `max_position_pct=0.10` and `regime_multiplier=1.5` produces a position capped at `balance * 0.10`, not `balance * 0.15`.

---

### C2-15 [MEDIUM] Portfolio silently clamps oversized position to available balance

**File:** `portfolio.py:93-97`

**Problem:** When `size_usd > self.balance`, the portfolio executes a clamped (smaller) position without telling the risk engine. The risk engine's exposure tracking is based on the originally approved size — a phantom position. Over time this creates divergence between tracked and actual exposure.

**Fix:** Reject rather than clamp. Return an error that surfaces back to the caller.

```python
# BEFORE:
if size_usd > self.balance:
    size_usd = self.balance   # silent clamp

# AFTER:
if size_usd > self.balance:
    raise InsufficientBalanceError(
        f"Position size {size_usd:.2f} exceeds available balance {self.balance:.2f}"
    )
```

**Alternative:** If clamping is intentional (graceful degradation), add a post-trade reconciliation call back to the risk engine with the actual executed size so exposure tracking stays accurate.

**Test:** Assert that `execute_trade(size_usd=balance+1)` raises `InsufficientBalanceError` rather than silently executing at `balance`.

---

### C2-12 [HIGH] Partial fills tracked at full requested quantity

**File:** `live_executor.py:904-928`

**Problem:** If the exchange returns a market order response without an explicit `filled` field, `filled_qty` defaults to the full calculated quantity. `close_position()` then tries to close more than actually filled — on futures in hedge mode, the excess opens a reverse position.

**Fix:** After placing the market order, fetch the order by ID to confirm actual filled quantity before recording the position.

```python
order_result = await self.exchange.create_market_order(symbol, side, qty)

# Confirm actual fill — don't trust the create response
confirmed = await self.exchange.fetch_order(order_result["id"], symbol)
filled_qty = confirmed.get("filled")
if not filled_qty:
    raise OrderConfirmationError(
        f"Could not confirm fill for order {order_result['id']}"
    )

# Record position with confirmed quantity, not requested quantity
pos.quantity = filled_qty
```

**Test:** Mock a partial fill response (exchange returns `filled=0.5` when `0.8` was requested) and assert the recorded position quantity is `0.5`.

---

### C2-53 [LOW] stored_atr fallback 0.0 can place SL at entry price

**File:** `engine.py:949`

**Problem:** When ATR is unavailable, `stored_atr` falls back to `0.0`. A zero ATR produces `sl_price = entry_price - 0 * multiplier = entry_price`. SL placed at entry immediately triggers, closing the trade at open.

**Fix:**
```python
atr = self._stored_atr.get(symbol, None)
if not atr or atr <= 0:
    logger.warning(
        "No valid ATR for %s — skipping trade to avoid SL-at-entry", symbol
    )
    return None   # or raise, depending on caller contract
```

**Test:** Assert that a trade request with no ATR in store is rejected (returns None / raises) rather than producing a zero-ATR SL.

---

## Sprint 2 — State persistence consistency

**Goal:** Ensure a crash at any point between two state writes leaves recoverable, consistent books.  
**Files:** `portfolio.py`, `risk_engine.py`, `live_executor.py`  
**Suggested PR:** `fix/v340-state-persistence`

---

### C2-14 [HIGH] Cancelled limit orders never written to closed_trades.json

**File:** `live_executor.py:1712-1715`

**⚠ Verify first:** Check whether this was included in v3.3.1. It appeared in the HIGH list in the audit but is absent from the v3.3.1 patch table. If it shipped, mark closed and skip.

**Problem:** `_check_pending_limit` sets `status="closed"` and calls `_save_positions()` for cancelled/expired limit orders, but never calls `_append_closed_trade()`. The position is silently dropped from both memory and the open positions file without being written to `closed_trades.json`. It is invisible to all downstream analysis.

**Fix:**
```python
pos.status = "closed"
pos.close_reason = "cancelled"   # or "expired"
await self._append_closed_trade(pos)   # ← add this line
await self._save_positions()
```

**Test:** Cancel a pending limit order and assert that `closed_trades.json` contains an entry for it with `close_reason="cancelled"`.

---

### C2-13 [HIGH] Reconciliation guesses PnL from current price, not actual fill

**File:** `live_executor.py:2446-2576`

**Problem:** When a position disappears from the exchange, reconciliation uses the current ticker price to estimate whether SL or TP was hit and what the PnL was. If the position closed by SL hours ago and price has since recovered past entry, the reconciliation attributes a win (TP hit) with incorrect PnL. This corrupts `closed_trades.json` — the primary data source for forward walk evaluation.

**Fix:** Query `fetchMyTrades` or closed order history for actual fill price first. Fall back to ticker estimation only as a last resort, and flag estimated records explicitly.

```python
async def _reconcile_missing_position(self, pos, symbol):
    # 1. Try actual trade history first
    try:
        trades = await self.exchange.fetch_my_trades(symbol, limit=50)
        relevant = [t for t in trades if t["order"] in (pos.sl_order_id, pos.tp_order_id)]
        if relevant:
            fill = relevant[-1]
            return self._build_close_result(pos, fill["price"], source="exchange_fill")
    except Exception as e:
        logger.warning("fetchMyTrades failed for %s: %s", symbol, e)

    # 2. Try closed orders
    try:
        closed = await self.exchange.fetch_closed_orders(symbol, limit=20)
        for o in closed:
            if o["id"] in (pos.sl_order_id, pos.tp_order_id):
                return self._build_close_result(pos, o["average"], source="closed_order")
    except Exception as e:
        logger.warning("fetchClosedOrders failed for %s: %s", symbol, e)

    # 3. Last resort: current ticker — flag as estimated
    ticker = await self.exchange.fetch_ticker(symbol)
    result = self._build_close_result(pos, ticker["last"], source="estimated")
    result.pnl_estimated = True
    logger.warning(
        "PnL for %s is estimated from current price — actual fill unavailable", symbol
    )
    return result
```

**Test:** Mock a disappeared position where the actual fill (from `fetchMyTrades`) is at SL price but current ticker is above entry. Assert reconciliation records a loss, not a win.

---

### C2-33 [MEDIUM] Portfolio save missing fsync before os.replace

**File:** `portfolio.py:467-469`

**Problem:** No `f.flush()` or `os.fsync()` before `os.replace()`. A crash between the write and the replace can leave a partially-written `.tmp` file. `risk_engine.py` already does this correctly.

**Fix:**
```python
with open(tmp_path, "w") as f:
    json.dump(state, f, indent=2)
    f.flush()
    os.fsync(f.fileno())   # ← add these two lines
os.replace(tmp_path, self._state_path)
```

**Test:** Verify `portfolio_state.json` is valid JSON after a forced process kill mid-write (manual test or use a mock that raises after partial write).

---

### C2-34 [MEDIUM] Two separate state files with no transactional consistency

**File:** `risk_engine.py` + `portfolio.py`

**Problem:** `risk_state.json` and `portfolio_state.json` are saved in separate operations. A crash after portfolio saves but before risk engine saves leaves the books in an inconsistent state — position shows as closed but loss streak not incremented, or vice versa.

**Recommended approach:** Combine into a single `combined_state.json` written atomically in one `os.replace`. Add a migration on first boot to read from legacy files if combined file is absent.

```python
# combined_state.json structure:
{
    "portfolio": { ... },   # current portfolio_state.json content
    "risk": { ... },        # current risk_state.json content
    "written_at": "ISO8601 timestamp",
    "version": 1
}
```

**Migration on boot:**
```python
def _load_state(self):
    if combined_path.exists():
        state = json.loads(combined_path.read_text())
        self._portfolio.load(state["portfolio"])
        self._risk.load(state["risk"])
    else:
        # Legacy migration — read separate files, write combined
        self._portfolio.load_legacy(portfolio_path)
        self._risk.load_legacy(risk_path)
        self._save_combined()   # write combined going forward
```

**Note:** This is the most structurally significant change in Sprint 2. Write it last, after C2-33 is confirmed working, so fsync behaviour is already solid before the combined write is introduced.

**Test:** Simulate a crash (raise after portfolio state write, before risk state write) and assert that on restart both states are consistent — no half-committed trade results.

---

### C2-16 [MEDIUM] Limit order cancellation doesn't verify actual cancel before marking closed

**File:** `live_executor.py:1726-1737`

**Problem:** If `cancel_order` fails (network error, order already partially filled), the position is marked closed locally but the limit order may still be live on the exchange. A subsequent fill creates an untracked position.

**Fix:**
```python
try:
    await self.exchange.cancel_order(order_id, symbol)
except Exception as e:
    logger.warning("cancel_order failed for %s: %s — verifying status", order_id, e)

# Always verify, regardless of whether cancel raised
order = await self.exchange.fetch_order(order_id, symbol)
if order["status"] not in ("canceled", "expired"):
    raise OrderCancellationError(
        f"Order {order_id} still active after cancel attempt: {order['status']}"
    )

# Only now mark closed locally
pos.status = "closed"
```

**Test:** Mock `cancel_order` raising a network error but `fetch_order` returning `status="open"`. Assert the position is not marked closed locally.

---

## Sprint 3 — Architecture and concurrency

**Goal:** Fix structural issues that are safe today but become dangerous under load or refactor.  
**Files:** `risk_engine.py`, `portfolio.py`, `engine.py`, `live_executor.py`  
**Suggested PR:** `fix/v340-concurrency-architecture`

**Note:** Write tests first for C2-23 before touching the code. The lock ordering fix is mechanical but easy to introduce a subtler ordering bug in the process.

---

### C2-23 [CRITICAL] Lock ordering inversion — latent deadlock

**File:** `risk_engine.py:128-133`, `portfolio.py:201-207`

**Problem:** `evaluate()` acquires `RiskEngine._lock` then calls `portfolio.snapshot()` (acquires `portfolio._lock`). `close_position()` acquires `portfolio._lock` then calls `record_trade_result()` (acquires `RiskEngine._lock`). Classic A→B vs B→A deadlock. Currently safe only because both paths run on the same asyncio thread with RLocks. Any refactor to multi-threading or concurrent asyncio tasks will deadlock.

**Fix:** Defer the trade-close callback to after the portfolio lock is released. The callback should never be called while holding another lock.

```python
# BEFORE (in close_position):
async with self._portfolio._lock:
    # ... close logic ...
    await self._risk_engine.record_trade_result(trade_result)   # ← acquires risk lock while holding portfolio lock

# AFTER:
async with self._portfolio._lock:
    # ... close logic ...
    trade_result = build_trade_result(pos)
# portfolio lock released here ↑
await self._risk_engine.record_trade_result(trade_result)       # ← called outside portfolio lock
```

**Establish lock ordering rule (document in code):**
```python
# LOCK ORDERING CONTRACT (must never be violated):
# If both locks must be held, always acquire in this order:
#   1. RiskEngine._lock
#   2. portfolio._lock
# Never acquire risk lock while holding portfolio lock.
```

**Test (write before fixing):**
```python
async def test_no_deadlock_concurrent_close_and_evaluate():
    """Simulate concurrent close_position and evaluate() calls."""
    async with asyncio.timeout(2.0):   # deadlock = timeout
        await asyncio.gather(
            engine.close_position(trade_id),
            risk_engine.evaluate(ctx)
        )
```

---

### C2-26 [HIGH] FSM transitions to SCANNING while trades are CONFIRMING

**File:** `engine.py:380-439`

**Problem:** If pending ideas are in `CONFIRMING` state and the user hasn't responded, the next tick transitions to `SCANNING`. A concurrent `confirm_trade` call finds the idea in `_pending_ideas` while the engine is mid-scan, creating a race on shared state.

**Fix:**
```python
async def _tick(self):
    # Skip scanning when awaiting confirmation
    if self._pending_ideas:
        logger.debug("Skipping scan tick — %d ideas awaiting confirmation", len(self._pending_ideas))
        await self._check_open_positions()
        return

    # ... normal scan logic ...
```

**Test:** Assert that a tick fired while `_pending_ideas` is non-empty does not enter the scanning path (mock the scan method and assert it is not called).

---

### C2-27 [HIGH] Single fetch_tickers call — one delisted symbol blocks ALL position monitoring

**File:** `live_executor.py:1557-1558`

**Problem:** `check_positions()` calls `fetch_tickers(open_symbols)` with all symbols in a single batch. If one symbol is delisted or returns an exchange error, the entire call fails, preventing SL/TP checks for all open positions.

**Fix:**
```python
# BEFORE:
tickers = await self.exchange.fetch_tickers(open_symbols)

# AFTER:
tickers = {}
for sym in open_symbols:
    try:
        tickers[sym] = await self.exchange.fetch_ticker(sym)
    except Exception as e:
        logger.error(
            "fetch_ticker failed for %s — skipping in this check cycle: %s", sym, e
        )
        # Continue monitoring other symbols; alert on repeated failures
        self._ticker_failure_count[sym] = self._ticker_failure_count.get(sym, 0) + 1
        if self._ticker_failure_count[sym] >= 3:
            await self._send_alert(f"⚠ {sym} ticker unavailable for 3+ cycles — manual check required")
```

**Test:** Mock one symbol raising `ExchangeError` and assert that SL/TP checks proceed for all other symbols.

---

### C2-30 [MEDIUM] reject_trade does not clean up _pending_pyramid

**File:** `engine.py:1073-1085`

**Problem:** `reject_trade` pops from `_pending_ideas` and `_pending_atr` but not `_pending_pyramid`. Orphaned entries accumulate indefinitely and can cause stale pyramid state to apply to future trades on the same asset.

**Fix:**
```python
def reject_trade(self, trade_id: str):
    self._pending_ideas.pop(trade_id, None)
    self._pending_atr.pop(trade_id, None)
    self._pending_pyramid.pop(trade_id, None)   # ← add this line
```

**Test:** Reject a trade that has a pyramid entry and assert `_pending_pyramid` does not contain the trade_id afterward.

---

### C2-31 [MEDIUM] Dedup replaces pending idea without cleaning pyramid flag for old idea

**File:** `engine.py:428-435`

**Problem:** When a new idea for the same asset replaces an existing pending idea, `_pending_pyramid` is not cleaned for the old idea's ID. The new idea may then incorrectly inherit the old idea's pyramid flag.

**Fix:**
```python
if existing_id := self._find_pending_idea_for_asset(asset):
    self._pending_ideas.pop(existing_id, None)
    self._pending_atr.pop(existing_id, None)
    self._pending_pyramid.pop(existing_id, None)   # ← add this line
# Then register new idea
self._pending_ideas[new_id] = new_idea
```

**Test:** Replace a pending idea that has a pyramid flag with a new idea for the same asset. Assert the new idea does not inherit the pyramid flag.

---

### C2-36 [MEDIUM] get_regime_adjusted_params mutates state — naming implies pure accessor

**File:** `risk_engine.py:807-819`

**Problem:** This method named `get_*` mutates `_current_regime` and `_current_vol_state` as a side effect. If called from multiple contexts (logging, UI status, actual evaluation), the side-effect overwrites state with potentially stale regime data.

**Fix:** Separate the setter from the getter.

```python
def set_regime_params(self, regime, vol_state) -> None:
    """Update internal regime and volatility state."""
    self._current_regime = regime
    self._current_vol_state = vol_state

def get_regime_adjusted_params(self) -> RegimeParams:
    """Pure accessor — reads _current_regime and _current_vol_state. No side effects."""
    return self._compute_params(self._current_regime, self._current_vol_state)
```

Update all callers: evaluation path calls `set_regime_params` once per evaluation, then any number of `get_regime_adjusted_params` calls are safe.

**Test:** Call `get_regime_adjusted_params` twice and assert `_current_regime` and `_current_vol_state` are unchanged after the second call.

---

## Sprint 4 — Config and validation hardening

**Goal:** Eliminate silent misconfiguration. Every bad env var should be loud.  
**Files:** `config.py`, `risk_engine.py`, `models.py`, `portfolio.py`  
**Suggested PR:** `fix/v340-config-hardening`

All items in this sprint are small. Group into a single PR with one commit per finding for easy bisection.

---

### C2-05 [MEDIUM] max_open_positions / max_consecutive_losses allow 0

**File:** `config.py:62, 72-73`

```python
# BEFORE:
max_open_positions: int = int(_env_float("MAX_OPEN_POSITIONS", 3))
max_consecutive_losses: int = int(_env_float("MAX_CONSECUTIVE_LOSSES", 5))

# AFTER:
max_open_positions: int = int(_env_float_bounded("MAX_OPEN_POSITIONS", 3, min_val=1))
max_consecutive_losses: int = int(_env_float_bounded("MAX_CONSECUTIVE_LOSSES", 5, min_val=1))
```

If `_env_float_bounded` doesn't exist yet, add it:
```python
def _env_float_bounded(key: str, default: float, min_val: float = None, max_val: float = None) -> float:
    val = _env_float(key, default)
    if min_val is not None and val < min_val:
        raise ConfigurationError(f"{key}={val} is below minimum {min_val}")
    if max_val is not None and val > max_val:
        raise ConfigurationError(f"{key}={val} is above maximum {max_val}")
    return val
```

---

### C2-06 [MEDIUM] US market hours hardcoded to EDT — off by 1 hour Nov–Mar

**File:** `config.py:231-234`

```python
# BEFORE:
US_MARKET_OPEN_HOUR_UTC = 13   # hardcoded EDT
US_REGULAR_CLOSE_HOUR_UTC = 20

# AFTER:
from zoneinfo import ZoneInfo
from datetime import datetime, time

_NY = ZoneInfo("America/New_York")

def us_market_open_utc() -> int:
    """Returns the UTC hour of NYSE open for the current date."""
    now_ny = datetime.now(_NY)
    open_ny = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    return open_ny.astimezone(ZoneInfo("UTC")).hour

def us_market_close_utc() -> int:
    """Returns the UTC hour of NYSE close for the current date."""
    now_ny = datetime.now(_NY)
    close_ny = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    return close_ny.astimezone(ZoneInfo("UTC")).hour
```

Replace all references to `US_MARKET_OPEN_HOUR_UTC` / `US_REGULAR_CLOSE_HOUR_UTC` with function calls. Verify `zoneinfo` is available (stdlib Python 3.9+; add `tzdata` to requirements for Windows/Docker environments where the tz database may not be present).

---

### C2-07 [MEDIUM] Empty SIMULATION_MODE= returns False, may enable live trading unintentionally

**File:** `config.py:21-33`

```python
def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    if raw == "":
        if default is True:
            logger.warning(
                "Safety switch %s is set to empty string — treating as True (safe default). "
                "Set explicitly to 'false' to disable.", key
            )
            return True
        return default
    return raw.lower() in ("1", "true", "yes", "on")
```

---

### C2-08 [MEDIUM] CONFIG is import-time frozen — not documented

**File:** `config.py:408-409`

No code change needed. Add a module-level docstring and a comment at the freeze point:

```python
"""
config.py — RUNECLAW runtime configuration.

CONFIG is a frozen dataclass instantiated at import time from environment variables.
Changes to env vars after import have no effect without a full process restart.
RuntimeState handles hot-reloadable runtime flags (e.g. kill switch, simulation override).
"""

# --- Module-level freeze point ---
CONFIG = _build_config()   # ← import-time, not hot-reloadable
```

---

### C2-35 [MEDIUM] Hardcoded loss streak soft-reject threshold

**File:** `risk_engine.py:411`

```python
# BEFORE:
if self._consecutive_losses >= 3:   # hardcoded — inconsistent with max_consecutive_losses config

# AFTER:
soft_limit = max(2, CONFIG.risk.max_consecutive_losses - 2)
if self._consecutive_losses >= soft_limit:
    # soft reject path
```

This ensures the soft limit always stays 2 below the hard circuit-breaker limit regardless of what the operator sets. If `max_consecutive_losses=3`, soft fires at 1 and hard fires at 3 — both still meaningful.

---

### C2-44 [LOW] State dir validation allows relative paths outside cwd

**File:** `risk_engine.py:54`

```python
state_dir = Path(CONFIG.risk.state_dir).resolve()
if not state_dir.is_absolute():
    raise ConfigurationError(f"state_dir must resolve to an absolute path, got: {state_dir}")
# Optionally: assert it stays within an allowed root
allowed_root = Path("/var/runeclaw").resolve()
if not str(state_dir).startswith(str(allowed_root)):
    logger.warning("state_dir %s is outside expected root %s", state_dir, allowed_root)
```

---

### C2-47 [LOW] initial_balance treats 0.0 as falsy

**File:** `portfolio.py:49`

```python
# BEFORE:
self.balance = initial_balance or CONFIG.portfolio.initial_balance

# AFTER:
self.balance = initial_balance if initial_balance is not None else CONFIG.portfolio.initial_balance
```

---

### C2-59 [LOW] TradeIdea allows entry_price ≤ 0

**File:** `models.py:97-98`

```python
@validator("entry_price")
def entry_price_must_be_positive(cls, v):
    if v <= 0:
        raise ValueError(f"entry_price must be positive, got {v}")
    return v
```

---

### C2-60 [LOW] MarketSignal missing ge=0 constraint on price/volume

**File:** `models.py:51-60`

```python
class MarketSignal(BaseModel):
    price: float = Field(..., ge=0, description="Current market price")
    volume: float = Field(..., ge=0, description="24h volume")
    # ... other fields ...
```

---

### C2-61 [LOW] DEFAULT_LEVERAGE upper bound 125× with no warning above 20×

**File:** `config.py:109`

```python
leverage = _env_float_bounded("DEFAULT_LEVERAGE", 5.0, min_val=1.0, max_val=125.0)
if leverage > 20:
    logger.warning(
        "DEFAULT_LEVERAGE=%s× is above 20× — high leverage dramatically increases liquidation risk. "
        "Confirm this is intentional.", leverage
    )
```

---

## Sprint 5 — Analysis pipeline

**Goal:** Fix silent failures in the analyzer and skill registry that degrade signal quality.  
**Files:** `analyzer.py`, `skill_registry.py`, `risk_engine.py`, `engine.py`, `portfolio.py`  
**Suggested PR:** `fix/v340-analysis-pipeline`

---

### C2-28 [HIGH] VaR z-score fallback formula is mathematically wrong

**File:** `risk_engine.py:999-1001`

**Problem:** `sqrt(2) * erfc(2 * confidence_level - 1)` yields ~0.007 at 99% confidence instead of the correct 2.326. Currently unreachable (default path uses hardcoded 1.645), but any caller with a custom confidence level gets wildly wrong VaR.

**Fix:**
```python
_VAR_Z_SCORES = {
    0.90: 1.282,
    0.95: 1.645,
    0.99: 2.326,
    0.999: 3.090,
}

def _z_score_for_confidence(confidence: float) -> float:
    if confidence in _VAR_Z_SCORES:
        return _VAR_Z_SCORES[confidence]
    # Nearest-key fallback for non-standard values
    nearest = min(_VAR_Z_SCORES.keys(), key=lambda k: abs(k - confidence))
    logger.warning(
        "No exact z-score for confidence %.4f — using nearest key %.3f (z=%.3f)",
        confidence, nearest, _VAR_Z_SCORES[nearest]
    )
    return _VAR_Z_SCORES[nearest]
```

**Test:** Assert `_z_score_for_confidence(0.99)` returns `2.326`, not `~0.007`.

---

### C2-19 [MEDIUM] Operator precedence bug in capitulation detection

**File:** `analyzer.py:1000-1003`

```python
# BEFORE (ternary binds to only the right operand):
is_large_red = (closes[-1] < opens[-1] and body / candle_range > 0.6 if candle_range > 0 else False)

# AFTER (explicit guard first, division only when safe):
is_large_red = (
    candle_range > 0
    and closes[-1] < opens[-1]
    and body / candle_range > 0.6
)
```

**Test:** Assert `is_large_red` is `False` when `candle_range == 0` (not an exception).

---

### C2-20 [MEDIUM] Counter-trend + regime double-penalty over-filters mean-reversion setups

**File:** `analyzer.py:472, 531`

**Problem:** Counter-trend penalty is multiplicative (`confidence * 0.5`), regime penalty is subtractive (applied after blending). Combined effect is 70–80% total confidence reduction, potentially eliminating legitimate mean-reversion setups entirely.

**Fix options:**
1. **Unify to one mechanism** — both penalties multiplicative: `confidence * counter_trend_factor * regime_factor`
2. **Cap combined penalty** — `combined_penalty = max(counter_trend_factor * regime_factor, 0.25)` so confidence never drops below 25% of raw signal
3. **Document intent** — if 70–80% reduction is deliberate, add a comment explaining the reasoning

Recommended: option 1 (unified multiplicative) for mathematical consistency. Update the docstring to state the combined penalty range explicitly.

---

### C2-21 [MEDIUM] MarketSignal with price=0 can reach analyzer

**File:** `skill_registry.py:253-277`

```python
sig = MarketSignal(price=ticker.get("last", 0), ...)

# Add guard before passing to analyzer:
if sig.price <= 0:
    logger.error("Invalid price %s for %s — skipping analysis", sig.price, symbol)
    return AnalysisResult.error(f"Zero or negative price for {symbol}")
```

---

### C2-22 [MEDIUM] volume_spike_min filter references nonexistent attribute — always reads 0

**File:** `skill_registry.py:1170-1172`

```python
# BEFORE (nonexistent attribute, getattr returns 0 always):
ratio = getattr(signal, "volume_spike_ratio", 0)
if ratio < self.volume_spike_min:
    return False

# AFTER option A — add the attribute to MarketSignal:
# In models.py: volume_spike_ratio: float = Field(0.0, ge=0)
# Then compute it when building the signal

# AFTER option B — remove the check if volume spike filtering is not actively used:
# Delete lines 1170-1172 and add a TODO for future implementation
```

---

### C2-32 [MEDIUM] Paper-mode pyramid trades execute at full size

**File:** `engine.py:905-906`

**Problem:** The pyramid half-size and SL-to-breakeven logic only runs in the live execution path. Paper-mode pyramid trades execute at full size, making paper results non-representative of live behaviour.

**Fix:** Extract pyramid sizing logic into a shared method and call it from both paths.

```python
def _apply_pyramid_sizing(self, trade_idea, is_pyramid: bool) -> TradeIdea:
    if not is_pyramid:
        return trade_idea
    # Half size
    trade_idea.position_size *= 0.5
    # Move SL to breakeven on original position
    self._move_sl_to_breakeven(trade_idea.original_trade_id)
    return trade_idea

# Call from both live and paper paths:
trade_idea = self._apply_pyramid_sizing(trade_idea, is_pyramid=self._pending_pyramid.get(trade_id, False))
```

---

### C2-37 [MEDIUM] get_trailing_status reports original SL, not trailing-adjusted SL

**File:** `portfolio.py:279`

```python
# BEFORE:
"current_sl": pos.stop_loss   # original SL, never updated

# AFTER:
trailing = self._compute_trailing_sl(pos)
"current_sl": trailing.effective_sl if trailing else pos.stop_loss
```

Where `_compute_trailing_sl` is the same method used internally when evaluating trailing stop moves — pull it up if it's currently inlined.

---

## Sprint 6 — Hygiene and observability

**Goal:** Eliminate low-severity correctness issues, improve log quality, prevent unbounded growth.  
**Files:** `risk_engine.py`, `portfolio.py`, `engine.py`, `live_executor.py`, `backtest/engine.py`  
**Suggested PR:** `fix/v340-hygiene`

All items here are small. One commit per finding. Review as a batch.

---

### C2-38 [HIGH] Backtest trailing stop updated before SL check — same-bar phantom stop-outs

**File:** `backtest/engine.py:256-258`

```python
# BEFORE: trailing updated with favorable extreme first, then SL checked
update_trailing(bar.high, pos)   # ← wrong order for LONG
sl_hit = check_sl(bar.low, pos.stop_loss)

# AFTER: check adverse extreme first, then update trailing
sl_hit = check_sl(bar.low, pos.stop_loss)
if not sl_hit:
    update_trailing(bar.high, pos)   # only update if not stopped out
```

This may slightly improve (less pessimistic) backtest PnL. If forward walk results are being compared against historical backtests, note this change in the evaluation log — it affects result comparability.

---

### C2-39 [MEDIUM] Same-bar SL+TP — SL checked first (conservative, undocumented)

**File:** `backtest/engine.py:262-268`

No code change required. Add a comment:

```python
# When both SL and TP are breachable within the same bar, SL is checked first.
# This is a conservative (pessimistic) assumption — in reality, which fires first
# depends on intrabar price path which is not available at this resolution.
# If a more optimistic assumption is needed, use bar.open relative to entry
# to estimate which was likely hit first.
```

---

### C2-40 [MEDIUM] gross_profit / gross_loss use net PnL values — misleading names

**File:** `backtest/engine.py:403-405`

```python
# BEFORE:
gross_profit += trade.net_pnl_usd if trade.net_pnl_usd > 0 else 0
gross_loss   += trade.net_pnl_usd if trade.net_pnl_usd < 0 else 0

# AFTER:
net_profit += trade.net_pnl_usd if trade.net_pnl_usd > 0 else 0
net_loss   += trade.net_pnl_usd if trade.net_pnl_usd < 0 else 0
```

Update all downstream references (profit_factor calculation, reporting).

---

### C2-41 [LOW] Epsilon +0.01 allows 1% overage above limit

**File:** `risk_engine.py:315`

```python
# BEFORE:
if position_usd > limit * (1 + 0.01):   # allows 1% overage

# AFTER:
if position_usd > limit * (1 + 1e-9):   # floating-point tolerance only
```

---

### C2-42 [LOW] daily_loss_pct set to 0.0 in except block — masks actual loss

**File:** `risk_engine.py:329`

```python
# BEFORE:
except Exception:
    daily_loss_pct = 0.0   # masks the actual loss on error

# AFTER:
except Exception as e:
    logger.error("Failed to compute daily_loss_pct: %s — using last known value", e)
    daily_loss_pct = self._last_known_daily_loss_pct   # persist last known, don't zero
```

---

### C2-43 [LOW] rejection_history returns shallow copy — inner dicts are mutable references

**File:** `risk_engine.py:170-174`

```python
# BEFORE:
return list(self._rejection_history)   # inner dicts still mutable

# AFTER:
import copy
return copy.deepcopy(list(self._rejection_history))
```

---

### C2-45 [LOW] Rejection history size jump from 51 to 25 entries

**File:** `risk_engine.py:622-633`

```python
# BEFORE: manual pruning logic that jumps from 51 to 25
if len(self._rejection_history) > 50:
    self._rejection_history = self._rejection_history[-25:]

# AFTER:
from collections import deque
# At init:
self._rejection_history: deque = deque(maxlen=50)
# Append works automatically, no manual pruning needed
```

---

### C2-46 [LOW] PCA uses population variance, VaR uses sample variance — inconsistent

**File:** `risk_engine.py:867`

Decide on one convention and apply consistently. For financial risk calculations, sample variance (`ddof=1`) is standard. Update whichever is inconsistent:

```python
# For sample variance (ddof=1):
np.var(returns, ddof=1)   # VaR path
np.cov(returns_matrix, ddof=1)   # PCA path — note: np.cov uses ddof=1 by default
```

Add a module-level comment stating the convention:
```python
# Statistical convention: sample variance (ddof=1) throughout this module.
```

---

### C2-48 [LOW] ATR derived from stop distance, not actual ATR

**File:** `portfolio.py:128-130`

```python
# BEFORE: reverse-engineering ATR from stored stop distance — not ATR
derived_atr = abs(pos.entry_price - pos.stop_loss) / atr_multiplier

# AFTER: store actual ATR at trade entry
# In live_executor / engine, when recording a new position:
pos.entry_atr = ctx.atr   # store real ATR value
# In portfolio.py, use pos.entry_atr directly
```

This requires a schema addition to the position model. Add `entry_atr: float = 0.0` to avoid breaking existing state files.

---

### C2-49 [LOW] get_position_value logs warning on every call when price missing

**File:** `portfolio.py:308-316`

```python
# Add a rate-limit counter per symbol:
_missing_price_warned: dict[str, int] = {}

def get_position_value(self, pos) -> float:
    if pos.current_price is None:
        count = self._missing_price_warned.get(pos.symbol, 0) + 1
        self._missing_price_warned[pos.symbol] = count
        if count == 1 or count % 10 == 0:   # log first time and every 10th
            logger.warning("No price for %s (seen %d times)", pos.symbol, count)
        return pos.margin   # fallback
    self._missing_price_warned.pop(pos.symbol, None)   # reset on success
    return pos.margin + pos.unrealized_pnl
```

---

### C2-50 [LOW] Trade history serialization grows unbounded

**File:** `portfolio.py:447-448`

```python
MAX_TRADE_HISTORY = 1000   # configurable

# When appending:
self._trade_history.append(trade)
if len(self._trade_history) > MAX_TRADE_HISTORY:
    # Keep most recent, archive the rest
    overflow = self._trade_history[:-MAX_TRADE_HISTORY]
    self._trade_history = self._trade_history[-MAX_TRADE_HISTORY:]
    await self._archive_trades(overflow)   # write to rotating archive file
```

---

### C2-51 [LOW] Trailing state restored from JSON without validation

**File:** `portfolio.py:509`

```python
trailing_raw = state.get("trailing_state", {})
for trade_id, ts in trailing_raw.items():
    try:
        validated = TrailingState(**ts)
        self._trailing[trade_id] = validated
    except (TypeError, ValueError) as e:
        logger.warning("Discarding invalid trailing state for %s: %s", trade_id, e)
```

---

### C2-52 [LOW] Bare except swallows website sync errors silently

**File:** `engine.py:118-120`

```python
# BEFORE:
except:
    pass

# AFTER:
except Exception as e:
    logger.warning("Website sync failed: %s", e)
```

---

### C2-54 [LOW] _ohlcv_cache has no size bound independent of TTL

**File:** `engine.py:141`

```python
# BEFORE: TTL only, no size cap
self._ohlcv_cache: dict = {}

# AFTER: bounded + TTL
from functools import lru_cache
# Or manual approach:
MAX_OHLCV_CACHE = 200   # symbols × timeframes

def _set_ohlcv_cache(self, key, value):
    if len(self._ohlcv_cache) >= MAX_OHLCV_CACHE:
        # Evict oldest entry
        oldest = next(iter(self._ohlcv_cache))
        del self._ohlcv_cache[oldest]
    self._ohlcv_cache[key] = (value, time.monotonic())
```

---

### C2-55 [LOW] Live balance cache returns stale data on fetch failure

**File:** `engine.py:183`

```python
# BEFORE: stale data returned silently on error
except Exception:
    return self._cached_balance

# AFTER: stale data returned but staleness is visible
except Exception as e:
    age_s = time.monotonic() - self._balance_cache_time
    logger.warning(
        "Balance fetch failed (%s) — returning cached value (%.1fs old): %.2f",
        e, age_s, self._cached_balance
    )
    if age_s > 300:   # 5 minutes
        logger.error("Balance cache is >5m stale — risk calculations may be wrong")
    return self._cached_balance
```

---

### C2-56 [LOW] Spot SL/TP clientOid lacks uniqueness across trades

**File:** `live_executor.py:336-350`

```python
# BEFORE: non-unique clientOid
client_oid = f"sl_{symbol}_{int(time.time())}"

# AFTER: include trade_id for guaranteed uniqueness
client_oid = f"sl_{trade_id}_{int(time.time() * 1000)}"[:32]   # Bitget max length
```

---

### C2-57 [LOW] Hold mode detection uses hardcoded BTCUSDT probe

**File:** `live_executor.py:208-285`

```python
# BEFORE:
probe_symbol = "BTC/USDT:USDT"   # hardcoded

# AFTER:
probe_symbol = CONFIG.exchange.hold_mode_probe_symbol   # default: "BTC/USDT:USDT"
```

Add to config:
```python
hold_mode_probe_symbol: str = os.getenv("HOLD_MODE_PROBE_SYMBOL", "BTC/USDT:USDT")
```

---

### C2-58 [LOW] PnL percentage display ignores leverage

**File:** `live_executor.py:1971-1973`

```python
# BEFORE: pnl_pct = pnl / entry_value (ignores leverage — shows wrong %)
pnl_pct = pos.pnl / pos.notional

# AFTER: show both return on margin (what the trader feels) and return on notional
pnl_pct_margin = pos.pnl / pos.margin           # leveraged return — what hits the account
pnl_pct_notional = pos.pnl / pos.notional       # unleveraged return — comparable across leverage

# Display: "+5.2% (margin) / +1.0% (notional, 5× leverage)"
```

---

## Concurrent-caller test (C2-02 follow-up)

The v3.3.1 fix for the double-close race (C2-02) added a per-trade lock. Confirm test coverage exists before v3.4.0 ships:

```python
async def test_concurrent_close_position_no_double_close():
    """
    Two concurrent close_position calls on the same trade must result in exactly
    one close order submitted to the exchange, not two.
    """
    executor = LiveExecutor(...)
    pos = create_open_position(trade_id="test-001")
    executor._positions["test-001"] = pos

    close_order_count = 0
    async def mock_create_order(*args, **kwargs):
        nonlocal close_order_count
        close_order_count += 1
        await asyncio.sleep(0.01)   # simulate network latency
        return {"id": "order-001", "status": "closed"}

    executor.exchange.create_order = mock_create_order

    # Fire two concurrent closes
    await asyncio.gather(
        executor.close_position("test-001", reason="test_a"),
        executor.close_position("test-001", reason="test_b"),
    )

    assert close_order_count == 1, f"Expected 1 close order, got {close_order_count}"
    assert executor._positions["test-001"].status == "closed"
```

Place this test in `tests/test_live_executor.py`. If it fails, the lock implementation has a gap.

---

## Forward walk integrity note

If any Sprint 1 or 2 change alters the sizing, fill recording, or PnL attribution of trades, document it in the evaluation log with the commit SHA and the date deployed. The pre-registered evaluation criteria (PF ≥ 1.3, Sharpe ≥ 0.5) apply to the walk data as-collected — changes to how that data is recorded need to be disclosed even if they are correctness fixes.

Specifically:
- **C2-11 / C2-29** change position sizes for trades with size multipliers or strong-trend regime. Note the change date in the log.
- **C2-13** changes PnL attribution for reconciled trades. Any trade reconciled before this fix and after this fix may show different PnL for the same exchange outcome.
- **C2-38** (backtest) changes backtest PnL slightly — not walk data, but affects historical comparability.

---

## Definition of done for v3.4.0

- [ ] All 52 findings marked Closed in this document
- [ ] All 368 existing tests still passing
- [ ] New tests added for: C2-23 (deadlock), C2-27 (per-symbol fallback), C2-28 (VaR z-score), C2-12 (partial fill), C2-02 concurrent caller (if not already present), C2-13 (reconciliation with mock exchange history)
- [ ] Superseded claims section updated if any finding was invalidated before its sprint ran
- [ ] Forward walk evaluation log updated with any sizing or PnL-recording behaviour changes
- [ ] C2-34 (combined state file) migration verified on a copy of production state files before deploying to live

---

*Generated post v3.3.1 deployment — 2026-06-19*  
*Source: RUNECLAW Deep Audit v2 (61 findings, 4 parallel audit agents)*  
*v3.3.1 closed: C2-01, C2-02, C2-03, C2-04, C2-09, C2-10, C2-17, C2-24, C2-25*
