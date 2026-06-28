"""RUNECLAW AI Learning — Macro Learning Module.

Track macro event reactions and build an event-reaction memory.
Macro learning is context only:
- It may reduce risk.
- It may block trades.
- It may NOT create direct buy/sell orders by itself.
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import MacroEventMemory
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.macro")

# Supported macro event types
MACRO_EVENT_TYPES = frozenset({
    "FOMC", "CPI", "PCE", "NFP", "PPI", "GDP",
    "UNEMPLOYMENT", "RETAIL_SALES", "FED_SPEECH",
})


class MacroLearner:
    """Learn from macro event market reactions.

    For each macro event, track:
    - Event details (name, datetime, values, surprise)
    - Crypto reactions at 5min, 30min, 4h, 24h
    - Volatility, spread, liquidity, and funding impact
    - Lesson learned

    Rules:
    - Macro learning is context only.
    - It may reduce risk or block trades.
    - It may NOT create direct buy/sell orders.
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def record_event(
        self,
        *,
        event_name: str,
        event_type: str,
        event_datetime_utc: Optional[str] = None,
        previous_value: str = "",
        forecast_value: str = "",
        actual_value: str = "",
        surprise_score: float = 0.0,
        btc_5min_pct: Optional[float] = None,
        btc_30min_pct: Optional[float] = None,
        btc_4h_pct: Optional[float] = None,
        btc_24h_pct: Optional[float] = None,
        volatility_expansion: Optional[float] = None,
        spread_impact: Optional[float] = None,
        liquidity_impact: str = "",
        funding_rate_shift: Optional[float] = None,
    ) -> MacroEventMemory:
        """Record a macro event and its market reaction."""
        # Derive lesson from reaction data
        lesson = self._derive_lesson(
            event_type=event_type,
            surprise_score=surprise_score,
            btc_5min_pct=btc_5min_pct,
            btc_30min_pct=btc_30min_pct,
            btc_4h_pct=btc_4h_pct,
        )

        record = MacroEventMemory(
            event_name=event_name,
            event_type=event_type,
            previous_value=previous_value,
            forecast_value=forecast_value,
            actual_value=actual_value,
            surprise_score=surprise_score,
            btc_5min_reaction_pct=btc_5min_pct,
            btc_30min_reaction_pct=btc_30min_pct,
            btc_4h_reaction_pct=btc_4h_pct,
            btc_24h_reaction_pct=btc_24h_pct,
            volatility_expansion=volatility_expansion,
            spread_impact=spread_impact,
            liquidity_impact=liquidity_impact,
            funding_rate_shift=funding_rate_shift,
            lesson_learned=lesson,
        )

        self._store.record_macro_event(record)
        return record

    def get_event_history(self, event_type: str, limit: int = 20) -> list[MacroEventMemory]:
        """Get historical reactions for a specific event type."""
        return self._store.get_macro_events(event_type=event_type, limit=limit)

    def get_average_reaction(self, event_type: str) -> dict:
        """Compute average market reaction for an event type."""
        events = self._store.get_macro_events(event_type=event_type, limit=100)
        if not events:
            return {"event_type": event_type, "sample_size": 0}

        def avg_non_none(values: list) -> Optional[float]:
            filtered = [v for v in values if v is not None]
            return sum(filtered) / len(filtered) if filtered else None

        return {
            "event_type": event_type,
            "sample_size": len(events),
            "avg_5min_reaction": avg_non_none([e.btc_5min_reaction_pct for e in events]),
            "avg_30min_reaction": avg_non_none([e.btc_30min_reaction_pct for e in events]),
            "avg_4h_reaction": avg_non_none([e.btc_4h_reaction_pct for e in events]),
            "avg_24h_reaction": avg_non_none([e.btc_24h_reaction_pct for e in events]),
            "avg_surprise": avg_non_none([e.surprise_score for e in events]),
            "avg_vol_expansion": avg_non_none([e.volatility_expansion for e in events]),
        }

    def get_risk_context(self, upcoming_event_type: str) -> dict:
        """Get risk context for an upcoming event.

        This is context for the risk engine — NOT a trade signal.
        """
        avg = self.get_average_reaction(upcoming_event_type)
        history = self.get_event_history(upcoming_event_type, limit=10)

        # Count how often there was a >2% move
        big_moves = sum(
            1 for e in history
            if e.btc_30min_reaction_pct is not None
            and abs(e.btc_30min_reaction_pct) > 2.0
        )
        big_move_rate = big_moves / len(history) if history else 0.0

        return {
            "event_type": upcoming_event_type,
            "historical_samples": len(history),
            "avg_30min_reaction": avg.get("avg_30min_reaction"),
            "big_move_probability": big_move_rate,
            "recommendation": self._risk_recommendation(big_move_rate, avg),
            "may_create_trade_signal": False,  # ALWAYS False
        }

    @staticmethod
    def _risk_recommendation(big_move_rate: float, avg: dict) -> str:
        if big_move_rate > 0.5:
            return "HIGH_CAUTION: >50% of past events caused >2% moves. Reduce position or sit out."
        if big_move_rate > 0.3:
            return "MODERATE_CAUTION: 30-50% big-move rate. Tighten stops."
        return "NORMAL: Historical reactions within typical range."

    @staticmethod
    def _derive_lesson(
        event_type: str,
        surprise_score: float,
        btc_5min_pct: Optional[float],
        btc_30min_pct: Optional[float],
        btc_4h_pct: Optional[float],
    ) -> str:
        parts = [f"{event_type} event"]

        if surprise_score > 1.0:
            parts.append("significant surprise")
        elif surprise_score < -1.0:
            parts.append("significant miss")
        else:
            parts.append("inline with expectations")

        if btc_5min_pct is not None:
            if abs(btc_5min_pct) > 2.0:
                parts.append(f"immediate spike: {btc_5min_pct:+.1f}%")
            else:
                parts.append(f"muted initial reaction: {btc_5min_pct:+.1f}%")

        if btc_30min_pct is not None and btc_5min_pct is not None:
            if btc_30min_pct * btc_5min_pct < 0:
                parts.append("reversal within 30min (whipsaw)")
            else:
                parts.append("continuation within 30min")

        if btc_4h_pct is not None and btc_30min_pct is not None:
            if btc_4h_pct * btc_30min_pct < 0:
                parts.append("4h reversal from 30min direction")

        return ". ".join(parts)
