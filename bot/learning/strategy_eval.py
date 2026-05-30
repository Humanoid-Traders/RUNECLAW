"""RUNECLAW AI Learning — Strategy Evaluation Module.

Continuously evaluate strategies using paper-trading and backtest results.
Never promote based on short-term profit alone. Penalize high drawdown,
bad macro-event performance, and high leverage dependency.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from .models import DecisionMemory, LearningTier, StrategyScorecard
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.strategy")


class StrategyEvaluator:
    """Evaluate and rank strategies by risk-adjusted, explainable metrics.

    Scoring philosophy:
    - Safety > profit
    - Stability > peak performance
    - Explainability > complexity
    - Low drawdown > high win rate
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def evaluate_strategy(
        self,
        strategy_name: str,
        decisions: Optional[list[DecisionMemory]] = None,
    ) -> StrategyScorecard:
        """Build or update a strategy scorecard from decision history."""
        if decisions is None:
            decisions = self._store.get_decisions(limit=1000)

        # Filter to this strategy
        trades = [d for d in decisions if d.strategy_signal == strategy_name and d.pnl_result is not None]
        rejected = [d for d in decisions if d.strategy_signal == strategy_name and d.risk_engine_result == "REJECTED"]

        if not trades:
            return StrategyScorecard(
                strategy_name=strategy_name,
                learning_tier=LearningTier.C.value,
            )

        wins = [t for t in trades if t.pnl_result and t.pnl_result > 0]
        losses = [t for t in trades if t.pnl_result is not None and t.pnl_result <= 0]

        total = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total if total > 0 else 0.0

        avg_win = sum(t.pnl_result for t in wins) / win_count if win_count > 0 else 0.0
        avg_loss = abs(sum(t.pnl_result for t in losses) / loss_count) if loss_count > 0 else 1.0

        profit_factor = (avg_win * win_count) / (avg_loss * loss_count) if loss_count > 0 and avg_loss > 0 else 0.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Max drawdown from PnL series
        equity_curve = []
        running = 0.0
        for t in trades:
            running += (t.pnl_result or 0.0)
            equity_curve.append(running)
        max_dd = self._max_drawdown(equity_curve)

        # Sharpe-like: mean / std of returns
        returns = [t.pnl_result or 0.0 for t in trades]
        sharpe = self._sharpe_like(returns)

        # Performance by regime
        perf_by_regime: dict[str, dict] = {}
        for t in trades:
            r = t.market_regime or "unknown"
            if r not in perf_by_regime:
                perf_by_regime[r] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            perf_by_regime[r]["trades"] += 1
            if t.pnl_result and t.pnl_result > 0:
                perf_by_regime[r]["wins"] += 1
            perf_by_regime[r]["total_pnl"] += t.pnl_result or 0.0

        # Performance by symbol
        perf_by_symbol: dict[str, dict] = {}
        for t in trades:
            s = t.symbol or "unknown"
            if s not in perf_by_symbol:
                perf_by_symbol[s] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            perf_by_symbol[s]["trades"] += 1
            if t.pnl_result and t.pnl_result > 0:
                perf_by_symbol[s]["wins"] += 1
            perf_by_symbol[s]["total_pnl"] += t.pnl_result or 0.0

        # Performance around macro events
        perf_macro: dict[str, dict] = {}
        for t in trades:
            ms = t.macro_state or "unknown"
            if ms not in perf_macro:
                perf_macro[ms] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            perf_macro[ms]["trades"] += 1
            if t.pnl_result and t.pnl_result > 0:
                perf_macro[ms]["wins"] += 1
            perf_macro[ms]["total_pnl"] += t.pnl_result or 0.0

        # False positive/negative rates
        false_positives = sum(
            1 for t in trades
            if t.confidence > 0.6 and t.pnl_result is not None and t.pnl_result < 0
        )
        false_negatives = len([
            r for r in rejected
            if r.confidence > 0.6  # high-confidence rejections that might have been profitable
        ])
        fp_rate = false_positives / total if total > 0 else 0.0
        fn_rate = false_negatives / (total + len(rejected)) if (total + len(rejected)) > 0 else 0.0

        # Safety score (0-100)
        safety = self._compute_safety_score(
            max_dd=max_dd,
            profit_factor=profit_factor,
            fp_rate=fp_rate,
            total_trades=total,
        )

        # Overfitting warning
        overfitting = total < 30 and win_rate > 0.8

        # Learning tier
        tier = self._compute_tier(
            safety_score=safety,
            sharpe=sharpe,
            max_dd=max_dd,
            total_trades=total,
            overfitting=overfitting,
        )

        scorecard = StrategyScorecard(
            strategy_name=strategy_name,
            total_trades=total,
            winning_trades=win_count,
            losing_trades=loss_count,
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            sharpe_like_score=sharpe,
            max_drawdown=max_dd,
            false_positive_rate=fp_rate,
            false_negative_rate=fn_rate,
            rejected_by_risk_engine=len(rejected),
            performance_by_regime=perf_by_regime,
            performance_by_symbol=perf_by_symbol,
            performance_around_macro=perf_macro,
            learning_tier=tier,
            safety_score=safety,
            overfitting_warning=overfitting,
        )

        self._store.update_scorecard(scorecard)
        return scorecard

    def rank_strategies(self) -> list[StrategyScorecard]:
        """Return all strategies ranked by safety score (descending)."""
        scorecards = self._store.get_scorecards()
        ranked = sorted(scorecards.values(), key=lambda s: s.safety_score, reverse=True)
        return ranked

    @staticmethod
    def _max_drawdown(equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _sharpe_like(returns: list[float]) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 1.0
        return mean / std

    @staticmethod
    def _compute_safety_score(
        max_dd: float,
        profit_factor: float,
        fp_rate: float,
        total_trades: int,
    ) -> float:
        """Composite safety score 0-100. Higher = safer."""
        score = 50.0  # baseline

        # Drawdown penalty (max -30)
        if max_dd > 500:
            score -= 30
        elif max_dd > 200:
            score -= 20
        elif max_dd > 100:
            score -= 10

        # Profit factor bonus (max +20)
        if profit_factor > 2.0:
            score += 20
        elif profit_factor > 1.5:
            score += 15
        elif profit_factor > 1.0:
            score += 10

        # False positive penalty (max -20)
        if fp_rate > 0.5:
            score -= 20
        elif fp_rate > 0.3:
            score -= 10

        # Sample size bonus (max +10)
        if total_trades >= 50:
            score += 10
        elif total_trades >= 20:
            score += 5

        return max(0.0, min(100.0, score))

    @staticmethod
    def _compute_tier(
        safety_score: float,
        sharpe: float,
        max_dd: float,
        total_trades: int,
        overfitting: bool,
    ) -> str:
        if overfitting:
            return LearningTier.C.value
        if safety_score >= 80 and sharpe > 1.0 and total_trades >= 30:
            return LearningTier.S.value
        if safety_score >= 65 and sharpe > 0.5:
            return LearningTier.A.value
        if safety_score >= 50:
            return LearningTier.B.value
        if safety_score >= 30:
            return LearningTier.C.value
        return LearningTier.D.value
