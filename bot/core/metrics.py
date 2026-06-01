"""
RUNECLAW Metrics Engine -- performance analytics and tracking.
Computes trading performance metrics from portfolio history.
"""

from __future__ import annotations

from datetime import datetime
from bot.compat import UTC
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
        self._timestamps.append(ts or datetime.now(UTC))
        MAX_EQUITY_POINTS = 10000
        if len(self._equity_curve) > MAX_EQUITY_POINTS:
            self._equity_curve = self._equity_curve[-MAX_EQUITY_POINTS:]
            self._timestamps = self._timestamps[-MAX_EQUITY_POINTS:]

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

        # Sharpe / Sortino: prefer per-trade returns when trades exist,
        # because the equity-snapshot series is mostly zeros between trade
        # closes and produces badly distorted ratios.
        if closed:
            sharpe = self._compute_sharpe_from_trades(closed)
            sortino = self._compute_sortino_from_trades(closed)
        else:
            sharpe = self._compute_sharpe()
            sortino = self._compute_sortino()

        # Drawdown
        max_dd = self._compute_max_drawdown()

        # Calmar: return % / drawdown % (both must be in same units)
        total_pnl = sum(t.pnl for t in closed)
        if self._equity_curve and self._equity_curve[0] > 0:
            total_return_pct = (total_pnl / self._equity_curve[0]) * 100
        else:
            total_return_pct = 0.0
        calmar = (total_return_pct / max_dd) if max_dd > 0 else 0.0

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
            total_commission=sum(getattr(t, "commission", 0.0) or 0.0 for t in closed),
            net_pnl=round(total_pnl, 2),
            equity_high=round(equity_high, 2),
            risk_checks_total=self._risk_checks_total,
            risk_checks_rejected=self._risk_checks_rejected,
            circuit_breaker_trips=self._circuit_breaker_trips,
            timestamp=datetime.now(UTC),
        )

    def _compute_sharpe(self, risk_free_rate: float = 0.0) -> float:
        """Annualized Sharpe from equity curve.
        Computes annualization factor from actual timestamp cadence.
        Zero returns (flat periods between trades) are excluded so that
        sparse equity snapshots do not inflate volatility artificially."""
        if len(self._equity_curve) < 2:
            return 0.0
        returns = np.diff(self._equity_curve) / np.array(self._equity_curve[:-1])
        # Filter out flat periods — only keep observations where price moved.
        nonzero_mask = returns != 0.0
        active_returns = returns[nonzero_mask]
        if len(active_returns) < 2 or np.std(active_returns) == 0:
            return 0.0
        # Compute actual periods per year from timestamps
        if len(self._timestamps) >= 2:
            total_seconds = (self._timestamps[-1] - self._timestamps[0]).total_seconds()
            if total_seconds > 0:
                seconds_per_obs = total_seconds / len(returns)
                periods_per_year = (365.25 * 24 * 3600) / seconds_per_obs
            else:
                periods_per_year = 2190  # fallback
        else:
            periods_per_year = 2190
        excess = active_returns - risk_free_rate / periods_per_year
        return float(np.mean(excess) / np.std(excess) * np.sqrt(periods_per_year))

    def _compute_sortino(self, risk_free_rate: float = 0.0) -> float:
        """Annualized Sortino from equity curve.
        Uses actual timestamp cadence for annualization.
        Zero returns (flat periods between trades) are excluded before
        computing downside deviation."""
        if len(self._equity_curve) < 2:
            return 0.0
        returns = np.diff(self._equity_curve) / np.array(self._equity_curve[:-1])
        # Filter out flat periods before computing downside deviation.
        active_returns = returns[returns != 0.0]
        if len(active_returns) < 2:
            return 0.0
        downside = active_returns[active_returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0
        # Compute actual periods per year from timestamps
        if len(self._timestamps) >= 2:
            total_seconds = (self._timestamps[-1] - self._timestamps[0]).total_seconds()
            if total_seconds > 0:
                seconds_per_obs = total_seconds / len(returns)
                periods_per_year = (365.25 * 24 * 3600) / seconds_per_obs
            else:
                periods_per_year = 2190
        else:
            periods_per_year = 2190
        excess = np.mean(active_returns) - risk_free_rate / periods_per_year
        return float(excess / np.std(downside) * np.sqrt(periods_per_year))

    def _compute_sharpe_from_trades(self, closed: list[TradeExecution]) -> float:
        """Annualized Sharpe computed from per-trade PnL returns.

        Each trade's return is ``pnl / entry_cost`` where ``entry_cost`` is
        approximated as ``quantity * entry_price``.  The annualization factor
        is derived from the observed trade frequency (trades per year) over
        the actual elapsed trading period, so that a bot with 10 trades/day
        gets the same annualized Sharpe as one with 1 trade/week — no
        hard-coded ``periods_per_year`` constant is needed.

        Falls back to 0.0 when there are fewer than 2 trades or zero
        return-volatility.
        """
        if len(closed) < 2:
            return 0.0

        pct_returns: list[float] = []
        for t in closed:
            cost = abs(getattr(t, "quantity", 0.0) * getattr(t, "entry_price", 0.0))
            if cost > 0:
                pct_returns.append(t.pnl / cost)
            else:
                # Fallback: use raw pnl (dimensionless weight in the series)
                pct_returns.append(t.pnl)

        arr = np.array(pct_returns, dtype=float)
        if np.std(arr) == 0:
            return 0.0

        trades_per_year = self._trades_per_year(closed)
        sharpe = float(np.mean(arr) / np.std(arr) * np.sqrt(trades_per_year))
        return sharpe

    def _compute_sortino_from_trades(self, closed: list[TradeExecution]) -> float:
        """Annualized Sortino computed from per-trade PnL returns.

        Uses the same per-trade percentage return series as
        ``_compute_sharpe_from_trades``.  Downside deviation is computed only
        from losing trades (return < 0).  Returns 0.0 when there are no
        losing trades or fewer than 2 trades total.
        """
        if len(closed) < 2:
            return 0.0

        pct_returns: list[float] = []
        for t in closed:
            cost = abs(getattr(t, "quantity", 0.0) * getattr(t, "entry_price", 0.0))
            if cost > 0:
                pct_returns.append(t.pnl / cost)
            else:
                pct_returns.append(t.pnl)

        arr = np.array(pct_returns, dtype=float)
        downside = arr[arr < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0

        trades_per_year = self._trades_per_year(closed)
        sortino = float(np.mean(arr) / np.std(downside) * np.sqrt(trades_per_year))
        return sortino

    def _trades_per_year(self, closed: list[TradeExecution]) -> float:
        """Estimate annualized trade frequency from actual close timestamps.

        Uses the span between the first and last closed trade.  Falls back to
        252 (typical daily bar count) when timestamps are missing or the span
        is zero.
        """
        timestamps = [t.closed_at for t in closed if t.closed_at is not None]
        if len(timestamps) < 2:
            return 252.0
        span_seconds = (max(timestamps) - min(timestamps)).total_seconds()
        if span_seconds <= 0:
            return 252.0
        trades_per_second = (len(timestamps) - 1) / span_seconds
        return trades_per_second * 365.25 * 24 * 3600

    def _compute_max_drawdown(self) -> float:
        """Max drawdown percentage from equity curve."""
        if len(self._equity_curve) < 2:
            return 0.0
        curve = np.array(self._equity_curve)
        peak = np.maximum.accumulate(curve)
        dd = (peak - curve) / peak * 100
        return float(np.max(dd))
