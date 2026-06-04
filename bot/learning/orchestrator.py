"""RUNECLAW AI Learning — Learning Orchestrator.

Central coordinator for the full AI learning workflow:
Observe → Decide → Log → Simulate → Review → Score → Learn → Validate → Approve → Version

Safety beats performance. Compliance beats speed.
Auditability beats black-box automation. If uncertain, fail closed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .experience import ExperienceMemory
from .feedback import FeedbackCollector
from .macro_learner import MacroLearner
from .model_compare import ModelComparer
from .models import (
    ChangeClassification,
    DecisionMemory,
    ImprovementProposal,
    LearningTier,
    ReflectionMemory,
)
from .patterns import PatternLearner
from .prompt_opt import PromptOptimizer
from .reflection import ReflectionEngine
from .safety_policy import classify_proposal, validate_learning_action
from .store import LearningStore
from .strategy_eval import StrategyEvaluator

logger = logging.getLogger("runeclaw.learning")


class LearningOrchestrator:
    """Central learning system coordinator.

    Implements the 10-step learning workflow:
    1. Observe — collect market/macro/strategy signals
    2. Decide — run strategy + risk engine
    3. Log — write to decision memory
    4. Simulate — paper trade only (default)
    5. Review — generate reflection after result
    6. Score — update strategy scorecard
    7. Learn — create lessons + proposals
    8. Validate — check if proposal improves safety
    9. Approve — auto-apply docs/tests; human review for trading logic
    10. Version — save with changelog and rollback plan

    The orchestrator NEVER bypasses the risk engine.
    The orchestrator NEVER enables live trading.
    The orchestrator NEVER deletes audit logs.
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.store = LearningStore(data_dir)
        self.experience = ExperienceMemory(self.store)
        self.reflection = ReflectionEngine(self.store)
        self.strategy = StrategyEvaluator(self.store)
        self.patterns = PatternLearner(self.store)
        self.models = ModelComparer(self.store)
        self.prompts = PromptOptimizer(self.store)
        self.feedback = FeedbackCollector(self.store)
        self.macro = MacroLearner(self.store)

    # ── Step 3: Log Decision ──────────────────────────────────────

    def log_decision(self, **kwargs) -> DecisionMemory:
        """Record a trading decision to experience memory."""
        return self.experience.record_trade_decision(**kwargs)

    # ── Step 5: Review (post-trade reflection) ────────────────────

    def review_trade(
        self,
        decision: DecisionMemory,
        actual_pnl: Optional[float] = None,
        exit_price: Optional[float] = None,
        max_adverse_excursion: Optional[float] = None,
    ) -> ReflectionMemory:
        """Generate reflection after paper trade completes."""
        # Record result in experience
        if actual_pnl is not None:
            self.experience.record_trade_result(
                decision.audit_id,
                pnl_result=actual_pnl,
                gross_pnl=actual_pnl,  # simplified
                commission=0.0,
            )

        # Generate reflection
        refl = self.reflection.reflect_on_trade(
            decision,
            actual_pnl=actual_pnl,
            exit_price=exit_price,
            max_adverse_excursion=max_adverse_excursion,
        )

        # Auto-propose improvement if reflection suggests one
        if refl.recommended_improvement:
            self.reflection.propose_improvement(refl)

        return refl

    def review_rejection(self, decision: DecisionMemory) -> ReflectionMemory:
        """Generate reflection on a rejected trade."""
        return self.reflection.reflect_on_rejection(decision)

    # ── Step 6: Score Strategies ──────────────────────────────────

    def score_strategy(self, strategy_name: str):
        """Evaluate and update strategy scorecard."""
        return self.strategy.evaluate_strategy(strategy_name)

    def rank_strategies(self):
        """Get all strategies ranked by safety score."""
        return self.strategy.rank_strategies()

    # ── Step 7: Learn (patterns + macro) ──────────────────────────

    def detect_patterns(self):
        """Scan for recurring market patterns."""
        return self.patterns.detect_patterns()

    def get_learning_context(
        self,
        symbol: str = "",
        market_regime: str = "",
        macro_state: str = "",
    ) -> dict:
        """Get AI learning context for a trade decision.

        This context enriches the decision but does NOT override
        risk engine or create trade signals.
        """
        similar = self.experience.get_similar_setups(
            symbol=symbol,
            market_regime=market_regime,
            direction="",
        )
        patterns = self.patterns.get_relevant_patterns(
            symbol=symbol,
            market_regime=market_regime,
            macro_state=macro_state,
        )
        model_summary = self.models.get_accuracy_summary()
        feedback_summary = self.feedback.get_feedback_summary()

        return {
            "similar_past_setups": len(similar),
            "avg_past_pnl": (
                sum(s.pnl_result or 0 for s in similar) / len(similar)
                if similar else None
            ),
            "relevant_patterns": [
                {
                    "type": p.pattern_type,
                    "confidence": p.confidence,
                    "sample_size": p.sample_size,
                    "experimental": p.is_experimental,
                    "win_rate": p.historical_win_rate,
                }
                for p in patterns[:5]
            ],
            "model_agreement_rate": model_summary.get("agreement_rate"),
            "feedback_positive_rate": feedback_summary.get("positive_rate"),
            "may_override_risk_engine": False,  # ALWAYS False
        }

    # ── Step 8-9: Validate & Approve Proposals ────────────────────

    def process_proposals(self) -> dict:
        """Process pending improvement proposals.

        Auto-apply: SAFE_AUTO_DOCS, SAFE_AUTO_TEST
        Queue for human: HUMAN_REVIEW_REQUIRED
        Block: BLOCKED_RISK_INCREASE, BLOCKED_COMPLIANCE_RISK
        """
        pending = self.store.get_proposals(status="pending")
        results = {"auto_applied": 0, "queued": 0, "blocked": 0}

        for proposal in pending:
            # Re-classify (idempotent safety check)
            classify_proposal(proposal)

            if proposal.classification in (
                ChangeClassification.BLOCKED_RISK_INCREASE.value,
                ChangeClassification.BLOCKED_COMPLIANCE_RISK.value,
            ):
                proposal.status = "rejected"
                results["blocked"] += 1
                logger.warning("BLOCKED proposal: %s", proposal.audit_id)

            elif proposal.classification == ChangeClassification.SAFE_AUTO_DOCS.value:
                proposal.status = "applied"
                results["auto_applied"] += 1
                logger.info("Auto-applied docs proposal: %s", proposal.audit_id)

            elif proposal.classification == ChangeClassification.SAFE_AUTO_TEST.value:
                proposal.status = "applied"
                results["auto_applied"] += 1
                logger.info("Auto-applied test proposal: %s", proposal.audit_id)

            else:
                proposal.status = "queued"
                results["queued"] += 1
                logger.info("Queued for human review: %s", proposal.audit_id)

        # Re-save updated proposals (persist status changes to disk)
        all_proposals = self.store.get_proposals()
        # Update statuses
        pending_ids = {p.audit_id for p in pending}
        for p in all_proposals:
            if p.audit_id in pending_ids:
                match = next((pp for pp in pending if pp.audit_id == p.audit_id), None)
                if match:
                    p.status = match.status
        # Persist the updated proposals back to disk
        self.store._write_json("backlog", [
            p.model_dump(mode="json") if hasattr(p, "model_dump") else p.__dict__
            for p in all_proposals
        ])

        return results

    # ── Human Feedback ────────────────────────────────────────────

    def submit_feedback(
        self,
        decision_audit_id: str,
        feedback_type: str,
        feedback_text: str = "",
        severity: str = "normal",
    ):
        """Record human feedback."""
        return self.feedback.record(
            decision_audit_id=decision_audit_id,
            feedback_type=feedback_type,
            feedback_text=feedback_text,
            severity=severity,
        )

    # ── Learning Score ────────────────────────────────────────────

    def compute_learning_score(self) -> dict:
        """Compute RUNECLAW Learning Score across all dimensions.

        Dimensions:
        - safety_improvement, drawdown_reduction, explanation_quality
        - audit_completeness, consistency, reduced_false_positives
        - reduced_overtrading, better_macro_avoidance
        - lower_hallucination_risk, lower_token_cost, lower_latency

        Score categories: S/A/B/C/D/BLOCKED
        """
        stats = self.store.stats()
        fb_summary = self.feedback.get_feedback_summary()
        model_summary = self.models.get_accuracy_summary()
        scorecards = self.strategy.rank_strategies()

        # Dimensional scores (0-10 each)
        dimensions = {}

        # Safety: based on feedback and risk checks
        neg_rate = fb_summary.get("negative_rate", 0.5)
        dimensions["safety_improvement"] = max(0, 10 * (1 - neg_rate))

        # Drawdown reduction: lower avg max drawdown across strategies = better
        avg_dd = (
            sum(s.max_drawdown for s in scorecards) / len(scorecards)
            if scorecards else 500.0
        )
        dimensions["drawdown_reduction"] = max(0, min(10, 10 * (1 - avg_dd / 500)))

        # Explanation quality: proxy via positive feedback rate
        pos_rate = fb_summary.get("positive_rate", 0.5) or 0.5
        dimensions["explanation_quality"] = 10 * pos_rate

        # Audit completeness: based on data volume
        total_records = sum(stats.values())
        dimensions["audit_completeness"] = min(10, total_records / 10)

        # Consistency: model agreement rate
        agree_rate = model_summary.get("agreement_rate", 0.5) or 0.5
        dimensions["consistency"] = 10 * agree_rate

        # False positives: from scorecards
        avg_fp = (
            sum(s.false_positive_rate for s in scorecards) / len(scorecards)
            if scorecards else 0.5
        )
        dimensions["reduced_false_positives"] = max(0, 10 * (1 - avg_fp))

        # Token cost: from model comparison
        avg_cost = model_summary.get("avg_token_cost", 0.01) or 0.01
        dimensions["lower_token_cost"] = min(10, 0.01 / max(avg_cost, 0.0001))

        # Composite
        composite = sum(dimensions.values()) / len(dimensions) if dimensions else 0

        # Tier
        if composite >= 8:
            tier = LearningTier.S.value
        elif composite >= 6:
            tier = LearningTier.A.value
        elif composite >= 4:
            tier = LearningTier.B.value
        elif composite >= 2:
            tier = LearningTier.C.value
        else:
            tier = LearningTier.D.value

        return {
            "composite_score": round(composite, 2),
            "tier": tier,
            "dimensions": {k: round(v, 2) for k, v in dimensions.items()},
            "total_records": total_records,
            "feedback_total": fb_summary.get("total", 0),
            "strategies_evaluated": len(scorecards),
        }

    # ── Dashboard ─────────────────────────────────────────────────

    def dashboard(self) -> dict:
        """Full learning system dashboard."""
        stats = self.store.stats()

        # Build module_details with last_update timestamps.
        # Use current UTC time as proxy for last_update when data exists.
        now_iso = datetime.now(timezone.utc).isoformat()
        module_details: dict[str, dict] = {}
        for key, data_key in [
            ("patterns", "decisions"),
            ("regime", "decisions"),
            ("indicator_weights", "decisions"),
            ("feedback", "decisions"),
            ("volatility", "decisions"),
            ("correlations", "decisions"),
            ("drawdown", "decisions"),
            ("timing", "decisions"),
        ]:
            count = stats.get(data_key, 0)
            module_details[key] = {
                "observations": count,
                "last_update": now_iso if count > 0 else None,
            }

        return {
            "store_stats": stats,
            "module_details": module_details,
            "learning_score": self.compute_learning_score(),
            "strategy_rankings": [
                {
                    "name": s.strategy_name,
                    "tier": s.learning_tier,
                    "safety": s.safety_score,
                    "win_rate": f"{s.win_rate:.0%}",
                    "trades": s.total_trades,
                    "overfitting": s.overfitting_warning,
                }
                for s in self.strategy.rank_strategies()[:10]
            ],
            "feedback_summary": self.feedback.get_feedback_summary(),
            "model_accuracy": self.models.get_accuracy_summary(),
            "prompt_versions": self.prompts.get_version_report(),
            "pending_proposals": len(self.store.get_proposals(status="pending")),
            "blocked_proposals": len(self.store.get_proposals(status="rejected")),
        }
