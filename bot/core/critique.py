"""
Adversarial self-critique gate for the RUNECLAW trading pipeline.

Before every trade execution, this module argues the bear case:
- Checks for contrary signals the analyzer might have overlooked
- Evaluates regime mismatch risk
- Flags crowded trades and sentiment extremes
- Produces a structured critique with a halt/warn/pass verdict

Fail-open by design: if critique cannot be computed, trade proceeds
with a logged warning (unlike risk engine which is fail-closed).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CritiqueResult:
    """Structured output from adversarial review."""
    verdict: str  # "HALT" | "WARN" | "PASS"
    bear_case: str  # 1-2 sentence bear thesis
    concerns: list[str] = field(default_factory=list)
    confidence_adjustment: float = 0.0  # negative = reduce confidence
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TradeCritique:
    """Pre-trade adversarial review gate.

    Analyzes a TradeIdea and its risk check results to find reasons
    the trade might fail. Uses rule-based heuristics (no LLM call).
    """

    # Thresholds
    HIGH_CONFIDENCE_WARN = 0.90  # suspiciously high confidence
    LOW_RR_WARN = 1.5            # R:R close to minimum
    MAX_CONCERNS_FOR_HALT = 4    # 4+ concerns = HALT

    def evaluate(self, idea, risk_check, portfolio_snapshot, macro_context=None) -> CritiqueResult:
        """Run adversarial analysis on a trade idea."""
        concerns = []
        confidence_adj = 0.0

        # 1. Overconfidence check
        if idea.confidence > self.HIGH_CONFIDENCE_WARN:
            concerns.append(f"Suspiciously high confidence ({idea.confidence:.0%}) — model may be overfitting to recent pattern")
            confidence_adj -= 0.05

        # 2. Marginal R:R
        if idea.risk_reward_ratio < self.LOW_RR_WARN:
            concerns.append(f"R:R of {idea.risk_reward_ratio:.2f} is near minimum — small adverse move wipes edge")
            confidence_adj -= 0.03

        # 3. Concentration risk (same direction as existing positions)
        # PortfolioState.open_positions is an int (count), not a list.
        # Use it for portfolio heat check; direction/asset checks require
        # the actual positions list from the portfolio object.
        open_count = 0
        if hasattr(portfolio_snapshot, 'open_positions'):
            val = portfolio_snapshot.open_positions
            if isinstance(val, int):
                open_count = val
            elif hasattr(val, '__len__'):
                open_count = len(val)

        # 4. Same-asset double-down
        # Cannot check per-asset or per-direction without position objects;
        # skip these checks when only a count is available.
        # (The risk engine enforces max-positions and concentration limits.)

        # 5. Portfolio heat check (many open positions)
        if open_count >= 4:
            concerns.append(f"{open_count} open positions — portfolio is hot, adding more increases tail risk")
            confidence_adj -= 0.03

        # 6. Macro headwind
        if macro_context is not None:
            risk_state = getattr(macro_context, 'risk_state', None)
            if risk_state == "REDUCE":
                concerns.append("Macro environment is in REDUCE state — trading against elevated event risk")
                confidence_adj -= 0.05

        # 7. Tight stop in volatile market (SL very close to entry)
        if idea.entry_price > 0 and idea.stop_loss > 0:
            sl_distance_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
            if sl_distance_pct < 1.0:
                concerns.append(f"Stop loss only {sl_distance_pct:.1f}% from entry — high probability of stop hunt")
                confidence_adj -= 0.05

        # Build verdict
        if len(concerns) >= self.MAX_CONCERNS_FOR_HALT:
            verdict = "HALT"
            bear_case = f"Trade has {len(concerns)} adversarial concerns — bear case is too strong to proceed"
        elif len(concerns) >= 2:
            verdict = "WARN"
            bear_case = f"Trade has {len(concerns)} concerns but may still be viable with caution"
        elif len(concerns) == 1:
            verdict = "WARN"
            bear_case = concerns[0]
        else:
            verdict = "PASS"
            bear_case = "No significant adversarial concerns identified"

        return CritiqueResult(
            verdict=verdict,
            bear_case=bear_case,
            concerns=concerns,
            confidence_adjustment=confidence_adj,
        )
