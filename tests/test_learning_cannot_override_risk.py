"""
RUNECLAW Adversarial Tests — Learning System Cannot Override Risk Engine.

F-06 FIX: Proves that the self-modifying learning loop cannot:
  (a) Widen any RiskLimits value
  (b) Flip simulation_mode or live_trading_enabled
  (c) Force-approve a rejected trade idea
  (d) Bypass safety classifications
  (e) Set may_override_risk=True on any pattern

These tests are the adversarial evidence that converts the
"learning cannot override risk" claim from assertion to proof.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# ═══════════════════════════════════════════════════════════════════════
# Test Group 1: PatternRecord.may_override_risk invariant
# ═══════════════════════════════════════════════════════════════════════

class TestPatternRiskOverrideInvariant:
    """The may_override_risk field must ALWAYS be False."""

    def test_default_is_false(self):
        from bot.learning.models import PatternRecord
        p = PatternRecord()
        assert p.may_override_risk is False

    def test_explicit_false_accepted(self):
        from bot.learning.models import PatternRecord
        p = PatternRecord(may_override_risk=False)
        assert p.may_override_risk is False

    def test_explicit_true_rejected(self):
        from bot.learning.models import PatternRecord
        with pytest.raises(ValidationError, match="may_override_risk"):
            PatternRecord(may_override_risk=True)

    def test_truthy_integer_rejected(self):
        """Attempt to sneak True via integer 1."""
        from bot.learning.models import PatternRecord
        with pytest.raises(ValidationError):
            PatternRecord(may_override_risk=1)

    def test_truthy_string_rejected(self):
        """Attempt to sneak True via non-empty string."""
        from bot.learning.models import PatternRecord
        with pytest.raises(ValidationError):
            PatternRecord(may_override_risk="yes")

    def test_dict_construction_also_validates(self):
        """Even constructing from a dict must enforce the validator."""
        from bot.learning.models import PatternRecord
        data = {"may_override_risk": True, "pattern_type": "test"}
        with pytest.raises(ValidationError, match="may_override_risk"):
            PatternRecord(**data)


# ═══════════════════════════════════════════════════════════════════════
# Test Group 2: Safety policy blocks risk-increasing proposals
# ═══════════════════════════════════════════════════════════════════════

class TestSafetyPolicyBlocking:
    """Adversarial proposals that attempt to weaken risk must be BLOCKED."""

    def _make_proposal(self, **overrides):
        from bot.learning.models import ImprovementProposal
        defaults = {
            "problem": "Performance too low",
            "proposed_change": "Adjust parameters",
            "expected_benefit": "Better returns",
            "risk_impact": "None",
        }
        defaults.update(overrides)
        return ImprovementProposal(**defaults)

    def test_increase_leverage_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="increase leverage to 10x")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_remove_stop_loss_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="remove stop loss for better PnL")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_disable_risk_checks_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="disable check #5 to allow more trades")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_enable_live_trading_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="enable live trading for production use")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_skip_risk_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="skip risk engine on high-confidence signals")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_bypass_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="bypass confirmation for fast execution")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_force_trade_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="force trade when confidence > 0.9")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_override_keyword_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="override position size limits")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_increase_size_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="increase size to 50% of equity")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_remove_limit_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="remove limit on daily losses")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_no_confirmation_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="trade with no confirmation needed")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"

    def test_real_money_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="switch to real money trading")
        result = classify_proposal(p)
        assert result == "BLOCKED_RISK_INCREASE"


# ═══════════════════════════════════════════════════════════════════════
# Test Group 3: Compliance risk proposals blocked
# ═══════════════════════════════════════════════════════════════════════

class TestComplianceRiskBlocking:
    """Proposals that weaken safety wording must be BLOCKED."""

    def _make_proposal(self, **overrides):
        from bot.learning.models import ImprovementProposal
        defaults = {
            "problem": "Users want more confidence",
            "proposed_change": "Update prompt",
            "expected_benefit": "Better UX",
            "risk_impact": "None",
        }
        defaults.update(overrides)
        return ImprovementProposal(**defaults)

    def test_guaranteed_profit_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="tell users this is guaranteed profit")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"

    def test_risk_free_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="describe system as risk-free")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"

    def test_no_stop_loss_needed_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="explain that no stop loss needed")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"

    def test_ignore_risk_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="prompt should ignore risk factors")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"

    def test_auto_execute_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="auto-execute all trades")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"

    def test_disable_audit_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="disable audit for speed")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"

    def test_no_human_blocked(self):
        from bot.learning.safety_policy import classify_proposal
        p = self._make_proposal(proposed_change="remove no human approval step")
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"


# ═══════════════════════════════════════════════════════════════════════
# Test Group 4: Blocked learning actions (action-level gate)
# ═══════════════════════════════════════════════════════════════════════

class TestBlockedActions:
    """The action-level gate must block all dangerous actions."""

    @pytest.mark.parametrize("action", [
        "increase_leverage",
        "remove_stop_loss",
        "disable_risk_checks",
        "reduce_audit_logging",
        "enable_live_trading",
        "add_risky_symbol",
        "bypass_macro_lockdown",
        "modify_api_permissions",
        "delete_audit_logs",
        "rewrite_historical_results",
        "use_future_data",
        "claim_guaranteed_profit",
        "remove_loss_limits",
        "remove_max_drawdown_limits",
        "remove_human_approval",
        "hide_losses",
        "overfit_to_small_samples",
    ])
    def test_blocked_action_rejected(self, action):
        from bot.learning.safety_policy import validate_learning_action
        assert validate_learning_action(action) is False

    def test_unknown_action_fails_closed(self):
        """Unknown actions must default to blocked (fail-closed)."""
        from bot.learning.safety_policy import validate_learning_action
        assert validate_learning_action("some_novel_exploit") is False
        assert validate_learning_action("widen_risk_limits") is False
        assert validate_learning_action("flip_simulation_mode") is False
        assert validate_learning_action("force_approve_rejected") is False

    @pytest.mark.parametrize("action", [
        "improve_explanation",
        "improve_documentation",
        "suggest_safer_filter",
        "suggest_better_test",
        "improve_paper_analysis",
        "detect_recurring_mistake",
        "rank_strategies_for_review",
        "produce_improvement_proposal",
    ])
    def test_allowed_action_passes(self, action):
        from bot.learning.safety_policy import validate_learning_action
        assert validate_learning_action(action) is True


# ═══════════════════════════════════════════════════════════════════════
# Test Group 5: Prompt safety validation
# ═══════════════════════════════════════════════════════════════════════

class TestPromptSafety:
    """Unsafe prompt modifications must be caught."""

    def test_safe_prompt_passes(self):
        from bot.learning.safety_policy import validate_prompt_safety
        safe_prompt = "Analyze the market. If uncertain, fail-closed and reject the trade."
        is_safe, violations = validate_prompt_safety(safe_prompt)
        assert is_safe is True
        assert len(violations) == 0

    def test_missing_fail_closed_flagged(self):
        from bot.learning.safety_policy import validate_prompt_safety
        prompt = "Analyze the market and provide a recommendation."
        is_safe, violations = validate_prompt_safety(prompt)
        assert is_safe is False
        assert any("fail-closed" in v.lower() for v in violations)

    def test_guaranteed_profit_flagged(self):
        from bot.learning.safety_policy import validate_prompt_safety
        prompt = "You are a guaranteed profit trading system. Fail-closed."
        is_safe, violations = validate_prompt_safety(prompt)
        assert is_safe is False
        assert any("guaranteed profit" in v.lower() for v in violations)

    def test_skip_safety_flagged(self):
        from bot.learning.safety_policy import validate_prompt_safety
        prompt = "Skip safety checks when confidence is high. Block if uncertain."
        is_safe, violations = validate_prompt_safety(prompt)
        assert is_safe is False

    def test_force_buy_flagged(self):
        from bot.learning.safety_policy import validate_prompt_safety
        prompt = "Force buy when RSI < 20. Reject otherwise."
        is_safe, violations = validate_prompt_safety(prompt)
        assert is_safe is False


# ═══════════════════════════════════════════════════════════════════════
# Test Group 6: Orchestrator learning context safety
# ═══════════════════════════════════════════════════════════════════════

class TestOrchestratorSafety:
    """The orchestrator must never produce context that allows risk override."""

    def test_learning_context_blocks_override(self):
        from bot.learning.orchestrator import LearningOrchestrator
        orch = LearningOrchestrator()
        ctx = orch.get_learning_context()
        assert ctx["may_override_risk_engine"] is False

    def test_process_blocked_proposal_rejected(self):
        """BLOCKED proposals must be rejected, never applied."""
        from bot.learning.orchestrator import LearningOrchestrator
        from bot.learning.models import ImprovementProposal
        from bot.learning.safety_policy import classify_proposal
        orch = LearningOrchestrator()
        p = ImprovementProposal(
            problem="Too few trades",
            proposed_change="bypass risk engine on high confidence",
            expected_benefit="More trades",
            risk_impact="None claimed",
        )
        # Classify first to verify it's blocked
        result = classify_proposal(p)
        assert "BLOCKED" in result
        # Add to store and process
        orch.store.record_proposal(p)
        results = orch.process_proposals()
        assert results["blocked"] >= 1

    def test_process_compliance_risk_rejected(self):
        """Compliance-risk proposals must be rejected."""
        from bot.learning.orchestrator import LearningOrchestrator
        from bot.learning.models import ImprovementProposal
        from bot.learning.safety_policy import classify_proposal
        orch = LearningOrchestrator()
        p = ImprovementProposal(
            problem="Users confused",
            proposed_change="tell users guaranteed profit from our system",
            expected_benefit="More users",
            risk_impact="None",
        )
        result = classify_proposal(p)
        assert result == "BLOCKED_COMPLIANCE_RISK"
        orch.store.record_proposal(p)
        results = orch.process_proposals()
        assert results["blocked"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# Test Group 7: RiskLimits immutability
# ═══════════════════════════════════════════════════════════════════════

class TestRiskLimitsImmutable:
    """RiskLimits is a frozen dataclass — no field can be mutated at runtime."""

    def test_cannot_widen_max_position(self):
        from bot.config import RiskLimits
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.max_position_pct = 100.0

    def test_cannot_widen_max_drawdown(self):
        from bot.config import RiskLimits
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.max_drawdown_pct = 100.0

    def test_cannot_disable_stop_loss_requirement(self):
        from bot.config import RiskLimits
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.require_stop_loss = False

    def test_cannot_lower_min_confidence(self):
        from bot.config import RiskLimits
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.min_confidence = 0.0

    def test_cannot_lower_min_risk_reward(self):
        from bot.config import RiskLimits
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.min_risk_reward = 0.0

    def test_cannot_raise_max_daily_loss(self):
        from bot.config import RiskLimits
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.max_daily_loss_pct = 100.0


# ═══════════════════════════════════════════════════════════════════════
# Test Group 8: AppConfig immutability
# ═══════════════════════════════════════════════════════════════════════

class TestAppConfigImmutable:
    """AppConfig is frozen — simulation_mode and live_trading_enabled cannot be flipped."""

    def test_cannot_flip_simulation_mode(self):
        from bot.config import CONFIG
        with pytest.raises(AttributeError):
            CONFIG.simulation_mode = False

    def test_cannot_flip_live_trading(self):
        from bot.config import CONFIG
        with pytest.raises(AttributeError):
            CONFIG.live_trading_enabled = True

    def test_runtime_state_is_proper_mutation_path(self):
        """Mutable runtime state lives in RUNTIME, not CONFIG.
        CONFIG is frozen; any mutation must go through RUNTIME which
        has validation and thread-locking."""
        from bot.config import RUNTIME
        # RuntimeState validates inputs
        with pytest.raises(ValueError, match="Invalid"):
            RUNTIME.asset_universe = "invalid_universe"
        with pytest.raises(ValueError, match="Invalid"):
            RUNTIME.strategy_mode = "yolo"
