"""
RUNECLAW Portfolio Tracker -- paper trading ledger.

Upgraded with:
  - Entry price validation (prevents division by zero)
  - Proper daily PnL tracking with date boundaries
  - Peak equity tracking that's robust to edge cases
  - Trade result callback to risk engine for streak tracking
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from typing import Optional, Callable

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log
from bot.utils.models import (
    Direction, PortfolioState, TradeExecution, TradeIdea, TradeStatus,
)


class PortfolioTracker:
    """In-memory paper trading portfolio with PnL tracking."""

    def __init__(
        self,
        initial_balance: Optional[float] = None,
        on_trade_close: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.balance = initial_balance or CONFIG.paper_balance_usd
        self._initial_balance = self.balance
        self._peak_equity = self.balance
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

    # -- Public API --

    def open_position(self, idea: TradeIdea, size_usd: float) -> TradeExecution:
        """Open a new paper position from an approved TradeIdea."""
        with self._lock:
            return self._open_position_locked(idea, size_usd)

    def _open_position_locked(self, idea: TradeIdea, size_usd: float) -> TradeExecution:
        # Guard: prevent division by zero or negative entry
        if idea.entry_price <= 0:
            audit(trade_log, f"Invalid entry price: {idea.entry_price}",
                  action="open_position", result="REJECTED")
            raise ValueError(f"Entry price must be positive, got {idea.entry_price}")

        # Guard: don't exceed balance
        if size_usd > self.balance:
            size_usd = self.balance  # cap at available balance

        if size_usd <= 0:
            audit(trade_log, "Insufficient balance for position",
                  action="open_position", result="REJECTED")
            raise ValueError("Insufficient balance to open position")

        qty = size_usd / idea.entry_price

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
            opened_at=datetime.now(UTC),
        )

        self.balance -= size_usd
        self._positions[idea.id] = trade

        # STRATEGY: trailing stop after 1R profit -- initialize tracking
        initial_risk = abs(idea.entry_price - idea.stop_loss)
        self._trailing_state[idea.id] = {
            "best_price": idea.entry_price,
            "trailing_active": False,
            "initial_risk": initial_risk,
            "atr": initial_risk / 2.5 if initial_risk > 0 else idea.entry_price * 0.02,
        }

        audit(trade_log, f"Opened {trade.direction.value} {trade.asset}",
              action="open_position", result="EXECUTED",
              data={"trade_id": trade.trade_id, "size_usd": round(size_usd, 2),
                    "qty": round(qty, 8), "entry": idea.entry_price})
        return trade

    def close_position(self, trade_id: str, exit_price: float) -> Optional[TradeExecution]:
        """Close an existing position at the given price."""
        with self._lock:
            return self._close_position_locked(trade_id, exit_price)

    def _close_position_locked(self, trade_id: str, exit_price: float) -> Optional[TradeExecution]:
        trade = self._positions.pop(trade_id, None)
        if trade is None:
            return None

        # Clean up trailing state
        self._trailing_state.pop(trade_id, None)

        # Guard: exit price must be positive
        if exit_price <= 0:
            audit(trade_log, f"Invalid exit price: {exit_price}",
                  action="close_position", result="ERROR")
            self._positions[trade_id] = trade  # put it back
            return None

        if trade.direction == Direction.LONG:
            pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            pnl = (trade.entry_price - exit_price) * trade.quantity

        size_usd = trade.entry_price * trade.quantity
        trade = trade.model_copy(update={
            "status": TradeStatus.EXECUTED,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "closed_at": datetime.now(UTC),
        })

        self.balance += size_usd + pnl
        self._history.append(trade)
        self._record_daily_pnl(pnl)
        self._update_peak()

        # Notify risk engine of trade result for streak tracking
        if self._on_trade_close:
            try:
                self._on_trade_close(pnl)
            except Exception as exc:
                audit(trade_log, f"Trade close callback error: {exc}",
                      action="trade_close_callback", result="ERROR")

        audit(trade_log, f"Closed {trade.asset} PnL=${pnl:.2f}",
              action="close_position", result="CLOSED",
              data={"trade_id": trade_id, "pnl": round(pnl, 2),
                    "exit": exit_price, "balance": round(self.balance, 2)})
        return trade

    def check_stops(self, prices: dict[str, float]) -> list[TradeExecution]:
        """Check all open positions against current prices for SL/TP hits.
        Includes trailing stop logic for live/paper trading."""
        closed: list[TradeExecution] = []
        for tid, pos in list(self._positions.items()):
            price = prices.get(pos.asset)
            if price is None or price <= 0:
                continue

            sl = pos.stop_loss

            # STRATEGY: trailing stop after 1R profit
            # Update best_price, activate trailing when profit >= 1R,
            # then trail at 1.5x ATR behind the best price
            ts = self._trailing_state.get(tid)
            if ts is not None:
                atr = ts.get("atr", 0)
                initial_risk = ts.get("initial_risk", 0)

                if pos.direction == Direction.LONG:
                    if price > ts["best_price"]:
                        ts["best_price"] = price
                    if not ts["trailing_active"] and initial_risk > 0:
                        if ts["best_price"] - pos.entry_price >= initial_risk:
                            ts["trailing_active"] = True
                    if ts["trailing_active"] and atr > 0:
                        trailing_sl = ts["best_price"] - 1.5 * atr
                        if trailing_sl > sl:
                            sl = trailing_sl
                else:
                    if price < ts["best_price"]:
                        ts["best_price"] = price
                    if not ts["trailing_active"] and initial_risk > 0:
                        if pos.entry_price - ts["best_price"] >= initial_risk:
                            ts["trailing_active"] = True
                    if ts["trailing_active"] and atr > 0:
                        trailing_sl = ts["best_price"] + 1.5 * atr
                        if trailing_sl < sl:
                            sl = trailing_sl

            hit_sl = (price <= sl) if pos.direction == Direction.LONG else (price >= sl)
            hit_tp = (price >= pos.take_profit) if pos.direction == Direction.LONG else (price <= pos.take_profit)
            if hit_sl or hit_tp:
                result = self.close_position(tid, price)
                if result:
                    closed.append(result)
        return closed

    def snapshot(self) -> PortfolioState:
        """Current portfolio state."""
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> PortfolioState:
        open_value = sum(
            p.entry_price * p.quantity for p in self._positions.values()
        )
        equity = self.balance + open_value
        self._update_peak()

        wins = [t for t in self._history if t.pnl > 0]
        total = len(self._history)
        today_key = date.today().isoformat()
        daily_pnl = self._daily_pnl.get(today_key, 0.0)
        drawdown = ((self._peak_equity - equity) / self._peak_equity * 100) if self._peak_equity > 0 else 0

        return PortfolioState(
            balance_usd=round(self.balance, 2),
            equity_usd=round(equity, 2),
            open_positions=len(self._positions),
            total_trades=total,
            win_rate=round(len(wins) / total, 2) if total > 0 else 0.0,
            total_pnl=round(sum(t.pnl for t in self._history), 2),
            daily_pnl=round(daily_pnl, 2),
            max_drawdown_pct=round(max(drawdown, 0), 2),
        )

    @property
    def open_positions(self) -> list[TradeExecution]:
        return list(self._positions.values())

    @property
    def trade_history(self) -> list[TradeExecution]:
        return list(self._history)

    # -- Internal --

    def _record_daily_pnl(self, pnl: float) -> None:
        key = date.today().isoformat()
        self._daily_pnl[key] = self._daily_pnl.get(key, 0.0) + pnl

    def _update_peak(self) -> None:
        open_val = sum(p.entry_price * p.quantity for p in self._positions.values())
        equity = self.balance + open_val
        if equity > self._peak_equity:
            self._peak_equity = equity
