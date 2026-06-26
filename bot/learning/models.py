"""RUNECLAW AI Learning System — Data Models.

All learning data structures. Every record must carry audit_id, timestamp,
source, and confidence. No secrets, no PII, no unaudited mutations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────

class ChangeClassification(str, Enum):
    """Safety classification for AI-proposed changes."""
    SAFE_AUTO_DOCS = "SAFE_AUTO_DOCS"
    SAFE_AUTO_TEST = "SAFE_AUTO_TEST"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    BLOCKED_RISK_INCREASE = "BLOCKED_RISK_INCREASE"
    BLOCKED_COMPLIANCE_RISK = "BLOCKED_COMPLIANCE_RISK"


class LearningTier(str, Enum):
    """Risk-aware learning score tier."""
    S = "S"   # safer, clearer, more stable
    A = "A"   # useful improvement, low risk
    B = "B"   # promising but needs more data
    C = "C"   # weak evidence
    D = "D"   # rejected
    BLOCKED = "BLOCKED"  # unsafe or non-compliant


class PatternType(str, Enum):
    TREND_CONTINUATION = "trend_continuation"
    BREAKOUT_FAILURE = "breakout_failure"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    VOLATILITY_COMPRESSION = "volatility_compression"
    FUNDING_RATE_SQUEEZE = "funding_rate_squeeze"
    OI_EXPANSION = "oi_expansion"
    LIQUIDATION_CASCADE = "liquidation_cascade"
    MEAN_REVERSION = "mean_reversion"
    MACRO_EVENT_WHIPSAW = "macro_event_whipsaw"
    POST_CPI_FADE = "post_cpi_fade"
    POST_FOMC_CONTINUATION = "post_fomc_continuation"
    RISK_OFF_SELLOFF = "risk_off_selloff"
    WEEKEND_LIQUIDITY_TRAP = "weekend_liquidity_trap"


class FeedbackType(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    TOO_RISKY = "too_risky"
    TOO_CONSERVATIVE = "too_conservative"
    UNCLEAR_EXPLANATION = "unclear_explanation"
    MISSING_MACRO_CONTEXT = "missing_macro_context"
    MISSING_RISK_REASON = "missing_risk_reason"
    BAD_SYMBOL_CHOICE = "bad_symbol_choice"
    GOOD_REJECTION = "good_rejection"
    GOOD_EXPLANATION = "good_explanation"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    NEEDS_DOC_UPDATE = "needs_doc_update"


# ── Core Records ───────────────────────────────────────────────────────

def _audit_id() -> str:
    return f"LRN-{uuid.uuid4().hex[:12].upper()}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DecisionMemory(BaseModel):
    """Every trading decision and rejection — the ground truth log."""
    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    source: str = "runeclaw_engine"
    mode: str = "paper"  # paper | demo | live-blocked
    symbol: str = ""
    timeframe: str = "1h"
    market_regime: str = ""
    macro_state: str = ""
    volatility_state: str = ""
    funding_state: str = ""
    oi_state: str = ""
    liquidity_state: str = ""
    strategy_signal: str = ""
    direction: str = ""
    confidence: float = 0.0
    confluence_score: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_reward: float = 0.0
    position_size_usd: float = 0.0
    risk_engine_result: str = ""  # APPROVED / REJECTED
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    rejected_reason: str = ""
    decision: str = ""  # TRADE_ACCEPTED_PAPER / TRADE_REJECTED_FAIL_CLOSED / NO_TRADE_UNCERTAIN / ALERT_ONLY
    paper_trade_id: str = ""
    pnl_result: Optional[float] = None
    gross_pnl: Optional[float] = None
    commission: Optional[float] = None
    max_drawdown: Optional[float] = None
    slippage: Optional[float] = None
    invalidation_level: Optional[float] = None
    post_trade_review: str = ""
    prompt_version: str = "v1"
    strategy_version: str = "v1"
    risk_engine_version: str = "v1"


class ReflectionMemory(BaseModel):
    """Post-trade / post-event reflection — what was learned."""
    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    source: str = "reflection_engine"
    decision_audit_id: str = ""  # links to DecisionMemory
    symbol: str = ""
    mode: str = "paper"
    # Reflection questions
    what_was_expected: str = ""
    what_happened: str = ""
    signal_valid: Optional[bool] = None
    risk_decision_correct: Optional[bool] = None
    macro_context_helped: Optional[bool] = None
    trade_avoided_correctly: Optional[bool] = None
    position_sizing_correct: Optional[bool] = None
    sl_tp_behaved_correctly: Optional[bool] = None
    improvement_suggestion: str = ""
    # Output
    lesson_learned: str = ""
    confidence: float = 0.0
    recommended_improvement: str = ""
    needs_human_review: bool = True
    allowed_to_auto_apply: bool = False
    change_classification: str = ChangeClassification.HUMAN_REVIEW_REQUIRED.value


class StrategyScorecard(BaseModel):
    """Per-strategy performance and risk-adjusted rankings."""
    strategy_name: str = ""
    last_updated: datetime = Field(default_factory=_utc_now)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe_like_score: float = 0.0
    max_drawdown: float = 0.0
    avg_trade_duration_hours: float = 0.0
    false_positive_rate: float = 0.0
    false_negative_rate: float = 0.0
    rejected_by_risk_engine: int = 0
    # Per-regime breakdown
    performance_by_regime: dict[str, dict] = Field(default_factory=dict)
    performance_by_symbol: dict[str, dict] = Field(default_factory=dict)
    performance_by_timeframe: dict[str, dict] = Field(default_factory=dict)
    performance_around_macro: dict[str, dict] = Field(default_factory=dict)
    # Scoring
    learning_tier: str = LearningTier.C.value
    safety_score: float = 0.0
    overfitting_warning: bool = False


class PatternRecord(BaseModel):
    """Detected recurring market pattern."""
    # validate_assignment makes the may_override_risk validator fire on
    # post-construction assignment too — without it, `p.may_override_risk = True`
    # silently bypassed the safety invariant the docs claim is unbreakable.
    model_config = ConfigDict(validate_assignment=True)

    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    pattern_type: str = ""
    symbol: str = ""
    timeframe: str = ""
    market_regime: str = ""
    confidence: float = 0.0
    sample_size: int = 0
    is_experimental: bool = True  # True if sample_size < 20
    description: str = ""
    historical_win_rate: float = 0.0
    avg_pnl: float = 0.0
    # Rule: patterns are observations, not trade commands
    may_override_risk: bool = False  # ALWAYS False

    @field_validator("may_override_risk")
    @classmethod
    def must_be_false(cls, v):
        """Safety invariant: patterns may NEVER override the risk engine."""
        if v is not False:
            raise ValueError("may_override_risk must ALWAYS be False")
        return False


class MacroEventMemory(BaseModel):
    """Macro event reaction tracking and learning."""
    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    event_name: str = ""
    event_datetime_utc: Optional[datetime] = None
    event_type: str = ""  # FOMC / CPI / PCE / NFP / PPI / GDP / etc.
    previous_value: str = ""
    forecast_value: str = ""
    actual_value: str = ""
    surprise_score: float = 0.0
    # Crypto reactions
    btc_5min_reaction_pct: Optional[float] = None
    btc_30min_reaction_pct: Optional[float] = None
    btc_4h_reaction_pct: Optional[float] = None
    btc_24h_reaction_pct: Optional[float] = None
    volatility_expansion: Optional[float] = None
    spread_impact: Optional[float] = None
    liquidity_impact: str = ""
    funding_rate_shift: Optional[float] = None
    lesson_learned: str = ""


class PromptVersion(BaseModel):
    """Prompt version tracking and performance scoring."""
    version_id: str = ""
    created_at: datetime = Field(default_factory=_utc_now)
    model_used: str = ""
    prompt_text_hash: str = ""  # SHA256 of prompt text (not the full text)
    total_uses: int = 0
    json_validity_rate: float = 0.0
    reasoning_clarity_score: float = 0.0
    false_signal_rate: float = 0.0
    avg_cost_per_call: float = 0.0
    avg_latency_ms: float = 0.0
    human_feedback_score: float = 0.0
    safety_wording_intact: bool = True
    fail_closed_intact: bool = True


class ModelComparison(BaseModel):
    """Model-vs-model decision comparison."""
    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    symbol: str = ""
    # Outputs from different sources
    base_strategy_direction: str = ""
    base_strategy_confidence: float = 0.0
    llm_direction: str = ""
    llm_confidence: float = 0.0
    macro_adjusted_direction: str = ""
    risk_adjusted_decision: str = ""
    final_paper_result: Optional[float] = None
    # Metrics
    models_agree: bool = False
    disagreement_action: str = ""  # NO_TRADE_UNCERTAIN if disagree strongly
    accuracy_base: Optional[float] = None
    accuracy_llm: Optional[float] = None
    hallucination_risk: str = "unknown"
    token_cost: float = 0.0
    latency_ms: float = 0.0
    safety_score: float = 0.0


class HumanFeedback(BaseModel):
    """User feedback record."""
    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    decision_audit_id: str = ""  # links to DecisionMemory
    feedback_type: str = ""
    feedback_text: str = ""
    severity: str = "normal"  # low / normal / high / critical
    applied_to_reflection: bool = False
    applied_to_strategy_score: bool = False
    applied_to_prompt_score: bool = False


class ImprovementProposal(BaseModel):
    """Self-improvement proposal — must be classified before action."""
    audit_id: str = Field(default_factory=_audit_id)
    timestamp_utc: datetime = Field(default_factory=_utc_now)
    source: str = "reflection_engine"
    problem: str = ""
    evidence: str = ""
    proposed_change: str = ""
    expected_benefit: str = ""
    risk_impact: str = ""
    rollback_plan: str = ""
    test_plan: str = ""
    classification: str = ChangeClassification.HUMAN_REVIEW_REQUIRED.value
    human_approval_required: bool = True
    status: str = "pending"  # pending / approved / rejected / applied / rolled_back
    applied_at: Optional[datetime] = None
    applied_by: str = ""
