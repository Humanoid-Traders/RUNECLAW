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

import asyncio
import re
import uuid
from datetime import datetime
from bot.compat import UTC
from typing import Optional

# AG-H1: Symbol validation regex — only uppercase alphanumeric, optional /pair
_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$")


def _sanitize_symbol(symbol: str) -> str:
    """Validate and sanitize a trading symbol before use in LLM prompts.

    Raises ValueError if the symbol doesn't match the expected format.
    """
    s = symbol.strip().upper()
    if not _VALID_SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol format: {symbol!r}")
    return s

import numpy as np
from openai import AsyncOpenAI

from bot.config import CONFIG
from bot.core.llm_cache import SemanticLLMCache
from bot.core.ta_utils import Regime, _ema, _compute_adx  # shared TA utils
from bot.core.token_optimizer import (
    AdaptiveFrequency,
    OptimizationStats,
    TieredPipeline,
)
from bot.core.explainability import ExplainabilityEngine
from bot.core.multi_timeframe import MTFConfluence
from bot.core.sentiment import SentimentEngine
from bot.core.smart_money import SmartMoneyEngine
from bot.core.strategy_modes import StrategySelector, MODE_CONFIGS
from bot.llm.provider import BYOK, LLMProvider, LLMTier, PROVIDER_CATALOG, create_llm_client, llm_complete, LLMConfig, resolve_tier_config
from bot.core.volume_profile import compute_volume_profile, poc_magnet_signal
from bot.core.order_flow import OrderFlowAnalyzer
from bot.utils.logger import audit, system_log, trade_log, scan_log
from bot.utils.models import Direction, MarketSignal, TradeIdea


# Re-export for backward compatibility (tests import from here)
__all__ = ["Analyzer", "Regime", "_ema", "_compute_adx", "_sanitize_symbol"]


class Analyzer:
    """Produces TradeIdea objects from raw market signals.

    LLM optimization:
      - Multi-tier routing: different providers for scan vs thesis vs learning
        e.g. Groq for speed-critical scans, Gemini 3.1 Pro for thesis reasoning
      - Prompt compression: strips redundant whitespace, enforces hard cap
      - Structured JSON output mode where possible (fewer tokens, reliable parsing)
      - Per-category cost tracking via CostTracker
      - Async rate limiting to stay within provider RPM limits
    """

    # Model routing: use configured model, or fall back to defaults
    # When using non-OpenAI providers (Groq, Qwen, etc.), both tiers use the same model
    SCAN_MODEL = "gpt-4o-mini"     # overridden by CONFIG.llm.model if set
    THESIS_MODEL = "gpt-4o"        # overridden by CONFIG.llm.model if set

    def __init__(self, cost_tracker: Optional["CostTracker"] = None) -> None:  # noqa: F821
        # Build LLM client — supports 10 providers via BYOK system
        # Resolve provider config (runtime BYOK overrides .env)
        self._llm_config = self._resolve_llm_config()
        self._llm = self._build_llm_client()

        # Multi-tier routing: resolve separate configs for scan vs thesis
        self._scan_config = resolve_tier_config(LLMTier.SCAN, self._llm_config) if self._llm_config else None
        self._thesis_config = resolve_tier_config(LLMTier.THESIS, self._llm_config) if self._llm_config else None
        self._scan_client = self._build_client_for_config(self._scan_config)
        self._thesis_client = self._build_client_for_config(self._thesis_config)

        # When a non-OpenAI provider is used, use the configured model
        # for both tiers instead of OpenAI-specific model names
        resolved_model = self._llm_config.model if self._llm_config else ""
        if resolved_model and self._llm_config and self._llm_config.provider != "openai":
            self.SCAN_MODEL = resolved_model
            self.THESIS_MODEL = resolved_model
        elif CONFIG.llm.base_url and CONFIG.llm.model:
            self.SCAN_MODEL = CONFIG.llm.model
            self.THESIS_MODEL = CONFIG.llm.model

        # Override model names from tier configs if they have different providers
        if self._scan_config and self._scan_config != self._llm_config:
            self.SCAN_MODEL = self._scan_config.model
        if self._thesis_config and self._thesis_config != self._llm_config:
            self.THESIS_MODEL = self._thesis_config.model

        self._llm_calls_today: int = 0
        self._llm_day: str = ""  # YYYY-MM-DD, reset counter on new day
        self._cost = cost_tracker
        # Async rate limiter: prevent 429s without blocking the event loop
        from bot.utils.rate_limiter import AsyncRateLimiter
        self._rate_limiter = AsyncRateLimiter(
            max_rpm=int(CONFIG.llm.daily_call_limit / 24 * 60) or 40,
            name="llm",
        )
        # Token optimization: semantic cache + stats
        self._llm_cache = SemanticLLMCache(
            max_size=CONFIG.cache.max_size,
            default_ttl=CONFIG.cache.ttl_seconds,
        )
        self._opt_stats = OptimizationStats()
        # Advanced modules
        self._mtf = MTFConfluence()
        self._smart_money = SmartMoneyEngine()
        self._sentiment = SentimentEngine()
        self._strategy_selector = StrategySelector()
        self._explainability = ExplainabilityEngine()

    def _resolve_llm_config(self) -> Optional[LLMConfig]:
        """Build LLMConfig from BYOK runtime or .env config."""
        # Check BYOK runtime override first
        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
            temperature=CONFIG.llm.temperature,
            max_tokens=CONFIG.llm.max_tokens,
            timeout_seconds=CONFIG.llm.timeout_seconds,
        )
        return BYOK.get_active_config(env_config)

    def _build_llm_client(self):
        """Create LLM client from resolved config."""
        return self._build_client_for_config(self._resolve_llm_config())

    @staticmethod
    def _build_client_for_config(cfg):
        """Create LLM client from a specific config."""
        if cfg is None or not cfg.is_configured():
            return None
        try:
            return create_llm_client(cfg)
        except ImportError as e:
            audit(trade_log, f"LLM SDK import error: {e}", action="llm_init", result="FAIL")
            return None

    def refresh_llm_client(self) -> None:
        """Refresh LLM client after BYOK /setllm change."""
        self._llm_config = self._resolve_llm_config()
        self._llm = self._build_llm_client()
        # Refresh tier-specific clients
        self._scan_config = resolve_tier_config(LLMTier.SCAN, self._llm_config) if self._llm_config else None
        self._thesis_config = resolve_tier_config(LLMTier.THESIS, self._llm_config) if self._llm_config else None
        self._scan_client = self._build_client_for_config(self._scan_config)
        self._thesis_client = self._build_client_for_config(self._thesis_config)
        # Update model routing for non-OpenAI providers
        if self._llm_config and self._llm_config.model:
            provider = self._llm_config.provider
            if isinstance(provider, LLMProvider):
                provider_str = provider.value
            else:
                provider_str = str(provider)
            if provider_str != "openai":
                self.SCAN_MODEL = self._llm_config.model
                self.THESIS_MODEL = self._llm_config.model
        # Override from tier configs
        if self._scan_config and self._scan_config != self._llm_config:
            self.SCAN_MODEL = self._scan_config.model
        if self._thesis_config and self._thesis_config != self._llm_config:
            self.THESIS_MODEL = self._thesis_config.model

    async def analyze(self, signal: MarketSignal, candles: list[list[float]], order_flow=None,
                       candles_4h=None, candles_1d=None) -> Optional[TradeIdea]:
        """
        Full analysis pipeline:
        1. Compute technical indicators from OHLCV candles.
        2. Detect market regime via ADX.
        3. Run multi-timeframe analysis (if HTF candles available).
        4. Run smart money analysis (if order flow available).
        5. Select strategy mode based on regime + context.
        6. Score confluence across all indicator voters.
        7. Ask LLM for a directional thesis (or rule-based fallback).
        8. Structure the result as a TradeIdea with explainability report.
        Returns None if conviction is too low (<0.5).
        """
        if len(candles) < CONFIG.analyzer.min_candles:
            audit(trade_log, "Not enough candle data", action="analyze",
                  result="SKIP", data={"symbol": signal.symbol})
            return None

        # Validate candle data integrity before processing
        try:
            for i, c in enumerate(candles):
                if len(c) < 5:
                    raise ValueError(f"Candle {i} has {len(c)} fields (need >=5)")
            opens = np.array([c[1] for c in candles], dtype=float)
            highs = np.array([c[2] for c in candles], dtype=float)
            lows = np.array([c[3] for c in candles], dtype=float)
            closes = np.array([c[4] for c in candles], dtype=float)
            volumes = np.array([c[5] for c in candles], dtype=float) if len(candles[0]) > 5 else None
            # Reject NaN/Inf in OHLCV data
            for name, arr in [("opens", opens), ("highs", highs), ("lows", lows), ("closes", closes)]:
                if not np.all(np.isfinite(arr)):
                    raise ValueError(f"Non-finite values in {name}")
                if np.any(arr <= 0):
                    raise ValueError(f"Non-positive values in {name}")
        except (ValueError, IndexError, TypeError) as exc:
            audit(trade_log, f"Invalid candle data: {exc}", action="analyze",
                  result="SKIP", data={"symbol": signal.symbol, "error": str(exc)})
            return None

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

        # ── Multi-Timeframe Analysis ──
        mtf_result = None
        if candles_4h or candles_1d:
            mtf_result = self._mtf.analyze(
                candles_1h=candles,  # primary timeframe as 1H
                candles_4h=candles_4h,
                candles_1d=candles_1d,
            )

        # ── Smart Money Analysis ──
        smart_money_score = None
        if order_flow is not None:
            smart_money_score = self._smart_money.analyze(order_flow)

        # ── Sentiment Analysis ──
        try:
            self._sentiment.update(
                symbol=signal.symbol,
                price=signal.price,
                volume=signal.volume_usd_24h or 0,
                price_change_pct=signal.change_pct_24h or 0,
            )
        except Exception as e:
            system_log.debug("Sentiment update error: %s", e)

        # ── Strategy Mode Selection ──
        mode_selection = self._strategy_selector.select(
            regime=regime,
            indicators=indicators,
            mtf_result=mtf_result,
            smart_money=smart_money_score,
        )
        strategy_mode = mode_selection.selected_mode
        mode_config = mode_selection.config

        confluence = self._score_confluence(
            indicators, regime, signal,
            order_flow=order_flow,
            mtf_result=mtf_result,
            smart_money_score=smart_money_score,
            mode_config=mode_config,
            sentiment_engine=self._sentiment,
        )

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
        # Counter-trend trades are dangerous -- apply heavy penalty.
        # RANGE/CHOP: allow with confidence penalty instead of auto-skip.
        regime_confidence_penalty = 0.0
        regime_sl_override = None
        regime_tp_override = None
        counter_trend_penalty = 1.0

        if regime == Regime.TREND_UP and direction == Direction.SHORT:
            audit(trade_log, "Regime filter: TREND_UP but SHORT signal -- heavy penalty",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol, "regime": regime.value})
            counter_trend_penalty = 0.5  # Heavy penalty for counter-trend
        if regime == Regime.TREND_DOWN and direction == Direction.LONG:
            audit(trade_log, "Regime filter: TREND_DOWN but LONG signal -- heavy penalty",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol, "regime": regime.value})
            counter_trend_penalty = 0.5  # Heavy penalty for counter-trend
        if regime == Regime.RANGE:
            # RANGE: needs high raw confluence (0.70+) to survive after penalty
            regime_confidence_penalty = CONFIG.analyzer.range_confidence_penalty
            regime_sl_override = CONFIG.analyzer.range_sl_mult
            regime_tp_override = CONFIG.analyzer.range_tp_mult
            audit(trade_log, "Regime: RANGE -- applying penalty",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol, "regime": regime.value,
                        "penalty": regime_confidence_penalty})
        elif regime == Regime.CHOP:
            # CHOP: needs very high raw confluence (0.75+) to survive after penalty
            regime_confidence_penalty = CONFIG.analyzer.chop_confidence_penalty
            regime_sl_override = CONFIG.analyzer.chop_sl_mult
            regime_tp_override = CONFIG.analyzer.chop_tp_mult
            audit(trade_log, "Regime: CHOP -- applying penalty",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol, "regime": regime.value,
                        "penalty": regime_confidence_penalty})

        confidence = max(0.0, min(1.0, thesis.get("confidence", 0.0))) * counter_trend_penalty

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

        # SIGNAL QUALITY: threshold at min_confidence (matches config)
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
        # Strategy mode provides baseline SL/TP; volatility/regime can override
        # Compute normalized volatility: ATR as a percentage of price
        vol_ratio = atr / entry if entry > 0 else 0.02

        # Start with strategy mode defaults
        sl_mult = mode_config.sl_mult
        tp_mult = mode_config.tp_mult

        # REGIME-SPECIFIC SL/TP: volatility overrides take priority
        if vol_ratio > CONFIG.analyzer.high_vol_threshold:
            # High volatility: widen stops to avoid noise-induced exits
            sl_mult = CONFIG.analyzer.high_vol_sl_mult
            tp_mult = CONFIG.analyzer.high_vol_tp_mult
        elif vol_ratio < CONFIG.analyzer.low_vol_threshold:
            # Low volatility: tighten stops to lock in smaller moves
            sl_mult = CONFIG.analyzer.low_vol_sl_mult
            tp_mult = CONFIG.analyzer.low_vol_tp_mult
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

        # ── Strategy mode + MTF + smart money context for reasoning ──
        mode_tag = strategy_mode.value
        mtf_tag = ""
        if mtf_result and mtf_result.narrative:
            mtf_tag = f" MTF:{mtf_result.htf_trend}"
        sm_tag = ""
        if smart_money_score and abs(smart_money_score.composite_score) > 0.1:
            sm_tag = f" SM:{smart_money_score.composite_score:+.2f}"

        # Adaptive rounding: more decimal places for low-priced assets
        price_decimals = 6
        if entry < 1.0:
            price_decimals = 8
        elif entry < 100.0:
            price_decimals = 6

        idea = TradeIdea(
            id=f"TI-{uuid.uuid4().hex[:8]}",
            asset=signal.symbol,
            direction=direction,
            entry_price=round(entry, price_decimals),
            stop_loss=round(stop_loss, price_decimals),
            take_profit=round(take_profit, price_decimals),
            confidence=blended_confidence,
            reasoning=(
                f"[{source}|{regime.value}|{mode_tag}|C={confluence:.2f}"
                f"{mtf_tag}{sm_tag}] {thesis.get('reasoning', '')}"
            ),
            signals_used=list(indicators.keys()),
            timestamp=datetime.now(UTC),
        )

        # ── Explainability Report ──
        try:
            explain_report = self._explainability.explain(
                trade_id=idea.id,
                symbol=signal.symbol,
                direction=direction.value,
                indicators=indicators,
                regime=regime.value,
                confluence=confluence,
                confidence=blended_confidence,
                strategy_mode=mode_tag,
                mtf_narrative=mtf_result.narrative if mtf_result else "",
                smart_money_narrative=smart_money_score.narrative if smart_money_score else "",
            )
            audit(trade_log, f"Explainability: {explain_report.summary}",
                  action="explain", result="OK",
                  data={"compliance": explain_report.compliance.overall,
                        "top_bullish": explain_report.top_bullish,
                        "top_bearish": explain_report.top_bearish})
        except Exception:
            pass  # explainability is non-critical

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
            std20 = np.std(closes[-20:], ddof=0)
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
                # Wilder's ATR
                period = 14
                atr_vals = np.zeros(len(true_range))
                atr_vals[period-1] = np.mean(true_range[:period])
                for i in range(period, len(true_range)):
                    atr_vals[i] = (atr_vals[i-1] * (period - 1) + true_range[i]) / period
                atr = float(atr_vals[-1])
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

        # ── Volume Profile (POC + Value Area) ──
        if volumes is not None and len(volumes) >= 10:
            vp = compute_volume_profile(closes, highs, lows, volumes)
            if vp is not None:
                results["poc_price"] = vp.poc_price
                results["value_area_high"] = vp.value_area_high
                results["value_area_low"] = vp.value_area_low
                results["price_vs_poc"] = vp.price_vs_poc
                results["poc_distance_pct"] = vp.poc_distance_pct

        # ── Volume Oscillator (5/20 EMA ratio) ──
        if volumes is not None and len(volumes) >= 20:
            vol_ema5 = float(_ema(volumes, 5)[-1])
            vol_ema20 = float(_ema(volumes, 20)[-1])
            results["vol_oscillator"] = round(
                (vol_ema5 - vol_ema20) / vol_ema20 * 100 if vol_ema20 > 0 else 0, 2
            )
            results["vol_momentum"] = "expanding" if results["vol_oscillator"] > 10 else (
                "contracting" if results["vol_oscillator"] < -10 else "neutral"
            )

        # ── Taker Volume proxy (up-volume vs down-volume) ──
        if volumes is not None and len(volumes) > 1:
            price_changes = np.diff(closes)
            up_vol = np.sum(volumes[1:][price_changes > 0])
            down_vol = np.sum(volumes[1:][price_changes < 0])
            total_vol = up_vol + down_vol
            results["taker_buy_ratio"] = round(float(up_vol / total_vol) if total_vol > 0 else 0.5, 4)
            results["taker_sell_ratio"] = round(float(down_vol / total_vol) if total_vol > 0 else 0.5, 4)
            results["taker_imbalance"] = round(float((up_vol - down_vol) / total_vol) if total_vol > 0 else 0, 4)

        # ── Keltner Channels (EMA-20 ± 2×ATR) ──
        if len(closes) >= 20 and "atr" in results:
            kc_mid = float(_ema(closes, 20)[-1])
            kc_atr = results["atr"]
            results["kc_upper"] = round(kc_mid + 2 * kc_atr, 6)
            results["kc_lower"] = round(kc_mid - 2 * kc_atr, 6)
            results["kc_mid"] = round(kc_mid, 6)
            # Squeeze: Bollinger inside Keltner = low volatility compression
            if "bb_upper" in results and "bb_lower" in results:
                results["kc_squeeze"] = (results["bb_upper"] < results["kc_upper"] and
                                          results["bb_lower"] > results["kc_lower"])

        # ── EMA Ribbon (9/21) — trend filter ──
        if len(closes) >= 21:
            ema9 = float(_ema(closes, 9)[-1])
            ema21 = float(_ema(closes, 21)[-1])
            results["ema_9"] = round(ema9, 6)
            results["ema_21"] = round(ema21, 6)
            results["ema_ribbon_spread"] = round((ema9 - ema21) / ema21 * 100, 4) if ema21 > 0 else 0
            results["ema_ribbon_trend"] = "bullish" if ema9 > ema21 else "bearish"

        # ── VWAP Bands (±1σ, ±2σ) — intraday statistical extremes ──
        if volumes is not None and len(volumes) >= 20 and "vwap" in results:
            typical_price = (highs + lows + closes) / 3
            cum_tp_vol = np.cumsum(typical_price * volumes)
            cum_vol = np.cumsum(volumes)
            vwap_series = cum_tp_vol / np.maximum(cum_vol, 1e-10)
            # Rolling variance of price around VWAP
            vwap_dev = np.sqrt(np.mean((typical_price[-20:] - vwap_series[-20:]) ** 2))
            results["vwap_upper_1"] = round(float(results["vwap"] + vwap_dev), 6)
            results["vwap_lower_1"] = round(float(results["vwap"] - vwap_dev), 6)
            results["vwap_upper_2"] = round(float(results["vwap"] + 2 * vwap_dev), 6)
            results["vwap_lower_2"] = round(float(results["vwap"] - 2 * vwap_dev), 6)

        # ── Session Range (last 24 bars as session proxy) ──
        session_len = min(24, len(closes))
        results["session_high"] = round(float(np.max(highs[-session_len:])), 6)
        results["session_low"] = round(float(np.min(lows[-session_len:])), 6)
        results["session_range_pct"] = round(
            (results["session_high"] - results["session_low"]) / results["session_low"] * 100
            if results["session_low"] > 0 else 0, 4
        )
        results["session_position"] = round(
            (closes[-1] - results["session_low"]) /
            (results["session_high"] - results["session_low"])
            if results["session_high"] > results["session_low"] else 0.5, 4
        )

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
    def _score_confluence(indicators: dict, regime: Regime, signal: MarketSignal,
                          order_flow=None, mtf_result=None, smart_money_score=None,
                          mode_config=None, sentiment_engine=None) -> float:
        """
        Score agreement across indicators on a 0-1 scale.

        Each indicator votes bullish (+1), bearish (-1), or neutral (0).
        Confluence = |sum of votes| / number of voters.
        Higher = more agreement = more conviction.

        Integrates: technical indicators, order flow, MTF alignment,
        smart money signals, with strategy-mode-specific boosts.
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
        # Guard: only vote when obv_trend is present to keep votes/weights aligned
        obv_trend = indicators.get("obv_trend")
        if obv_trend is not None:
            if obv_trend == "rising":
                votes.append(1.0)
            elif obv_trend == "falling":
                votes.append(-1.0)
            else:
                votes.append(0.0)
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
            of_votes, of_weights, of_labels = OrderFlowAnalyzer.to_confluence_votes(order_flow)
            votes += of_votes
            weights += of_weights

        # Multi-timeframe votes (if available)
        if mtf_result is not None:
            mtf_votes, mtf_weights, mtf_labels = MTFConfluence.to_confluence_votes(mtf_result)
            votes += mtf_votes
            weights += mtf_weights

        # Smart money votes (if available)
        if smart_money_score is not None:
            sm_votes, sm_weights, sm_labels = SmartMoneyEngine.to_confluence_votes(smart_money_score)
            votes += sm_votes
            weights += sm_weights

        # Sentiment voter
        if sentiment_engine is not None:
            try:
                sentiment_votes = sentiment_engine.to_confluence_votes()
                for _name, vote_val, vote_weight in sentiment_votes:
                    votes.append(vote_val)
                    weights.append(vote_weight)
            except Exception:
                pass

        # Volume Profile POC-magnet voter
        poc_price = indicators.get("poc_price", 0)
        atr = indicators.get("atr", 0)
        if poc_price > 0 and atr > 0:
            price = signal.price
            magnet = poc_magnet_signal(price, poc_price, atr)
            if magnet and magnet.get("direction"):
                poc_vote = 0.5 if magnet["direction"] == "pull_up" else -0.5
                poc_vote *= magnet.get("strength", 0.5)
                votes.append(poc_vote)
                weights.append(0.6)

        # EMA ribbon voter
        ema9 = indicators.get("ema_9")
        ema21 = indicators.get("ema_21")
        if ema9 is not None and ema21 is not None:
            if ema9 > ema21:
                votes.append(0.6)
                weights.append(0.5)
            elif ema9 < ema21:
                votes.append(-0.6)
                weights.append(0.5)

        # Keltner squeeze voter (volatility compression = breakout imminent)
        squeeze = indicators.get("kc_squeeze", False)
        if squeeze:
            # Squeeze detected — direction from MACD histogram
            macd_hist_val = indicators.get("macd_histogram", 0)
            if macd_hist_val > 0:
                votes.append(0.5)
                weights.append(0.7)
            elif macd_hist_val < 0:
                votes.append(-0.5)
                weights.append(0.7)

        # Taker buy/sell imbalance voter
        taker_buy_ratio = indicators.get("taker_buy_ratio", 0.5)
        if taker_buy_ratio > 0.55:
            votes.append(0.5)
            weights.append(0.5)
        elif taker_buy_ratio < 0.45:
            votes.append(-0.5)
            weights.append(0.5)

        # Strategy mode boosts: amplify weights for mode-relevant factors
        if mode_config is not None and mode_config.confluence_boost:
            # Build label list for all votes (we need to track labels for boost)
            # Note: boost is applied by increasing weight on matching labels
            # This is a soft influence, not a hard override
            pass  # boosts applied via mode_config during strategy selection

        # LB-2 FIX: assert votes/weights are aligned before computation.
        # zip() silently truncates to the shorter list, hiding mismatches.
        assert len(votes) == len(weights), (
            f"Confluence votes/weights desync: {len(votes)} votes vs {len(weights)} weights"
        )

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
        """Ask the LLM for a directional call with reasoning.

        Token optimization pipeline:
          1. Semantic cache check -- return cached response if available
          2. Adaptive frequency -- skip LLM for quiet markets
          3. Tiered pipeline -- route to rules/mini/full based on signal quality
          4. Budget guards -- fall back to rules if limits exceeded
          5. LLM call with rate limiting
          6. Cache the response for future use
        """
        if self._llm is None:
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE"
            return result

        # ── Optimization 1: Semantic Cache ──
        cache_key = SemanticLLMCache.build_cache_key(signal.symbol, indicators)
        cached = self._llm_cache.get(cache_key)
        if cached is not None:
            # Stats tracked by cache internally -- no double-count
            cached_copy = dict(cached)
            cached_copy["source"] = cached_copy.get("source", "LLM") + "_CACHED"
            return cached_copy
        # Stats tracked by cache internally -- no double-count

        # ── Optimization 2: Adaptive Frequency ──
        if not AdaptiveFrequency.should_use_llm(signal, indicators):
            self._opt_stats.record_adaptive_skip()
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE_ADAPTIVE"
            return result

        # ── Optimization 3: Tiered Pipeline ──
        tier = TieredPipeline.classify_tier(indicators, signal)
        self._opt_stats.record_tier(tier)

        if tier == 1:
            # Tier 1: Rule engine handles clear-cut signals (FREE)
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE_TIER1"
            # Cache the rule result too (saves re-computation)
            self._llm_cache.put(cache_key, result, signal.symbol)
            return result

        # Budget guard: fall back to rules when daily limit exceeded (fix J)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._llm_day:
            self._llm_day = today
            self._llm_calls_today = 0
        if self._llm_calls_today >= CONFIG.llm.daily_call_limit:
            audit(trade_log, f"LLM daily budget exhausted ({self._llm_calls_today} calls), using rules",
                  action="analyze", result="LLM_BUDGET")
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE_BUDGET"
            return result

        # Dollar budget guard: fall back to rules when daily spend exceeded
        if self._cost is not None:
            snap = self._cost.snapshot()
            if snap.llm_cost_usd >= CONFIG.llm.daily_budget_usd:
                audit(trade_log, f"LLM daily dollar budget exhausted (${snap.llm_cost_usd:.4f} >= ${CONFIG.llm.daily_budget_usd}), using rules",
                      action="analyze", result="LLM_BUDGET_USD")
                result = self._rule_based_thesis(signal, indicators)
                result["source"] = "RULE_ENGINE_BUDGET"
                return result

        prompt = self._build_prompt(signal, indicators, order_flow)

        # Tier-based model routing:
        #   Tier 2 → scan model (cheap/fast — e.g. Groq)
        #   Tier 3 → thesis model (strong reasoning — e.g. Gemini 3.1 Pro)
        use_full_model = tier == 3
        # Multi-tier routing: use tier-specific client/config if available
        if use_full_model and self._thesis_client is not None:
            active_client = self._thesis_client
            active_cfg = self._thesis_config
            model = self._thesis_config.model
        elif not use_full_model and self._scan_client is not None:
            active_client = self._scan_client
            active_cfg = self._scan_config
            model = self._scan_config.model
        else:
            active_client = self._llm
            active_cfg = self._resolve_llm_config()
            model = self.THESIS_MODEL if use_full_model else self.SCAN_MODEL
        category = "thesis" if use_full_model else "analyze"
        max_tokens = CONFIG.llm.max_tokens if use_full_model else 512
        tier_label = TieredPipeline.tier_label(tier)

        try:
            # Rate-limit before calling to prevent 429s
            await self._rate_limiter.acquire()

            sdk_type = active_cfg.sdk_type() if active_cfg else "openai"

            # System prompt must mention "json" when using json_object response_format
            # (required by Groq and some other providers)
            use_json_format = not use_full_model and sdk_type != "anthropic"
            sys_content = (
                "You are RUNECLAW, a risk-first crypto analyst. "
                "Return concise analysis in json format with keys: direction, confidence, reasoning."
                if use_json_format else
                "You are RUNECLAW, a risk-first crypto analyst. Return concise analysis."
            )

            if sdk_type == "anthropic":
                # Use unified llm_complete for Anthropic (different API format)
                raw_text = await llm_complete(
                    active_client, active_cfg, sys_content, prompt)
                self._llm_calls_today += 1
                # Anthropic doesn't return usage in the same format — skip cost tracking
                result = self._parse_llm_response(raw_text or "")
            else:
                # OpenAI-compatible path (OpenAI, Groq, Gemini, DeepSeek, etc.)
                resp = await asyncio.wait_for(
                    active_client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": sys_content},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=CONFIG.llm.temperature,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"} if use_json_format else None,
                    ),
                    timeout=CONFIG.llm.timeout_seconds,
                )
                self._llm_calls_today += 1
                # Record actual token usage for cost accounting
                usage = getattr(resp, "usage", None)
                if usage is not None and self._cost is not None:
                    self._cost.record_llm(
                        model=model,
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        symbol=signal.symbol,
                        category=category,
                    )
                raw_text = resp.choices[0].message.content or ""
                result = self._parse_llm_response(raw_text)
            if not result.pop("_parsed", False):
                audit(trade_log, "LLM response could not be parsed, using defaults",
                      action="analyze", result="LLM_PARSE_FAIL",
                      data={"raw_text": raw_text[:200]})
            result["source"] = f"LLM_{tier_label}"
            result["model_used"] = model

            # ── Cache the LLM response ──
            self._llm_cache.put(cache_key, result, signal.symbol)

            return result
        except Exception as exc:
            audit(trade_log, f"LLM error on primary provider, trying fallback: {exc}",
                  action="analyze", result="LLM_FAIL")
            # ── Cascading fallback: try alternate providers before rules ──
            fallback_result = await self._try_llm_fallback(prompt, signal, use_full_model)
            if fallback_result is not None:
                fallback_result["source"] = f"LLM_FALLBACK_{fallback_result.get('_fallback_provider', 'UNKNOWN')}"
                fallback_result.pop("_fallback_provider", None)
                self._llm_cache.put(cache_key, fallback_result, signal.symbol)
                return fallback_result
            result = self._rule_based_thesis(signal, indicators)
            result["source"] = "RULE_ENGINE_FALLBACK"
            return result

    async def _try_llm_fallback(
        self,
        prompt: str,
        signal: MarketSignal,
        use_full_model: bool,
    ) -> Optional[dict]:
        """Try alternate LLM providers when the primary fails (rate limit, error).

        Cascading order:
          1. Gemini (free tier, high quota)
          2. Groq (free tier, fast)
          3. Anthropic (paid, high quality)
          4. DeepSeek (cheap, good quality)

        Skips the provider that just failed (the current primary).
        Returns parsed result dict or None if all fallbacks fail.
        """
        import os as _os

        primary_provider = None
        if self._llm_config:
            primary_provider = (
                self._llm_config.provider.value
                if isinstance(self._llm_config.provider, LLMProvider)
                else str(self._llm_config.provider)
            )

        # Build fallback chain — skip the primary that just failed
        fallback_chain = [
            (LLMProvider.GEMINI, "GEMINI_API_KEY", "gemini-2.5-flash"),
            (LLMProvider.GROQ, "GROQ_API_KEY", "llama-3.3-70b-versatile"),
            (LLMProvider.ANTHROPIC, "ANTHROPIC_API_KEY", "claude-sonnet-4-6"),
            (LLMProvider.DEEPSEEK, "DEEPSEEK_API_KEY", "deepseek-chat"),
        ]

        for provider, key_env, default_model in fallback_chain:
            if provider.value == primary_provider:
                continue  # Skip the one that just failed

            api_key = _os.getenv(key_env, "")
            if not api_key:
                continue  # No key configured for this provider

            try:
                catalog = PROVIDER_CATALOG.get(provider, {})
                fb_config = LLMConfig(
                    provider=provider,
                    api_key=api_key,
                    model=default_model,
                    base_url=catalog.get("base_url", ""),
                )
                fb_client = create_llm_client(fb_config)
                if fb_client is None:
                    continue

                sdk_type = fb_config.sdk_type()
                sys_content = (
                    "You are RUNECLAW, a risk-first crypto analyst. "
                    "Return concise analysis in json format with keys: direction, confidence, reasoning."
                )

                if sdk_type == "anthropic":
                    raw_text = await llm_complete(fb_client, fb_config, sys_content, prompt)
                else:
                    resp = await asyncio.wait_for(
                        fb_client.chat.completions.create(
                            model=default_model,
                            messages=[
                                {"role": "system", "content": sys_content},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=CONFIG.llm.temperature,
                            max_tokens=CONFIG.llm.max_tokens if use_full_model else 512,
                        ),
                        timeout=CONFIG.llm.timeout_seconds + 5,  # extra grace for fallback
                    )
                    raw_text = resp.choices[0].message.content or ""

                result = self._parse_llm_response(raw_text or "")
                result["_fallback_provider"] = provider.value.upper()
                result["model_used"] = default_model
                audit(scan_log,
                      f"LLM fallback succeeded via {provider.value}: {signal.symbol}",
                      action="llm_fallback", result="OK",
                      data={"provider": provider.value, "model": default_model})
                return result

            except Exception as fb_exc:
                audit(trade_log,
                      f"LLM fallback {provider.value} also failed: {fb_exc}",
                      action="llm_fallback", result="FAIL",
                      data={"provider": provider.value})
                continue

        # All fallbacks exhausted
        audit(trade_log, "All LLM fallback providers exhausted, using rule engine",
              action="llm_fallback", result="ALL_EXHAUSTED")
        return None

    @property
    def optimization_stats(self) -> dict:
        """Combined optimization stats: cache + tiers + adaptive + batching."""
        cache_snap = self._llm_cache.snapshot()
        opt_snap = self._opt_stats.snapshot()
        opt_snap["cache"] = cache_snap
        # Merge cost savings
        total_saved = (
            opt_snap["savings"]["estimated_cost_saved_usd"]
            + cache_snap["estimated_cost_saved_usd"]
        )
        opt_snap["savings"]["total_estimated_cost_saved_usd"] = round(total_saved, 4)
        total_tokens = (
            opt_snap["savings"]["estimated_tokens_saved"]
            + cache_snap["estimated_tokens_saved"]
        )
        opt_snap["savings"]["total_estimated_tokens_saved"] = total_tokens
        return opt_snap

    @staticmethod
    def _build_prompt(signal: MarketSignal, indicators: dict, order_flow=None) -> str:
        """Build a compressed prompt for LLM analysis.

        Token optimization:
          - Single-line KV format instead of verbose prose
          - Strip redundant whitespace
          - Hard cap at 4000 chars (~1000 tokens) to prevent prompt bloat
          - Order flow appended only when available

        AG-H1: Symbol is validated before interpolation into the prompt.
        """
        # Sanitize symbol to prevent prompt injection via symbol strings
        safe_symbol = _sanitize_symbol(signal.symbol)

        parts = [
            f"Analyze {safe_symbol}.",
            f"Price=${signal.price} 24h={signal.change_pct_24h}% vol_spike={signal.volume_spike}",
            f"Regime={indicators.get('regime', 'UNKNOWN')} Confluence={indicators.get('confluence', 0):.2f}",
            f"RSI={indicators.get('rsi')} MACD={indicators.get('macd')} MACD_hist={indicators.get('macd_histogram')}",
            f"ADX={indicators.get('adx')} +DI={indicators.get('plus_di')} -DI={indicators.get('minus_di')}",
            f"BB_upper={indicators.get('bb_upper')} BB_lower={indicators.get('bb_lower')} BB_%B={indicators.get('bb_pct_b')}",
            f"VWAP={indicators.get('vwap', 'N/A')} OBV={indicators.get('obv_trend', 'N/A')}",
            f"Fib: zone={indicators.get('fib_zone', 'N/A')} 618={indicators.get('fib_618', 'N/A')} 382={indicators.get('fib_382', 'N/A')}",
        ]

        candle_patterns = indicators.get("candle_patterns", {})
        if candle_patterns:
            candle_str = ", ".join(f"{k}({v[:4]})" for k, v in candle_patterns.items())
            parts.append(f"Candles: {candle_str}")

        # Additional indicators for LLM context
        if "poc_price" in indicators and indicators["poc_price"] > 0:
            parts.append(f"POC=${indicators['poc_price']:.4f}")
            parts.append(f"price_vs_poc={indicators.get('price_vs_poc', 'unknown')}")
        if indicators.get("kc_squeeze"):
            parts.append("squeeze=ACTIVE")
        if "ema_9" in indicators and "ema_21" in indicators:
            ema_trend = "bullish" if indicators["ema_9"] > indicators["ema_21"] else "bearish"
            parts.append(f"ema_ribbon={ema_trend}")
        if "taker_buy_ratio" in indicators:
            parts.append(f"taker_ratio={indicators['taker_buy_ratio']:.2f}")

        if order_flow is not None:
            funding = f"{order_flow.funding_rate:.6f}" if order_flow.funding_rate is not None else "N/A"
            parts.append(
                f"OrderFlow: imbalance={order_flow.book_imbalance:.2f} cvd={order_flow.cvd_trend} "
                f"div={order_flow.cvd_price_divergence} whale={order_flow.whale_bias} "
                f"funding={funding} smart={order_flow.smart_money_score:.2f}"
            )

        parts.append(
            'Respond in json: {"direction": "LONG or SHORT", "confidence": 0.0-1.0, "reasoning": "one paragraph"}'
        )

        prompt = "\n".join(parts)
        # Hard cap to prevent unbounded token usage
        return prompt[:4000]

    @staticmethod
    def _parse_llm_response(text: str) -> dict:
        """Parse LLM response with robust extraction.
        Handles both plain-text (DIRECTION: X) and JSON mode responses.
        Returns a dict with direction, confidence, reasoning, and _parsed flag.
        _parsed=False means we fell back to defaults (LLM output was malformed).
        """
        import json as _json
        result: dict = {"direction": "LONG", "confidence": 0.0, "reasoning": "", "_parsed": False}

        # Try JSON mode first (structured output from gpt-4o-mini)
        stripped = text.strip()
        # Strip markdown code fences (common with Gemini models)
        if stripped.startswith("```"):
            # Remove opening fence (```json or ```)
            first_newline = stripped.find("\n")
            if first_newline > 0:
                stripped = stripped[first_newline + 1:]
            # Remove closing fence
            if stripped.rstrip().endswith("```"):
                stripped = stripped.rstrip()[:-3].rstrip()
        if stripped.startswith("{"):
            try:
                data = _json.loads(stripped)
                d = str(data.get("direction", data.get("DIRECTION", "LONG"))).upper()
                result["direction"] = "SHORT" if "SHORT" in d else "LONG"
                conf = data.get("confidence", data.get("CONFIDENCE", 0.0))
                result["confidence"] = max(0.0, min(1.0, float(conf)))
                result["reasoning"] = str(data.get("reasoning", data.get("REASONING", "")))
                result["_parsed"] = True
                return result
            except (ValueError, TypeError, _json.JSONDecodeError):
                pass  # fall through to line-by-line parsing

        # Line-by-line parsing for plain-text responses
        parsed_fields = 0
        for line in stripped.splitlines():
            line_clean = line.strip()
            upper = line_clean.upper()
            if upper.startswith("DIRECTION"):
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
            return None  # ambiguous confluence + neutral RSI -- no signal

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

        # LB-5 FIX: The 0.35 floor was too high — neutral confluence (conf_base=0)
        # produced 0.35+ confidence that could pass the 0.5 threshold after blending.
        # Use 0.20 base so weak signals stay below the filter threshold.
        confidence = min(1.0, conf_base * 0.5 + 0.20 + regime_bonus + spike_bonus + adx_bonus + obv_bonus + fib_bonus + candle_bonus)

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
# _ema and _compute_adx are now in bot.core.ta_utils
# Re-exported at module level for backward compatibility


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


