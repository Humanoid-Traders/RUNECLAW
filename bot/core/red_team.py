"""
RUNECLAW Red Team Engine -- adversarial stress testing for the risk engine.

Generates adversarial TradeIdea scenarios designed to bypass or confuse
the 18-check risk engine, runs each through the real engine, and produces
a structured report of what was caught and what slipped through.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from bot.compat import UTC
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.config import CONFIG
from bot.utils.models import Direction, RiskVerdict, TradeIdea

# Default ATR for scenarios not specifically testing the volatility guard.
# Expressed as a fraction of entry price (2%), well within the 6% guard threshold.
_DEFAULT_ATR_PCT = 0.02


# ---------------------------------------------------------------------------
# Report models
# ---------------------------------------------------------------------------

class StressTestScenario(BaseModel):
    """Result of a single adversarial scenario."""
    name: str
    category: str
    description: str
    trade_idea: dict
    expected_verdict: str
    actual_verdict: str
    passed: bool
    checks_triggered: list[str] = Field(default_factory=list)


class StressTestReport(BaseModel):
    """Aggregated stress test results."""
    total_scenarios: int
    passed: int
    failed: int
    pass_rate: float
    scenarios: list[StressTestScenario]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

def _make_idea(
    asset: str = "BTC/USDT",
    direction: Direction = Direction.LONG,
    entry: float = 50000.0,
    sl: float = 49000.0,       # 2% stop — within 10% margin risk cap at 5x leverage
    tp: float = 51200.0,       # R:R = 1200/1000 = 1.2 — at minimum threshold
    confidence: float = 0.75,
    reasoning: str = "red-team scenario",
    source: str = "red_team",
    timestamp: Optional[datetime] = None,
    idea_id: Optional[str] = None,
) -> TradeIdea:
    """Build a TradeIdea with sensible defaults, overridable per-scenario."""
    kwargs: dict = dict(
        asset=asset,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        reasoning=reasoning,
        source=source,
    )
    if timestamp is not None:
        kwargs["timestamp"] = timestamp
    if idea_id is not None:
        kwargs["id"] = idea_id
    return TradeIdea(**kwargs)


# ---------------------------------------------------------------------------
# RedTeamEngine
# ---------------------------------------------------------------------------

class RedTeamEngine:
    """Adversarial stress tester that attacks the risk engine."""

    def __init__(self, risk_engine: RiskEngine, portfolio: PortfolioTracker) -> None:
        self._engine = risk_engine
        self._portfolio = portfolio

    # -- placeholder: scenario generators --
    # SECTION: _generate_scenarios
    # SECTION: _run_scenario
    # SECTION: run_stress_test

    def run_stress_test(self) -> StressTestReport:
        """Run all adversarial scenarios and return results."""
        results: list[StressTestScenario] = []
        for scenario in self._generate_scenarios():
            results.append(self._run_scenario(scenario))

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        rate = (passed / len(results) * 100.0) if results else 0.0

        failed_names = [r.name for r in results if not r.passed]
        if failed_names:
            summary = (
                f"Red-team stress test: {passed}/{len(results)} scenarios handled "
                f"correctly ({rate:.1f}%). FAILURES: {', '.join(failed_names)}"
            )
        else:
            summary = (
                f"Red-team stress test: {passed}/{len(results)} scenarios handled "
                f"correctly ({rate:.1f}%). All adversarial scenarios caught."
            )

        return StressTestReport(
            total_scenarios=len(results),
            passed=passed,
            failed=failed,
            pass_rate=round(rate, 2),
            scenarios=results,
            summary=summary,
        )

    def _run_scenario(self, spec: dict) -> StressTestScenario:
        """Execute one scenario spec and return the result."""
        name = spec["name"]
        category = spec["category"]
        description = spec["description"]
        expected = spec["expected_verdict"]
        atr = spec.get("atr", "auto")

        # Optional pre-setup: open positions, trip breaker, etc.
        if "pre_setup" in spec:
            spec["pre_setup"]()

        # Attempt to build the TradeIdea -- validator may reject it
        try:
            idea = spec["build_idea"]()
        except (ValidationError, ValueError) as exc:
            # Model-level rejection counts as the system correctly blocking it
            actual = "REJECTED_BY_VALIDATOR"
            return StressTestScenario(
                name=name,
                category=category,
                description=description,
                trade_idea={"error": str(exc)},
                expected_verdict=expected,
                actual_verdict=actual,
                passed=(expected == "REJECTED"),
                checks_triggered=["MODEL_VALIDATOR"],
            )

        # Compute ATR if "auto" — use 2% of entry price (safe, below 6% guard)
        if atr == "auto":
            atr = idea.entry_price * _DEFAULT_ATR_PCT if idea.entry_price > 0 else 1.0

        # Run through the real risk engine
        # Cap position size at 20% of equity (mirrors real execution flow)
        equity = self._portfolio.snapshot().equity_usd
        max_pos = equity * (CONFIG.risk.max_position_pct / 100.0) if equity > 0 else 2000.0
        result = self._engine.evaluate(idea, atr=atr, max_position_usd=max_pos)
        actual = result.verdict.value

        return StressTestScenario(
            name=name,
            category=category,
            description=description,
            trade_idea=idea.model_dump(mode="json"),
            expected_verdict=expected,
            actual_verdict=actual,
            passed=(actual == expected),
            checks_triggered=list(result.checks_failed),
        )

    # -----------------------------------------------------------------
    # Scenario generators
    # -----------------------------------------------------------------

    def _generate_scenarios(self) -> list[dict]:
        """Build the full adversarial scenario list (20+ scenarios)."""
        scenarios: list[dict] = []
        scenarios.extend(self._flash_crash_scenarios())
        scenarios.extend(self._liquidity_drain_scenarios())
        scenarios.extend(self._correlated_selloff_scenarios())
        scenarios.extend(self._stale_data_scenarios())
        scenarios.extend(self._confidence_manipulation_scenarios())
        scenarios.extend(self._rr_gaming_scenarios())
        scenarios.extend(self._circuit_breaker_evasion_scenarios())
        scenarios.extend(self._zero_negative_scenarios())
        scenarios.extend(self._direction_inversion_scenarios())
        scenarios.extend(self._max_position_flood_scenarios())
        return scenarios

    # -- Flash Crash --

    def _flash_crash_scenarios(self) -> list[dict]:
        price = 50000.0
        extreme_atr = price * 0.12  # 12% of price, well above 6% guard
        return [
            {
                "name": "flash_crash_extreme_atr",
                "category": "flash_crash",
                "description": (
                    "Trade submitted during extreme volatility where ATR is "
                    "12% of price (threshold is 6%). Should trigger volatility guard."
                ),
                "expected_verdict": "REJECTED",
                "atr": extreme_atr,
                "build_idea": lambda: _make_idea(
                    entry=price, sl=price * 0.88, tp=price * 1.15,
                    confidence=0.80,
                ),
            },
            {
                "name": "flash_crash_borderline_atr",
                "category": "flash_crash",
                "description": (
                    "Trade with ATR at exactly 10% of price -- still above "
                    "the 6% volatility guard threshold."
                ),
                "expected_verdict": "REJECTED",
                "atr": price * 0.10,
                "build_idea": lambda: _make_idea(
                    entry=price, sl=price * 0.90, tp=price * 1.12,
                    confidence=0.80,
                ),
            },
            {
                "name": "flash_crash_zero_atr",
                "category": "flash_crash",
                "description": (
                    "Trade with ATR=0 (bad data from feed). Should be "
                    "rejected as fail-closed (zero ATR = missing data)."
                ),
                "expected_verdict": "REJECTED",
                "atr": 0.0,
                "build_idea": lambda: _make_idea(
                    entry=price, sl=price * 0.95, tp=price * 1.10,
                    confidence=0.80,
                ),
            },
        ]

    # -- Liquidity Drain --

    def _liquidity_drain_scenarios(self) -> list[dict]:
        price = 50000.0
        # The risk engine auto-caps oversized positions at 20% of equity.
        # Tight stops produce large theoretical positions, but the auto-cap
        # brings them within limits.  These scenarios verify the system
        # handles tight stops gracefully (APPROVED with capped position).
        return [
            {
                "name": "liquidity_drain_50pct_equity",
                "category": "liquidity_drain",
                "description": (
                    "Position sized at 50% of equity via a very tight stop. "
                    "Risk engine auto-caps to 20% notional — should APPROVE."
                ),
                "expected_verdict": "APPROVED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    entry=price, sl=price * 0.999, tp=price * 1.01,
                    confidence=0.80,
                ),
            },
            {
                "name": "liquidity_drain_100pct_equity",
                "category": "liquidity_drain",
                "description": (
                    "Position sized at 100% of equity via ultra-tight stop. "
                    "Risk engine auto-caps to 20% notional — should APPROVE."
                ),
                "expected_verdict": "APPROVED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    entry=price, sl=price * 0.9999, tp=price * 1.005,
                    confidence=0.80,
                ),
            },
        ]

    # -- Correlated Selloff --

    def _correlated_selloff_scenarios(self) -> list[dict]:
        # The risk engine allows max_correlation_per_group=2 positions in the
        # same correlation group.  We open 2 real positions, then the 3rd
        # evaluation should be rejected by the correlation/concentration check.
        alt_l1_assets = ["SOL/USDT", "AVAX/USDT", "NEAR/USDT"]
        scenarios = []
        for i, asset in enumerate(alt_l1_assets):
            expected = "APPROVED" if i < 2 else "REJECTED"

            def _pre_setup(idx=i, assets=alt_l1_assets) -> None:
                """Open all prior ALT_L1 positions so correlation check fires."""
                for j in range(idx):
                    a = assets[j]
                    if not any(p.asset == a for p in self._portfolio.open_positions):
                        idea = _make_idea(
                            asset=a, entry=100.0, sl=98.0, tp=102.4,
                            confidence=0.80, idea_id=f"corr-setup-{j}",
                        )
                        self._portfolio.open_position(idea, size_usd=100.0)

            scenario: dict = {
                "name": f"correlated_selloff_{asset.split('/')[0].lower()}",
                "category": "correlated_selloff",
                "description": (
                    f"Open position #{i+1} in ALT_L1 group ({asset}). "
                    f"Group limit is 2; position #{i+1} should be "
                    f"{'allowed' if i < 2 else 'rejected by concentration check'}."
                ),
                "expected_verdict": expected,
                "atr": "auto",
                "pre_setup": _pre_setup,
                "build_idea": lambda a=asset: _make_idea(
                    asset=a,
                    entry=100.0, sl=98.0, tp=102.4,
                    confidence=0.80,
                ),
            }
            scenarios.append(scenario)
        return scenarios

    # -- Stale Data --

    def _stale_data_scenarios(self) -> list[dict]:
        stale_time = datetime.now(UTC) - timedelta(minutes=10)
        return [
            {
                "name": "stale_data_10min_old",
                "category": "stale_data",
                "description": (
                    "Trade idea with a timestamp 10 minutes in the past. "
                    "Stale data guard threshold is 300 seconds (5 min)."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    timestamp=stale_time, confidence=0.80,
                ),
            },
            {
                "name": "stale_data_6min_old",
                "category": "stale_data",
                "description": (
                    "Trade idea with a timestamp 6 minutes in the past. "
                    "Just over the 5-minute stale threshold."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    timestamp=datetime.now(UTC) - timedelta(minutes=6),
                    confidence=0.80,
                ),
            },
        ]

    # -- Confidence Manipulation --

    def _confidence_manipulation_scenarios(self) -> list[dict]:
        return [
            {
                "name": "confidence_below_threshold_0.59",
                "category": "confidence_manipulation",
                "description": (
                    "Confidence 0.59, just below the 0.60 threshold. "
                    "Should be rejected."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(confidence=0.59),
            },
            {
                "name": "confidence_at_threshold_0.60",
                "category": "confidence_manipulation",
                "description": (
                    "Confidence exactly 0.60 -- at the minimum threshold. "
                    "Should be approved (>= 0.60)."
                ),
                "expected_verdict": "APPROVED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(confidence=0.60),
            },
            {
                "name": "confidence_above_threshold_0.61",
                "category": "confidence_manipulation",
                "description": (
                    "Confidence 0.61, just above threshold. "
                    "Should be approved."
                ),
                "expected_verdict": "APPROVED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(confidence=0.61),
            },
        ]

    # -- R:R Gaming --

    def _rr_gaming_scenarios(self) -> list[dict]:
        # min_risk_reward = 1.2, but risk engine uses 0.01 epsilon tolerance:
        #   rr < min_risk_reward - 0.01  →  rr < 1.19
        # So R:R 1.19 passes (1.19 < 1.19 is False). Use 1.18 to trigger rejection.
        # R:R = reward / risk = (TP - entry) / (entry - SL) for LONG
        # For entry=50000, SL=49000 (risk=1000, 2% stop → within margin risk cap):
        #   R:R 1.18 -> TP = 50000 + 1180 = 51180  (below epsilon, rejected)
        #   R:R 1.20 -> TP = 50000 + 1200 = 51200  (at threshold, approved)
        #   R:R 1.21 -> TP = 50000 + 1210 = 51210  (above threshold, approved)
        entry, sl = 50000.0, 49000.0
        risk = entry - sl  # 1000
        return [
            {
                "name": "rr_below_threshold_1.18",
                "category": "rr_gaming",
                "description": (
                    "R:R ratio 1.18, below the 1.20 minimum even with 0.01 "
                    "epsilon tolerance. Should be rejected."
                ),
                "expected_verdict": "REJECTED",
                "atr": entry * 0.02,  # valid ATR (2% of price)
                "build_idea": lambda: _make_idea(
                    entry=entry, sl=sl, tp=entry + risk * 1.18,
                    confidence=0.80,
                ),
            },
            {
                "name": "rr_at_threshold_1.20",
                "category": "rr_gaming",
                "description": (
                    "R:R ratio exactly 1.20 -- at the minimum. "
                    "Should be approved (>= 1.2)."
                ),
                "expected_verdict": "APPROVED",
                "atr": entry * 0.02,
                "build_idea": lambda: _make_idea(
                    entry=entry, sl=sl, tp=entry + risk * 1.20,
                    confidence=0.80,
                ),
            },
            {
                "name": "rr_above_threshold_1.21",
                "category": "rr_gaming",
                "description": (
                    "R:R ratio 1.21, just above threshold. "
                    "Should be approved."
                ),
                "expected_verdict": "APPROVED",
                "atr": entry * 0.02,
                "build_idea": lambda: _make_idea(
                    entry=entry, sl=sl, tp=entry + risk * 1.21,
                    confidence=0.80,
                ),
            },
        ]

    # -- Circuit Breaker Evasion --

    def _circuit_breaker_evasion_scenarios(self) -> list[dict]:
        return [
            {
                "name": "circuit_breaker_trade_after_trip",
                "category": "circuit_breaker_evasion",
                "description": (
                    "Simulate 5 consecutive losses to trip the circuit breaker, "
                    "then submit a perfectly valid trade. Should be rejected."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: self._trip_breaker_and_build_idea(),
            },
        ]

    def _trip_breaker_and_build_idea(self) -> TradeIdea:
        """Record enough losses to trip the circuit breaker, then build a valid idea."""
        for _ in range(6):
            self._engine.record_trade_result(-100.0)
        return _make_idea(confidence=0.80)

    # -- Zero / Negative Values --

    def _zero_negative_scenarios(self) -> list[dict]:
        return [
            {
                "name": "zero_entry_price",
                "category": "zero_negative",
                "description": (
                    "Entry price of 0. Should be caught by the model validator "
                    "or the entry-price sanity check."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    entry=0.0, sl=-1.0, tp=1.0, confidence=0.80,
                ),
            },
            {
                "name": "zero_stop_loss",
                "category": "zero_negative",
                "description": (
                    "Stop loss of 0 with positive entry. "
                    "Risk calculations become degenerate."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    entry=50000.0, sl=0.0, tp=55000.0, confidence=0.80,
                ),
            },
            {
                "name": "negative_entry_price",
                "category": "zero_negative",
                "description": (
                    "Negative entry price. Should be blocked by validator "
                    "or sanity checks."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    entry=-100.0, sl=-200.0, tp=100.0, confidence=0.80,
                ),
            },
        ]

    # -- Direction Inversion --

    def _direction_inversion_scenarios(self) -> list[dict]:
        return [
            {
                "name": "long_sl_above_entry",
                "category": "direction_inversion",
                "description": (
                    "LONG trade with stop loss ABOVE entry price. "
                    "Model validator should reject this."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    direction=Direction.LONG,
                    entry=50000.0, sl=51000.0, tp=55000.0,
                    confidence=0.80,
                ),
            },
            {
                "name": "short_sl_below_entry",
                "category": "direction_inversion",
                "description": (
                    "SHORT trade with stop loss BELOW entry price. "
                    "Model validator should reject this."
                ),
                "expected_verdict": "REJECTED",
                "atr": "auto",
                "build_idea": lambda: _make_idea(
                    direction=Direction.SHORT,
                    entry=50000.0, sl=49000.0, tp=45000.0,
                    confidence=0.80,
                ),
            },
        ]

    # -- Max Position Flood --

    def _max_position_flood_scenarios(self) -> list[dict]:
        # max_open_positions = 5; open 5 valid ones then try a 6th.
        # Each scenario's pre_setup opens all prior positions so the portfolio
        # state is correct when the risk engine evaluates position count.
        # Stop distances are 2% to stay within margin risk cap at 5x leverage.
        assets = [
            ("BTC/USDT", 50000.0),
            ("ETH/USDT", 3000.0),
            ("DOGE/USDT", 0.15),
            ("LINK/USDT", 15.0),
            ("UNI/USDT", 10.0),
            ("BNB/USDT", 600.0),
        ]
        scenarios = []
        for i, (asset, price) in enumerate(assets):
            is_overflow = i >= 5
            sl = round(price * 0.98, 8)    # 2% stop
            tp = round(price * 1.024, 8)   # R:R = 2.4% / 2% = 1.2

            def _pre_setup(idx=i, assets_list=assets) -> None:
                """Ensure all positions before this index are open."""
                # Reset risk engine state (prior circuit-breaker tests may have
                # tripped it) -- we only care about position-count checks here.
                if idx == 0:
                    self._engine.reset_circuit_breaker()
                    # Close any positions left from correlated-selloff tests
                    for pos in list(self._portfolio.open_positions):
                        self._portfolio.close_position(pos.trade_id, pos.entry_price)
                for j in range(idx):
                    a, p = assets_list[j]
                    if not any(pos.asset == a for pos in self._portfolio.open_positions):
                        idea = _make_idea(
                            asset=a, entry=p,
                            sl=round(p * 0.98, 8),
                            tp=round(p * 1.024, 8),
                            confidence=0.80, idea_id=f"flood-setup-{j}",
                        )
                        self._portfolio.open_position(idea, size_usd=100.0)

            scenario: dict = {
                "name": f"position_flood_{i+1}_{asset.split('/')[0].lower()}",
                "category": "max_position_flood",
                "description": (
                    f"Open position #{i+1} of max 5. "
                    f"{'Should be REJECTED (exceeds max open positions).' if is_overflow else 'Should be APPROVED.'}"
                ),
                "expected_verdict": "REJECTED" if is_overflow else "APPROVED",
                "atr": "auto",
                "pre_setup": _pre_setup,
                "build_idea": lambda a=asset, p=price, s=sl, t=tp: _make_idea(
                    asset=a, entry=p, sl=s, tp=t,
                    confidence=0.80,
                ),
            }
            scenarios.append(scenario)
        return scenarios
