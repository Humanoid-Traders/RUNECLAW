"""
RUNECLAW Portfolio Tracker -- paper trading ledger.

Upgraded with:
  - Entry price validation (prevents division by zero)
  - Proper daily PnL tracking with date boundaries
  - Peak equity tracking that's robust to edge cases
  - Trade result callback to risk engine for streak tracking
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from bot.compat import UTC
from pathlib import Path
from typing import Any, Optional, Callable

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log
from bot.utils.models import (
    Direction, PortfolioState, TradeExecution, TradeIdea, TradeStatus,
)
from bot.utils.trailing import make_trailing_state, update_trailing_stop


@dataclass
class TrailingStopConfig:
    """Configuration for the enhanced trailing stop engine."""
    activation_pct: float = 50.0         # activate after price reaches 50% of TP distance
    trail_distance_atr_mult: float = 2.0  # trail at ATR * this multiplier
    min_profit_lock_pct: float = 0.3      # minimum profit to lock in (0.3% of entry price)


class PortfolioTracker:
    """In-memory paper trading portfolio with PnL tracking."""

    def __init__(
        self,
        initial_balance: Optional[float] = None,
        on_trade_close: Optional[Callable[[float], None]] = None,
        state_file: Optional[str] = None,
        trailing_config: Optional[TrailingStopConfig] = None,
    ) -> None:
        # C2-47 FIX: Use `is not None` instead of `or` to allow explicit 0.0
        self.balance = initial_balance if initial_balance is not None else CONFIG.paper_balance_usd
        self.trailing_config = trailing_config or TrailingStopConfig()
        self._initial_balance = self.balance
        self._peak_equity = self.balance
        self._max_drawdown_ever: float = 0.0  # M-01: track historical max drawdown
        self._last_daily_reset: Optional[str] = None  # M-08: track last daily PnL reset date
        self._positions: dict[str, TradeExecution] = {}
        self._history: list[TradeExecution] = []
        self._daily_pnl: dict[str, float] = {}  # date-string -> pnl
        self._on_trade_close = on_trade_close  # callback for risk engine streak tracking
        self._lock = threading.RLock()
        # STRATEGY: trailing stop after 1R profit
        # Tracks best favorable price and trailing state per position
        # Keys: trade_id -> {"best_price": float, "trailing_active": bool,
        #                     "initial_risk": float, "atr": float}
        self._trailing_state: dict[str, dict] = {}
        # Mark-to-market: latest prices for unrealized PnL
        self._last_prices: dict[str, float] = {}  # asset -> price
        # C2-49: rate-limit missing price warnings per symbol
        self._missing_price_warned: dict[str, int] = {}
        # C2-34: combined state saver — when set, _auto_save delegates to this
        # instead of writing portfolio_state.json independently
        self._combined_saver: Optional[Callable[[], None]] = None
        # Persistence: only auto-load if no explicit initial_balance was given
        # (explicit balance = test/reset mode; default = production mode)
        self._state_file: str = state_file or CONFIG.portfolio_state_file
        self._persistence_active: bool = False  # enabled after successful load or explicit save
        if initial_balance is None:
            self._load_state_on_init()
        if state_file is not None:
            self._persistence_active = True

    # -- Public API --

    def open_position(self, idea: TradeIdea, size_usd: float, leverage: int = 1) -> TradeExecution:
        """Open a new paper position from an approved TradeIdea."""
        with self._lock:
            result = self._open_position_locked(idea, size_usd, leverage)
            self._auto_save()
            return result

    def _open_position_locked(self, idea: TradeIdea, size_usd: float, leverage: int = 1) -> TradeExecution:
        # Guard: prevent division by zero or negative entry
        if idea.entry_price <= 0:
            audit(trade_log, f"Invalid entry price: {idea.entry_price}",
                  action="open_position", result="REJECTED")
            raise ValueError(f"Entry price must be positive, got {idea.entry_price}")

        # C2-15 FIX: Reject (not clamp) when size exceeds balance.
        # Silent clamping causes the risk engine's exposure tracking to diverge
        # from actual position size — a phantom position.
        if size_usd > self.balance:
            audit(trade_log,
                  f"Position size ${size_usd:.2f} exceeds available balance ${self.balance:.2f}",
                  action="open_position", result="REJECTED",
                  data={"requested": round(size_usd, 2), "balance": round(self.balance, 2)})
            raise ValueError(
                f"Insufficient balance: position ${size_usd:.2f} exceeds available ${self.balance:.2f}"
            )

        if size_usd <= 0:
            audit(trade_log, "Insufficient balance for position",
                  action="open_position", result="REJECTED")
            raise ValueError("Insufficient balance to open position")

        # For futures with leverage: size_usd is the margin (collateral).
        # Notional = margin * leverage, qty = notional / price.
        notional = size_usd * leverage
        qty = notional / idea.entry_price

        trade = TradeExecution(
            trade_id=idea.id,
            asset=idea.asset,
            direction=idea.direction,
            entry_price=idea.entry_price,
            quantity=round(qty, 8),
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            status=TradeStatus.EXECUTED,
            is_paper=True,
            leverage=leverage,
            opened_at=datetime.now(UTC),
        )

        self.balance -= size_usd
        self._positions[idea.id] = trade

        # STRATEGY: trailing stop after 1R profit -- initialize tracking
        initial_risk = abs(idea.entry_price - idea.stop_loss)
        # M2 fix: read sl_mult from config instead of hardcoding
        sl_mult = CONFIG.analyzer.sl_atr_mult_default
        canonical_atr = initial_risk / sl_mult if initial_risk > 0 else idea.entry_price * 0.02
        ts = make_trailing_state(idea.entry_price, idea.direction.value, initial_risk, canonical_atr)
        ts["entry_price"] = idea.entry_price  # needed for activation check
        self._trailing_state[idea.id] = ts

        audit(trade_log, f"Opened {trade.direction.value} {trade.asset}",
              action="open_position", result="EXECUTED",
              data={"trade_id": trade.trade_id, "size_usd": round(size_usd, 2),
                    "qty": round(qty, 8), "entry": idea.entry_price})
        return trade

    def close_position(self, trade_id: str, exit_price: float) -> Optional[TradeExecution]:
        """Close an existing position at the given price.
        C2-23 FIX: callback is deferred to AFTER the lock is released,
        preventing the A→B / B→A deadlock between portfolio._lock and risk._lock."""
        net_pnl = None
        with self._lock:
            result = self._close_position_locked(trade_id, exit_price)
            if result is not None:
                net_pnl = result.pnl
                self._auto_save()

        # C2-23: Call _on_trade_close OUTSIDE the lock to prevent deadlock.
        # Lock ordering contract: risk._lock → portfolio._lock (never reversed).
        if net_pnl is not None and self._on_trade_close:
            try:
                self._on_trade_close(net_pnl)
            except Exception as exc:
                audit(trade_log, f"Trade close callback error: {exc}",
                      action="trade_close_callback", result="ERROR")

        return result

    def _close_position_locked(self, trade_id: str, exit_price: float) -> Optional[TradeExecution]:
        trade = self._positions.pop(trade_id, None)
        if trade is None:
            return None

        # H-13 FIX: Guard exit price BEFORE popping trailing state,
        # so invalid exit doesn't lose trailing data.
        # Guard: exit price must be positive
        if exit_price <= 0:
            audit(trade_log, f"Invalid exit price: {exit_price}",
                  action="close_position", result="ERROR")
            self._positions[trade_id] = trade  # put it back
            return None

        # Clean up trailing state (only after validation passes)
        self._trailing_state.pop(trade_id, None)

        if trade.direction == Direction.LONG:
            pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            pnl = (trade.entry_price - exit_price) * trade.quantity

        size_usd = trade.entry_price * trade.quantity
        exit_notional = exit_price * trade.quantity
        # Margin is the actual collateral locked (notional / leverage)
        lev = getattr(trade, 'leverage', 1) or 1
        margin_usd = size_usd / lev

        # Exchange commission: 0.1% taker fee each side (entry + exit)
        commission_pct = CONFIG.risk.commission_pct
        commission = (size_usd + exit_notional) * (commission_pct / 100.0)
        net_pnl = pnl - commission

        trade = trade.model_copy(update={
            "status": TradeStatus.EXECUTED,
            "exit_price": exit_price,
            "pnl": round(net_pnl, 2),
            "gross_pnl": round(pnl, 2),
            "commission": round(commission, 2),
            "closed_at": datetime.now(UTC),
        })

        self.balance += margin_usd + net_pnl
        # H-12 FIX: Clamp balance to zero to prevent negative balance from leveraged losses
        if self.balance < 0:
            audit(trade_log, f"Balance went negative ({self.balance:.2f}), clamping to 0",
                  action="close_position", result="BALANCE_CLAMPED")
            self.balance = 0.0
        self._history.append(trade)
        # C2-50 FIX: Cap trade history to prevent unbounded memory/serialization growth
        if len(self._history) > 1000:
            self._history = self._history[-1000:]
        self._record_daily_pnl(net_pnl)
        self._update_peak()

        # C2-23: _on_trade_close callback is now called by close_position()
        # AFTER the lock is released, to prevent lock ordering inversion.

        audit(trade_log, f"Closed {trade.asset} PnL=${net_pnl:.2f} (gross=${pnl:.2f}, comm=${commission:.2f})",
              action="close_position", result="CLOSED",
              data={"trade_id": trade_id, "pnl": round(net_pnl, 2),
                    "gross_pnl": round(pnl, 2), "commission": round(commission, 2),
                    "exit": exit_price, "balance": round(self.balance, 2)})
        return trade

    def check_stops(self, prices: dict[str, float]) -> list[TradeExecution]:
        """Check all open positions against current prices for SL/TP hits.
        Includes trailing stop logic for live/paper trading.
        C2-23 FIX: callbacks deferred to after lock release."""
        closed: list[TradeExecution] = []
        with self._lock:
            for tid, pos in list(self._positions.items()):
                price = prices.get(pos.asset)
                if price is None or price <= 0:
                    continue

                sl = pos.stop_loss

                # STRATEGY: trailing stop via shared utility
                ts = self._trailing_state.get(tid)
                if ts is not None:
                    ts["entry_price"] = pos.entry_price  # ensure entry_price is set
                    sl, _ = update_trailing_stop(ts, price, sl, pos.direction.value)

                hit_sl = (price <= sl) if pos.direction == Direction.LONG else (price >= sl)
                hit_tp = (price >= pos.take_profit) if pos.direction == Direction.LONG else (price <= pos.take_profit)
                if hit_sl or hit_tp:
                    result = self._close_position_locked(tid, price)
                    if result:
                        closed.append(result)
            if closed:
                self._auto_save()

        # C2-23: Notify risk engine OUTSIDE the lock for each closed trade
        if closed and self._on_trade_close:
            for trade in closed:
                try:
                    self._on_trade_close(trade.pnl)
                except Exception as exc:
                    audit(trade_log, f"Trade close callback error: {exc}",
                          action="trade_close_callback", result="ERROR")

        return closed

    def snapshot(self) -> PortfolioState:
        """Current portfolio state."""
        with self._lock:
            return self._snapshot_locked()

    def mark_to_market(self, prices: dict[str, float]) -> None:
        """Update last-known prices for unrealized PnL computation.
        Call this before snapshot() whenever fresh prices are available."""
        with self._lock:
            for asset, price in prices.items():
                if price > 0:
                    self._last_prices[asset] = price
            # LB-4 FIX: Update peak equity on every mark-to-market tick.
            # Without this, peak only updates on trade close / snapshot,
            # so intra-bar equity highs are missed and drawdown is overstated.
            self._update_peak()

    # C-02 FIX: Removed _update_trailing_stops_locked() and its public wrapper
    # update_trailing_stops(). The canonical trailing stop system lives in
    # bot.utils.trailing and is invoked via check_stops(). Having a second
    # trailing stop engine here caused dual-update conflicts with different
    # activation criteria and trail distances.

    def get_trailing_status(self) -> dict:
        """Return current trailing stop info for all open positions.
        C2-37 FIX: current_sl reflects the trailing-adjusted stop, not the original."""
        with self._lock:
            status = {}
            for tid, pos in self._positions.items():
                ts = self._trailing_state.get(tid, {})
                price = self._last_prices.get(pos.asset, pos.entry_price)
                # C2-37: Compute the effective SL from trailing state
                effective_sl = pos.stop_loss
                if ts:
                    ts_copy = dict(ts)
                    ts_copy["entry_price"] = pos.entry_price
                    from bot.utils.trailing import update_trailing_stop
                    computed_sl, _ = update_trailing_stop(
                        ts_copy, price, pos.stop_loss, pos.direction.value
                    )
                    effective_sl = computed_sl
                status[tid] = {
                    "asset": pos.asset,
                    "direction": pos.direction.value,
                    "entry_price": pos.entry_price,
                    "current_price": price,
                    "current_sl": effective_sl,
                    "original_sl": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "trailing_active": ts.get("trailing_active", False),
                    "best_price": ts.get("best_price", pos.entry_price),
                    "atr": ts.get("atr", 0),
                }
            return status

    def get_position_value(self, asset: str | None = None) -> float:
        """Public API for mark-to-market position value.

        If *asset* is given, return value for that asset only.
        Otherwise return total open position value.
        Used by risk engine for exposure checks (replaces private _last_prices access).

        C-01 FIX: Returns margin + unrealized PnL, not full notional,
        so leveraged positions are valued correctly.
        """
        with self._lock:
            total = 0.0
            for p in self._positions.values():
                if asset is not None and p.asset != asset:
                    continue
                price = self._last_prices.get(p.asset, None)
                if price is None:
                    price = p.entry_price
                    # C2-49 FIX: rate-limit warning — log first occurrence and every 10th
                    count = self._missing_price_warned.get(p.asset, 0) + 1
                    self._missing_price_warned[p.asset] = count
                    if count == 1 or count % 10 == 0:
                        logging.getLogger(__name__).warning(
                            "No market price for %s — falling back to entry price %.8f (stale valuation, seen %d times)",
                            p.asset, p.entry_price, count,
                        )
                else:
                    self._missing_price_warned.pop(p.asset, None)  # reset on success
                lev = getattr(p, 'leverage', 1) or 1
                margin = p.entry_price * p.quantity / lev
                if p.direction == Direction.LONG:
                    upnl = (price - p.entry_price) * p.quantity
                else:
                    upnl = (p.entry_price - price) * p.quantity
                total += margin + upnl
            return total

    def _snapshot_locked(self) -> PortfolioState:
        # C-01 FIX: For leveraged positions, open_value should be margin + unrealized PnL,
        # not full notional. open_position() deducts margin (= entry_price * qty / leverage)
        # from balance, so equity = balance + sum(margin + unrealized) for each position.
        open_value = 0.0
        unrealized_pnl = 0.0
        for p in self._positions.values():
            current_price = self._last_prices.get(p.asset, None)
            if current_price is None:
                current_price = p.entry_price
                # C2-49 FIX: rate-limit warning (shares counter with get_position_value)
                count = self._missing_price_warned.get(p.asset, 0) + 1
                self._missing_price_warned[p.asset] = count
                if count == 1 or count % 10 == 0:
                    logging.getLogger(__name__).warning(
                        "No market price for %s — falling back to entry price %.8f (stale valuation, seen %d times)",
                        p.asset, p.entry_price, count,
                    )
            else:
                self._missing_price_warned.pop(p.asset, None)
            lev = getattr(p, 'leverage', 1) or 1
            margin = p.entry_price * p.quantity / lev
            if p.direction == Direction.LONG:
                upnl = (current_price - p.entry_price) * p.quantity
            else:
                upnl = (p.entry_price - current_price) * p.quantity
            unrealized_pnl += upnl
            open_value += margin + upnl

        equity = self.balance + open_value
        self._update_peak()

        wins = [t for t in self._history if t.pnl > 0]
        total = len(self._history)
        # M5 fix: use UTC date, not local timezone
        today_key = datetime.now(UTC).date().isoformat()

        # M-08 FIX: Reset daily realized PnL when the date rolls over
        if self._last_daily_reset is not None and self._last_daily_reset != today_key:
            # New day: previous day's realized PnL is already stored in _daily_pnl
            # under its own date key. Nothing to zero out -- just note the transition.
            pass
        self._last_daily_reset = today_key

        realized_daily = self._daily_pnl.get(today_key, 0.0)
        # Include unrealized PnL in daily figure for conservative risk management:
        # this ensures risk checks account for paper losses before they are realized,
        # preventing the system from opening new positions while sitting on large
        # unrealized drawdowns that could breach daily loss limits on close.
        daily_pnl = realized_daily + unrealized_pnl
        # M-01 FIX: Use historical max drawdown, not just current drawdown
        current_drawdown = ((self._peak_equity - equity) / self._peak_equity * 100) if self._peak_equity > 0 else 0
        max_drawdown = max(self._max_drawdown_ever, current_drawdown, 0)

        # M-02 NOTE: Commission is deferred to exit (charged as entry + exit fee at close time).
        # This is a deliberate design choice for PnL reconciliation with exchange statements.
        # Unrealized PnL therefore does not include commission drag -- acceptable tradeoff.
        total_gross = round(sum(t.gross_pnl for t in self._history), 2)
        total_commission = round(sum(t.commission for t in self._history), 2)

        return PortfolioState(
            balance_usd=round(self.balance, 2),
            equity_usd=round(equity, 2),
            open_positions=len(self._positions),
            total_trades=total,
            win_rate=round(len(wins) / total, 2) if total > 0 else 0.0,
            total_pnl=round(sum(t.pnl for t in self._history), 2),
            total_gross_pnl=total_gross,
            total_commission=total_commission,
            daily_pnl=round(daily_pnl, 2),
            max_drawdown_pct=round(max_drawdown, 2),
        )

    @property
    def open_positions(self) -> list[TradeExecution]:
        with self._lock:
            return list(self._positions.values())

    @property
    def trade_history(self) -> list[TradeExecution]:
        with self._lock:
            return list(self._history)

    # -- Internal --

    def _record_daily_pnl(self, pnl: float) -> None:
        # M5 fix: use UTC date, not local timezone
        key = datetime.now(UTC).date().isoformat()
        self._daily_pnl[key] = self._daily_pnl.get(key, 0.0) + pnl
        # L6 fix: prune entries older than 30 days to prevent unbounded growth
        if len(self._daily_pnl) > 30:
            sorted_keys = sorted(self._daily_pnl.keys())
            for old_key in sorted_keys[:-30]:
                del self._daily_pnl[old_key]

    def _update_peak(self) -> None:
        # C-01 FIX: Use margin + unrealized PnL, not full notional
        open_val = 0.0
        for p in self._positions.values():
            price = self._last_prices.get(p.asset, p.entry_price)
            lev = getattr(p, 'leverage', 1) or 1
            margin = p.entry_price * p.quantity / lev
            if p.direction == Direction.LONG:
                upnl = (price - p.entry_price) * p.quantity
            else:
                upnl = (p.entry_price - price) * p.quantity
            open_val += margin + upnl
        equity = self.balance + open_val
        if equity > self._peak_equity:
            self._peak_equity = equity
        # M-01 FIX: Track historical max drawdown
        if self._peak_equity > 0:
            current_dd = (self._peak_equity - equity) / self._peak_equity * 100
            if current_dd > self._max_drawdown_ever:
                self._max_drawdown_ever = current_dd

    # -- Persistence --

    def save_state(self, path: Optional[str] = None) -> None:
        """Serialize full portfolio state to a JSON file (thread-safe).
        Also enables auto-save for subsequent trade executions."""
        with self._lock:
            self._save_state_locked(path)
            self._persistence_active = True

    def _save_state_locked(self, path: Optional[str] = None) -> None:
        target = path or self._state_file
        state: dict[str, Any] = {
            "schema_version": 1,  # F-09 FIX: version tag for future migrations
            "balance": self.balance,
            "initial_balance": self._initial_balance,
            "peak_equity": self._peak_equity,
            "max_drawdown_ever": self._max_drawdown_ever,
            "last_daily_reset": self._last_daily_reset,
            "positions": {
                tid: t.model_dump(mode="json") for tid, t in self._positions.items()
            },
            "history": [t.model_dump(mode="json") for t in self._history],
            "daily_pnl": dict(self._daily_pnl),
            "trailing_state": dict(self._trailing_state),
            "last_prices": dict(self._last_prices),
            "saved_at": datetime.now(UTC).isoformat(),
        }
        try:
            target_path = Path(target)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            # F-09 FIX: keep one backup of the previous state
            if target_path.exists():
                backup = target_path.with_suffix(".json.bak")
                try:
                    import shutil
                    shutil.copy2(str(target_path), str(backup))
                except Exception as e:
                    trade_log.debug("Best-effort backup copy failed: %s", e)
            tmp = str(target_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)
                # C2-33 FIX: Flush + fsync before os.replace to prevent
                # partial writes on crash. Matches risk_engine.py pattern.
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(target_path))
        except Exception as exc:
            audit(trade_log, f"Failed to save portfolio state: {exc}",
                  action="save_state", result="ERROR")

    def load_state(self, path: Optional[str] = None) -> bool:
        """Deserialize portfolio state from a JSON file (thread-safe).
        Returns True if state was loaded, False if starting fresh."""
        with self._lock:
            return self._load_state_locked(path)

    def _load_state_locked(self, path: Optional[str] = None) -> bool:
        target = path or self._state_file
        target_path = Path(target)
        if not target_path.exists():
            return False
        try:
            with open(target_path, "r") as f:
                data = json.load(f)
            # F-09 FIX: validate required schema fields before loading
            if "balance" not in data:
                raise ValueError("Missing 'balance' field in state file")
            self.balance = float(data["balance"])
            self._initial_balance = float(data.get("initial_balance", self.balance))
            self._peak_equity = float(data.get("peak_equity", self.balance))
            self._max_drawdown_ever = float(data.get("max_drawdown_ever", 0.0))
            self._last_daily_reset = data.get("last_daily_reset", None)
            # Restore open positions
            self._positions = {}
            for tid, tdata in data.get("positions", {}).items():
                self._positions[tid] = TradeExecution.model_validate(tdata)
            # Restore trade history
            self._history = [
                TradeExecution.model_validate(t) for t in data.get("history", [])
            ]
            # Restore daily PnL
            self._daily_pnl = {
                k: float(v) for k, v in data.get("daily_pnl", {}).items()
            }
            # Restore trailing state (C2-51 FIX: validate required keys)
            raw_trailing = data.get("trailing_state", {})
            self._trailing_state = {}
            _required_trailing_keys = {"best_price", "trailing_active", "initial_risk", "atr"}
            for tid, ts in raw_trailing.items():
                if not isinstance(ts, dict):
                    trade_log.warning("Discarding invalid trailing state for %s: not a dict", tid)
                    continue
                missing = _required_trailing_keys - set(ts.keys())
                if missing:
                    trade_log.warning("Discarding incomplete trailing state for %s: missing %s", tid, missing)
                    continue
                self._trailing_state[tid] = ts
            # Restore last prices
            self._last_prices = {
                k: float(v) for k, v in data.get("last_prices", {}).items()
            }
            audit(trade_log, f"Loaded portfolio state from {target}",
                  action="load_state", result="OK",
                  data={"balance": self.balance,
                        "open_positions": len(self._positions),
                        "history_count": len(self._history)})
            return True
        except Exception as exc:
            # F-09 FIX: Attempt backup recovery before starting fresh
            backup_path = target_path.with_suffix(".json.bak")
            if backup_path.exists() and path != str(backup_path):
                audit(trade_log,
                      f"Primary state corrupted ({exc}), trying backup {backup_path}",
                      action="load_state", result="TRYING_BACKUP",
                      level=logging.WARNING)
                recovered = self._load_state_locked(str(backup_path))
                if recovered:
                    audit(trade_log, "Recovered portfolio state from backup",
                          action="load_state", result="RECOVERED")
                    return True
            audit(trade_log,
                  f"CRITICAL: Corrupted state file {target}, starting fresh: {exc}",
                  action="load_state", result="CORRUPTED",
                  level=logging.CRITICAL,
                  data={"file": str(target), "error": str(exc)})
            return False

    def _export_state_dict(self) -> dict[str, Any]:
        """C2-34: Extract portfolio state as a dict without writing to disk.
        Must be called with self._lock held."""
        return {
            "schema_version": 1,
            "balance": self.balance,
            "initial_balance": self._initial_balance,
            "peak_equity": self._peak_equity,
            "max_drawdown_ever": self._max_drawdown_ever,
            "last_daily_reset": self._last_daily_reset,
            "positions": {
                tid: t.model_dump(mode="json") for tid, t in self._positions.items()
            },
            "history": [t.model_dump(mode="json") for t in self._history],
            "daily_pnl": dict(self._daily_pnl),
            "trailing_state": dict(self._trailing_state),
            "last_prices": dict(self._last_prices),
        }

    def _load_from_state_dict(self, data: dict[str, Any]) -> None:
        """C2-34: Restore portfolio from a state dict (no file I/O).
        Must be called with self._lock held."""
        if "balance" not in data:
            raise ValueError("Missing 'balance' field in state dict")
        self.balance = float(data["balance"])
        self._initial_balance = float(data.get("initial_balance", self.balance))
        self._peak_equity = float(data.get("peak_equity", self.balance))
        self._max_drawdown_ever = float(data.get("max_drawdown_ever", 0.0))
        self._last_daily_reset = data.get("last_daily_reset", None)
        self._positions = {}
        for tid, tdata in data.get("positions", {}).items():
            self._positions[tid] = TradeExecution.model_validate(tdata)
        self._history = [
            TradeExecution.model_validate(t) for t in data.get("history", [])
        ]
        self._daily_pnl = {
            k: float(v) for k, v in data.get("daily_pnl", {}).items()
        }
        # C2-51 FIX: validate trailing state keys on restore (same as _load_state_locked)
        raw_trailing = data.get("trailing_state", {})
        self._trailing_state = {}
        _required_trailing_keys = {"best_price", "trailing_active", "initial_risk", "atr"}
        for tid, ts in raw_trailing.items():
            if not isinstance(ts, dict):
                trade_log.warning("Discarding invalid trailing state for %s: not a dict", tid)
                continue
            missing = _required_trailing_keys - set(ts.keys())
            if missing:
                trade_log.warning("Discarding incomplete trailing state for %s: missing %s", tid, missing)
                continue
            self._trailing_state[tid] = ts
        self._last_prices = {
            k: float(v) for k, v in data.get("last_prices", {}).items()
        }

    def _auto_save(self) -> None:
        """Save state after trade execution. Called within the lock.
        Only active if state was loaded on init or persistence was explicitly enabled.
        C2-34: delegates to combined saver when wired, for atomic consistency."""
        if not self._persistence_active:
            return
        try:
            if self._combined_saver is not None:
                self._combined_saver()
            else:
                self._save_state_locked()
        except Exception as exc:
            audit(trade_log, f"Auto-save failed: {exc}",
                  action="auto_save", result="ERROR")

    def _load_state_on_init(self) -> None:
        """Attempt to load persisted state on construction."""
        target_path = Path(self._state_file)
        if target_path.exists():
            loaded = self._load_state_locked()
            if loaded:
                self._persistence_active = True
                audit(trade_log,
                      "Portfolio state restored from disk",
                      action="init", result="RESTORED")
