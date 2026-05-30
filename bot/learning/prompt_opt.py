"""RUNECLAW AI Learning — Prompt Optimization Module.

Track prompt versions, measure performance, and propose improvements.
Rules:
- Prompt changes must be versioned and tested in sandbox.
- Prompt changes must NOT weaken safety wording.
- Prompt changes must NOT remove fail-closed behavior.
- Prompt changes must NOT create profit guarantees.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from .models import PromptVersion, ImprovementProposal, ChangeClassification
from .safety_policy import validate_prompt_safety, classify_proposal
from .store import LearningStore

logger = logging.getLogger("runeclaw.learning.prompt_opt")


class PromptOptimizer:
    """Version, evaluate, and safely improve prompts.

    Every prompt change goes through safety validation before recording.
    """

    def __init__(self, store: LearningStore):
        self._store = store
        self._current_version: Optional[str] = None

    def register_version(
        self,
        version_id: str,
        prompt_text: str,
        model_used: str = "gpt-4o",
    ) -> PromptVersion:
        """Register a new prompt version after safety validation."""
        # Safety check FIRST
        is_safe, violations = validate_prompt_safety(prompt_text)
        if not is_safe:
            logger.error(
                "BLOCKED prompt version %s: %s",
                version_id, "; ".join(violations),
            )
            raise ValueError(
                f"Prompt version {version_id} failed safety validation: "
                + "; ".join(violations)
            )

        pv = PromptVersion(
            version_id=version_id,
            model_used=model_used,
            prompt_text_hash=hashlib.sha256(prompt_text.encode()).hexdigest()[:16],
            safety_wording_intact=True,
            fail_closed_intact=True,
        )
        self._store.record_prompt_version(pv)
        self._current_version = version_id
        logger.info("Prompt version registered: %s", version_id)
        return pv

    def record_usage(
        self,
        version_id: str,
        *,
        json_valid: bool = True,
        reasoning_clarity: float = 0.5,
        false_signal: bool = False,
        cost: float = 0.0,
        latency_ms: float = 0.0,
    ) -> None:
        """Record a prompt usage observation for scoring."""
        versions = self._store.get_prompt_versions()
        pv = versions.get(version_id)
        if not pv:
            logger.warning("Unknown prompt version: %s", version_id)
            return

        # Update running averages
        n = pv.total_uses + 1
        pv.total_uses = n
        pv.json_validity_rate = ((pv.json_validity_rate * (n - 1)) + (1.0 if json_valid else 0.0)) / n
        pv.reasoning_clarity_score = ((pv.reasoning_clarity_score * (n - 1)) + reasoning_clarity) / n
        pv.false_signal_rate = ((pv.false_signal_rate * (n - 1)) + (1.0 if false_signal else 0.0)) / n
        pv.avg_cost_per_call = ((pv.avg_cost_per_call * (n - 1)) + cost) / n
        pv.avg_latency_ms = ((pv.avg_latency_ms * (n - 1)) + latency_ms) / n

        self._store.record_prompt_version(pv)

    def record_human_feedback(self, version_id: str, score: float) -> None:
        """Incorporate human feedback score (0-1) for a prompt version."""
        versions = self._store.get_prompt_versions()
        pv = versions.get(version_id)
        if not pv:
            return
        n = pv.total_uses or 1
        pv.human_feedback_score = ((pv.human_feedback_score * (n - 1)) + score) / n
        self._store.record_prompt_version(pv)

    def get_best_version(self) -> Optional[str]:
        """Return the best-performing prompt version by composite score."""
        versions = self._store.get_prompt_versions()
        if not versions:
            return None

        scored = []
        for vid, pv in versions.items():
            if pv.total_uses < 5:
                continue  # need minimum data
            if not pv.safety_wording_intact or not pv.fail_closed_intact:
                continue  # skip unsafe versions

            # Composite score: clarity, validity, low false signals, low cost
            composite = (
                pv.json_validity_rate * 0.25
                + pv.reasoning_clarity_score * 0.25
                + (1 - pv.false_signal_rate) * 0.25
                + pv.human_feedback_score * 0.15
                + min(1.0, 0.01 / max(pv.avg_cost_per_call, 0.0001)) * 0.10  # cost efficiency
            )
            scored.append((vid, composite))

        if not scored:
            return None

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    def propose_prompt_improvement(
        self,
        current_version_id: str,
        problem: str,
        proposed_change: str,
    ) -> Optional[ImprovementProposal]:
        """Propose a prompt change — goes through full safety classification."""
        proposal = ImprovementProposal(
            source="prompt_optimizer",
            problem=problem,
            evidence=f"Based on {current_version_id} performance metrics",
            proposed_change=proposed_change,
            expected_benefit="Improved prompt clarity or accuracy",
            risk_impact="Prompt changes may affect signal quality",
            rollback_plan=f"Revert to prompt version {current_version_id}",
            test_plan="Run 20 paper-trading signals with new prompt and compare",
        )

        classification = classify_proposal(proposal)

        # Extra guard: check for safety-weakening language
        is_safe, violations = validate_prompt_safety(proposed_change)
        if not is_safe:
            proposal.classification = ChangeClassification.BLOCKED_COMPLIANCE_RISK.value
            proposal.human_approval_required = True
            logger.warning("Prompt proposal blocked: %s", violations)

        self._store.record_proposal(proposal)
        return proposal

    def get_version_report(self) -> list[dict]:
        """Get all prompt versions with performance scores."""
        versions = self._store.get_prompt_versions()
        report = []
        for vid, pv in sorted(versions.items()):
            report.append({
                "version": vid,
                "uses": pv.total_uses,
                "json_validity": f"{pv.json_validity_rate:.0%}",
                "clarity": f"{pv.reasoning_clarity_score:.2f}",
                "false_signals": f"{pv.false_signal_rate:.0%}",
                "avg_cost": f"${pv.avg_cost_per_call:.4f}",
                "avg_latency": f"{pv.avg_latency_ms:.0f}ms",
                "human_score": f"{pv.human_feedback_score:.2f}",
                "safe": pv.safety_wording_intact and pv.fail_closed_intact,
            })
        return report
