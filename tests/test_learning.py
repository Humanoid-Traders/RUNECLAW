"""
RUNECLAW AI Learning System — Comprehensive Test Suite.

Tests all 8 learning modules, safety policy, data stores,
and integration with the engine.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# ── Imports ───────────────────────────────────────────────────────────

from bot.learning.models import (
    ChangeClassification,
    DecisionMemory,
    FeedbackType,
    HumanFeedback,
    ImprovementProposal,
    LearningTier,
    MacroEventMemory,
    ModelComparison,
    PatternType,
    PromptVersion,
    ReflectionMemory,
    StrategyScorecard,
)
from bot.learning.safety_policy import (
    BLOCKED_ACTIONS,
    classify_proposal,
    validate_learning_action,
    validate_prompt_safety,
)
from bot.learning.store import LearningStore
from bot.learning.experience import ExperienceMemory
from bot.learning.reflection import ReflectionEngine
from bot.learning.strategy_eval import StrategyEvaluator
from bot.learning.patterns import PatternLearner
from bot.learning.model_compare import ModelComparer
from bot.learning.prompt_opt import PromptOptimizer
from bot.learning.feedback import FeedbackCollector
from bot.learning.macro_learner import MacroLearner
from bot.learning.orchestrator import LearningOrchestrator


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def store(tmp_dir):
    return LearningStore(data_dir=tmp_dir)


@pytest.fixture
def orchestrator(tmp_dir):
    return LearningOrchestrator(data_dir=tmp_dir)


# ══════════════════════════════════════════════════════════════════════
# 1. DATA MODELS
# ══════════════════════════════════════════════════════════════════════

class TestLearningModels:
    def test_decision_memory_has_audit_id(self):
        d = DecisionMemory(symbol="BTCUSDT", direction="LONG")
        assert d.audit_id.startswith("LRN-")
        assert len(d.audit_id) == 16  # LRN- + 12 hex chars

    def test_decision_memory_defaults(self):
        d = DecisionMemory()
        assert d.source == "runeclaw_engine"
        assert d.mode == "paper"
        assert d.confidence == 0.0

    def test_reflection_memory_defaults(self):
        r = ReflectionMemory()
        assert r.needs_human_review is True
        assert r.allowed_to_auto_apply is False

    def test_pattern_record_may_not_override_risk(self):
        from bot.learning.models import PatternRecord
        p = PatternRecord(pattern_type=PatternType.TREND_CONTINUATION.value)
        assert p.may_override_risk is False  # ALWAYS False

    def test_improvement_proposal_defaults_to_human_review(self):
        p = ImprovementProposal(problem="test", proposed_change="test")
        assert p.human_approval_required is True
        assert p.classification == ChangeClassification.HUMAN_REVIEW_REQUIRED.value

    def test_strategy_scorecard_defaults(self):
        s = StrategyScorecard(strategy_name="test")
        assert s.learning_tier == LearningTier.C.value
        assert s.overfitting_warning is False


# ══════════════════════════════════════════════════════════════════════
# 2. SAFETY POLICY
# ══════════════════════════════════════════════════════════════════════

class TestSafetyPolicy:
    def test_blocked_actions_cannot_be_validated(self):
        for action in BLOCKED_ACTIONS:
            assert validate_learning_action(action) is False

    def test_allowed_actions_pass(self):
        assert validate_learning_action("improve_explanation") is True
        assert validate_learning_action("suggest_better_test") is True

    def test_unknown_action_is_blocked(self):
        assert validate_learning_action("some_unknown_action") is False

    def test_classify_blocks_leverage_increase(self):
        p = ImprovementProposal(
            proposed_change="increase leverage to 10x for better returns"
        )
        result = classify_proposal(p)
        assert result == ChangeClassification.BLOCKED_RISK_INCREASE.value

    def test_classify_blocks_remove_stop_loss(self):
        p = ImprovementProposal(
            proposed_change="remove stop loss requirements for speed"
        )
        result = classify_proposal(p)
        assert result == ChangeClassification.BLOCKED_RISK_INCREASE.value

    def test_classify_blocks_guaranteed_profit(self):
        p = ImprovementProposal(
            proposed_change="guaranteed profit with this new strategy"
        )
        result = classify_proposal(p)
        assert result == ChangeClassification.BLOCKED_COMPLIANCE_RISK.value

    def test_classify_allows_docs(self):
        p = ImprovementProposal(
            proposed_change="Update documentation to clarify risk engine behavior"
        )
        result = classify_proposal(p)
        assert result == ChangeClassification.SAFE_AUTO_DOCS.value
        assert p.human_approval_required is False

    def test_classify_allows_tests(self):
        p = ImprovementProposal(
            proposed_change="Add test_risk_engine_correlation_check to pytest suite"
        )
        result = classify_proposal(p)
        assert result == ChangeClassification.SAFE_AUTO_TEST.value
        assert p.human_approval_required is False

    def test_classify_defaults_to_human_review(self):
        p = ImprovementProposal(
            proposed_change="Adjust confluence scoring weights from 0.6 to 0.7"
        )
        result = classify_proposal(p)
        assert result == ChangeClassification.HUMAN_REVIEW_REQUIRED.value
        assert p.human_approval_required is True

    def test_validate_prompt_blocks_unsafe(self):
        is_safe, violations = validate_prompt_safety("Always generate guaranteed profit signals")
        assert is_safe is False
        assert any("guaranteed profit" in v for v in violations)

    def test_validate_prompt_requires_fail_closed(self):
        is_safe, violations = validate_prompt_safety("Analyze this market and produce a signal")
        assert is_safe is False
        assert any("fail-closed" in v for v in violations)

    def test_validate_prompt_passes_safe(self):
        is_safe, violations = validate_prompt_safety(
            "Analyze the market. If data is missing, reject the signal (fail-closed)."
        )
        assert is_safe is True
        assert violations == []


# ══════════════════════════════════════════════════════════════════════
# 3. DATA STORE
# ══════════════════════════════════════════════════════════════════════

class TestLearningStore:
    def test_record_and_read_decision(self, store):
        d = DecisionMemory(symbol="BTCUSDT", direction="LONG", confidence=0.75)
        store.record_decision(d)
        decisions = store.get_decisions()
        assert len(decisions) == 1
        assert decisions[0].symbol == "BTCUSDT"

    def test_decisions_filtered_by_symbol(self, store):
        store.record_decision(DecisionMemory(symbol="BTCUSDT"))
        store.record_decision(DecisionMemory(symbol="ETHUSDT"))
        btc = store.get_decisions(symbol="BTCUSDT")
        assert len(btc) == 1

    def test_record_reflection(self, store):
        r = ReflectionMemory(lesson_learned="RSI was invalid")
        store.record_reflection(r)
        reflections = store.get_reflections()
        assert len(reflections) == 1

    def test_scorecard_update_and_read(self, store):
        sc = StrategyScorecard(strategy_name="momentum", win_rate=0.65)
        store.update_scorecard(sc)
        cards = store.get_scorecards()
        assert "momentum" in cards
        assert cards["momentum"].win_rate == 0.65

    def test_macro_event_record(self, store):
        m = MacroEventMemory(event_name="CPI", event_type="CPI", surprise_score=0.5)
        store.record_macro_event(m)
        events = store.get_macro_events(event_type="CPI")
        assert len(events) == 1

    def test_feedback_record(self, store):
        fb = HumanFeedback(feedback_type="correct")
        store.record_feedback(fb)
        assert len(store.get_feedback()) == 1

    def test_proposal_record(self, store):
        p = ImprovementProposal(problem="test", proposed_change="add test")
        store.record_proposal(p)
        assert len(store.get_proposals()) == 1

    def test_stats(self, store):
        store.record_decision(DecisionMemory(symbol="BTCUSDT"))
        store.record_reflection(ReflectionMemory())
        stats = store.stats()
        assert stats["decisions"] == 1
        assert stats["reflections"] == 1

    def test_corrupt_jsonl_line_skipped(self, store):
        # Write a valid line then a corrupt line
        path = store._files["decision"]
        d = DecisionMemory(symbol="BTCUSDT")
        store.record_decision(d)
        with open(path, "a") as f:
            f.write("THIS IS NOT VALID JSON\n")
        store.record_decision(DecisionMemory(symbol="ETHUSDT"))
        decisions = store.get_decisions()
        assert len(decisions) == 2  # corrupt line skipped


# ══════════════════════════════════════════════════════════════════════
# 4. EXPERIENCE MEMORY
# ══════════════════════════════════════════════════════════════════════

class TestExperienceMemory:
    def test_record_trade_decision(self, store):
        exp = ExperienceMemory(store)
        d = exp.record_trade_decision(
            symbol="BTCUSDT", direction="LONG", confidence=0.75,
            confluence_score=0.68, entry_price=67000, stop_loss=66000,
            take_profit=70000, risk_reward=3.0, position_size_usd=200,
            decision="TRADE_ACCEPTED_PAPER",
        )
        assert d.audit_id.startswith("LRN-")
        assert d.symbol == "BTCUSDT"

    def test_get_similar_setups(self, store):
        exp = ExperienceMemory(store)
        exp.record_trade_decision(
            symbol="BTCUSDT", direction="LONG", confidence=0.7,
            confluence_score=0.6, entry_price=67000, stop_loss=66000,
            take_profit=70000, risk_reward=3.0, position_size_usd=200,
            market_regime="TREND_UP", decision="TRADE_ACCEPTED_PAPER",
        )
        # Record a result
        decisions = store.get_decisions()
        decisions[0].pnl_result = 150.0
        store.record_decision(decisions[0])

        similar = exp.get_similar_setups("BTCUSDT", "TREND_UP", "LONG")
        assert len(similar) >= 1

    def test_get_rejection_patterns(self, store):
        exp = ExperienceMemory(store)
        exp.record_trade_decision(
            symbol="BTCUSDT", direction="LONG", confidence=0.5,
            confluence_score=0.4, entry_price=67000, stop_loss=66000,
            take_profit=70000, risk_reward=3.0, position_size_usd=200,
            risk_engine_result="REJECTED", rejected_reason="low confidence",
            decision="TRADE_REJECTED_FAIL_CLOSED",
        )
        rejects = exp.get_rejection_patterns()
        assert len(rejects) == 1


# ══════════════════════════════════════════════════════════════════════
# 5. REFLECTION ENGINE
# ══════════════════════════════════════════════════════════════════════

class TestReflectionEngine:
    def test_reflect_on_winning_trade(self, store):
        re = ReflectionEngine(store)
        d = DecisionMemory(
            symbol="BTCUSDT", direction="LONG", confidence=0.75,
            entry_price=67000, stop_loss=66000, take_profit=70000,
            risk_reward=3.0, risk_engine_result="APPROVED",
        )
        refl = re.reflect_on_trade(d, actual_pnl=150.0, exit_price=70000)
        assert refl.signal_valid is True
        assert refl.risk_decision_correct is True
        assert "Profitable" in refl.lesson_learned

    def test_reflect_on_losing_trade(self, store):
        re = ReflectionEngine(store)
        d = DecisionMemory(
            symbol="BTCUSDT", direction="LONG", confidence=0.8,
            entry_price=67000, stop_loss=66000, take_profit=70000,
            risk_reward=3.0, risk_engine_result="APPROVED",
        )
        refl = re.reflect_on_trade(d, actual_pnl=-100.0, exit_price=66000)
        assert refl.signal_valid is False
        assert refl.risk_decision_correct is False

    def test_reflect_on_rejection(self, store):
        re = ReflectionEngine(store)
        d = DecisionMemory(
            symbol="ETHUSDT", direction="SHORT", confidence=0.55,
            risk_engine_result="REJECTED", rejected_reason="low confidence",
        )
        refl = re.reflect_on_rejection(d)
        assert "REJECTED" in refl.what_happened
        assert refl.needs_human_review is False

    def test_propose_improvement_from_reflection(self, store):
        re = ReflectionEngine(store)
        d = DecisionMemory(
            symbol="BTCUSDT", direction="LONG", confidence=0.8,
            risk_engine_result="APPROVED",
        )
        refl = re.reflect_on_trade(d, actual_pnl=-100.0, exit_price=66000)
        proposal = re.propose_improvement(refl)
        assert proposal is not None
        assert proposal.classification in [c.value for c in ChangeClassification]

    def test_reflection_confidence_capped(self, store):
        re = ReflectionEngine(store)
        d = DecisionMemory(confidence=0.95)
        refl = re.reflect_on_trade(d, actual_pnl=50.0)
        assert refl.confidence <= 0.8  # never overconfident in lessons


# ══════════════════════════════════════════════════════════════════════
# 6. STRATEGY EVALUATION
# ══════════════════════════════════════════════════════════════════════

class TestStrategyEvaluation:
    def _seed_decisions(self, store, strategy, wins, losses):
        for i in range(wins):
            d = DecisionMemory(
                strategy_signal=strategy, pnl_result=50.0,
                confidence=0.7, market_regime="TREND_UP",
            )
            store.record_decision(d)
        for i in range(losses):
            d = DecisionMemory(
                strategy_signal=strategy, pnl_result=-30.0,
                confidence=0.65, market_regime="TREND_UP",
            )
            store.record_decision(d)

    def test_evaluate_empty(self, store):
        ev = StrategyEvaluator(store)
        sc = ev.evaluate_strategy("momentum")
        assert sc.total_trades == 0
        assert sc.learning_tier == LearningTier.C.value

    def test_evaluate_winning_strategy(self, store):
        self._seed_decisions(store, "momentum", 20, 5)
        ev = StrategyEvaluator(store)
        sc = ev.evaluate_strategy("momentum")
        assert sc.win_rate == 0.8
        assert sc.profit_factor > 1.0
        assert sc.total_trades == 25

    def test_overfitting_warning(self, store):
        self._seed_decisions(store, "magic", 9, 1)
        ev = StrategyEvaluator(store)
        sc = ev.evaluate_strategy("magic")
        assert sc.overfitting_warning is True  # <30 trades, >80% win rate

    def test_rank_strategies(self, store):
        self._seed_decisions(store, "strategy_a", 30, 10)
        self._seed_decisions(store, "strategy_b", 5, 15)
        ev = StrategyEvaluator(store)
        ev.evaluate_strategy("strategy_a")
        ev.evaluate_strategy("strategy_b")
        ranked = ev.rank_strategies()
        assert len(ranked) == 2
        assert ranked[0].safety_score >= ranked[1].safety_score

    def test_max_drawdown_calculation(self):
        curve = [100, 120, 110, 90, 130, 85]
        dd = StrategyEvaluator._max_drawdown(curve)
        assert dd == 45  # peak 130, trough 85

    def test_performance_by_regime(self, store):
        d = DecisionMemory(
            strategy_signal="test", pnl_result=50.0, market_regime="TREND_UP"
        )
        store.record_decision(d)
        ev = StrategyEvaluator(store)
        sc = ev.evaluate_strategy("test")
        assert "TREND_UP" in sc.performance_by_regime


# ══════════════════════════════════════════════════════════════════════
# 7. PATTERN LEARNING
# ══════════════════════════════════════════════════════════════════════

class TestPatternLearning:
    def test_detect_trend_continuation(self, store):
        for i in range(10):
            store.record_decision(DecisionMemory(
                market_regime="TREND_UP", pnl_result=50.0,
                strategy_signal="test",
            ))
        pl = PatternLearner(store)
        patterns = pl.detect_patterns()
        trend_patterns = [p for p in patterns if p.pattern_type == PatternType.TREND_CONTINUATION.value]
        assert len(trend_patterns) > 0
        assert trend_patterns[0].may_override_risk is False  # CRITICAL

    def test_detect_breakout_failure(self, store):
        for i in range(8):
            store.record_decision(DecisionMemory(
                market_regime="RANGE", pnl_result=-30.0,
                strategy_signal="test",
            ))
        pl = PatternLearner(store)
        patterns = pl.detect_patterns()
        failures = [p for p in patterns if p.pattern_type == PatternType.BREAKOUT_FAILURE.value]
        assert len(failures) > 0

    def test_experimental_flag_on_small_sample(self, store):
        for i in range(5):
            store.record_decision(DecisionMemory(
                market_regime="TREND_DOWN", pnl_result=20.0,
            ))
        pl = PatternLearner(store)
        patterns = pl.detect_patterns()
        for p in patterns:
            if p.sample_size < 20:
                assert p.is_experimental is True

    def test_patterns_never_override_risk(self, store):
        for i in range(30):
            store.record_decision(DecisionMemory(
                market_regime="TREND_UP", pnl_result=100.0,
            ))
        pl = PatternLearner(store)
        for p in pl.detect_patterns():
            assert p.may_override_risk is False


# ══════════════════════════════════════════════════════════════════════
# 8. MODEL COMPARISON
# ══════════════════════════════════════════════════════════════════════

class TestModelComparison:
    def test_record_agreement(self, store):
        mc = ModelComparer(store)
        comp = mc.record_comparison(
            symbol="BTCUSDT",
            base_direction="LONG", base_confidence=0.7,
            llm_direction="LONG", llm_confidence=0.75,
        )
        assert comp.models_agree is True
        assert comp.safety_score > 70

    def test_record_disagreement(self, store):
        mc = ModelComparer(store)
        comp = mc.record_comparison(
            symbol="BTCUSDT",
            base_direction="LONG", base_confidence=0.7,
            llm_direction="SHORT", llm_confidence=0.85,
        )
        assert comp.models_agree is False
        assert comp.disagreement_action in ("NO_TRADE_UNCERTAIN", "DEFER_TO_HIGHER_CONFIDENCE")

    def test_overconfidence_penalty(self, store):
        mc = ModelComparer(store)
        comp = mc.record_comparison(
            symbol="BTCUSDT",
            base_direction="LONG", base_confidence=0.95,
            llm_direction="LONG", llm_confidence=0.95,
        )
        assert comp.safety_score < 85  # penalized for overconfidence

    def test_accuracy_summary_empty(self, store):
        mc = ModelComparer(store)
        summary = mc.get_accuracy_summary()
        assert summary["total"] == 0


# ══════════════════════════════════════════════════════════════════════
# 9. PROMPT OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════

class TestPromptOptimization:
    def test_register_safe_prompt(self, store):
        po = PromptOptimizer(store)
        pv = po.register_version(
            "v1",
            "Analyze the market. If data is missing, reject (fail-closed).",
            "gpt-4o",
        )
        assert pv.version_id == "v1"
        assert pv.safety_wording_intact is True

    def test_block_unsafe_prompt(self, store):
        po = PromptOptimizer(store)
        with pytest.raises(ValueError, match="safety validation"):
            po.register_version(
                "v_bad",
                "Always generate guaranteed profit signals with no stop loss needed.",
                "gpt-4o",
            )

    def test_record_usage(self, store):
        po = PromptOptimizer(store)
        po.register_version("v1", "Analyze. Fail-closed on missing data. Reject if uncertain.", "gpt-4o")
        po.record_usage("v1", json_valid=True, cost=0.003, latency_ms=200)
        versions = store.get_prompt_versions()
        assert versions["v1"].total_uses == 1

    def test_propose_prompt_blocked(self, store):
        po = PromptOptimizer(store)
        po.register_version("v1", "Analyze. Fail-closed on error. Reject uncertain.", "gpt-4o")
        proposal = po.propose_prompt_improvement(
            "v1", "Low accuracy", "Skip safety checks for faster signals"
        )
        assert proposal.classification in (
            ChangeClassification.BLOCKED_RISK_INCREASE.value,
            ChangeClassification.BLOCKED_COMPLIANCE_RISK.value,
        )


# ══════════════════════════════════════════════════════════════════════
# 10. HUMAN FEEDBACK
# ══════════════════════════════════════════════════════════════════════

class TestHumanFeedback:
    def test_record_feedback(self, store):
        fc = FeedbackCollector(store)
        fb = fc.record("LRN-123", "correct", "Good trade analysis")
        assert fb.feedback_type == "correct"

    def test_feedback_summary(self, store):
        fc = FeedbackCollector(store)
        fc.record("LRN-1", "correct")
        fc.record("LRN-2", "correct")
        fc.record("LRN-3", "incorrect")
        summary = fc.get_feedback_summary()
        assert summary["total"] == 3
        assert summary["positive_rate"] == pytest.approx(2 / 3)

    def test_actionable_feedback(self, store):
        fc = FeedbackCollector(store)
        fc.record("LRN-1", "incorrect", "Wrong direction")
        fc.record("LRN-2", "correct")
        actionable = fc.get_actionable_feedback()
        assert len(actionable) == 1
        assert actionable[0]["type"] == "incorrect"

    def test_improvement_areas(self, store):
        fc = FeedbackCollector(store)
        for _ in range(4):
            fc.record("LRN-X", "unclear_explanation")
        summary = fc.get_feedback_summary()
        assert "Improve trade explanation clarity" in summary["improvement_areas"]


# ══════════════════════════════════════════════════════════════════════
# 11. MACRO LEARNER
# ══════════════════════════════════════════════════════════════════════

class TestMacroLearner:
    def test_record_event(self, store):
        ml = MacroLearner(store)
        m = ml.record_event(
            event_name="CPI Jan 2026",
            event_type="CPI",
            surprise_score=0.5,
            btc_5min_pct=-1.2,
            btc_30min_pct=-2.5,
        )
        assert m.audit_id.startswith("LRN-")

    def test_get_average_reaction(self, store):
        ml = MacroLearner(store)
        ml.record_event(event_name="CPI 1", event_type="CPI",
                        btc_5min_pct=-1.0, btc_30min_pct=-2.0)
        ml.record_event(event_name="CPI 2", event_type="CPI",
                        btc_5min_pct=1.0, btc_30min_pct=2.0)
        avg = ml.get_average_reaction("CPI")
        assert avg["sample_size"] == 2
        assert avg["avg_5min_reaction"] == pytest.approx(0.0)

    def test_risk_context(self, store):
        ml = MacroLearner(store)
        for i in range(5):
            ml.record_event(event_name=f"FOMC {i}", event_type="FOMC",
                            btc_30min_pct=(-3.0 if i < 3 else 0.5))
        ctx = ml.get_risk_context("FOMC")
        assert ctx["may_create_trade_signal"] is False  # ALWAYS False
        assert ctx["big_move_probability"] > 0.5

    def test_whipsaw_detection(self, store):
        ml = MacroLearner(store)
        m = ml.record_event(
            event_name="CPI Whipsaw", event_type="CPI",
            btc_5min_pct=2.0, btc_30min_pct=-1.5,
        )
        assert "reversal" in m.lesson_learned.lower() or "whipsaw" in m.lesson_learned.lower()


# ══════════════════════════════════════════════════════════════════════
# 12. ORCHESTRATOR INTEGRATION
# ══════════════════════════════════════════════════════════════════════

class TestOrchestrator:
    def test_log_decision(self, orchestrator):
        d = orchestrator.log_decision(
            symbol="BTCUSDT", direction="LONG", confidence=0.75,
            confluence_score=0.68, entry_price=67000, stop_loss=66000,
            take_profit=70000, risk_reward=3.0, position_size_usd=200,
            decision="TRADE_ACCEPTED_PAPER",
        )
        assert d.symbol == "BTCUSDT"

    def test_review_trade(self, orchestrator):
        d = orchestrator.log_decision(
            symbol="BTCUSDT", direction="LONG", confidence=0.75,
            confluence_score=0.68, entry_price=67000, stop_loss=66000,
            take_profit=70000, risk_reward=3.0, position_size_usd=200,
            risk_engine_result="APPROVED",
            decision="TRADE_ACCEPTED_PAPER",
        )
        refl = orchestrator.review_trade(d, actual_pnl=150.0, exit_price=70000)
        assert refl.signal_valid is True

    def test_review_rejection(self, orchestrator):
        d = orchestrator.log_decision(
            symbol="ETHUSDT", direction="SHORT", confidence=0.5,
            confluence_score=0.4, entry_price=2500, stop_loss=2600,
            take_profit=2300, risk_reward=2.0, position_size_usd=100,
            risk_engine_result="REJECTED", rejected_reason="low confidence",
            decision="TRADE_REJECTED_FAIL_CLOSED",
        )
        refl = orchestrator.review_rejection(d)
        assert "REJECTED" in refl.what_happened

    def test_learning_context(self, orchestrator):
        ctx = orchestrator.get_learning_context(
            symbol="BTCUSDT", market_regime="TREND_UP",
        )
        assert ctx["may_override_risk_engine"] is False  # ALWAYS False

    def test_compute_learning_score(self, orchestrator):
        score = orchestrator.compute_learning_score()
        assert "composite_score" in score
        assert "tier" in score
        assert score["tier"] in [t.value for t in LearningTier]

    def test_dashboard(self, orchestrator):
        dash = orchestrator.dashboard()
        assert "store_stats" in dash
        assert "learning_score" in dash
        assert "strategy_rankings" in dash

    def test_process_proposals_blocks_unsafe(self, orchestrator):
        p = ImprovementProposal(
            proposed_change="bypass risk engine for fast execution",
            status="pending",
        )
        orchestrator.store.record_proposal(p)
        results = orchestrator.process_proposals()
        assert results["blocked"] >= 1

    def test_process_proposals_auto_applies_docs(self, orchestrator):
        p = ImprovementProposal(
            proposed_change="Update documentation for the risk engine",
            status="pending",
        )
        orchestrator.store.record_proposal(p)
        results = orchestrator.process_proposals()
        assert results["auto_applied"] >= 1

    def test_submit_feedback(self, orchestrator):
        fb = orchestrator.submit_feedback(
            decision_audit_id="LRN-TEST123",
            feedback_type="correct",
            feedback_text="Good analysis",
        )
        assert fb.feedback_type == "correct"


# ══════════════════════════════════════════════════════════════════════
# 13. SAFETY INVARIANTS (CRITICAL)
# ══════════════════════════════════════════════════════════════════════

class TestSafetyInvariants:
    """These tests verify that the AI learning system NEVER
    violates core safety rules, regardless of inputs."""

    def test_patterns_never_override_risk_engine(self, store):
        """No pattern may ever set may_override_risk=True."""
        from bot.learning.models import PatternRecord
        for pt in PatternType:
            p = PatternRecord(pattern_type=pt.value)
            assert p.may_override_risk is False

    def test_learning_context_never_overrides_risk(self, orchestrator):
        ctx = orchestrator.get_learning_context(symbol="BTCUSDT")
        assert ctx["may_override_risk_engine"] is False

    def test_macro_context_never_creates_trade(self, store):
        ml = MacroLearner(store)
        ctx = ml.get_risk_context("FOMC")
        assert ctx["may_create_trade_signal"] is False

    def test_blocked_actions_comprehensive(self):
        """All dangerous actions must be in the blocked list."""
        dangerous = [
            "increase_leverage", "remove_stop_loss", "disable_risk_checks",
            "enable_live_trading", "delete_audit_logs", "bypass_macro_lockdown",
            "remove_human_approval", "hide_losses",
        ]
        for action in dangerous:
            assert action in BLOCKED_ACTIONS, f"{action} not in BLOCKED_ACTIONS!"

    def test_no_proposal_can_enable_live_trading(self, store):
        p = ImprovementProposal(
            proposed_change="Enable live trading for production deployment"
        )
        classify_proposal(p)
        assert p.classification in (
            ChangeClassification.BLOCKED_RISK_INCREASE.value,
            ChangeClassification.BLOCKED_COMPLIANCE_RISK.value,
        )

    def test_reflection_never_auto_applies(self, store):
        re = ReflectionEngine(store)
        d = DecisionMemory(direction="LONG", confidence=0.9)
        refl = re.reflect_on_trade(d, actual_pnl=1000.0)
        assert refl.allowed_to_auto_apply is False

    def test_scorecard_penalizes_high_drawdown(self, store):
        ev = StrategyEvaluator(store)
        safety_high_dd = ev._compute_safety_score(max_dd=600, profit_factor=3.0, fp_rate=0.1, total_trades=50)
        safety_low_dd = ev._compute_safety_score(max_dd=50, profit_factor=3.0, fp_rate=0.1, total_trades=50)
        assert safety_low_dd > safety_high_dd
