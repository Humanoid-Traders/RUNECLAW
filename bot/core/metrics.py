"""
RUNECLAW Metrics Engine -- performance analytics and tracking.
Computes trading performance metrics from portfolio history.
"""

from __future__ import annotations

from datetime import datetime
from bot.compat import UTC
from typing import Optional

import numpy as np

from collections import defaultdict

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
        losses = [t for t in closed if t.pnl < 0]

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
        if len(closed) >= 2:
            sharpe = self._compute_sharpe_from_trades(closed)
            sortino = self._compute_sortino_from_trades(closed)
        else:
            sharpe = 0.0
            sortino = 0.0

        # Drawdown
        max_dd = self._compute_max_drawdown()

        # Calmar: return % / drawdown % (both must be in same units)
        total_pnl = sum(getattr(t, 'gross_pnl', t.pnl) or t.pnl for t in closed)
        if self._equity_curve and self._equity_curve[0] > 0:
            total_return_pct = (total_pnl / self._equity_curve[0]) * 100
        else:
            total_return_pct = 0.0
        calmar = (total_return_pct / max_dd) if max_dd > 0 else 0.0

        equity_high = max(self._equity_curve) if self._equity_curve else 0.0

        # Per-symbol and per-strategy summary stats
        per_symbol_stats = self._compute_group_stats(closed, key_fn=lambda t: t.asset)
        per_strategy_stats = self._compute_group_stats(closed, key_fn=lambda t: t.strategy_type)

        # Signal attribution
        attribution = self.compute_attribution(trades)

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
            net_pnl=round(sum(t.pnl for t in closed), 2),
            equity_high=round(equity_high, 2),
            risk_checks_total=self._risk_checks_total,
            risk_checks_rejected=self._risk_checks_rejected,
            circuit_breaker_trips=self._circuit_breaker_trips,
            per_symbol_stats=per_symbol_stats,
            per_strategy_stats=per_strategy_stats,
            signals_attribution=attribution,
            timestamp=datetime.now(UTC),
        )

    def compute_attribution(self, trades: list[TradeExecution]) -> dict[str, dict]:
        """Compute signal attribution — which indicators contribute to wins vs losses.

        For each trade, looks at signals_used field and tallies wins/losses per signal.
        Returns accuracy stats per signal for auto-weighting.
        """
        attribution: dict[str, dict] = {}

        closed = [t for t in trades if t.closed_at is not None]
        if not closed:
            return attribution

        for trade in closed:
            # Get signals_used from the trade's metadata
            signals = getattr(trade, '_signals_used', None)
            if signals is None:
                # Try to get from trade idea linkage
                continue

            is_win = trade.pnl > 0
            for signal_name in signals:
                if signal_name not in attribution:
                    attribution[signal_name] = {
                        "total": 0, "wins": 0, "losses": 0,
                        "total_pnl": 0.0, "win_pnl": 0.0, "loss_pnl": 0.0,
                    }
                attr = attribution[signal_name]
                attr["total"] += 1
                attr["total_pnl"] += trade.pnl
                if is_win:
                    attr["wins"] += 1
                    attr["win_pnl"] += trade.pnl
                else:
                    attr["losses"] += 1
                    attr["loss_pnl"] += trade.pnl

        # Compute derived stats
        for name, attr in attribution.items():
            if attr["total"] > 0:
                attr["win_rate"] = round(attr["wins"] / attr["total"], 3)
                attr["avg_pnl"] = round(attr["total_pnl"] / attr["total"], 2)
                attr["edge_score"] = round(
                    attr["win_rate"] * abs(attr.get("avg_pnl", 0)) if attr["win_rate"] > 0.5
                    else -((1 - attr["win_rate"]) * abs(attr.get("avg_pnl", 0))), 2
                )

        return attribution

    # DEPRECATED: equity-curve based — use _compute_sharpe_from_trades instead
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
        if len(active_returns) < 2 or np.std(active_returns, ddof=1) == 0:
            return 0.0
        # Compute actual periods per year from timestamps
        if len(self._timestamps) >= 2:
            total_seconds = (self._timestamps[-1] - self._timestamps[0]).total_seconds()
            if total_seconds > 0:
                seconds_per_obs = total_seconds / len(active_returns)
                periods_per_year = (365.25 * 24 * 3600) / seconds_per_obs
            else:
                periods_per_year = 2190  # fallback
        else:
            periods_per_year = 2190
        excess = active_returns - risk_free_rate / periods_per_year
        return float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(periods_per_year))

    # DEPRECATED: equity-curve based — use _compute_sortino_from_trades instead
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
        if len(downside) == 0 or np.std(downside, ddof=1) == 0:
            return 0.0
        # Compute actual periods per year from timestamps
        if len(self._timestamps) >= 2:
            total_seconds = (self._timestamps[-1] - self._timestamps[0]).total_seconds()
            if total_seconds > 0:
                seconds_per_obs = total_seconds / len(active_returns)
                periods_per_year = (365.25 * 24 * 3600) / seconds_per_obs
            else:
                periods_per_year = 2190
        else:
            periods_per_year = 2190
        excess = np.mean(active_returns) - risk_free_rate / periods_per_year
        return float(excess / np.std(downside, ddof=1) * np.sqrt(periods_per_year))

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
                # Skip trades with no cost to avoid mixing dollar and % units
                continue

        arr = np.array(pct_returns, dtype=float)
        if len(arr) < 2 or np.std(arr, ddof=1) == 0:
            return 0.0

        trades_per_year = self._trades_per_year(closed)
        sharpe = float(np.mean(arr) / np.std(arr, ddof=1) * np.sqrt(trades_per_year))
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
                # Skip trades with no cost to avoid mixing dollar and % units
                continue

        arr = np.array(pct_returns, dtype=float)
        downside = arr[arr < 0]
        if len(downside) < 2 or np.std(downside, ddof=1) == 0:
            return 0.0

        trades_per_year = self._trades_per_year(closed)
        sortino = float(np.mean(arr) / np.std(downside, ddof=1) * np.sqrt(trades_per_year))
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
        dd = np.where(peak > 0, (peak - curve) / peak * 100, 0.0)
        return float(np.max(dd))

    # -- Per-group stats helper ---------------------------------------------------

    @staticmethod
    def _build_group_dict(group_trades: list[TradeExecution]) -> dict:
        """Compute stats dict for a list of closed trades belonging to one group."""
        total = len(group_trades)
        wins = [t for t in group_trades if t.pnl > 0]
        losses = [t for t in group_trades if t.pnl < 0]
        win_pnls = [t.pnl for t in wins]
        loss_pnls = [t.pnl for t in losses]
        gross_profit = sum(win_pnls) if win_pnls else 0.0
        gross_loss = abs(sum(loss_pnls)) if loss_pnls else 0.0
        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            profit_factor = 999.99
        else:
            profit_factor = 0.0
        all_pnls = [t.pnl for t in group_trades]
        return {
            "trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / total, 4) if total > 0 else 0.0,
            "avg_pnl": round(float(np.mean(all_pnls)), 2) if all_pnls else 0.0,
            "total_pnl": round(sum(all_pnls), 2),
            "profit_factor": profit_factor,
        }

    def _compute_group_stats(
        self,
        closed: list[TradeExecution],
        key_fn,
    ) -> dict[str, dict]:
        """Group closed trades by *key_fn* and return per-group stats."""
        buckets: dict[str, list[TradeExecution]] = defaultdict(list)
        for t in closed:
            buckets[key_fn(t)].append(t)
        return {k: self._build_group_dict(v) for k, v in buckets.items()}

    # -- Public breakdown / skip methods ------------------------------------------

    def compute_breakdown(self, trades: list[TradeExecution]) -> dict:
        """Return a full performance breakdown by symbol, strategy, and direction.

        Parameters
        ----------
        trades : list[TradeExecution]
            Full trade list (open + closed). Only closed trades are analysed.

        Returns
        -------
        dict with keys:
            by_symbol, by_strategy, by_direction,
            best_symbols, worst_symbols, losing_combos
        """
        closed = [t for t in trades if t.closed_at is not None]

        by_symbol = self._compute_group_stats(closed, key_fn=lambda t: t.asset)
        by_strategy = self._compute_group_stats(closed, key_fn=lambda t: t.strategy_type)
        by_direction = self._compute_group_stats(
            closed, key_fn=lambda t: t.direction.value if hasattr(t.direction, "value") else str(t.direction),
        )

        # Best / worst symbols (min 3 trades)
        qualified = {s: v for s, v in by_symbol.items() if v["trades"] >= 3}
        sorted_by_wr = sorted(qualified.items(), key=lambda kv: kv[1]["win_rate"], reverse=True)
        best_symbols = [{"symbol": s, **v} for s, v in sorted_by_wr[:5]]
        worst_symbols = [{"symbol": s, **v} for s, v in sorted_by_wr[-5:]] if sorted_by_wr else []

        # Losing combos: (symbol, strategy) with win_rate < 30% and >= 3 trades
        combo_buckets: dict[tuple[str, str], list[TradeExecution]] = defaultdict(list)
        for t in closed:
            combo_buckets[(t.asset, t.strategy_type)].append(t)

        losing_combos: list[dict] = []
        for (sym, strat), combo_trades in combo_buckets.items():
            stats = self._build_group_dict(combo_trades)
            if stats["trades"] >= 3 and stats["win_rate"] < 0.30:
                losing_combos.append({
                    "symbol": sym,
                    "strategy": strat,
                    **stats,
                    "flag": "AUTO_SKIP",
                })

        return {
            "by_symbol": by_symbol,
            "by_strategy": by_strategy,
            "by_direction": by_direction,
            "best_symbols": best_symbols,
            "worst_symbols": worst_symbols,
            "losing_combos": losing_combos,
        }

    def should_skip_symbol(
        self,
        symbol: str,
        strategy_type: str,
        trades: list[TradeExecution],
        min_trades: int = 5,
        min_win_rate: float = 0.25,
    ) -> bool:
        """Return True if *symbol* + *strategy_type* has been consistently losing.

        A combo is considered losing when there are at least *min_trades* closed
        trades and the win rate is strictly below *min_win_rate*.
        """
        closed = [
            t for t in trades
            if t.closed_at is not None and t.asset == symbol and t.strategy_type == strategy_type
        ]
        if len(closed) < min_trades:
            return False
        wins = sum(1 for t in closed if t.pnl > 0)
        return (wins / len(closed)) < min_win_rate
