"""
RUNECLAW Explainability Engine -- structured reasoning chains and compliance narratives.

Provides human-readable, auditable explanations for every trade decision:
  - Structured reasoning chain: step-by-step logic from signal to decision
  - Factor attribution: which indicators contributed how much
  - Risk narrative: why risk engine approved/rejected
  - Compliance scoring: regulatory-ready decision audit trail
  - Natural language summary for Telegram/dashboard

Design rules:
  - Pure computation, no side effects
  - Every field is deterministic from inputs (reproducible)
  - No LLM dependency -- works entirely from structured data
  - Compliant with MiCA/regulatory explainability requirements
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Output Models ─────────────────────────────────────────────────

class ReasoningStep(BaseModel):
    """Single step in the reasoning chain."""
    stage: str           # e.g. "regime_detection", "confluence_scoring"
    input_summary: str   # what data went in
    output_summary: str  # what came out
    impact: str          # "bullish", "bearish", "neutral", "filter"


class FactorAttribution(BaseModel):
    """How much one factor contributed to the final decision."""
    factor: str          # e.g. "rsi", "mtf_alignment", "smart_money_composite"
    vote: float          # [-1, 1]
    weight: float        # raw weight
    contribution_pct: float = 0.0  # % of total weighted vote
    direction: str = "neutral"     # "bullish" | "bearish" | "neutral"


class ComplianceScore(BaseModel):
    """Regulatory compliance assessment of the decision."""
    explainability: float = 0.0   # [0, 1] how well-explained is this decision
    data_sufficiency: float = 0.0 # [0, 1] enough data to justify the trade
    risk_documented: bool = False  # risk checks are documented
    audit_trail: bool = False      # full audit trail exists
    overall: float = 0.0          # [0, 1] composite compliance score


class ExplainabilityReport(BaseModel):
    """Full explainability report for one trade decision."""
    trade_id: str = ""
    symbol: str = ""
    direction: str = ""
    # Reasoning chain
    reasoning_chain: list[ReasoningStep] = Field(default_factory=list)
    # Factor attribution
    factors: list[FactorAttribution] = Field(default_factory=list)
    top_bullish: list[str] = Field(default_factory=list)  # top 3 bullish factors
    top_bearish: list[str] = Field(default_factory=list)  # top 3 bearish factors
    # Scores
    confluence_score: float = 0.0
    confidence: float = 0.0
    # Risk
    risk_approved: bool = False
    risk_checks_passed: int = 0
    risk_checks_total: int = 0
    risk_rejection_reason: str = ""
    # Compliance
    compliance: ComplianceScore = Field(default_factory=ComplianceScore)
    # Narrative
    summary: str = ""
    detailed_narrative: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Explainability Engine ────────────────────────────────────────

class ExplainabilityEngine:
    """Generate structured explanations for trade decisions.

    Usage:
        engine = ExplainabilityEngine()
        report = engine.explain(
            trade_id="TI-abc123",
            symbol="BTC/USDT",
            direction="LONG",
            indicators=indicators,
            regime="TREND_UP",
            confluence=0.72,
            confidence=0.68,
            votes=votes,
            weights=weights,
            labels=labels,
            risk_verdict=risk_verdict,
            strategy_mode="TREND_CONTINUATION",
            mtf_narrative="All TFs aligned bullish",
            smart_money_narrative="Whales accumulating",
        )
    """

    def explain(
        self,
        trade_id: str = "",
        symbol: str = "",
        direction: str = "",
        indicators: Optional[dict] = None,
        regime: str = "UNKNOWN",
        confluence: float = 0.0,
        confidence: float = 0.0,
        votes: Optional[list[float]] = None,
        weights: Optional[list[float]] = None,
        labels: Optional[list[str]] = None,
        risk_verdict=None,
        strategy_mode: str = "CONSERVATIVE",
        mtf_narrative: str = "",
        smart_money_narrative: str = "",
    ) -> ExplainabilityReport:
        """Build a full explainability report."""
        indicators = indicators or {}
        votes = votes or []
        weights = weights or []
        labels = labels or []

        report = ExplainabilityReport(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            confluence_score=round(confluence, 4),
            confidence=round(confidence, 4),
        )

        # 1. Build reasoning chain
        report.reasoning_chain = self._build_chain(
            indicators, regime, confluence, confidence,
            strategy_mode, mtf_narrative, smart_money_narrative,
            risk_verdict,
        )

        # 2. Factor attribution
        report.factors = self._attribute_factors(votes, weights, labels)
        bullish = sorted(
            [f for f in report.factors if f.vote > 0],
            key=lambda f: -abs(f.vote * f.weight),
        )
        bearish = sorted(
            [f for f in report.factors if f.vote < 0],
            key=lambda f: -abs(f.vote * f.weight),
        )
        report.top_bullish = [f.factor for f in bullish[:3]]
        report.top_bearish = [f.factor for f in bearish[:3]]

        # 3. Risk
        if risk_verdict is not None:
            report.risk_approved = getattr(risk_verdict, "approved", False)
            checks = getattr(risk_verdict, "checks", [])
            report.risk_checks_total = len(checks)
            report.risk_checks_passed = sum(
                1 for c in checks if getattr(c, "passed", False)
            )
            if not report.risk_approved:
                failed = [
                    c for c in checks if not getattr(c, "passed", False)
                ]
                if failed:
                    report.risk_rejection_reason = getattr(
                        failed[0], "reason", "Unknown"
                    )

        # 4. Compliance scoring
        report.compliance = self._score_compliance(report, indicators)

        # 5. Narratives
        report.summary = self._build_summary(report, strategy_mode)
        report.detailed_narrative = self._build_detailed(
            report, regime, strategy_mode, mtf_narrative, smart_money_narrative
        )

        return report

    # ── Internal Methods ─────────────────────────────────────────

    @staticmethod
    def _build_chain(
        indicators: dict,
        regime: str,
        confluence: float,
        confidence: float,
        strategy_mode: str,
        mtf_narrative: str,
        smart_money_narrative: str,
        risk_verdict,
    ) -> list[ReasoningStep]:
        """Build step-by-step reasoning chain."""
        chain: list[ReasoningStep] = []

        # Step 1: Data collection
        indicator_count = len([
            k for k in indicators
            if k not in ("regime", "confluence", "candle_patterns")
        ])
        chain.append(ReasoningStep(
            stage="data_collection",
            input_summary=f"{indicator_count} technical indicators computed",
            output_summary=f"RSI={indicators.get('rsi', 'N/A')}, "
                           f"ADX={indicators.get('adx', 'N/A')}, "
                           f"ATR={indicators.get('atr', 'N/A')}",
            impact="neutral",
        ))

        # Step 2: Regime detection
        regime_impact = "neutral"
        if regime in ("TREND_UP", "TREND_DOWN"):
            regime_impact = "bullish" if regime == "TREND_UP" else "bearish"
        chain.append(ReasoningStep(
            stage="regime_detection",
            input_summary=f"ADX={indicators.get('adx', 'N/A')}, "
                          f"+DI={indicators.get('plus_di', 'N/A')}, "
                          f"-DI={indicators.get('minus_di', 'N/A')}",
            output_summary=f"Regime: {regime}",
            impact=regime_impact,
        ))

        # Step 3: Multi-timeframe analysis
        if mtf_narrative:
            mtf_impact = "neutral"
            if "bullish" in mtf_narrative.lower():
                mtf_impact = "bullish"
            elif "bearish" in mtf_narrative.lower():
                mtf_impact = "bearish"
            chain.append(ReasoningStep(
                stage="multi_timeframe",
                input_summary="1H/4H/1D candle analysis",
                output_summary=mtf_narrative[:120],
                impact=mtf_impact,
            ))

        # Step 4: Smart money analysis
        if smart_money_narrative:
            sm_impact = "neutral"
            if "accumulating" in smart_money_narrative.lower() or \
               "bullish" in smart_money_narrative.lower():
                sm_impact = "bullish"
            elif "distributing" in smart_money_narrative.lower() or \
                 "bearish" in smart_money_narrative.lower():
                sm_impact = "bearish"
            chain.append(ReasoningStep(
                stage="smart_money",
                input_summary="Order flow, whale tracking, funding analysis",
                output_summary=smart_money_narrative[:120],
                impact=sm_impact,
            ))

        # Step 5: Confluence scoring
        conf_impact = "bullish" if confluence > 0.55 else \
                      "bearish" if confluence < 0.45 else "neutral"
        chain.append(ReasoningStep(
            stage="confluence_scoring",
            input_summary="All indicator votes weighted and aggregated",
            output_summary=f"Confluence: {confluence:.2f}, Confidence: {confidence:.2f}",
            impact=conf_impact,
        ))

        # Step 6: Strategy mode selection
        chain.append(ReasoningStep(
            stage="strategy_selection",
            input_summary=f"Regime={regime}, confluence={confluence:.2f}",
            output_summary=f"Strategy mode: {strategy_mode}",
            impact="neutral",
        ))

        # Step 7: Risk assessment
        if risk_verdict is not None:
            approved = getattr(risk_verdict, "approved", False)
            checks = getattr(risk_verdict, "checks", [])
            failed = [c for c in checks if not getattr(c, "passed", False)]
            chain.append(ReasoningStep(
                stage="risk_assessment",
                input_summary=f"{len(checks)} risk checks evaluated",
                output_summary=f"{'APPROVED' if approved else 'REJECTED'}"
                               f"{': ' + getattr(failed[0], 'reason', '') if failed else ''}",
                impact="filter",
            ))

        return chain

    @staticmethod
    def _attribute_factors(
        votes: list[float],
        weights: list[float],
        labels: list[str],
    ) -> list[FactorAttribution]:
        """Compute per-factor contribution percentages."""
        if not votes or not weights or not labels:
            return []

        total_abs = sum(abs(v * w) for v, w in zip(votes, weights))
        if total_abs == 0:
            total_abs = 1.0

        factors: list[FactorAttribution] = []
        for vote, weight, label in zip(votes, weights, labels):
            contrib = abs(vote * weight) / total_abs * 100
            direction = "bullish" if vote > 0.05 else \
                        "bearish" if vote < -0.05 else "neutral"
            factors.append(FactorAttribution(
                factor=label,
                vote=round(vote, 4),
                weight=round(weight, 4),
                contribution_pct=round(contrib, 1),
                direction=direction,
            ))

        return sorted(factors, key=lambda f: -f.contribution_pct)

    @staticmethod
    def _score_compliance(
        report: ExplainabilityReport,
        indicators: dict,
    ) -> ComplianceScore:
        """Score the decision for regulatory compliance."""
        # Explainability: based on reasoning chain completeness
        chain_stages = {s.stage for s in report.reasoning_chain}
        required_stages = {"data_collection", "regime_detection", "confluence_scoring"}
        explainability = len(chain_stages & required_stages) / len(required_stages)

        # Data sufficiency
        key_indicators = ["rsi", "macd", "atr", "adx", "bb_pct_b"]
        present = sum(1 for k in key_indicators if k in indicators)
        data_sufficiency = present / len(key_indicators)

        # Risk documentation
        risk_documented = report.risk_checks_total > 0

        # Audit trail: we have all the data
        audit_trail = len(report.factors) > 0 and len(report.reasoning_chain) >= 3

        # Overall
        overall = (
            explainability * 0.3
            + data_sufficiency * 0.3
            + (1.0 if risk_documented else 0.0) * 0.2
            + (1.0 if audit_trail else 0.0) * 0.2
        )

        return ComplianceScore(
            explainability=round(explainability, 2),
            data_sufficiency=round(data_sufficiency, 2),
            risk_documented=risk_documented,
            audit_trail=audit_trail,
            overall=round(overall, 2),
        )

    @staticmethod
    def _build_summary(report: ExplainabilityReport, strategy_mode: str) -> str:
        """One-line summary for Telegram/dashboard."""
        direction_emoji = "LONG" if report.direction == "LONG" else "SHORT"
        status = "APPROVED" if report.risk_approved else "PENDING"

        top_factor = report.top_bullish[0] if report.direction == "LONG" and report.top_bullish \
            else report.top_bearish[0] if report.direction == "SHORT" and report.top_bearish \
            else "confluence"

        return (
            f"{direction_emoji} {report.symbol} | "
            f"Conf={report.confidence:.0%} | "
            f"Mode={strategy_mode} | "
            f"Key={top_factor} | "
            f"{status}"
        )

    @staticmethod
    def _build_detailed(
        report: ExplainabilityReport,
        regime: str,
        strategy_mode: str,
        mtf_narrative: str,
        smart_money_narrative: str,
    ) -> str:
        """Multi-paragraph detailed explanation."""
        parts: list[str] = []

        # Opening
        parts.append(
            f"Trade analysis for {report.symbol} ({report.direction}): "
            f"confluence {report.confluence_score:.2f}, "
            f"confidence {report.confidence:.0%}."
        )

        # Regime context
        parts.append(f"Market regime: {regime}. Strategy mode: {strategy_mode}.")

        # MTF
        if mtf_narrative:
            parts.append(f"Multi-timeframe: {mtf_narrative}")

        # Smart money
        if smart_money_narrative:
            parts.append(f"Smart money: {smart_money_narrative}")

        # Top factors
        if report.top_bullish:
            parts.append(f"Top bullish factors: {', '.join(report.top_bullish)}.")
        if report.top_bearish:
            parts.append(f"Top bearish factors: {', '.join(report.top_bearish)}.")

        # Risk
        if report.risk_approved:
            parts.append(
                f"Risk engine approved: {report.risk_checks_passed}/"
                f"{report.risk_checks_total} checks passed."
            )
        elif report.risk_checks_total > 0:
            parts.append(
                f"Risk engine REJECTED: {report.risk_rejection_reason}. "
                f"{report.risk_checks_passed}/{report.risk_checks_total} checks passed."
            )

        # Compliance
        parts.append(
            f"Compliance score: {report.compliance.overall:.0%} "
            f"(explainability={report.compliance.explainability:.0%}, "
            f"data={report.compliance.data_sufficiency:.0%})."
        )

        return " ".join(parts)
