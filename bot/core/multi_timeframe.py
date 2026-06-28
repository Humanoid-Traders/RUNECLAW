"""
RUNECLAW Multi-Timeframe Analysis -- HTF trend alignment and market structure.

Provides higher-timeframe confluence beyond the single-timeframe SMA-50 proxy:
  - Per-timeframe trend direction (EMA20 vs EMA50, RSI, ADX)
  - Alignment score across 1H/4H/1D
  - Market structure: HH/HL (bullish), LH/LL (bearish), BOS, CHoCH
  - Feeds into confluence scorer via to_confluence_votes()

Design rules:
  - Fail-closed: missing HTF data → neutral vote, lower confidence
  - Pure computation, no side effects
  - All scores normalized to [-1, 1]
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from pydantic import BaseModel, Field

from bot.core.ta_utils import _ema, _compute_adx


# ── Output Model ──────────────────────────────────────────────────

class MTFResult(BaseModel):
    """Multi-timeframe analysis result."""
    alignment_score: float = 0.0       # [-1, 1] positive = bullish alignment
    structure_bias: float = 0.0        # [-1, 1] from market structure
    htf_trend: str = "neutral"         # "bullish" | "bearish" | "neutral"
    aligned_timeframes: list[str] = Field(default_factory=list)
    conflicting_timeframes: list[str] = Field(default_factory=list)
    bos_detected: bool = False         # break of structure
    choch_detected: bool = False       # change of character
    per_tf: dict[str, dict] = Field(default_factory=dict)  # per-TF details
    confidence: float = 0.0            # [0, 1]
    narrative: str = ""


# ── Swing Detection ───────────────────────────────────────────────

def _find_swings(highs: np.ndarray, lows: np.ndarray, lookback: int = 5) -> dict:
    """Detect swing highs and swing lows.

    A swing high is a high that is higher than `lookback` bars on each side.
    Returns the last 4 swing points for structure analysis.
    """
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    for i in range(lookback, len(highs) - lookback):
        # Swing high: higher than all neighbors
        if all(highs[i] > highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, lookback + 1)):
            swing_highs.append((i, float(highs[i])))

        # Swing low: lower than all neighbors
        if all(lows[i] < lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, lookback + 1)):
            swing_lows.append((i, float(lows[i])))

    return {
        "swing_highs": swing_highs[-4:] if swing_highs else [],
        "swing_lows": swing_lows[-4:] if swing_lows else [],
    }


# ── Structure Analysis ────────────────────────────────────────────

def _analyze_structure(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, lookback: int = 5) -> dict:
    """Detect market structure: HH/HL, LH/LL, BOS, CHoCH.

    Args:
        highs: High prices array
        lows: Low prices array
        closes: Close prices array
        lookback: Swing detection lookback window

    Returns:
        structure: "bullish" | "bearish" | "ranging"
        bos: True if break of structure detected
        choch: True if change of character detected
        bias: float [-1, 1]
    """
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    result = {"structure": "ranging", "bos": False, "choch": False, "bias": 0.0}

    if len(sh) < 2 or len(sl) < 2:
        return result

    # Check last two swing highs and lows
    hh = sh[-1][1] > sh[-2][1]  # higher high
    hl = sl[-1][1] > sl[-2][1]  # higher low
    lh = sh[-1][1] < sh[-2][1]  # lower high
    ll = sl[-1][1] < sl[-2][1]  # lower low

    # Bullish structure: HH + HL
    if hh and hl:
        result["structure"] = "bullish"
        result["bias"] = 0.7

    # Bearish structure: LH + LL
    elif lh and ll:
        result["structure"] = "bearish"
        result["bias"] = -0.7

    # Mixed: potential BOS or CHoCH
    elif hh and ll:
        # HH but LL — conflicting, could be a breakout
        result["structure"] = "ranging"
        result["bias"] = 0.0
    elif lh and hl:
        # LH but HL — compression / triangle
        result["structure"] = "ranging"
        result["bias"] = 0.0

    # Break of Structure: price closes beyond the last swing
    current_price = float(closes[-1])
    if len(sh) >= 1 and current_price > sh[-1][1] * 1.001:
        result["bos"] = True
        result["bias"] = min(1.0, result["bias"] + 0.3)
    elif len(sl) >= 1 and current_price < sl[-1][1] * 0.999:
        result["bos"] = True
        result["bias"] = max(-1.0, result["bias"] - 0.3)

    # Change of Character: structure was one way, now swings reverse
    if len(sh) >= 3 and len(sl) >= 3:
        prev_bullish = sh[-3][1] < sh[-2][1] and sl[-3][1] < sl[-2][1]
        now_bearish = sh[-1][1] < sh[-2][1] and sl[-1][1] < sl[-2][1]
        prev_bearish = sh[-3][1] > sh[-2][1] and sl[-3][1] > sl[-2][1]
        now_bullish = sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]

        if (prev_bullish and now_bearish) or (prev_bearish and now_bullish):
            result["choch"] = True
    elif len(sh) >= 2 and len(sl) >= 2:
        # With only 2 swing points, detect reversal from structure bias
        now_bearish = sh[-1][1] < sh[-2][1] and sl[-1][1] < sl[-2][1]
        now_bullish = sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]
        # CHoCH if current structure opposes the BOS direction
        if result["bos"] and ((result["bias"] > 0 and now_bearish) or
                               (result["bias"] < 0 and now_bullish)):
            result["choch"] = True

    return result


# ── Per-Timeframe Analysis ────────────────────────────────────────

def _analyze_single_tf(
    candles: list[list[float]], label: str
) -> Optional[dict]:
    """Analyze a single timeframe's candles.

    Returns dict with trend_direction, momentum, regime, or None if
    insufficient data.
    """
    if len(candles) < 30:
        return None

    closes = np.array([c[4] for c in candles], dtype=float)
    highs = np.array([c[2] for c in candles], dtype=float)
    lows = np.array([c[3] for c in candles], dtype=float)

    # EMA 20 vs EMA 50 for trend
    ema20 = _ema(closes, min(20, len(closes) - 1))
    ema50 = _ema(closes, min(50, len(closes) - 1))

    ema20_val = float(ema20[-1])
    ema50_val = float(ema50[-1])
    price = float(closes[-1])

    # Trend direction
    if price > ema20_val > ema50_val:
        trend = "bullish"
        trend_score = 1.0
    elif price < ema20_val < ema50_val:
        trend = "bearish"
        trend_score = -1.0
    elif ema20_val > ema50_val:
        trend = "weak_bullish"
        trend_score = 0.4
    elif ema20_val < ema50_val:
        trend = "weak_bearish"
        trend_score = -0.4
    else:
        trend = "neutral"
        trend_score = 0.0

    # RSI for momentum (Wilder's smoothing)
    deltas = np.diff(closes)
    gain = np.where(deltas > 0, deltas, 0.0)
    loss = np.where(deltas < 0, -deltas, 0.0)
    period = 14
    if len(deltas) < period:
        avg_gain = float(np.mean(gain)) if len(gain) else 0
        avg_loss = float(np.mean(loss)) if len(loss) else 1e-10
    else:
        # Initial SMA seed
        avg_gain = float(np.mean(gain[:period]))
        avg_loss = float(np.mean(loss[:period]))
        # Wilder's smoothing for remaining bars
        for i in range(period, len(gain)):
            avg_gain = (avg_gain * (period - 1) + gain[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        avg_loss = max(avg_loss, 1e-10)
    if avg_loss > 0:
        rsi = 100 - 100 / (1 + avg_gain / avg_loss)
    else:
        rsi = 50.0

    # ADX for regime
    adx_data = _compute_adx(highs, lows, closes, 14)
    adx = adx_data["adx"]

    # Market structure
    structure = _analyze_structure(highs, lows, closes, lookback=min(5, len(highs) // 6))

    return {
        "label": label,
        "trend": trend,
        "trend_score": round(trend_score, 2),
        "rsi": round(rsi, 2),
        "adx": adx,
        "ema20": round(ema20_val, 6),
        "ema50": round(ema50_val, 6),
        "price": round(price, 6),
        "structure": structure["structure"],
        "structure_bias": round(structure["bias"], 2),
        "bos": structure["bos"],
        "choch": structure["choch"],
    }


# ── MTF Confluence ────────────────────────────────────────────────

class MTFConfluence:
    """Multi-timeframe trend alignment analyzer.

    Usage:
        mtf = MTFConfluence()
        result = mtf.analyze(candles_1h, candles_4h, candles_1d)
        votes, weights, labels = MTFConfluence.to_confluence_votes(result)
    """

    def analyze(
        self,
        candles_1h: Optional[list[list[float]]] = None,
        candles_4h: Optional[list[list[float]]] = None,
        candles_1d: Optional[list[list[float]]] = None,
    ) -> MTFResult:
        """Analyze alignment across timeframes."""
        result = MTFResult()
        tf_results: dict[str, dict] = {}

        for label, candles in [("1h", candles_1h), ("4h", candles_4h), ("1d", candles_1d)]:
            if candles and len(candles) >= 30:
                tf = _analyze_single_tf(candles, label)
                if tf:
                    tf_results[label] = tf

        if not tf_results:
            result.narrative = "No timeframe data available for MTF analysis."
            return result

        result.per_tf = tf_results

        # Compute alignment: do all timeframes agree on direction?
        scores = [tf["trend_score"] for tf in tf_results.values()]
        labels_list = list(tf_results.keys())

        # Alignment: weighted average — daily carries most weight
        tf_weights = {"1d": 0.5, "4h": 0.3, "1h": 0.2}
        weighted_sum = 0.0
        weight_total = 0.0
        for tf_key, result_tf in tf_results.items():
            w = tf_weights.get(tf_key, 0.2)
            weighted_sum += result_tf["trend_score"] * w
            weight_total += w
        alignment_score = weighted_sum / weight_total if weight_total > 0 else 0.0
        result.alignment_score = round(float(np.clip(alignment_score, -1, 1)), 4)

        # Classify aligned vs conflicting
        majority_bullish = alignment_score > 0.2
        majority_bearish = alignment_score < -0.2
        for label, tf in tf_results.items():
            if (majority_bullish and tf["trend_score"] > 0) or \
               (majority_bearish and tf["trend_score"] < 0):
                result.aligned_timeframes.append(label)
            elif (majority_bullish and tf["trend_score"] < -0.2) or \
                 (majority_bearish and tf["trend_score"] > 0.2):
                result.conflicting_timeframes.append(label)
            else:
                # Only count as aligned if trend is clear (not neutral)
                if abs(tf["trend_score"]) > 0.2:
                    result.aligned_timeframes.append(label)

        # HTF trend: prefer daily, then 4h
        if "1d" in tf_results:
            htf = tf_results["1d"]
        elif "4h" in tf_results:
            htf = tf_results["4h"]
        else:
            htf = list(tf_results.values())[0]

        if htf["trend_score"] > 0.3:
            result.htf_trend = "bullish"
        elif htf["trend_score"] < -0.3:
            result.htf_trend = "bearish"
        else:
            result.htf_trend = "neutral"

        # Structure from HTF
        result.structure_bias = round(htf["structure_bias"], 4)
        result.bos_detected = htf.get("bos", False)
        result.choch_detected = htf.get("choch", False)

        # Confidence: based on how many TFs we have and how aligned they are
        tf_count_conf = len(tf_results) / 3.0
        alignment_conf = 1.0 - len(result.conflicting_timeframes) / max(len(tf_results), 1)
        result.confidence = round(tf_count_conf * alignment_conf, 2)

        # Narrative
        result.narrative = self._build_narrative(result, tf_results)
        return result

    @staticmethod
    def to_confluence_votes(result: MTFResult) -> tuple[list[float], list[float], list[str]]:
        """Return (votes, weights, labels) for the confluence scorer."""
        votes: list[float] = []
        weights: list[float] = []
        labels: list[str] = []
        conf = max(0.0, result.confidence)

        if conf == 0:
            return votes, weights, labels

        # HTF alignment vote
        if abs(result.alignment_score) > 0.1:
            votes.append(result.alignment_score)
            weights.append(1.2 * conf)  # strong weight — HTF alignment is important
            labels.append("mtf_alignment")

        # Market structure vote
        if abs(result.structure_bias) > 0.1:
            votes.append(result.structure_bias)
            weights.append(0.9 * conf)
            labels.append("mtf_structure")

        # BOS gets extra weight — structural breakout is significant
        if result.bos_detected and abs(result.alignment_score) > 0.2:
            votes.append(result.alignment_score)  # same direction as alignment
            weights.append(0.6 * conf)
            labels.append("mtf_bos")

        return votes, weights, labels

    @staticmethod
    def _build_narrative(result: MTFResult, tf_results: dict) -> str:
        parts: list[str] = []

        # Overall alignment
        if len(result.aligned_timeframes) == len(tf_results):
            direction = "bullish" if result.alignment_score > 0 else "bearish"
            parts.append(
                f"All timeframes ({', '.join(result.aligned_timeframes)}) "
                f"aligned {direction}"
            )
        elif result.conflicting_timeframes:
            parts.append(
                f"Timeframe conflict: {', '.join(result.conflicting_timeframes)} "
                f"diverge from {', '.join(result.aligned_timeframes)}"
            )

        # HTF trend
        parts.append(f"HTF trend: {result.htf_trend}")

        # Structure
        if result.bos_detected:
            parts.append("Break of structure detected on HTF")
        if result.choch_detected:
            parts.append("Change of character (CHoCH) detected — potential trend reversal")

        return ". ".join(parts) + "." if parts else "Insufficient data for MTF analysis."
