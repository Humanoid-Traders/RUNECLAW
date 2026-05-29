"""
RUNECLAW AI Analyzer -- generates trade theses using LLM + technicals.

Upgraded with:
  - Proper MACD signal line (full history EMA, not truncated)
  - True ATR (high-low, high-close, low-close) instead of close-only proxy
  - ADX-14 for trend strength / regime detection
  - VWAP approximation + rolling VWAP (20/50-bar) for institutional bias
  - On-Balance Volume (OBV) with trend detection
  - Fibonacci retracement levels (swing high/low, 23.6%/38.2%/50%/61.8%/78.6%)
  - Candlestick pattern recognition (doji, hammer, engulfing, harami, morning/evening star, etc.)
  - Confluence scoring model (10-voter weighted indicator agreement)
  - Robust LLM response parsing with fallback
  - Source tagging (LLM vs rule-based) on every output
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Optional

import numpy as np
from openai import AsyncOpenAI

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log
from bot.utils.models import Direction, MarketSignal, TradeIdea


class Regime(str, Enum):
    """Market regime classification based on ADX + directional movement."""
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    CHOP = "CHOP"
    UNKNOWN = "UNKNOWN"


class Analyzer:
    """Produces TradeIdea objects from raw market signals."""

    def __init__(self) -> None:
        self._llm = AsyncOpenAI(api_key=CONFIG.llm.api_key) if CONFIG.llm.api_key else None

    async def analyze(self, signal: MarketSignal, candles: list[list[float]], order_flow=None) -> Optional[TradeIdea]:
        """
        Full analysis pipeline:
        1. Compute technical indicators from OHLCV candles.
        2. Detect market regime via ADX.
        3. Score confluence across indicators.
        4. Ask LLM for a directional thesis (or rule-based fallback).
        5. Structure the result as a TradeIdea.
        Returns None if conviction is too low (<0.5).
        """
        if len(candles) < CONFIG.analyzer.min_candles:
            audit(trade_log, "Not enough candle data", action="analyze",
                  result="SKIP", data={"symbol": signal.symbol})
            return None

        opens = np.array([c[1] for c in candles], dtype=float)
        highs = np.array([c[2] for c in candles], dtype=float)
        lows = np.array([c[3] for c in candles], dtype=float)
        closes = np.array([c[4] for c in candles], dtype=float)
        volumes = np.array([c[5] for c in candles], dtype=float) if len(candles[0]) > 5 else None

        indicators = self._compute_indicators(highs, lows, closes, volumes)
        if indicators is None:
            audit(trade_log, "Indicator computation failed (insufficient data)", action="analyze",
                  result="SKIP", data={"symbol": signal.symbol, "candles": len(candles)})
            return None

        # Candlestick pattern detection (needs opens)
        candle_patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        if candle_patterns:
            indicators["candle_patterns"] = candle_patterns
            # Summarize bullish/bearish pattern counts for confluence
            bullish_patterns = [k for k, v in candle_patterns.items() if v == "bullish"]
            bearish_patterns = [k for k, v in candle_patterns.items() if v == "bearish"]
            indicators["candle_bullish_count"] = len(bullish_patterns)
            indicators["candle_bearish_count"] = len(bearish_patterns)

        regime = self._detect_regime(indicators)
        confluence = self._score_confluence(indicators, regime, signal, order_flow=order_flow)

        indicators["regime"] = regime.value
        indicators["confluence"] = confluence

        # SIGNAL QUALITY: multi-timeframe SMA50 trend alignment
        sma50 = float(np.mean(closes[-CONFIG.analyzer.sma_period:])) if len(closes) >= CONFIG.analyzer.sma_period else float(np.mean(closes))
        indicators["sma50"] = round(sma50, 6)

        thesis = await self._llm_thesis(signal, indicators, order_flow=order_flow)

        if thesis is None:
            return None

        direction = Direction.LONG if thesis["direction"] == "LONG" else Direction.SHORT

        # SIGNAL QUALITY: ADX regime-aligned trading filter
        # Counter-trend trades are dangerous -- skip entirely.
        # RANGE/CHOP: allow with confidence penalty instead of auto-skip.
        regime_confidence_penalty = 0.0
        regime_sl_override = None
        regime_tp_override = None

        if regime == Regime.TREND_UP and direction == Direction.SHORT:
            audit(trade_log, "Regime filter: TREND_UP but SHORT signal -- skipping",
                  action="analyze", result="SKIP",
                  data={"symbol": signal.symbol, "regime": regime.value})
            return None
        if regime == Regime.TREND_DOWN and direction == Direction.LONG:
            audit(trade_log, "Regime filter: TREND_DOWN but LONG signal -- skipping",
                  action="analyze", result="SKIP",
                  data={"symbol": signal.symbol, "regime": regime.value})
            return None
        if regime == Regime.RANGE:
            # RANGE: needs high raw confluence (0.70+) to survive after penalty
            regime_confidence_penalty = 0.10
            regime_sl_override = 1.5
            regime_tp_override = 2.5
            audit(trade_log, "Regime: RANGE -- applying penalty",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol, "regime": regime.value,
                        "penalty": regime_confidence_penalty})
        elif regime == Regime.CHOP:
            # CHOP: needs very high raw confluence (0.75+) to survive after penalty
            regime_confidence_penalty = 0.15
            regime_sl_override = 1.5
            regime_tp_override = 2.0
            audit(trade_log, "Regime: CHOP -- applying penalty",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol, "regime": regime.value,
                        "penalty": regime_confidence_penalty})

        confidence = max(0.0, min(1.0, thesis.get("confidence", 0.0)))

        # Blend LLM/rule-based confidence with confluence score
        blended_confidence = confidence * CONFIG.analyzer.llm_weight + confluence * CONFIG.analyzer.confluence_weight

        # SIGNAL QUALITY: multi-timeframe confirmation via SMA50
        # Acts as a proxy for higher-timeframe trend alignment on 1H data
        if signal.price > sma50 and direction == Direction.LONG:
            blended_confidence += CONFIG.analyzer.trend_alignment_bonus   # aligned with uptrend
        elif signal.price < sma50 and direction == Direction.SHORT:
            blended_confidence += CONFIG.analyzer.trend_alignment_bonus   # aligned with downtrend
        elif signal.price > sma50 and direction == Direction.SHORT:
            blended_confidence -= CONFIG.analyzer.trend_misalignment_penalty   # counter-trend SHORT
        elif signal.price < sma50 and direction == Direction.LONG:
            blended_confidence -= CONFIG.analyzer.trend_misalignment_penalty   # counter-trend LONG

        # STRATEGY: volume confirmation for direction alignment
        # If volume spike aligns with trade direction, boost confidence;
        # if it conflicts, penalize -- volume should confirm the move.
        if signal.volume_spike:
            price_moving_up = signal.change_pct_24h > 0
            if (price_moving_up and direction == Direction.LONG) or \
               (not price_moving_up and direction == Direction.SHORT):
                blended_confidence += 0.05  # volume confirms direction
            else:
                blended_confidence -= 0.05  # volume contradicts direction

        blended_confidence = round(max(0.0, min(1.0, blended_confidence)), 2)

        # Apply regime penalty (RANGE: -0.10, CHOP: -0.15, else: 0)
        blended_confidence = round(max(0.0, blended_confidence - regime_confidence_penalty), 2)

        # SIGNAL QUALITY: threshold at 0.60 (matches config.min_confidence)
        # RANGE/CHOP trades need high raw confluence to survive after penalty
        if blended_confidence < CONFIG.risk.min_confidence:
            audit(trade_log, "Low blended confidence -- skipping",
                  action="analyze", result="SKIP",
                  data={"symbol": signal.symbol, "raw_conf": confidence,
                        "confluence": confluence, "blended": blended_confidence})
            return None

        entry = signal.price
        atr = indicators.get("atr", entry * 0.02)

        # STRATEGY: adaptive ATR multipliers based on volatility regime
        # Compute normalized volatility: ATR as a percentage of price
        vol_ratio = atr / entry if entry > 0 else 0.02

        # REGIME-SPECIFIC SL/TP: tighter stops in RANGE/CHOP regimes
        # Note: high/low volatility overrides take priority over regime overrides
        if vol_ratio > 0.03:
            # High volatility: widen stops to avoid noise-induced exits
            sl_mult, tp_mult = 3.0, 4.5  # R:R = 1.5 (was 6.0 TP -- never hit)
        elif vol_ratio < 0.01:
            # Low volatility: tighten stops to lock in smaller moves
            sl_mult, tp_mult = 2.0, 3.0  # R:R = 1.5 (was 4.0 TP -- never hit)
        elif regime_sl_override is not None and regime_tp_override is not None:
            # RANGE/CHOP regime: use tighter SL/TP set by regime filter
            sl_mult, tp_mult = regime_sl_override, regime_tp_override
        elif regime == Regime.TREND_UP and direction == Direction.LONG:
            sl_mult, tp_mult = CONFIG.analyzer.sl_atr_mult_trending, CONFIG.analyzer.tp_atr_mult_trending
        elif regime == Regime.TREND_DOWN and direction == Direction.SHORT:
            sl_mult, tp_mult = CONFIG.analyzer.sl_atr_mult_trending, CONFIG.analyzer.tp_atr_mult_trending
        else:
            sl_mult, tp_mult = CONFIG.analyzer.sl_atr_mult_default, CONFIG.analyzer.tp_atr_mult_default

        stop_loss = entry - sl_mult * atr if direction == Direction.LONG else entry + sl_mult * atr
        take_profit = entry + tp_mult * atr if direction == Direction.LONG else entry - tp_mult * atr

        # Tag source
        source = thesis.get("source", "unknown")

        idea = TradeIdea(
            id=f"TI-{uuid.uuid4().hex[:8]}",
            asset=signal.symbol,
            direction=direction,
            entry_price=round(entry, 6),
            stop_loss=round(stop_loss, 6),
            take_profit=round(take_profit, 6),
            confidence=blended_confidence,
            reasoning=f"[{source}|{regime.value}|C={confluence:.2f}] {thesis.get('reasoning', '')}",
            signals_used=list(indicators.keys()),
            timestamp=datetime.now(UTC),
        )

        audit(trade_log, f"Trade idea: {idea.direction.value} {idea.asset}",
              action="analyze", result="IDEA",
              data=idea.model_dump(mode="json"))
        return idea

    # -- Technical Indicators --

    @staticmethod
    def _compute_indicators(
        highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        volumes: Optional[np.ndarray] = None,
    ) -> Optional[dict]:
        """
        Calculate RSI-14, MACD (12/26/9), Bollinger Bands (20/2),
        True ATR-14, ADX-14, and VWAP approximation.

        Returns None if insufficient data (< 30 bars) — fail-closed design.
        """
        if len(closes) < 30:
            return None

        results: dict = {}

        # ── RSI-14 (Wilder's smoothing) ──
        deltas = np.diff(closes)
        gain = np.where(deltas > 0, deltas, 0.0)
        loss = np.where(deltas < 0, -deltas, 0.0)

        # Use Wilder's exponential smoothing, not simple average
        period = 14
        if len(gain) >= period:
            avg_gain = np.mean(gain[:period])
            avg_loss = np.mean(loss[:period])
            for i in range(period, len(gain)):
                avg_gain = (avg_gain * (period - 1) + gain[i]) / period
                avg_loss = (avg_loss * (period - 1) + loss[i]) / period
            rs = avg_gain / max(avg_loss, 1e-10)
            results["rsi"] = round(100 - 100 / (1 + rs), 2)
        else:
            avg_gain = np.mean(gain) if len(gain) > 0 else 0
            avg_loss = np.mean(loss) if len(loss) > 0 else 1e-10
            rs = avg_gain / max(avg_loss, 1e-10)
            results["rsi"] = round(100 - 100 / (1 + rs), 2)

        # ── MACD (12, 26, 9) — full-history EMA ──
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line = ema12 - ema26
        signal_line = _ema(macd_line, 9)  # EMA of full MACD line, not truncated
        macd_histogram = macd_line - signal_line
        results["macd"] = round(float(macd_line[-1]), 6)
        results["macd_signal"] = round(float(signal_line[-1]), 6)
        results["macd_histogram"] = round(float(macd_histogram[-1]), 6)

        # ── Bollinger Bands (20, 2) ──
        if len(closes) >= 20:
            sma20 = np.mean(closes[-20:])
            std20 = np.std(closes[-20:])
            results["bb_upper"] = round(sma20 + 2 * std20, 6)
            results["bb_lower"] = round(sma20 - 2 * std20, 6)
            results["bb_mid"] = round(sma20, 6)
            bb_width = (results["bb_upper"] - results["bb_lower"]) / sma20 if sma20 > 0 else 0
            results["bb_width"] = round(bb_width, 6)
            # %B: where price sits in the band (0=lower, 1=upper)
            bb_range = results["bb_upper"] - results["bb_lower"]
            results["bb_pct_b"] = round(
                (closes[-1] - results["bb_lower"]) / bb_range if bb_range > 0 else 0.5, 4
            )

        # ── True ATR-14 (proper true range) ──
        if len(highs) >= 2:
            tr_hl = highs[1:] - lows[1:]
            tr_hc = np.abs(highs[1:] - closes[:-1])
            tr_lc = np.abs(lows[1:] - closes[:-1])
            true_range = np.maximum(tr_hl, np.maximum(tr_hc, tr_lc))
            if len(true_range) >= 14:
                atr = np.mean(true_range[-14:])
            else:
                atr = np.mean(true_range)
            results["atr"] = round(float(atr), 6)
        else:
            results["atr"] = round(float(closes[-1] * 0.02), 6)

        # ── ADX-14 (Average Directional Index) ──
        adx_data = _compute_adx(highs, lows, closes, 14)
        results["adx"] = adx_data["adx"]
        results["plus_di"] = adx_data["plus_di"]
        results["minus_di"] = adx_data["minus_di"]

        # ── VWAP approximation (if volume available) ──
        if volumes is not None and len(volumes) > 0:
            typical_price = (highs + lows + closes) / 3
            cum_tp_vol = np.cumsum(typical_price * volumes)
            cum_vol = np.cumsum(volumes)
            vwap = cum_tp_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else closes[-1]
            results["vwap"] = round(float(vwap), 6)

            # Rolling VWAP variants (20-bar and 50-bar lookbacks)
            for anchor_len, label in [(20, "vwap_20"), (50, "vwap_50")]:
                if len(volumes) >= anchor_len:
                    seg_tp = typical_price[-anchor_len:]
                    seg_vol = volumes[-anchor_len:]
                    cv = np.sum(seg_tp * seg_vol)
                    sv = np.sum(seg_vol)
                    results[label] = round(float(cv / sv) if sv > 0 else float(closes[-1]), 6)

        # ── OBV (On-Balance Volume) ──
        if volumes is not None and len(volumes) > 1:
            obv = _compute_obv(closes, volumes)
            results["obv"] = round(float(obv[-1]), 2)
            # OBV trend: compare last 5 vs previous 5
            if len(obv) >= 10:
                obv_recent = float(np.mean(obv[-5:]))
                obv_prev = float(np.mean(obv[-10:-5]))
                results["obv_trend"] = "rising" if obv_recent > obv_prev else "falling"
            else:
                results["obv_trend"] = "neutral"

        # ── Fibonacci Retracement Levels ──
        fib = _compute_fibonacci(highs, lows, closes)
        results.update(fib)

        # ── Candlestick Patterns ──
        # Need opens from candles — caller must pass them. Accept via highs[0] proxy
        # or use the static method directly in analyze(). Store placeholder here.
        # Actual detection happens in analyze() where we have opens.

        return results

    # -- Regime Detection --

    @staticmethod
    def _detect_regime(indicators: dict) -> Regime:
        """
        Classify market regime using ADX + directional indicators.

        ADX > 25 + DI+ > DI- → TREND_UP
        ADX > 25 + DI- > DI+ → TREND_DOWN
        ADX < 20             → RANGE (mean-reversion favorable)
        ADX 20-25            → CHOP (no clear structure)
        """
        adx = indicators.get("adx", 0)
        plus_di = indicators.get("plus_di", 0)
        minus_di = indicators.get("minus_di", 0)

        if adx > 25:
            if plus_di > minus_di:
                return Regime.TREND_UP
            else:
                return Regime.TREND_DOWN
        elif adx < 20:
            return Regime.RANGE
        else:
            return Regime.CHOP

    # -- Confluence Scoring --

    @staticmethod
    def _score_confluence(indicators: dict, regime: Regime, signal: MarketSignal, order_flow=None) -> float:
        """
        Score agreement across indicators on a 0-1 scale.

        Each indicator votes bullish (+1), bearish (-1), or neutral (0).
        Confluence = |sum of votes| / number of voters.
        Higher = more agreement = more conviction.
        """
        votes: list[float] = []
        weights: list[float] = []

        # RSI vote (weight 1.5 — strong mean-reversion signal)
        rsi = indicators.get("rsi", 50)
        if rsi < 30:
            votes.append(1.0)   # oversold → bullish
        elif rsi > 70:
            votes.append(-1.0)  # overbought → bearish
        elif rsi < 40:
            votes.append(0.3)
        elif rsi > 60:
            votes.append(-0.3)
        else:
            votes.append(0.0)
        weights.append(1.5)

        # MACD vote (weight 1.0)
        macd_hist = indicators.get("macd_histogram", 0)
        if macd_hist > 0:
            votes.append(1.0)
        elif macd_hist < 0:
            votes.append(-1.0)
        else:
            votes.append(0.0)
        weights.append(1.0)

        # Bollinger %B vote (weight 1.0)
        pct_b = indicators.get("bb_pct_b", 0.5)
        if pct_b < 0.2:
            votes.append(1.0)   # near lower band → bullish
        elif pct_b > 0.8:
            votes.append(-1.0)  # near upper band → bearish
        else:
            votes.append(0.0)
        weights.append(1.0)

        # Volume spike vote (weight 0.8 — confirms directional moves)
        if signal.volume_spike:
            # Volume spike confirms the direction of the price move
            votes.append(1.0 if signal.change_pct_24h > 0 else -1.0)
        else:
            votes.append(0.0)
        weights.append(0.8)

        # ADX trend strength vote (weight 0.7)
        adx = indicators.get("adx", 0)
        if adx > 30:
            votes.append(1.0 if indicators.get("plus_di", 0) > indicators.get("minus_di", 0) else -1.0)
        elif adx > 20:
            votes.append(0.3 if indicators.get("plus_di", 0) > indicators.get("minus_di", 0) else -0.3)
        else:
            votes.append(0.0)
        weights.append(0.7)

        # VWAP vote (weight 0.5 — institutional bias)
        vwap = indicators.get("vwap")
        if vwap is not None:
            if signal.price > vwap * 1.005:
                votes.append(1.0)   # above VWAP → bullish
            elif signal.price < vwap * 0.995:
                votes.append(-1.0)  # below VWAP → bearish
            else:
                votes.append(0.0)
            weights.append(0.5)

        # OBV trend vote (weight 0.6 — volume confirms price trend)
        obv_trend = indicators.get("obv_trend")
        if obv_trend == "rising":
            votes.append(1.0)
        elif obv_trend == "falling":
            votes.append(-1.0)
        else:
            votes.append(0.0)
        if obv_trend is not None:
            weights.append(0.6)

        # Candlestick pattern vote (weight 0.8 — price action signal)
        bull_count = indicators.get("candle_bullish_count", 0)
        bear_count = indicators.get("candle_bearish_count", 0)
        if bull_count > bear_count:
            votes.append(1.0)
            weights.append(0.8)
        elif bear_count > bull_count:
            votes.append(-1.0)
            weights.append(0.8)
        elif bull_count > 0 or bear_count > 0:
            votes.append(0.0)
            weights.append(0.4)

        # Fibonacci zone vote (weight 0.5 — mean-reversion near key levels)
        fib_zone = indicators.get("fib_zone")
        if fib_zone in ("618_786", "below_786"):
            votes.append(1.0)   # deep retracement → bullish bounce potential
            weights.append(0.5)
        elif fib_zone == "500_618":
            votes.append(0.5)   # moderate retracement → mildly bullish
            weights.append(0.5)
        elif fib_zone == "above_236":
            votes.append(-0.3)  # near swing high → mildly bearish
            weights.append(0.5)
        elif fib_zone is not None:
            votes.append(0.0)
            weights.append(0.3)

        # Order flow votes (if available)
        if order_flow is not None:
            from bot.core.order_flow import OrderFlowAnalyzer
            of_votes, of_weights, _ = OrderFlowAnalyzer.to_confluence_votes(order_flow)
            votes += of_votes
            weights += of_weights

        # Weighted confluence
        total_weight = sum(weights)
        if total_weight == 0:
            return 0.5

        weighted_sum = sum(v * w for v, w in zip(votes, weights))
        # Normalize to [0, 1]: -total_weight → 0, +total_weight → 1
        confluence = (weighted_sum / total_weight + 1) / 2
        return round(max(0.0, min(1.0, confluence)), 4)

    # -- LLM Reasoning --

    async def _llm_thesis(self, signal: MarketSignal, indicators: dict, order_flow=None) -> Optional[dict]:
        """Ask the LLM for a directional call with reasoning."""
        if self._llm is None:
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE"
            return result

        prompt = (
            f"You are a crypto trading analyst. Analyze {signal.symbol}.\n"
            f"Price: ${signal.price}, 24h change: {signal.change_pct_24h}%\n"
            f"Volume spike: {signal.volume_spike}\n"
            f"Regime: {indicators.get('regime', 'UNKNOWN')}\n"
            f"Confluence: {indicators.get('confluence', 0):.2f}\n"
            f"Indicators: RSI={indicators.get('rsi')}, MACD={indicators.get('macd')}, "
            f"MACD_hist={indicators.get('macd_histogram')}, "
            f"ADX={indicators.get('adx')}, +DI={indicators.get('plus_di')}, -DI={indicators.get('minus_di')}, "
            f"BB_upper={indicators.get('bb_upper')}, BB_lower={indicators.get('bb_lower')}, "
            f"BB_%B={indicators.get('bb_pct_b')}, "
            f"VWAP={indicators.get('vwap', 'N/A')}, "
            f"OBV_trend={indicators.get('obv_trend', 'N/A')}, "
            f"Fib_zone={indicators.get('fib_zone', 'N/A')}, "
            f"Fib_618={indicators.get('fib_618', 'N/A')}, "
            f"Fib_382={indicators.get('fib_382', 'N/A')}\n"
            f"Candle_patterns={indicators.get('candle_patterns', {})}\n"
        )

        # Append order flow context if available
        if order_flow is not None:
            funding_str = f"{order_flow.funding_rate:.6f}" if order_flow.funding_rate is not None else "N/A"
            prompt += (
                f"Order_flow: book_imbalance={order_flow.book_imbalance:.2f}, "
                f"cvd_trend={order_flow.cvd_trend}, "
                f"whale_bias={order_flow.whale_bias}, "
                f"funding_rate={funding_str}, "
                f"smart_money_score={order_flow.smart_money_score:.2f}\n"
            )

        prompt += (
            "Respond in EXACTLY this format (no markdown):\n"
            "DIRECTION: LONG or SHORT\n"
            "CONFIDENCE: 0.0-1.0\n"
            "REASONING: one paragraph\n"
        )
        try:
            resp = await self._llm.chat.completions.create(
                model=CONFIG.llm.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=CONFIG.llm.temperature,
                max_tokens=CONFIG.llm.max_tokens,
            )
            result = self._parse_llm_response(resp.choices[0].message.content or "")
            if not result.pop("_parsed", False):
                audit(trade_log, "LLM response could not be parsed, using defaults",
                      action="analyze", result="LLM_PARSE_FAIL",
                      data={"raw_text": (resp.choices[0].message.content or "")[:200]})
            result["source"] = "LLM"
            return result
        except Exception as exc:
            audit(trade_log, f"LLM error, falling back to rules: {exc}",
                  action="analyze", result="LLM_FAIL")
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE_FALLBACK"
            return result

    @staticmethod
    def _parse_llm_response(text: str) -> dict:
        """Parse LLM response with robust extraction.
        Returns a dict with direction, confidence, reasoning, and _parsed flag.
        _parsed=False means we fell back to defaults (LLM output was malformed).
        """
        result: dict = {"direction": "LONG", "confidence": 0.0, "reasoning": "", "_parsed": False}
        parsed_fields = 0
        for line in text.strip().splitlines():
            line_clean = line.strip()
            upper = line_clean.upper()
            if upper.startswith("DIRECTION"):
                # Handle "DIRECTION: LONG", "DIRECTION:LONG", "DIRECTION - LONG"
                rest = line_clean.split(":", 1)[-1] if ":" in line_clean else line_clean.split("-", 1)[-1]
                result["direction"] = "SHORT" if "SHORT" in rest.upper() else "LONG"
                parsed_fields += 1
            elif upper.startswith("CONFIDENCE"):
                rest = line_clean.split(":", 1)[-1] if ":" in line_clean else line_clean.split("-", 1)[-1]
                match = re.search(r'(?:CONFIDENCE[:\s]*)?(\d+\.\d+|\d+)', rest, re.IGNORECASE)
                if match:
                    try:
                        parsed = float(match.group(1))
                        result["confidence"] = max(0.0, min(1.0, parsed))
                        parsed_fields += 1
                    except ValueError:
                        pass
            elif upper.startswith("REASONING"):
                rest = line_clean.split(":", 1)[-1] if ":" in line_clean else line_clean
                result["reasoning"] = rest.strip()
                parsed_fields += 1
        result["_parsed"] = parsed_fields >= 2  # at least direction + confidence
        return result

    @staticmethod
    def _rule_based_thesis(signal: MarketSignal, ind: dict) -> dict:
        """
        Deterministic fallback using confluence scoring and regime detection.
        More sophisticated than simple RSI threshold.
        Incorporates candlestick patterns, OBV trend, and Fibonacci zone.
        """
        confluence = ind.get("confluence", 0.5)
        regime = ind.get("regime", "UNKNOWN")
        rsi = ind.get("rsi", 50)
        macd_hist = ind.get("macd_histogram", 0)
        adx = ind.get("adx", 0)
        obv_trend = ind.get("obv_trend", "neutral")
        fib_zone = ind.get("fib_zone", "")
        candle_patterns = ind.get("candle_patterns", {})

        # Direction from confluence (>0.5 = bullish, <0.5 = bearish)
        if confluence > 0.55:
            direction = "LONG"
        elif confluence < 0.45:
            direction = "SHORT"
        elif rsi < 35:
            direction = "LONG"
        elif rsi > 65:
            direction = "SHORT"
        else:
            direction = "LONG"  # default bias

        # Confidence from confluence strength + regime clarity
        conf_base = abs(confluence - 0.5) * 2  # 0-1 scale of confluence strength
        regime_bonus = 0.1 if regime in ("TREND_UP", "TREND_DOWN") else 0
        spike_bonus = 0.1 if signal.volume_spike else 0
        adx_bonus = 0.05 if adx > 25 else 0

        # New indicator bonuses
        obv_bonus = 0.05 if (
            (obv_trend == "rising" and direction == "LONG") or
            (obv_trend == "falling" and direction == "SHORT")
        ) else 0

        # Fib level support
        fib_bonus = 0.0
        if fib_zone in ("618_786", "below_786") and direction == "LONG":
            fib_bonus = 0.08  # deep retracement supports long
        elif fib_zone == "above_236" and direction == "SHORT":
            fib_bonus = 0.05  # near swing high supports short

        # Candlestick pattern bonus
        bull_patterns = sum(1 for v in candle_patterns.values() if v == "bullish")
        bear_patterns = sum(1 for v in candle_patterns.values() if v == "bearish")
        candle_bonus = 0.0
        if direction == "LONG" and bull_patterns > bear_patterns:
            candle_bonus = min(0.10, bull_patterns * 0.05)
        elif direction == "SHORT" and bear_patterns > bull_patterns:
            candle_bonus = min(0.10, bear_patterns * 0.05)

        confidence = min(1.0, conf_base * 0.5 + 0.35 + regime_bonus + spike_bonus + adx_bonus + obv_bonus + fib_bonus + candle_bonus)

        # Build pattern summary
        pattern_str = ", ".join(f"{k}({v})" for k, v in candle_patterns.items()) if candle_patterns else "none"

        reasoning = (
            f"Regime={regime}, RSI={rsi:.1f}, MACD_hist={macd_hist:.4f}, "
            f"ADX={adx:.1f}, confluence={confluence:.2f}, "
            f"vol_spike={signal.volume_spike}, OBV={obv_trend}, "
            f"fib_zone={fib_zone}, patterns=[{pattern_str}]"
        )
        return {"direction": direction, "confidence": round(confidence, 2), "reasoning": reasoning}


# ── Utility functions ─────────────────────────────────────────────

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average over full array."""
    alpha = 2 / (period + 1)
    out = np.empty_like(data, dtype=float)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


def _compute_obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On-Balance Volume: cumulative volume weighted by price direction."""
    obv = np.zeros(len(closes))
    obv[0] = volumes[0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]
    return obv


def _compute_fibonacci(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> dict:
    """
    Fibonacci retracement levels over the last 50 bars (or available data).
    Identifies swing high/low and computes standard retracement levels.
    """
    lookback = min(50, len(highs))
    seg_h = highs[-lookback:]
    seg_l = lows[-lookback:]

    swing_high = float(np.max(seg_h))
    swing_low = float(np.min(seg_l))
    diff = swing_high - swing_low

    if diff <= 0:
        return {"fib_swing_high": swing_high, "fib_swing_low": swing_low}

    # Standard Fibonacci levels (from swing high retracing down)
    fib_levels = {
        "fib_swing_high": round(swing_high, 6),
        "fib_swing_low": round(swing_low, 6),
        "fib_236": round(swing_high - 0.236 * diff, 6),
        "fib_382": round(swing_high - 0.382 * diff, 6),
        "fib_500": round(swing_high - 0.500 * diff, 6),
        "fib_618": round(swing_high - 0.618 * diff, 6),
        "fib_786": round(swing_high - 0.786 * diff, 6),
    }

    # Determine which zone the current price sits in
    price = float(closes[-1])
    if price >= fib_levels["fib_236"]:
        fib_levels["fib_zone"] = "above_236"
    elif price >= fib_levels["fib_382"]:
        fib_levels["fib_zone"] = "236_382"
    elif price >= fib_levels["fib_500"]:
        fib_levels["fib_zone"] = "382_500"
    elif price >= fib_levels["fib_618"]:
        fib_levels["fib_zone"] = "500_618"
    elif price >= fib_levels["fib_786"]:
        fib_levels["fib_zone"] = "618_786"
    else:
        fib_levels["fib_zone"] = "below_786"

    return fib_levels


def _detect_candlestick_patterns(
    opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
) -> dict:
    """
    Detect common candlestick patterns on the last few bars.
    Returns a dict with pattern names and signals (bullish/bearish/neutral).
    """
    patterns: dict = {}
    if len(opens) < 3:
        return patterns

    # Use last 3 bars for multi-bar patterns
    o, h, l, c = opens[-3:], highs[-3:], lows[-3:], closes[-3:]

    body = c - o  # positive = bullish candle
    abs_body = np.abs(body)
    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - l
    candle_range = h - l

    # -- Single-bar patterns (on last candle) --
    last_body = float(abs_body[-1])
    last_range = float(candle_range[-1])
    last_upper = float(upper_wick[-1])
    last_lower = float(lower_wick[-1])

    if last_range > 0:
        body_pct = last_body / last_range

        # Doji: body < 10% of range
        if body_pct < 0.10:
            patterns["doji"] = "neutral"

        # Hammer: small body at top, long lower wick (>= 2x body)
        if last_lower >= 2 * last_body and last_upper < last_body and body_pct < 0.4:
            patterns["hammer"] = "bullish"

        # Shooting Star: small body at bottom, long upper wick (>= 2x body)
        if last_upper >= 2 * last_body and last_lower < last_body and body_pct < 0.4:
            patterns["shooting_star"] = "bearish"

        # Spinning Top: small body, moderate wicks on both sides
        if body_pct < 0.25 and last_upper > 0.25 * last_range and last_lower > 0.25 * last_range:
            patterns["spinning_top"] = "neutral"

        # Marubozu: body is nearly entire range (>90%)
        if body_pct > 0.90:
            patterns["marubozu"] = "bullish" if float(body[-1]) > 0 else "bearish"

    # -- Two-bar patterns (bars -2 and -1) --
    prev_body = float(body[-2])
    curr_body = float(body[-1])
    prev_abs = float(abs_body[-2])
    curr_abs = float(abs_body[-1])

    # Bullish Engulfing: prev bearish, current bullish wraps prev entirely
    if prev_body < 0 and curr_body > 0 and curr_abs > prev_abs:
        if float(c[-1]) > float(o[-2]) and float(o[-1]) < float(c[-2]):
            patterns["bullish_engulfing"] = "bullish"

    # Bearish Engulfing: prev bullish, current bearish wraps prev entirely
    if prev_body > 0 and curr_body < 0 and curr_abs > prev_abs:
        if float(c[-1]) < float(o[-2]) and float(o[-1]) > float(c[-2]):
            patterns["bearish_engulfing"] = "bearish"

    # Bullish Harami: prev large bearish, current small bullish inside
    if prev_body < 0 and curr_body > 0 and curr_abs < prev_abs * 0.5:
        if float(c[-1]) < float(o[-2]) and float(o[-1]) > float(c[-2]):
            patterns["bullish_harami"] = "bullish"

    # Bearish Harami: prev large bullish, current small bearish inside
    if prev_body > 0 and curr_body < 0 and curr_abs < prev_abs * 0.5:
        if float(c[-1]) > float(o[-2]) and float(o[-1]) < float(c[-2]):
            patterns["bearish_harami"] = "bearish"

    # Tweezer Top: two consecutive bars with nearly equal highs
    if abs(float(h[-1]) - float(h[-2])) < 0.001 * float(h[-1]):
        if prev_body > 0 and curr_body < 0:
            patterns["tweezer_top"] = "bearish"

    # Tweezer Bottom: two consecutive bars with nearly equal lows
    if abs(float(l[-1]) - float(l[-2])) < 0.001 * float(l[-1]):
        if prev_body < 0 and curr_body > 0:
            patterns["tweezer_bottom"] = "bullish"

    # -- Three-bar patterns --
    # Morning Star: large bearish, small body (gap down), large bullish
    b0, b1, b2 = float(body[-3]), float(body[-2]), float(body[-1])
    a0, a1, a2 = float(abs_body[-3]), float(abs_body[-2]), float(abs_body[-1])
    if b0 < 0 and a0 > 0 and a1 < a0 * 0.3 and b2 > 0 and a2 > a0 * 0.5:
        patterns["morning_star"] = "bullish"

    # Evening Star: large bullish, small body (gap up), large bearish
    if b0 > 0 and a0 > 0 and a1 < a0 * 0.3 and b2 < 0 and a2 > a0 * 0.5:
        patterns["evening_star"] = "bearish"

    # Three White Soldiers: three consecutive bullish candles with higher closes
    if b0 > 0 and b1 > 0 and b2 > 0:
        if float(c[-2]) > float(c[-3]) and float(c[-1]) > float(c[-2]):
            patterns["three_white_soldiers"] = "bullish"

    # Three Black Crows: three consecutive bearish candles with lower closes
    if b0 < 0 and b1 < 0 and b2 < 0:
        if float(c[-2]) < float(c[-3]) and float(c[-1]) < float(c[-2]):
            patterns["three_black_crows"] = "bearish"

    return patterns


def _compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> dict:
    """
    Average Directional Index with +DI and -DI.
    Returns dict with 'adx', 'plus_di', 'minus_di'.
    """
    if len(highs) < period + 1:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0}

    # True Range
    tr_hl = highs[1:] - lows[1:]
    tr_hc = np.abs(highs[1:] - closes[:-1])
    tr_lc = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(tr_hl, np.maximum(tr_hc, tr_lc))

    # Directional Movement
    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder's smoothing
    atr = np.zeros(len(tr))
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    smoothed_plus = np.zeros(len(plus_dm))
    smoothed_plus[period - 1] = np.mean(plus_dm[:period])
    for i in range(period, len(plus_dm)):
        smoothed_plus[i] = (smoothed_plus[i - 1] * (period - 1) + plus_dm[i]) / period

    smoothed_minus = np.zeros(len(minus_dm))
    smoothed_minus[period - 1] = np.mean(minus_dm[:period])
    for i in range(period, len(minus_dm)):
        smoothed_minus[i] = (smoothed_minus[i - 1] * (period - 1) + minus_dm[i]) / period

    # +DI and -DI (safe division)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = np.where(atr > 0, 100 * smoothed_plus / atr, 0.0)
        minus_di = np.where(atr > 0, 100 * smoothed_minus / atr, 0.0)
        plus_di = np.nan_to_num(plus_di, nan=0.0)
        minus_di = np.nan_to_num(minus_di, nan=0.0)

    # DX and ADX
    di_sum = plus_di + minus_di
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(di_sum > 0, 100 * np.abs(plus_di - minus_di) / di_sum, 0.0)
        dx = np.nan_to_num(dx, nan=0.0)

    if len(dx) >= period * 2:
        adx = np.zeros(len(dx))
        adx[period * 2 - 1] = np.mean(dx[period:period * 2])
        for i in range(period * 2, len(dx)):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
        adx_val = float(adx[-1])
    else:
        adx_val = float(np.mean(dx[-period:])) if len(dx) >= period else 0.0

    return {
        "adx": round(adx_val, 2),
        "plus_di": round(float(plus_di[-1]), 2),
        "minus_di": round(float(minus_di[-1]), 2),
    }
