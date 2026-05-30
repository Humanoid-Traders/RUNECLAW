"""RUNECLAW AI Learning — Pattern Learning Module.

Detect recurring market patterns from historical data, paper trading,
and rejected trades. Patterns are observations, NOT trade commands.
No pattern may override the risk engine.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from .models import DecisionMemory, PatternRecord, PatternType
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.patterns")

# Minimum sample size to consider a pattern "confirmed" vs "experimental"
MIN_CONFIRMED_SAMPLES = 20


class PatternLearner:
    """Detect and catalog recurring market patterns.

    Rules:
    - Patterns are observations, not automatic trade commands.
    - Every pattern must include confidence and sample size.
    - Low sample-size patterns are marked experimental.
    - No pattern may override the risk engine.
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def detect_patterns(
        self,
        decisions: Optional[list[DecisionMemory]] = None,
    ) -> list[PatternRecord]:
        """Scan decision history for recurring patterns."""
        if decisions is None:
            decisions = self._store.get_decisions(limit=1000)

        patterns: list[PatternRecord] = []

        # Group by regime
        regime_groups = self._group_by(decisions, "market_regime")
        for regime, trades in regime_groups.items():
            if len(trades) < 5:
                continue

            completed = [t for t in trades if t.pnl_result is not None]
            if not completed:
                continue

            wins = [t for t in completed if t.pnl_result and t.pnl_result > 0]
            win_rate = len(wins) / len(completed)
            avg_pnl = sum(t.pnl_result or 0 for t in completed) / len(completed)

            # Detect trend continuation pattern
            if regime in ("TREND_UP", "TREND_DOWN") and win_rate > 0.55:
                patterns.append(PatternRecord(
                    pattern_type=PatternType.TREND_CONTINUATION.value,
                    market_regime=regime,
                    confidence=min(win_rate, 0.85),
                    sample_size=len(completed),
                    is_experimental=len(completed) < MIN_CONFIRMED_SAMPLES,
                    description=f"{regime} continuation: {win_rate:.0%} win rate over {len(completed)} trades",
                    historical_win_rate=win_rate,
                    avg_pnl=avg_pnl,
                    may_override_risk=False,  # ALWAYS False
                ))

            # Detect breakout failure pattern
            if regime in ("RANGE", "CHOP") and win_rate < 0.40:
                patterns.append(PatternRecord(
                    pattern_type=PatternType.BREAKOUT_FAILURE.value,
                    market_regime=regime,
                    confidence=min(1 - win_rate, 0.85),
                    sample_size=len(completed),
                    is_experimental=len(completed) < MIN_CONFIRMED_SAMPLES,
                    description=f"{regime} breakout failures: {1 - win_rate:.0%} loss rate over {len(completed)} trades",
                    historical_win_rate=win_rate,
                    avg_pnl=avg_pnl,
                    may_override_risk=False,
                ))

        # Detect macro-event patterns
        macro_groups = self._group_by(decisions, "macro_state")
        for state, trades in macro_groups.items():
            if state in ("NORMAL", "") or len(trades) < 3:
                continue

            completed = [t for t in trades if t.pnl_result is not None]
            if not completed:
                continue

            wins = [t for t in completed if t.pnl_result and t.pnl_result > 0]
            win_rate = len(wins) / len(completed) if completed else 0
            avg_pnl = sum(t.pnl_result or 0 for t in completed) / len(completed)

            if win_rate < 0.40:
                patterns.append(PatternRecord(
                    pattern_type=PatternType.MACRO_EVENT_WHIPSAW.value,
                    market_regime=state,
                    confidence=min(1 - win_rate, 0.80),
                    sample_size=len(completed),
                    is_experimental=len(completed) < MIN_CONFIRMED_SAMPLES,
                    description=f"Poor performance during {state}: {win_rate:.0%} win rate",
                    historical_win_rate=win_rate,
                    avg_pnl=avg_pnl,
                    may_override_risk=False,
                ))

        # Detect volatility compression patterns
        vol_trades = [t for t in decisions if t.volatility_state == "low" and t.pnl_result is not None]
        if len(vol_trades) >= 5:
            vol_wins = [t for t in vol_trades if t.pnl_result and t.pnl_result > 0]
            vol_wr = len(vol_wins) / len(vol_trades)
            patterns.append(PatternRecord(
                pattern_type=PatternType.VOLATILITY_COMPRESSION.value,
                confidence=min(vol_wr, 0.80),
                sample_size=len(vol_trades),
                is_experimental=len(vol_trades) < MIN_CONFIRMED_SAMPLES,
                description=f"Low-vol entries: {vol_wr:.0%} win rate over {len(vol_trades)} trades",
                historical_win_rate=vol_wr,
                avg_pnl=sum(t.pnl_result or 0 for t in vol_trades) / len(vol_trades),
                may_override_risk=False,
            ))

        return patterns

    def get_relevant_patterns(
        self,
        symbol: str = "",
        market_regime: str = "",
        macro_state: str = "",
    ) -> list[PatternRecord]:
        """Get patterns relevant to current market conditions.

        Used to add learning context to trade decisions without
        overriding risk engine.
        """
        all_patterns = self.detect_patterns()
        relevant = []
        for p in all_patterns:
            if market_regime and p.market_regime == market_regime:
                relevant.append(p)
            elif macro_state and p.market_regime == macro_state:
                relevant.append(p)
            elif symbol and p.symbol == symbol:
                relevant.append(p)
        return relevant

    @staticmethod
    def _group_by(decisions: list[DecisionMemory], field: str) -> dict[str, list[DecisionMemory]]:
        groups: dict[str, list[DecisionMemory]] = defaultdict(list)
        for d in decisions:
            key = getattr(d, field, "") or "unknown"
            groups[key].append(d)
        return dict(groups)
