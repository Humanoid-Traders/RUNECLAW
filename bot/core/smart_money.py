"""
RUNECLAW Smart Money Engine -- advanced institutional flow detection.

Builds on top of OrderFlowSignal (order_flow.py) to detect:
  - Liquidation cascade risk from crowded positioning
  - Funding rate squeeze setups
  - Whale accumulation/distribution patterns
  - Composite smart money score for confluence voting

Design rules (consistent with the rest of RUNECLAW):
  - Fail-closed: any failed computation → neutral, lower confidence
  - Read-only: no state outside this module is mutated
  - Thread-safe rolling state (RLock), bounded to cap memory
  - All scores normalized to [-1, 1]
  - Never generates trade signals — feeds into confluence scorer
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field

from bot.core.order_flow import OrderFlowSignal
from bot.utils.logger import audit, system_log


# ── Output Models ─────────────────────────────────────────────────

class SmartMoneyScore(BaseModel):
    """Composite smart money intelligence for one symbol."""
    symbol: str = ""
    # Core scores (all [-1, 1] where positive = bullish)
    institutional_bias: float = 0.0
    retail_contrarian: float = 0.0
    whale_accumulation: float = 0.0
    # Liquidation risk
    cascade_risk: float = 0.0          # [0, 1]
    cascade_direction: str = "none"    # "long_squeeze" | "short_squeeze" | "none"
    # Funding squeeze
    squeeze_signal: float = 0.0        # [-1, 1]
    squeeze_type: str = "none"         # "long_squeeze" | "short_squeeze" | "none"
    # Composite
    composite_score: float = 0.0       # [-1, 1]
    confidence: float = 0.0            # [0, 1]
    components_resolved: int = 0
    components_total: int = 4
    narrative: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Detectors ─────────────────────────────────────────────────────

class LiquidationCascadeDetector:
    """Detect conditions for cascading liquidations.

    When funding is extreme (crowded positioning) and price moves against
    the crowd, liquidations trigger more liquidations. This detector
    estimates that risk from funding rate + OI + price movement.
    """

    def __init__(self, funding_extreme: float = 0.0005) -> None:
        self._funding_extreme = funding_extreme

    def evaluate(self, sig: OrderFlowSignal) -> tuple[float, str]:
        """Returns (cascade_risk 0-1, direction)."""
        if sig.funding_rate is None:
            return 0.0, "none"

        fr = sig.funding_rate
        abs_fr = abs(fr)

        # No cascade risk if funding is mild
        if abs_fr < self._funding_extreme * 0.5:
            return 0.0, "none"

        # Crowd is long (positive funding) or short (negative funding)
        crowd_long = fr > 0

        # Risk scales with how extreme funding is
        intensity = min(1.0, abs_fr / (self._funding_extreme * 3))

        # OI change amplifies: rising OI = more leverage = more risk
        # W-P2 FIX: Check >10 before >5 so the higher amplifier isn't shadowed.
        oi_amp = 1.0
        if sig.oi_change_pct is not None and sig.oi_change_pct > 10:
            oi_amp = 1.6
        elif sig.oi_change_pct is not None and sig.oi_change_pct > 5:
            oi_amp = 1.3

        # CVD divergence: if CVD diverges from crowd direction → cascade imminent
        div_amp = 1.0
        if crowd_long and sig.cvd_trend == "falling":
            div_amp = 1.4  # crowd long but buying pressure dropping
        elif not crowd_long and sig.cvd_trend == "rising":
            div_amp = 1.4  # crowd short but selling pressure dropping

        risk = min(1.0, intensity * oi_amp * div_amp)
        direction = "long_squeeze" if crowd_long else "short_squeeze"

        return round(risk, 3), direction


class FundingSqueezeDetector:
    """Detect funding rate squeeze setups.

    Extreme funding = crowded trade. The contrarian bet is that the crowd
    gets squeezed. Positive = bullish contrarian (crowd is short, squeeze up).
    """

    def __init__(self, extreme: float = 0.0005) -> None:
        self._extreme = extreme
        self._lock = threading.RLock()
        self._history: dict[str, deque] = {}  # symbol → recent funding rates

    def evaluate(self, sig: OrderFlowSignal) -> tuple[float, str]:
        """Returns (squeeze_signal [-1,1], squeeze_type)."""
        if sig.funding_rate is None:
            return 0.0, "none"

        fr = sig.funding_rate

        # Track funding momentum
        with self._lock:
            hist = self._history.setdefault(sig.symbol, deque(maxlen=20))
            hist.append(fr)

        abs_fr = abs(fr)
        if abs_fr < self._extreme * 0.5:
            return 0.0, "none"

        # Contrarian: extreme positive funding → long squeeze likely → bearish
        # Extreme negative funding → short squeeze likely → bullish
        signal = -float(np.clip(fr / (self._extreme * 2), -1, 1))

        # Amplify if funding has been building (momentum)
        if len(hist) >= 5:
            recent_avg = float(np.mean(list(hist)[-5:]))
            prior_avg = float(np.mean(list(hist)[:max(1, len(hist) - 5)]))
            if abs(recent_avg) > abs(prior_avg) * 1.3:
                signal *= 1.3
                signal = float(np.clip(signal, -1, 1))

        squeeze_type = "short_squeeze" if signal > 0.2 else "long_squeeze" if signal < -0.2 else "none"
        return round(signal, 4), squeeze_type


class WhaleFlowTracker:
    """Track whale accumulation/distribution across multiple observations.

    Goes beyond single-snapshot whale detection (order_flow.py) to track
    patterns over time: stealth accumulation (many medium buys), persistent
    distribution, and whale/retail volume ratio.
    """

    def __init__(self, history_len: int = 30) -> None:
        self._lock = threading.RLock()
        self._whale_history: dict[str, deque] = {}  # symbol → (buy_usd, sell_usd) pairs
        self._history_len = history_len

    def evaluate(self, sig: OrderFlowSignal) -> float:
        """Returns whale_accumulation_score [-1, 1]."""
        # LB-7 FIX: Reject empty symbol to prevent cross-symbol history corruption.
        # All symbols sharing key "" would corrupt each other's whale tracking.
        symbol = sig.symbol
        if not symbol:
            return 0.0

        with self._lock:
            hist = self._whale_history.setdefault(
                symbol, deque(maxlen=self._history_len))
            hist.append((sig.whale_buy_usd, sig.whale_sell_usd))

        if len(hist) < 3:
            return 0.0

        # Compute rolling whale bias
        total_buy = sum(h[0] for h in hist)
        total_sell = sum(h[1] for h in hist)
        total = total_buy + total_sell

        if total <= 0:
            return 0.0

        # Net whale bias: positive = accumulation, negative = distribution
        bias = (total_buy - total_sell) / total

        # Check for stealth accumulation: consistent small-to-medium whale buys
        # (more significant than one big whale buy)
        recent = list(hist)[-min(10, len(hist)):]
        buy_sessions = sum(1 for b, s in recent if b > s)
        consistency = buy_sessions / len(recent)

        # Consistency amplifier: 8/10 sessions with whale buying > selling is strong
        if consistency > 0.7:
            bias *= 1.3
        elif consistency < 0.3:
            bias *= 1.3  # consistent selling is also strong

        return round(float(np.clip(bias, -1, 1)), 4)

    def prune(self, max_symbols: int = 300) -> None:
        """Cap tracked symbols."""
        with self._lock:
            if len(self._whale_history) > max_symbols:
                for k in list(self._whale_history)[:-max_symbols]:
                    del self._whale_history[k]


# ── Composite Engine ──────────────────────────────────────────────

class SmartMoneyEngine:
    """Blends all smart money signals into a composite score.

    Usage:
        engine = SmartMoneyEngine()
        score = engine.analyze(order_flow_signal)
        votes, weights, labels = SmartMoneyEngine.to_confluence_votes(score)
    """

    def __init__(self) -> None:
        self.cascade = LiquidationCascadeDetector()
        self.squeeze = FundingSqueezeDetector()
        self.whales = WhaleFlowTracker()

    def analyze(self, sig: OrderFlowSignal) -> SmartMoneyScore:
        """Produce a SmartMoneyScore from an OrderFlowSignal."""
        score = SmartMoneyScore(symbol=sig.symbol)
        resolved = 0

        # 1. Institutional bias from order flow composite
        if sig.confidence > 0:
            score.institutional_bias = round(sig.smart_money_score, 4)
            resolved += 1

        # 2. Liquidation cascade risk
        try:
            risk, direction = self.cascade.evaluate(sig)
            score.cascade_risk = risk
            score.cascade_direction = direction
            resolved += 1
        except Exception:
            pass

        # 3. Funding squeeze
        try:
            squeeze_sig, squeeze_type = self.squeeze.evaluate(sig)
            score.squeeze_signal = squeeze_sig
            score.squeeze_type = squeeze_type
            # Retail contrarian = inverse of crowd positioning
            score.retail_contrarian = squeeze_sig
            resolved += 1
        except Exception:
            pass

        # 4. Whale flow
        try:
            whale_score = self.whales.evaluate(sig)
            score.whale_accumulation = whale_score
            resolved += 1
        except Exception:
            pass

        score.components_resolved = resolved
        score.confidence = round(resolved / score.components_total, 2)

        # Composite: weighted blend
        weights = {
            "institutional": 0.35,
            "contrarian": 0.20,
            "whale": 0.25,
            "cascade_adj": 0.20,
        }
        cascade_adj = 0.0
        if score.cascade_direction == "long_squeeze":
            cascade_adj = -score.cascade_risk  # bearish
        elif score.cascade_direction == "short_squeeze":
            cascade_adj = score.cascade_risk   # bullish

        composite = (
            score.institutional_bias * weights["institutional"]
            + score.retail_contrarian * weights["contrarian"]
            + score.whale_accumulation * weights["whale"]
            + cascade_adj * weights["cascade_adj"]
        )
        score.composite_score = round(float(np.clip(composite, -1, 1)), 4)

        # Narrative
        score.narrative = self._build_narrative(score)

        self.whales.prune()
        return score

    @staticmethod
    def to_confluence_votes(score: SmartMoneyScore) -> tuple[list[float], list[float], list[str]]:
        """Return (votes, weights, labels) for the confluence scorer."""
        votes: list[float] = []
        weights: list[float] = []
        labels: list[str] = []
        conf = max(0.0, score.confidence)

        if conf == 0:
            return votes, weights, labels

        # Composite smart money vote
        if abs(score.composite_score) > 0.05:
            votes.append(score.composite_score)
            weights.append(1.0 * conf)
            labels.append("smart_money_composite")

        # Whale accumulation (independent signal)
        if abs(score.whale_accumulation) > 0.1:
            votes.append(score.whale_accumulation)
            weights.append(0.7 * conf)
            labels.append("whale_accumulation")

        # Liquidation cascade warning (if high risk, vote in squeeze direction)
        if score.cascade_risk > 0.5:
            cascade_vote = -1.0 if score.cascade_direction == "long_squeeze" else 1.0
            votes.append(cascade_vote)
            weights.append(0.8 * score.cascade_risk * conf)
            labels.append("liquidation_cascade")

        return votes, weights, labels

    @staticmethod
    def _build_narrative(score: SmartMoneyScore) -> str:
        """Human-readable explanation of smart money signals."""
        parts: list[str] = []

        if abs(score.institutional_bias) > 0.2:
            direction = "bullish" if score.institutional_bias > 0 else "bearish"
            parts.append(f"Institutional flow is {direction} ({score.institutional_bias:+.2f})")

        if abs(score.whale_accumulation) > 0.15:
            action = "accumulating" if score.whale_accumulation > 0 else "distributing"
            parts.append(f"Whales are {action} ({score.whale_accumulation:+.2f})")

        if score.cascade_risk > 0.4:
            parts.append(
                f"Liquidation cascade risk: {score.cascade_risk:.0%} "
                f"({score.cascade_direction.replace('_', ' ')})"
            )

        if score.squeeze_type != "none":
            parts.append(f"Funding squeeze setup: {score.squeeze_type.replace('_', ' ')}")

        if not parts:
            parts.append("No significant smart money signals detected")

        return ". ".join(parts) + "."

    # ------------------------------------------------------------------
    # On-Chain Flow Signal methods (intelligence layer upgrade, task 13)
    # ------------------------------------------------------------------

    @staticmethod
    def analyze_exchange_flow(net_flow_btc: float, avg_daily_flow: float) -> dict:
        """Analyse net exchange flow for accumulation/distribution signals.

        Parameters
        ----------
        net_flow_btc : float
            Net BTC flow to exchanges.  Negative = outflow (accumulation).
        avg_daily_flow : float
            Average daily absolute net flow for normalisation.

        Returns
        -------
        dict  with keys flow_signal, magnitude, interpretation.
        """
        if avg_daily_flow <= 0:
            return {
                "flow_signal": "NEUTRAL",
                "magnitude": 0.0,
                "interpretation": "Insufficient flow data",
            }

        ratio = net_flow_btc / avg_daily_flow
        magnitude = min(1.0, abs(ratio) / 3.0)

        if net_flow_btc < -avg_daily_flow * 1.5:
            signal = "BULLISH"
            interpretation = "Large exchange outflow — accumulation detected"
        elif net_flow_btc > avg_daily_flow * 1.5:
            signal = "BEARISH"
            interpretation = "Large exchange inflow — distribution detected"
        else:
            signal = "NEUTRAL"
            interpretation = "Exchange flow within normal range"

        return {
            "flow_signal": signal,
            "magnitude": round(magnitude, 4),
            "interpretation": interpretation,
        }

    @staticmethod
    def analyze_whale_activity(large_tx_count: int, avg_large_tx: int) -> dict:
        """Detect elevated whale transaction activity.

        Parameters
        ----------
        large_tx_count : int
            Number of large transactions in the current period.
        avg_large_tx : int
            Historical average large transactions per period.

        Returns
        -------
        dict  with keys signal, activity_ratio, interpretation.
        """
        if avg_large_tx <= 0:
            return {
                "signal": "NEUTRAL",
                "activity_ratio": 0.0,
                "interpretation": "No baseline whale data",
            }

        ratio = large_tx_count / avg_large_tx

        if ratio > 1.5:
            signal = "ACTIVE"
            interpretation = "Elevated whale activity — large transactions above average"
        elif ratio > 1.0:
            signal = "SLIGHTLY_ACTIVE"
            interpretation = "Whale activity slightly above average"
        else:
            signal = "QUIET"
            interpretation = "Whale activity at or below average"

        return {
            "signal": signal,
            "activity_ratio": round(ratio, 4),
            "interpretation": interpretation,
        }

    @staticmethod
    def composite_flow_signal(
        exchange_flow: dict,
        whale: dict,
        oi_change_pct: float = 0,
    ) -> dict:
        """Combine exchange flow, whale activity and OI change into a single signal.

        Returns
        -------
        dict  with keys bias, confidence, factors.
        """
        bias_map = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}

        # Exchange flow component
        flow_score = bias_map.get(exchange_flow.get("flow_signal", "NEUTRAL"), 0.0)
        flow_mag = exchange_flow.get("magnitude", 0.0)

        # Whale activity component — active whales amplify directional bias
        whale_signal = whale.get("signal", "QUIET")
        whale_ratio = whale.get("activity_ratio", 1.0)
        whale_amp = min(1.5, whale_ratio) if whale_signal in ("ACTIVE", "SLIGHTLY_ACTIVE") else 1.0

        # OI change: rising OI = conviction, falling OI = unwinding
        oi_factor = 0.0
        if abs(oi_change_pct) > 2:
            oi_factor = max(-1.0, min(1.0, oi_change_pct / 20.0))

        # Composite score
        composite = flow_score * flow_mag * whale_amp + 0.2 * oi_factor
        composite = max(-1.0, min(1.0, composite))

        if composite > 0.15:
            bias = "BULLISH"
        elif composite < -0.15:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        # Confidence based on data quality
        confidence = min(1.0, flow_mag * 0.5 + (0.3 if whale_signal != "QUIET" else 0.0) + (0.2 if abs(oi_change_pct) > 2 else 0.0))

        factors = []
        factors.append(f"Exchange flow: {exchange_flow.get('flow_signal', 'N/A')}")
        factors.append(f"Whale activity: {whale_signal} (ratio={whale.get('activity_ratio', 0):.2f})")
        if abs(oi_change_pct) > 0:
            factors.append(f"OI change: {oi_change_pct:+.1f}%")

        return {
            "bias": bias,
            "confidence": round(confidence, 4),
            "factors": factors,
        }
