"""RUNECLAW AI Learning — Human Feedback Module.

Allow Patrick / HUMANOID TRADERS to give feedback and improve the agent.
Feedback improves recommendations only — it must NOT directly bypass risk gates.
"""

from __future__ import annotations

import logging

from .models import FeedbackType, HumanFeedback
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.feedback")


class FeedbackCollector:
    """Collect and process human feedback.

    Feedback types:
    - correct / incorrect / too_risky / too_conservative
    - unclear_explanation / missing_macro_context
    - missing_risk_reason / bad_symbol_choice
    - good_rejection / good_explanation
    - needs_more_evidence / needs_doc_update

    Safety rule: feedback improves recommendations only.
    It must NOT directly bypass risk gates.
    """

    def __init__(self, store: LearningStore):
        self._store = store

    def record(
        self,
        decision_audit_id: str,
        feedback_type: str,
        feedback_text: str = "",
        severity: str = "normal",
    ) -> HumanFeedback:
        """Record human feedback on a specific decision."""
        # Validate feedback type
        valid_types = {ft.value for ft in FeedbackType}
        if feedback_type not in valid_types:
            logger.warning("Unknown feedback type '%s', recording as-is", feedback_type)

        fb = HumanFeedback(
            decision_audit_id=decision_audit_id,
            feedback_type=feedback_type,
            feedback_text=feedback_text,
            severity=severity,
        )
        self._store.record_feedback(fb)
        return fb

    def get_feedback_summary(self) -> dict:
        """Summarize all feedback by type."""
        feedback = self._store.get_feedback(limit=500)
        if not feedback:
            return {"total": 0, "by_type": {}}

        by_type: dict[str, int] = {}
        for fb in feedback:
            by_type[fb.feedback_type] = by_type.get(fb.feedback_type, 0) + 1

        # Compute satisfaction indicators
        positive = sum(
            1 for fb in feedback
            if fb.feedback_type in ("correct", "good_rejection", "good_explanation")
        )
        negative = sum(
            1 for fb in feedback
            if fb.feedback_type in ("incorrect", "too_risky", "bad_symbol_choice")
        )
        total = len(feedback)

        return {
            "total": total,
            "by_type": by_type,
            "positive_rate": positive / total if total > 0 else 0.0,
            "negative_rate": negative / total if total > 0 else 0.0,
            "improvement_areas": self._identify_improvement_areas(by_type),
        }

    def get_actionable_feedback(self) -> list[dict]:
        """Return feedback that should trigger improvements."""
        feedback = self._store.get_feedback(limit=100)
        actionable = []
        for fb in feedback:
            if fb.feedback_type in (
                "incorrect", "too_risky", "unclear_explanation",
                "missing_macro_context", "missing_risk_reason",
                "needs_more_evidence", "needs_doc_update",
            ):
                actionable.append({
                    "audit_id": fb.audit_id,
                    "decision_id": fb.decision_audit_id,
                    "type": fb.feedback_type,
                    "text": fb.feedback_text,
                    "severity": fb.severity,
                    "applied_to_reflection": fb.applied_to_reflection,
                    "applied_to_strategy": fb.applied_to_strategy_score,
                    "applied_to_prompt": fb.applied_to_prompt_score,
                })
        return actionable

    @staticmethod
    def _identify_improvement_areas(by_type: dict[str, int]) -> list[str]:
        """Identify areas needing improvement based on feedback patterns."""
        areas = []
        if by_type.get("unclear_explanation", 0) > 3:
            areas.append("Improve trade explanation clarity")
        if by_type.get("missing_macro_context", 0) > 2:
            areas.append("Add more macro context to analysis")
        if by_type.get("missing_risk_reason", 0) > 2:
            areas.append("Provide clearer risk rejection reasons")
        if by_type.get("too_risky", 0) > 3:
            areas.append("Review risk thresholds — may be too loose")
        if by_type.get("too_conservative", 0) > 5:
            areas.append("Review risk thresholds — may be too tight (requires human review)")
        if by_type.get("needs_doc_update", 0) > 1:
            areas.append("Update documentation")
        return areas
