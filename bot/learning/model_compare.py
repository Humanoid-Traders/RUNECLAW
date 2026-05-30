"""RUNECLAW AI Learning — Model Comparison Module.

Compare different AI models or decision engines safely.
The model with highest profit is NOT automatically best.
Prefer risk-adjusted, explainable, stable results.
If models disagree strongly, return NO_TRADE_UNCERTAIN.
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import ModelComparison
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.model_compare")


class ModelComparer:
    """Compare rule-based vs LLM-assisted vs macro-aware decisions.

    Metrics:
    - accuracy, consistency, hallucination risk
    - overconfidence, latency, token cost
    - audit quality, decision stability, safety score
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def record_comparison(
        self,
        *,
        symbol: str,
        base_direction: str,
        base_confidence: float,
        llm_direction: str,
        llm_confidence: float,
        macro_adjusted_direction: str = "",
        risk_adjusted_decision: str = "",
        token_cost: float = 0.0,
        latency_ms: float = 0.0,
        final_paper_result: Optional[float] = None,
    ) -> ModelComparison:
        """Record a side-by-side comparison of decision sources."""
        agree = base_direction == llm_direction
        disagreement_action = ""

        if not agree:
            confidence_gap = abs(base_confidence - llm_confidence)
            if confidence_gap > 0.3:
                disagreement_action = "NO_TRADE_UNCERTAIN"
            else:
                disagreement_action = "DEFER_TO_HIGHER_CONFIDENCE"

        # Safety score: penalize overconfidence and disagreement
        safety = 70.0
        if agree:
            safety += 15
        else:
            safety -= 15
        if max(base_confidence, llm_confidence) > 0.9:
            safety -= 10  # overconfidence penalty
        if token_cost > 0.01:
            safety -= 5  # cost penalty for expensive models
        safety = max(0, min(100, safety))

        comparison = ModelComparison(
            symbol=symbol,
            base_strategy_direction=base_direction,
            base_strategy_confidence=base_confidence,
            llm_direction=llm_direction,
            llm_confidence=llm_confidence,
            macro_adjusted_direction=macro_adjusted_direction,
            risk_adjusted_decision=risk_adjusted_decision,
            final_paper_result=final_paper_result,
            models_agree=agree,
            disagreement_action=disagreement_action,
            token_cost=token_cost,
            latency_ms=latency_ms,
            safety_score=safety,
        )

        self._store.record_comparison(comparison)
        return comparison

    def get_accuracy_summary(self) -> dict:
        """Summarize model accuracy from completed comparisons."""
        comparisons = self._store.get_comparisons(limit=500)
        completed = [c for c in comparisons if c.final_paper_result is not None]

        if not completed:
            return {"total": 0, "message": "No completed comparisons yet"}

        base_correct = sum(
            1 for c in completed
            if (c.base_strategy_direction == "LONG" and c.final_paper_result > 0)
            or (c.base_strategy_direction == "SHORT" and c.final_paper_result > 0)
        )
        llm_correct = sum(
            1 for c in completed
            if (c.llm_direction == "LONG" and c.final_paper_result > 0)
            or (c.llm_direction == "SHORT" and c.final_paper_result > 0)
        )
        agreement_rate = sum(1 for c in completed if c.models_agree) / len(completed)
        avg_cost = sum(c.token_cost for c in completed) / len(completed)
        avg_latency = sum(c.latency_ms for c in completed) / len(completed)

        return {
            "total": len(completed),
            "base_accuracy": base_correct / len(completed),
            "llm_accuracy": llm_correct / len(completed),
            "agreement_rate": agreement_rate,
            "avg_token_cost": avg_cost,
            "avg_latency_ms": avg_latency,
            "recommendation": self._recommend(base_correct, llm_correct, len(completed)),
        }

    @staticmethod
    def _recommend(base_correct: int, llm_correct: int, total: int) -> str:
        if total < 20:
            return "Insufficient data for recommendation (need 20+ completed trades)"
        base_acc = base_correct / total
        llm_acc = llm_correct / total
        if abs(base_acc - llm_acc) < 0.05:
            return "Models perform similarly — prefer rule-based for lower cost and latency"
        if llm_acc > base_acc:
            return "LLM shows marginal accuracy improvement — but validate with more data before weighting increase"
        return "Rule-based engine outperforms LLM — consider reducing LLM weight"
