"""RUNECLAW AI Learning — Reflection Engine.

After every paper trade, rejected trade, backtest, or market event,
generate a structured reflection. Reflections may recommend changes
but may NOT directly change production trading logic without approval.
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import (
    ChangeClassification,
    DecisionMemory,
    ImprovementProposal,
    ReflectionMemory,
)
from .safety_policy import classify_proposal
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.reflection")


class ReflectionEngine:
    """Generate structured post-trade / post-event reflections.

    Core rule: reflections are observations + recommendations.
    They may NOT auto-apply trading logic changes.
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def reflect_on_trade(
        self,
        decision: DecisionMemory,
        *,
        actual_pnl: Optional[float] = None,
        exit_price: Optional[float] = None,
        max_adverse_excursion: Optional[float] = None,
    ) -> ReflectionMemory:
        """Generate reflection after a paper trade completes."""

        # Determine if signal was valid
        signal_valid = None
        if actual_pnl is not None:
            if decision.direction == "LONG":
                signal_valid = actual_pnl > 0
            elif decision.direction == "SHORT":
                signal_valid = actual_pnl > 0

        # Determine if risk decision was correct
        risk_correct = None
        if decision.risk_engine_result == "REJECTED" and actual_pnl is not None:
            # If we rejected and PnL would have been negative → correct
            risk_correct = actual_pnl <= 0
        elif decision.risk_engine_result == "APPROVED" and actual_pnl is not None:
            risk_correct = actual_pnl > 0

        # Position sizing assessment
        sizing_correct = None
        if max_adverse_excursion is not None and decision.position_size_usd > 0:
            adverse_pct = abs(max_adverse_excursion) / decision.position_size_usd * 100
            sizing_correct = adverse_pct < 5.0  # position didn't exceed 5% adverse

        # SL/TP assessment
        sl_tp_correct = None
        if actual_pnl is not None and exit_price is not None:
            if decision.direction == "LONG":
                hit_sl = exit_price <= decision.stop_loss
                hit_tp = exit_price >= decision.take_profit
            else:
                hit_sl = exit_price >= decision.stop_loss
                hit_tp = exit_price <= decision.take_profit
            sl_tp_correct = hit_tp or (hit_sl and actual_pnl < 0)  # SL worked

        # Build what happened
        what_happened = f"Direction={decision.direction}, Confidence={decision.confidence:.2f}"
        if actual_pnl is not None:
            what_happened += f", PnL=${actual_pnl:.2f}"
        if exit_price is not None:
            what_happened += f", Exit=${exit_price:.2f}"

        # Generate lesson
        lesson = self._generate_lesson(
            decision=decision,
            signal_valid=signal_valid,
            risk_correct=risk_correct,
            actual_pnl=actual_pnl,
        )

        # Generate improvement suggestion
        improvement = self._generate_improvement(
            decision=decision,
            signal_valid=signal_valid,
            risk_correct=risk_correct,
            actual_pnl=actual_pnl,
        )

        reflection = ReflectionMemory(
            decision_audit_id=decision.audit_id,
            symbol=decision.symbol,
            mode=decision.mode,
            what_was_expected=f"{decision.direction} with {decision.confidence:.0%} confidence, R:R={decision.risk_reward:.1f}",
            what_happened=what_happened,
            signal_valid=signal_valid,
            risk_decision_correct=risk_correct,
            macro_context_helped=decision.macro_state in ("NORMAL", "PRE_EVENT_CAUTION"),
            trade_avoided_correctly=decision.risk_engine_result == "REJECTED" and (actual_pnl is None or actual_pnl <= 0),
            position_sizing_correct=sizing_correct,
            sl_tp_behaved_correctly=sl_tp_correct,
            improvement_suggestion=improvement,
            lesson_learned=lesson,
            confidence=min(decision.confidence, 0.8),  # never overconfident in lessons
            recommended_improvement=improvement,
            needs_human_review=True,  # default safe
            allowed_to_auto_apply=False,
        )

        self._store.record_reflection(reflection)
        return reflection

    def reflect_on_rejection(self, decision: DecisionMemory) -> ReflectionMemory:
        """Reflect on a rejected trade — was the rejection correct?"""
        lesson = f"Trade {decision.symbol} {decision.direction} was rejected: {decision.rejected_reason}"

        reflection = ReflectionMemory(
            decision_audit_id=decision.audit_id,
            symbol=decision.symbol,
            mode=decision.mode,
            what_was_expected=f"{decision.direction} signal at {decision.confidence:.0%}",
            what_happened=f"REJECTED by risk engine: {decision.rejected_reason}",
            signal_valid=None,  # unknown — was rejected
            risk_decision_correct=None,  # needs market follow-up
            lesson_learned=lesson,
            confidence=0.5,
            recommended_improvement="Monitor price after rejection to validate risk engine accuracy",
            needs_human_review=False,
            allowed_to_auto_apply=False,
        )

        self._store.record_reflection(reflection)
        return reflection

    def propose_improvement(
        self,
        reflection: ReflectionMemory,
    ) -> Optional[ImprovementProposal]:
        """Generate an improvement proposal from a reflection.

        Only creates proposals when there's clear evidence of improvement.
        All proposals are classified by safety policy before storage.
        """
        if not reflection.recommended_improvement:
            return None

        proposal = ImprovementProposal(
            source="reflection_engine",
            problem=f"Reflection on {reflection.symbol}: {reflection.what_happened}",
            evidence=f"Signal valid={reflection.signal_valid}, "
                     f"Risk correct={reflection.risk_decision_correct}, "
                     f"Lesson: {reflection.lesson_learned}",
            proposed_change=reflection.recommended_improvement,
            expected_benefit="Improved decision accuracy",
            risk_impact="Low — recommendation only, no direct trading logic change",
            rollback_plan="Revert to previous behavior by removing recommendation",
            test_plan="Validate against next 10 similar setups in paper trading",
        )

        # Classify by safety policy (the ONLY path for classification)
        classify_proposal(proposal)
        self._store.record_proposal(proposal)
        return proposal

    def _generate_lesson(
        self,
        decision: DecisionMemory,
        signal_valid: Optional[bool],
        risk_correct: Optional[bool],
        actual_pnl: Optional[float],
    ) -> str:
        """Generate a human-readable lesson from trade outcome."""
        parts = []

        if signal_valid is True:
            parts.append(f"Signal was valid for {decision.symbol} {decision.direction}")
        elif signal_valid is False:
            parts.append(f"Signal was INVALID for {decision.symbol} {decision.direction}")

        if risk_correct is True:
            parts.append("Risk engine decision was correct")
        elif risk_correct is False:
            parts.append("Risk engine decision may need review")

        if actual_pnl is not None:
            if actual_pnl > 0:
                parts.append(f"Profitable trade: +${actual_pnl:.2f}")
            else:
                parts.append(f"Loss: ${actual_pnl:.2f}")

        if decision.market_regime:
            parts.append(f"Regime: {decision.market_regime}")

        return ". ".join(parts) if parts else "Insufficient data for lesson"

    def _generate_improvement(
        self,
        decision: DecisionMemory,
        signal_valid: Optional[bool],
        risk_correct: Optional[bool],
        actual_pnl: Optional[float],
    ) -> str:
        """Generate improvement suggestion. Conservative by default."""
        if signal_valid is False and actual_pnl is not None and actual_pnl < 0:
            if decision.confidence > 0.7:
                return "Consider adding additional confirmation filters for high-confidence signals"
            return "Review confluence scoring weights for this regime/symbol combination"

        if risk_correct is False:
            return "Review risk engine check thresholds for potential adjustment"

        if actual_pnl is not None and actual_pnl > 0 and decision.risk_engine_result == "REJECTED":
            return "Risk engine rejected a profitable setup — review rejection criteria"

        return ""
