"""
RUNECLAW Metrics Engine -- performance analytics and tracking.
Computes trading performance metrics from portfolio history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from bot.utils.models import MetricsSnapshot, TradeExecution


class MetricsEngine:
    """Computes and tracks trading performance metrics."""

    def __init__(self) -> None:
        self._equity_curve: list[float] = []
        self._timestamps: list[datetime] = []
        self._risk_checks_total: int = 0
        self._risk_checks_rejected: int = 0
        self._circuit_breaker_trips: int = 0

    def record_equity(self, equity: float, ts: Optional[datetime] = None) -> None:
        """Record an equity data point."""
        self._equity_curve.append(equity)
        self._timestamps.append(ts or datetime.utcnow())

    def record_risk_check(self, rejected: bool) -> None:
        self._risk_checks_total += 1
        if rejected:
            self._risk_checks_rejected += 1

    def record_circuit_breaker_trip(self) -> None:
        self._circuit_breaker_trips += 1

    def compute(self, trades: list[TradeExecution]) -> MetricsSnapshot:
        """Compute full metrics from trade history."""
        closed = [t for t in trades if t.closed_at is not None]

        total = len(closed)
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]

        win_pnls = [t.pnl for t in wins]
        loss_pnls = [t.pnl for t in losses]

        # Win rate
        win_rate = len(wins) / total if total > 0 else 0.0

        # Averages
        avg_win = float(np.mean(win_pnls)) if win_pnls else 0.0
        avg_loss = float(np.mean(loss_pnls)) if loss_pnls else 0.0

        # Extremes
        largest_win = max(win_pnls) if win_pnls else 0.0
        largest_loss = min(loss_pnls) if loss_pnls else 0.0

        # Profit factor
        gross_profit = sum(win_pnls) if win_pnls else 0.0
        gross_loss = abs(sum(loss_pnls)) if loss_pnls else 0.0
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else float("inf") if gross_profit > 0 else 0.0
        )

        # Holding period
        holding_hours: list[float] = []
        for t in closed:
            if t.opened_at and t.closed_at:
                delta = (t.closed_at - t.opened_at).total_seconds() / 3600
                holding_hours.append(delta)
        avg_holding = float(np.mean(holding_hours)) if holding_hours else 0.0

        # Current streak
        streak = 0
        for t in reversed(closed):
            if streak == 0:
                streak = 1 if t.pnl > 0 else -1
            elif (streak > 0 and t.pnl > 0) or (streak < 0 and t.pnl <= 0):
                streak += 1 if streak > 0 else -1
            else:
                break

        # Sharpe ratio (annualized from equity curve)
        sharpe = self._compute_sharpe()
        sortino = self._compute_sortino()

        # Drawdown
        max_dd = self._compute_max_drawdown()

        # Calmar
        total_pnl = sum(t.pnl for t in closed)
        calmar = (total_pnl / abs(max_dd)) if max_dd != 0 else 0.0

        equity_high = max(self._equity_curve) if self._equity_curve else 0.0

        return MetricsSnapshot(
            total_trades=total,
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 2)
            if profit_factor != float("inf")
            else 999.99,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            largest_win=round(largest_win, 2),
            largest_loss=round(largest_loss, 2),
            avg_holding_period_hours=round(avg_holding, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            max_drawdown_pct=round(max_dd, 2),
            current_streak=streak,
            total_pnl=round(total_pnl, 2),
            total_commission=0.0,  # tracked externally in backtest
            net_pnl=round(total_pnl, 2),
            equity_high=round(equity_high, 2),
            risk_checks_total=self._risk_checks_total,
            risk_checks_rejected=self._risk_checks_rejected,
            circuit_breaker_trips=self._circuit_breaker_trips,
            timestamp=datetime.utcnow(),
        )

    def _compute_sharpe(self, risk_free_rate: float = 0.0) -> float:
        """Annualized Sharpe from equity curve."""
        if len(self._equity_curve) < 2:
            return 0.0
        returns = np.diff(self._equity_curve) / np.array(self._equity_curve[:-1])
        if np.std(returns) == 0:
            return 0.0
        excess = returns - risk_free_rate / 252
        return float(np.mean(excess) / np.std(excess) * np.sqrt(252))

    def _compute_sortino(self, risk_free_rate: float = 0.0) -> float:
        """Annualized Sortino from equity curve."""
        if len(self._equity_curve) < 2:
            return 0.0
        returns = np.diff(self._equity_curve) / np.array(self._equity_curve[:-1])
        downside = returns[returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0
        excess = np.mean(returns) - risk_free_rate / 252
        return float(excess / np.std(downside) * np.sqrt(252))

    def _compute_max_drawdown(self) -> float:
        """Max drawdown percentage from equity curve."""
        if len(self._equity_curve) < 2:
            return 0.0
        curve = np.array(self._equity_curve)
        peak = np.maximum.accumulate(curve)
        dd = (peak - curve) / peak * 100
        return float(np.max(dd))
