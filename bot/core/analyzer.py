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
  - Chart pattern detection (H&S, double top/bottom, flags, triangles, wedges, etc.)
  - Confluence scoring model with chart pattern voter (weighted indicator agreement)
  - Robust LLM response parsing with fallback
  - Source tagging (LLM vs rule-based) on every output
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from datetime import datetime
from pathlib import Path
from bot.compat import UTC
from typing import Optional

# AG-H1: Symbol validation regex — uppercase alphanumeric, optional /pair, optional :settle
# C2-17 FIX: Allow ':' suffix for futures/swap symbols (e.g., XAU/USDT:USDT, NATGAS/USDT:USDT)
_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15}(:[A-Z0-9]{1,15})?)?$")


def _sanitize_symbol(symbol: str) -> str:
    """Validate and sanitize a trading symbol before use in LLM prompts.

    Raises ValueError if the symbol doesn't match the expected format.
    """
    s = symbol.strip().upper()
    if not _VALID_SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol format: {symbol!r}")
    return s

import numpy as np

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
from bot.core.strategy_modes import StrategySelector
from bot.llm.provider import BYOK, LLMProvider, LLMTier, PROVIDER_CATALOG, create_llm_client, llm_complete, LLMConfig, resolve_tier_config
from bot.core.volume_profile import compute_volume_profile, poc_magnet_signal
from bot.core.liquidity_sweep import detect_sweeps, sweep_to_confluence_votes
from bot.core.supply_demand import detect_zones, zones_to_confluence
from bot.core.smart_exits import detect_squeeze
from bot.core.chart_patterns import scan_all_chart_patterns
from bot.core.order_flow import OrderFlowAnalyzer
from bot.utils.logger import audit, system_log, trade_log, scan_log
from bot.utils.models import Direction, MarketSignal, TradeIdea

# Module logger. Several exception handlers below (LLM-calibration writer,
# order-flow / funding / volume-profile / sentiment / supply-demand vote
# guards) call ``logger.*``; without this definition those handlers raised
# NameError and aborted trade-idea generation whenever their inner try block
# threw.
logger = logging.getLogger(__name__)


# ── Limit entry helper ───────────────────────────────────────────

def _compute_limit_entry(
    current_price: float, atr: float, direction: Direction,
    indicators: dict, closes: np.ndarray,
) -> Optional[float]:
    """Compute a smarter limit entry price using nearby support/resistance.

    For LONG: find a support level slightly below current price (pullback entry).
    For SHORT: find a resistance level slightly above current price.

    Returns a limit price, or None if market entry is best.

    The limit price must be:
      - Within 1 ATR of current price (not too far — order should fill)
      - At least 0.15 ATR better than current (worth waiting for)
      - Based on a real level: EMA, VWAP, Fibonacci, recent swing, or POC
    """
    if atr <= 0 or current_price <= 0:
        return None

    min_improvement = 0.15 * atr  # minimum edge to justify a limit
    max_distance = 1.0 * atr     # don't set limit too far away

    candidates: list[float] = []

    # 1. EMA support/resistance
    ema9 = indicators.get("ema_9")
    ema21 = indicators.get("ema_21")
    if ema9 is not None:
        candidates.append(float(ema9))
    if ema21 is not None:
        candidates.append(float(ema21))

    # 2. VWAP
    vwap = indicators.get("vwap")
    if vwap is not None and vwap > 0:
        candidates.append(float(vwap))

    # 3. Volume Profile POC
    poc = indicators.get("poc_price")
    if poc is not None and poc > 0:
        candidates.append(float(poc))

    # 4. Fibonacci levels from chart patterns
    chart_patterns = indicators.get("chart_patterns_geo", [])
    for p in chart_patterns:
        levels = p.get("key_levels", {})
        for key, val in levels.items():
            if isinstance(val, (int, float)) and val > 0:
                candidates.append(float(val))

    # 5. Recent swing low (LONG) or swing high (SHORT)
    if len(closes) >= 20:
        if direction == Direction.LONG:
            recent_low = float(np.min(closes[-20:]))
            candidates.append(recent_low)
        else:
            recent_high = float(np.max(closes[-20:]))
            candidates.append(recent_high)

    # 6. Fibonacci retracement levels
    fib_levels = {}
    for key in ("fib_236", "fib_382", "fib_500", "fib_618", "fib_786"):
        val = indicators.get(key)
        if val is not None and val > 0:
            candidates.append(float(val))
            fib_levels[key] = float(val)

    # 7. Supply/Demand zone boundaries
    sd_zones = indicators.get("_sd_zones")
    if sd_zones:
        for zone in sd_zones[:3]:  # top 3 zones
            if direction == Direction.LONG and zone.zone_type == "demand":
                candidates.append(zone.zone_high)  # buy at top of demand zone
            elif direction == Direction.SHORT and zone.zone_type == "supply":
                candidates.append(zone.zone_low)  # sell at bottom of supply zone

    # 8. Liquidity sweep suggested entries
    sweeps = indicators.get("liquidity_sweeps", [])
    for sw in sweeps[:2]:
        if isinstance(sw, dict):
            entry = sw.get("level")
            if entry and entry > 0:
                candidates.append(float(entry))

    # Filter candidates: must be a better price within the allowed range
    best_limit = None
    best_improvement = 0.0

    for level in candidates:
        if direction == Direction.LONG:
            # For longs, limit should be BELOW current price (buy cheaper)
            improvement = current_price - level
            if improvement >= min_improvement and improvement <= max_distance:
                if improvement > best_improvement:
                    best_improvement = improvement
                    best_limit = level
        else:
            # For shorts, limit should be ABOVE current price (sell higher)
            improvement = level - current_price
            if improvement >= min_improvement and improvement <= max_distance:
                if improvement > best_improvement:
                    best_improvement = improvement
                    best_limit = level

    return best_limit


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
        # Offline thesis hook (backtest recorded-LLM replay). None in live/paper;
        # set to a callable(signal, indicators, as_of) -> thesis|None by the
        # backtest for deterministic parity. See bot/backtest/recorded_llm.py.
        self._offline_thesis_fn = None
        # Confidence calibrator (Phase A): lazily loaded from disk. ``False`` is
        # the "looked, none on disk" sentinel so we don't re-stat every analyze().
        self._calibrator = None

        # Multi-tier routing: resolve separate configs for scan vs thesis
        self._scan_config = resolve_tier_config(LLMTier.SCAN, self._llm_config) if self._llm_config else None
        self._thesis_config = resolve_tier_config(LLMTier.THESIS, self._llm_config) if self._llm_config else None
        self._scan_client = self._build_client_for_config(self._scan_config)
        self._thesis_client = self._build_client_for_config(self._thesis_config)

        # Admin tier routing: premium models for admin users
        self._admin_scan_config = resolve_tier_config(LLMTier.SCAN, self._llm_config, is_admin=True) if self._llm_config else None
        self._admin_thesis_config = resolve_tier_config(LLMTier.THESIS, self._llm_config, is_admin=True) if self._llm_config else None
        self._admin_scan_client = self._build_client_for_config(self._admin_scan_config)
        self._admin_thesis_client = self._build_client_for_config(self._admin_thesis_config)

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
        # #43: cap RPM from the dedicated per-provider config, NOT the daily
        # budget. daily_call_limit/24*60 worked out to thousands of RPM, so the
        # limiter never throttled and never prevented a 429.
        self._rate_limiter = AsyncRateLimiter(
            max_rpm=int(getattr(CONFIG.llm, "max_rpm", 40)) or 40,
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
        # Diagnostic info for the last rejected analysis
        self._last_rejection_diag: Optional[dict] = None
        # Regime persistence: smooth out single-bar whipsaw changes
        self._regime_history: list[tuple[str, str]] = []  # (symbol, regime_value)
        self._current_regimes: dict[str, Regime] = {}  # per-symbol smoothed regime

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

    @staticmethod
    def _resolve_user_llm_config(user_id):
        """Build an LLMConfig from a user's own saved provider + (decrypted) key,
        or None to fall back to the operator config. Pure-ish (a DB read) and
        side-effect free so it is unit-testable. Returns None when: per-user is
        not applicable, the user has no key, the provider is unknown, or anything
        errors (fail-open)."""
        try:
            from bot.db.models import get_user_settings
            from bot.llm.provider import LLMConfig, LLMProvider
            settings = get_user_settings(int(user_id))
            key = (settings.llm_api_key or "").strip()
            if not key:
                return None
            try:
                provider = LLMProvider(settings.llm_provider)
            except ValueError:
                return None
            cfg = LLMConfig(provider=provider, api_key=key)
            return cfg if cfg.is_configured() else None
        except Exception as exc:
            logger.debug("per-user LLM config resolve failed for %s: %s", user_id, exc)
            return None

    def _maybe_user_client(self, user_id):
        """Return (client, cfg) routed to the user's OWN LLM key, or (None, None)
        to use the operator client. Gated by CONFIG.analyzer.per_user_llm_enabled;
        fail-open on any error."""
        try:
            if user_id is None or not getattr(CONFIG.analyzer, "per_user_llm_enabled", False):
                return None, None
            cfg = self._resolve_user_llm_config(user_id)
            if cfg is None:
                return None, None
            client = self._build_client_for_config(cfg)
            if client is None:
                return None, None
            return client, cfg
        except Exception as exc:
            logger.debug("per-user LLM client resolve failed for %s: %s", user_id, exc)
            return None, None

    def _maybe_tier_client(self, user_tier, use_full_model: bool):
        """Return (client, cfg, model) routed by the user's TIER to operator-funded
        premium models (elite/pro/admin), or (None, None, None) to keep the
        default. Gated by CONFIG.analyzer.per_user_llm_tiers_enabled; fail-open.
        BYOK (the user's own key) takes precedence and is applied before this."""
        try:
            if not getattr(CONFIG.analyzer, "per_user_llm_tiers_enabled", False):
                return None, None, None
            from bot.llm.provider import routing_for_user_tier, resolve_tier_config
            table = routing_for_user_tier(user_tier)
            if table is None or self._llm_config is None:
                return None, None, None
            task_tier = LLMTier.THESIS if use_full_model else LLMTier.SCAN
            cfg = resolve_tier_config(task_tier, self._llm_config, routing_override=table)
            client = self._build_client_for_config(cfg)
            if client is None:
                return None, None, None
            return client, cfg, cfg.model
        except Exception as exc:
            logger.debug("per-user tier client resolve failed for tier=%s: %s", user_tier, exc)
            return None, None, None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate (~4 chars/token) for billable LLM calls whose
        provider/helper does not return a usage object — used only by the
        cascading-fallback cost accounting so the dollar guard sees approximate
        spend rather than zero. Never less than 1 for non-empty text."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _llm_cache_scope(self, signal, indicators, is_admin, user_id, user_tier) -> str:
        """Routing-identity salt for the semantic-cache key (deep-audit medium).

        The semantic cache keys on bucketed market conditions only, so a cached
        thesis is reused for any request with the same buckets — regardless of
        which model produced it. But the answering model depends on:
          - the pipeline tier (tier 1 = rule engine, tier 2 = scan model,
            tier 3 = thesis model),
          - the admin/basic boundary (admins use premium operator clients),
          - a user's BYOK key (their own provider answers), and
          - their premium tier (elite/pro map to premium operator models).
        Salting the key with these dimensions stops a premium/admin/BYOK thesis
        (or a tier-1 rule result) from leaking to a basic user, and vice-versa.

        Cheap and side-effect-free: classify_tier is pure; the BYOK/tier
        dimensions are derived from config flags + identity without building any
        client or reading the key. Fail-open: any error yields a coarse salt
        rather than raising on the hot path."""
        parts = []
        try:
            parts.append(f"t{TieredPipeline.classify_tier(indicators, signal)}")
        except Exception:
            parts.append("t?")
        parts.append("admin" if is_admin else "user")
        # BYOK: when per-user LLM is enabled, each user may be answered by their
        # OWN provider/key, so isolate every user_id into its own namespace.
        if user_id is not None and getattr(CONFIG.analyzer, "per_user_llm_enabled", False):
            parts.append(f"byok:{user_id}")
        # Tier routing: when enabled, the user's tier selects premium operator
        # models, so a premium-tier thesis must not be served to a basic user.
        if user_tier is not None and getattr(CONFIG.analyzer, "per_user_llm_tiers_enabled", False):
            parts.append(f"tier:{user_tier}")
        return "|".join(parts)

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

    def _get_calibrator(self):
        """Lazily load the confidence calibrator from disk (once). Returns the
        calibrator or None. Cached so repeated analyze() calls don't re-read."""
        if self._calibrator is None:
            try:
                from bot.learning.confidence_calibration import ConfidenceCalibrator
                self._calibrator = ConfidenceCalibrator.load() or False
            except Exception as exc:
                logger.debug("Calibrator load failed: %s", exc)
                self._calibrator = False
        return self._calibrator or None

    def refresh_calibrator(self) -> None:
        """Force a reload of the calibrator on the next analyze() (after a refit)."""
        self._calibrator = None

    async def analyze(self, signal: MarketSignal, candles: list[list[float]], order_flow=None,
                       candles_4h=None, candles_1d=None, is_admin: bool = False,
                       as_of: Optional[datetime] = None, user_id=None, user_tier=None) -> Optional[TradeIdea]:
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
            volumes = np.array([c[5] if len(c) > 5 else 0 for c in candles], dtype=float)
            times = np.array([c[0] for c in candles], dtype=float)  # epoch ms (ccxt)
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

        indicators = self._compute_indicators(highs, lows, closes, volumes, opens=opens, times=times)
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

        # ── Geometric chart pattern detection (H&S, double top/bottom, flags, etc.) ──
        chart_patterns = scan_all_chart_patterns(opens, highs, lows, closes)
        if chart_patterns:
            indicators["chart_patterns_geo"] = chart_patterns
            bullish_geo = [p for p in chart_patterns if p.get("signal") == "bullish"]
            bearish_geo = [p for p in chart_patterns if p.get("signal") == "bearish"]
            indicators["chart_patterns_bullish_count"] = len(bullish_geo)
            indicators["chart_patterns_bearish_count"] = len(bearish_geo)
            # Weighted score uses pattern confidence values
            indicators["chart_patterns_bullish_weight"] = round(
                sum(p.get("confidence", 0.5) for p in bullish_geo), 2
            )
            indicators["chart_patterns_bearish_weight"] = round(
                sum(p.get("confidence", 0.5) for p in bearish_geo), 2
            )
            # Extract specific wave patterns for dedicated voters
            for p in chart_patterns:
                name = p.get("name", "")
                if "Wyckoff" in name:
                    indicators["wyckoff_pattern"] = p
                elif "Harmonic" in name or any(h in name for h in ("Gartley", "Butterfly", "Bat", "Crab")):
                    indicators["harmonic_pattern"] = p
                elif "Elliott" in name:
                    # Store ALL Elliott patterns (impulse + corrective + diagonal + WXY)
                    # keyed by type to avoid overwrite when multiple are detected.
                    if "Impulse" in name or "Truncated" in name or "Extended" in name:
                        indicators["elliott_impulse"] = p
                    elif "ABC" in name:
                        indicators["elliott_corrective"] = p
                    elif "Diagonal" in name:
                        indicators["elliott_diagonal"] = p
                    elif "WXY" in name or "WXYXZ" in name:
                        indicators["elliott_wxy"] = p
                    # Keep legacy key for backward compatibility
                    indicators["elliott_pattern"] = p
                elif "Fibonacci" in name and "ext" in name.lower():
                    indicators["fib_extensions"] = p

        # ── Divergence Scanner ──
        try:
            from bot.core.divergence import scan_divergences, divergence_to_confluence_votes
            div_signals = scan_divergences(closes, volumes, lookback=CONFIG.analyzer.divergence_lookback)
            if div_signals:
                div_votes, div_weights = divergence_to_confluence_votes(div_signals)
                indicators["_div_votes"] = div_votes
                indicators["_div_weights"] = div_weights
                indicators["divergences"] = [
                    {"type": s.div_type, "indicator": s.indicator, "confidence": s.confidence, "description": s.description}
                    for s in div_signals[:3]  # top 3
                ]
        except Exception as exc:
            system_log.debug("Divergence scan failed: %s", exc)

        # ── Volume Profile ──
        try:
            vp = compute_volume_profile(
                highs, lows, closes, volumes,
                num_bins=CONFIG.analyzer.volume_profile_bins,
                current_price=signal.price,
            )
            if vp is not None:
                indicators["volume_profile"] = {
                    "poc": vp.poc, "vah": vp.vah, "val": vp.val,
                    "price_vs_poc": vp.price_vs_poc,
                    "in_value_area": vp.price_in_value_area,
                    "skew": vp.profile_skew,
                }
                indicators["_vp_result"] = vp
        except Exception as exc:
            system_log.debug("Volume profile failed: %s", exc)

        # ── Liquidity Sweep Detection ──
        try:
            if opens is not None and len(opens) >= 20:
                sweep_signals = detect_sweeps(opens, highs, lows, closes, volumes)
                if sweep_signals:
                    sweep_v, sweep_w = sweep_to_confluence_votes(sweep_signals)
                    indicators["_sweep_votes"] = sweep_v
                    indicators["_sweep_weights"] = sweep_w
                    indicators["liquidity_sweeps"] = [
                        {"type": s.sweep_type, "level": s.level_price,
                         "confidence": s.confidence, "description": s.description}
                        for s in sweep_signals[:3]
                    ]
        except Exception as exc:
            system_log.debug("Liquidity sweep detection failed: %s", exc)

        # ── Supply/Demand Zones ──
        try:
            atr_val = indicators.get("atr", 0)
            if opens is not None and len(opens) >= 20 and atr_val > 0:
                sd_zones = detect_zones(opens, highs, lows, closes, volumes, atr=atr_val)
                if sd_zones:
                    indicators["_sd_zones"] = sd_zones
                    indicators["supply_demand_zones"] = [
                        {"type": z.zone_type, "high": z.zone_high, "low": z.zone_low,
                         "strength": z.strength, "status": z.status}
                        for z in sd_zones[:5]
                    ]
        except Exception as exc:
            system_log.debug("Supply/demand zone detection failed: %s", exc)

        # ── Volatility Squeeze (detailed) ──
        try:
            squeeze_sig = detect_squeeze(closes, highs, lows)
            if squeeze_sig is not None:
                indicators["_squeeze_signal"] = squeeze_sig
                indicators["squeeze_detail"] = {
                    "is_squeezing": squeeze_sig.is_squeezing,
                    "squeeze_bars": squeeze_sig.squeeze_bars,
                    "fired": squeeze_sig.squeeze_fired,
                    "direction": squeeze_sig.fire_direction,
                    "confidence": squeeze_sig.confidence,
                }
        except Exception as exc:
            system_log.debug("Squeeze detection failed: %s", exc)

        regime = self._detect_regime(indicators, signal.symbol)

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
            # When enabled, refresh the EXTERNAL market-wide Fear & Greed index
            # (alternative.me) so the sentiment voter blends real crowd sentiment
            # — a contrarian signal independent of this symbol's price action —
            # instead of being purely price-derived (which echoes other voters).
            # Cached with a TTL inside the engine; fail-open (network error keeps
            # the last/None value → zero external adjustment). Default OFF.
            if CONFIG.analyzer.external_sentiment_enabled:
                try:
                    await self._sentiment.refresh_fear_greed()
                except Exception as _fg_exc:
                    system_log.debug("External Fear & Greed refresh failed: %s", _fg_exc)
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

        # ── Determine strategy_type (hold duration class) early ──
        # Needed before SL/TP calculation which uses per-strategy-type multipliers.
        strategy_type = self._classify_strategy_type(
            strategy_mode, regime, strategy_mode.value,
            indicators=indicators,
            mtf_result=mtf_result,
            order_flow=order_flow,
            smart_money_score=smart_money_score,
            signal=signal,
        )

        # Phase B: capture the named per-voter breakdown alongside the score so
        # downstream recording can persist it for voter-weight learning. The
        # breakdown out-param does not affect the returned confluence value.
        _confluence_votes: list = []
        confluence = self._score_confluence(
            indicators, regime, signal,
            order_flow=order_flow,
            mtf_result=mtf_result,
            smart_money_score=smart_money_score,
            mode_config=mode_config,
            sentiment_engine=self._sentiment,
            strategy_type=strategy_type,
            breakdown=_confluence_votes,
        )

        indicators["regime"] = regime.value
        indicators["confluence"] = confluence

        # SIGNAL QUALITY: multi-timeframe SMA50 trend alignment
        sma50 = float(np.mean(closes[-CONFIG.analyzer.sma_period:])) if len(closes) >= CONFIG.analyzer.sma_period else None
        if sma50 is not None:
            indicators["sma50"] = round(sma50, 6)

        thesis = await self._llm_thesis(signal, indicators, order_flow=order_flow, is_admin=is_admin, user_id=user_id, user_tier=user_tier, as_of=as_of)

        if thesis is None:
            self._last_rejection_diag = {
                "symbol": signal.symbol, "regime": regime.value,
                "confluence": round(confluence, 3),
                "reason": (
                    "Ambiguous signal — confluence in neutral zone, "
                    "no strong directional bias from RSI or MACD"
                ),
                "source": "no_thesis",
            }
            return None

        # C-07 SAFETY: reject if LLM/rule thesis returned without a valid direction.
        # This guards against any path that returns a thesis dict with direction=None.
        if thesis.get("direction") not in ("LONG", "SHORT"):
            audit(trade_log,
                  f"Thesis has invalid direction={thesis.get('direction')!r}, blocking trade",
                  action="analyze", result="INVALID_DIRECTION",
                  data={"symbol": signal.symbol})
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
        if regime == Regime.EXPANSION:
            # EXPANSION: volatility breakout from compression. Boost confidence
            # slightly — these are the highest-probability setups.
            regime_confidence_penalty = -0.05  # negative = bonus
            audit(trade_log, "Regime: EXPANSION -- volatility breakout, confidence boost",
                  action="analyze", result="BOOST",
                  data={"symbol": signal.symbol, "regime": regime.value})
        elif regime == Regime.RANGE:
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

        # Regime HARD gates (opt-in, default OFF). The penalties above SOFTEN
        # the lowest-edge regimes; with the flag ON, the worst become hard
        # no-trades — chop has no directional edge, and a counter-trend entry
        # into a strong trend is where drawdowns cluster. Default OFF keeps the
        # soft-penalty behaviour byte-for-byte.
        if CONFIG.analyzer.regime_hard_gates_enabled:
            _adx = float(indicators.get("adx", 0) or 0)
            _gate_reason = self._regime_hard_gate_reason(regime, direction, _adx)
            if _gate_reason:
                self._last_rejection_diag = {
                    "symbol": signal.symbol,
                    "stage": "regime_hard_gate",
                    "regime": regime.value,
                    "direction": direction.value,
                    "adx": round(_adx, 1),
                    "reason": _gate_reason,
                }
                audit(trade_log,
                      f"Regime hard gate: {signal.symbol} skipped — {_gate_reason}",
                      action="analyze", result="REJECTED_REGIME_GATE",
                      data={"symbol": signal.symbol, "regime": regime.value,
                            "direction": direction.value, "adx": round(_adx, 1)})
                return None

        confidence = max(0.0, min(1.0, thesis.get("confidence", 0.0))) * counter_trend_penalty
        # C2-20 FIX: Cap combined penalty — confidence never drops below 25% of raw.
        # Without this, counter_trend (0.5x) + regime_penalty (-0.15) = ~70-80% total
        # reduction, eliminating legitimate mean-reversion setups entirely.

        # Blend LLM/rule-based confidence with confluence score. The weights are
        # capped if the uncalibrated-LLM guard is active (see _blend_weights).
        _llm_w, _conf_w = self._blend_weights()
        blended_confidence = confidence * _llm_w + confluence * _conf_w

        # ── LLM Calibration Log ──────────────────────────────────────
        # Captures raw LLM confidence vs confluence BEFORE any post-blend
        # adjustments.  Enables offline calibration study: correlation,
        # direction agreement, precision/recall at thresholds per model.
        try:
            _raw_llm_conf = max(0.0, min(1.0, thesis.get("confidence", 0.0)))
            _cal_entry = {
                "ts": datetime.now(UTC).isoformat(),
                "symbol": signal.symbol,
                "llm_model": thesis.get("model_used", "rule_engine"),
                "llm_source": thesis.get("source", "unknown"),
                "llm_direction": thesis.get("direction"),
                "llm_confidence_raw": round(_raw_llm_conf, 4),
                "confluence_score": round(confluence, 4),
                "confluence_direction": "LONG" if confluence > 0.55 else ("SHORT" if confluence < 0.45 else "NEUTRAL"),
                "blended_confidence": round(blended_confidence, 4),
                "regime": regime.value,
                "strategy_type": strategy_type,
                "counter_trend_penalty": counter_trend_penalty,
                "prompt_hash": thesis.get("prompt_hash", ""),
            }
            import json as _json_cal
            _cal_path = Path(__file__).resolve().parent.parent.parent / "data" / "learning" / "llm_calibration.jsonl"
            _cal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(_cal_path, "a") as _f:
                _f.write(_json_cal.dumps(_cal_entry) + "\n")
        except Exception as _cal_exc:
            logger.debug("LLM calibration log error: %s", _cal_exc)

        # SIGNAL QUALITY: multi-timeframe confirmation via SMA50
        # Acts as a proxy for higher-timeframe trend alignment on 1H data
        if sma50 is not None:
            if signal.price > sma50 and direction == Direction.LONG:
                blended_confidence += CONFIG.analyzer.trend_alignment_bonus   # aligned with uptrend
            elif signal.price < sma50 and direction == Direction.SHORT:
                blended_confidence += CONFIG.analyzer.trend_alignment_bonus   # aligned with downtrend
            elif signal.price > sma50 and direction == Direction.SHORT:
                blended_confidence -= CONFIG.analyzer.trend_misalignment_penalty   # counter-trend SHORT
            elif signal.price < sma50 and direction == Direction.LONG:
                blended_confidence -= CONFIG.analyzer.trend_misalignment_penalty   # counter-trend LONG

        # STRATEGY: volume confirmation for direction alignment
        # Per-strategy-type volume bonus (scalps benefit most from volume spikes)
        vol_bonus = CONFIG.strategy_types.get_volume_bonus(strategy_type)
        if signal.volume_spike:
            price_moving_up = signal.change_pct_24h > 0
            if (price_moving_up and direction == Direction.LONG) or \
               (not price_moving_up and direction == Direction.SHORT):
                blended_confidence += vol_bonus  # volume confirms direction
            else:
                blended_confidence -= vol_bonus  # volume contradicts direction

        # IMPROVEMENT #2: order-flow opposition guard.
        # Microstructure (book imbalance, CVD trend, CVD-price divergence) is
        # the fastest read on real positioning. If it strongly OPPOSES the
        # chosen direction we cut confidence — and veto outright when the
        # opposition is severe and well-evidenced. This only reduces confidence
        # or skips; it never raises it, so it can't manufacture a trade.
        try:
            opposition, of_conf, of_bias = self._order_flow_opposition(order_flow, direction)
            if opposition > 0.0:
                if opposition >= 0.7 and of_conf >= 0.5:
                    audit(trade_log,
                          "Order-flow veto: microstructure strongly opposes direction",
                          action="analyze", result="SKIP",
                          data={"symbol": signal.symbol,
                                "direction": direction.value,
                                "of_bias": round(of_bias, 3),
                                "of_confidence": round(of_conf, 3)})
                    self._last_rejection_diag = {
                        "symbol": signal.symbol, "regime": regime.value,
                        "confluence": round(confluence, 3),
                        "direction": direction.value,
                        "reason": f"Order-flow veto: microstructure opposes {direction.value}",
                        "source": "order_flow_veto",
                    }
                    return None
                blended_confidence -= 0.15 * opposition * of_conf
        except Exception as _of_exc:
            logger.warning("Order-flow opposition calc failed for %s: %s", signal.symbol, _of_exc)

        # ── Funding Rate Arbitrage Filter ──
        try:
            if order_flow is not None and hasattr(order_flow, 'funding_rate'):
                fr = order_flow.funding_rate
                if fr is not None and abs(fr) > 0.0005:  # extreme funding (> 0.05%)
                    if fr < -0.0005 and direction == Direction.LONG:
                        blended_confidence += 0.03
                        indicators["funding_arb"] = f"Extreme negative funding ({fr:.4%}) favors LONG"
                    elif fr > 0.0005 and direction == Direction.SHORT:
                        blended_confidence += 0.03
                        indicators["funding_arb"] = f"Extreme positive funding ({fr:.4%}) favors SHORT"
                    elif fr < -0.0005 and direction == Direction.SHORT:
                        blended_confidence -= 0.02
                        indicators["funding_arb"] = f"Extreme negative funding ({fr:.4%}) opposes SHORT"
                    elif fr > 0.0005 and direction == Direction.LONG:
                        blended_confidence -= 0.02
                        indicators["funding_arb"] = f"Extreme positive funding ({fr:.4%}) opposes LONG"
        except Exception as _fr_exc:
            logger.warning("Funding rate arb filter failed for %s: %s", signal.symbol, _fr_exc)

        # ── Funding carry-cost awareness (gated) ─────────────────────────────
        # funding_arb above rewards/penalises the INSTANTANEOUS funding direction;
        # this adds the missing dimension — the carry COST a trade would PAY over
        # its expected hold (a swing pays many funding intervals, a scalp ~none).
        # Bounded, only ever REDUCES confidence, fail-open, default OFF.
        try:
            if (CONFIG.analyzer.funding_cost_aware_enabled
                    and order_flow is not None and hasattr(order_flow, "funding_rate")):
                from bot.core.funding import funding_cost_haircut
                _hair = funding_cost_haircut(order_flow.funding_rate, direction.value, strategy_type)
                if _hair < 0.0:
                    blended_confidence += _hair
        except Exception as _fc_exc:
            logger.debug("Funding cost-aware haircut skipped for %s: %s", signal.symbol, _fc_exc)

        # IMPROVEMENT #3: Smart-money direct confidence boost.
        # When smart_money_score is strongly directional (+/-), give a small
        # direct bonus to blended_confidence. This gives whale/institutional
        # flow more influence beyond just the confluence voter weights.
        if smart_money_score is not None:
            _sm_val = getattr(smart_money_score, "composite_score", 0.0) or 0.0
            if abs(_sm_val) > 0.3:
                sm_alignment = 0.0
                if direction == Direction.LONG and _sm_val > 0:
                    sm_alignment = _sm_val
                elif direction == Direction.SHORT and _sm_val < 0:
                    sm_alignment = abs(_sm_val)
                # Max boost: ~0.05 (at score=1.0), penalty ~0.03 for misalignment
                if sm_alignment > 0:
                    blended_confidence += 0.05 * sm_alignment
                elif _sm_val != 0:
                    # Smart money opposes direction — small penalty
                    blended_confidence -= 0.03 * abs(_sm_val)

        blended_confidence = round(max(0.0, min(1.0, blended_confidence)), 2)

        # Apply regime penalty (RANGE: -0.10, CHOP: -0.15, else: 0)
        # C2-20 FIX: Ensure combined penalties don't reduce below 25% of original.
        # H-14 FIX: Use blended_confidence (after counter-trend but before regime
        # penalty) as the floor base, not raw_confidence from the LLM.
        min_floor = blended_confidence * 0.25
        blended_confidence = round(max(min_floor, blended_confidence - regime_confidence_penalty), 2)

        # Session-aware confidence adjustment: reduce confidence in
        # low-liquidity sessions (Asian, late NY), boost during peak overlap.
        try:
            from bot.core.session_aware import get_current_session
            # as_of lets the backtest pass the simulated bar time so session
            # adjustments are causal and reproducible; live passes None → now.
            session = get_current_session(now=as_of)
            if session.confidence_adjustment != 0:
                blended_confidence = round(
                    max(min_floor, blended_confidence + session.confidence_adjustment), 2)
        except Exception:
            pass  # fail-open: session check must never block analysis

        # Definitive final clamp. The line-761 clamp is undone by later additive
        # adjustments — notably the session boost above (+confidence_adjustment),
        # which is floored at min_floor but NOT capped at 1.0. An un-capped value
        # (e.g. 1.01) trips TradeIdea's `confidence <= 1` pydantic validator and
        # aborts the whole analysis. Clamp once here so every downstream use (the
        # min_conf gate and both TradeIdea constructions) sees a valid [0,1]
        # confidence. The lower bound never binds (the floors keep it ≥ 0).
        blended_confidence = max(0.0, min(1.0, blended_confidence))

        # ── Confidence calibration (Phase A) ─────────────────────────────────
        # Remap the final confidence through a reliability curve fitted from the
        # bot's own closed-trade history, so the number reflects realized win
        # rate. Fail-open: any error leaves confidence untouched. When the flag
        # is OFF we still compute the would-be value and log the delta (shadow
        # mode) so its effect can be observed before it is ever applied.
        try:
            _cal = self._get_calibrator()
            if _cal is not None and _cal.is_ready():
                _calibrated = round(_cal.calibrate(blended_confidence), 2)
                if CONFIG.analyzer.confidence_calibration_enabled:
                    if _calibrated != blended_confidence:
                        audit(trade_log,
                              f"Confidence calibrated {blended_confidence:.2f} -> {_calibrated:.2f}",
                              action="confidence_calibration", result="APPLIED",
                              data={"symbol": signal.symbol, "raw": blended_confidence,
                                    "calibrated": _calibrated})
                    blended_confidence = _calibrated
                elif _calibrated != blended_confidence:
                    # #36: shadow mode exists to be OBSERVED before enabling, but
                    # the delta went to DEBUG (no handler) → invisible. Emit it on
                    # the same visible audit channel as the APPLIED path.
                    audit(trade_log,
                          f"Confidence calibration SHADOW {blended_confidence:.2f} -> would={_calibrated:.2f}",
                          action="confidence_calibration", result="SHADOW",
                          data={"symbol": signal.symbol, "raw": blended_confidence,
                                "would": _calibrated})
        except Exception as _cal_exc:
            logger.debug("Confidence calibration skipped: %s", _cal_exc)

        # ── Per-setup expectancy nudge (Phase C) ─────────────────────────────
        # Shade confidence by THIS setup's own track record (symbol + regime +
        # direction win rate from completed trades). Small, bounded, and shrunk
        # by sample count, so it can only nudge — never dominate. Fail-open;
        # default OFF (shadow-logs the would-be nudge, applies nothing).
        try:
            from bot.learning.setup_expectancy import get_setup_expectancy
            _exp = get_setup_expectancy()
            if _exp is not None and _exp.is_ready():
                _nudge = _exp.confidence_nudge(signal.symbol, regime.value, direction.value)
                if _nudge != 0.0:
                    if CONFIG.analyzer.setup_expectancy_enabled:
                        _before = blended_confidence
                        blended_confidence = round(
                            max(0.0, min(1.0, blended_confidence + _nudge)), 2)
                        audit(trade_log,
                              f"Setup expectancy nudge {_before:.2f} -> {blended_confidence:.2f}",
                              action="setup_expectancy", result="APPLIED",
                              data={"symbol": signal.symbol, "regime": regime.value,
                                    "direction": direction.value, "nudge": round(_nudge, 4)})
                    else:
                        # #36: surface the would-be nudge on the visible audit
                        # channel (was DEBUG → invisible) so shadow mode can be
                        # evaluated before the flag is enabled.
                        audit(trade_log,
                              f"Setup expectancy SHADOW nudge would={_nudge:+.3f} on {blended_confidence:.2f}",
                              action="setup_expectancy", result="SHADOW",
                              data={"symbol": signal.symbol, "regime": regime.value,
                                    "direction": direction.value, "nudge": round(_nudge, 4)})
        except Exception as _exp_exc:
            logger.debug("Setup expectancy skipped: %s", _exp_exc)

        # SIGNAL QUALITY: threshold at min_confidence (matches config)
        # RANGE/CHOP trades need high raw confluence to survive after penalty
        # Per-strategy-type confidence threshold
        min_conf = CONFIG.strategy_types.get_min_confidence(strategy_type)
        if blended_confidence < min_conf:
            thesis_src = thesis.get("source", "unknown")
            self._last_rejection_diag = {
                "symbol": signal.symbol,
                "regime": regime.value,
                "confluence": round(confluence, 3),
                "direction": direction.value,
                "raw_confidence": round(confidence, 3),
                "blended": round(blended_confidence, 3),
                "threshold": min_conf,
                "min_confidence_used": min_conf,
                "strategy_type": strategy_type,
                "regime_penalty": round(regime_confidence_penalty, 3),
                "counter_trend_penalty": round(counter_trend_penalty, 3),
                "source": thesis_src,
                "reason": (
                    f"Score {blended_confidence:.0%} < {min_conf:.0%} threshold"
                    + (f" (regime {regime.value} penalty -{regime_confidence_penalty:.0%})" if regime_confidence_penalty > 0 else "")
                    + (" (counter-trend penalty)" if counter_trend_penalty < 1.0 else "")
                ),
            }
            audit(trade_log, "Low blended confidence -- skipping",
                  action="analyze", result="SKIP",
                  data={"symbol": signal.symbol, "raw_conf": confidence,
                        "confluence": confluence, "blended": blended_confidence})
            return None

        entry = signal.price
        atr = indicators.get("atr", entry * 0.02)

        # ── Smart limit entry detection ──
        # If price is extended from a key level, suggest a limit order at a
        # better entry (pullback to support for longs, resistance for shorts).
        # Only when CONFIG.limit_orders.enabled is True.
        order_type = CONFIG.limit_orders.default_order_type if CONFIG.limit_orders.enabled else "market"
        limit_entry = None
        if CONFIG.limit_orders.enabled:
            limit_entry = _compute_limit_entry(
                entry, atr, direction, indicators, closes
            )
            # If no pullback level found but default is "limit", use a small
            # offset (0.1 ATR) from market price to get price improvement
            if limit_entry is None and order_type == "limit":
                offset = 0.1 * atr
                if direction == Direction.LONG:
                    limit_entry = round(entry - offset, 8)
                else:
                    limit_entry = round(entry + offset, 8)

        # STRATEGY: adaptive ATR multipliers based on volatility regime
        # Strategy mode provides baseline SL/TP; volatility/regime can override
        # Compute normalized volatility: ATR as a percentage of price
        vol_ratio = atr / entry if entry > 0 else 0.02

        # Start with per-strategy-type defaults from config
        # These are the primary SL/TP settings based on trade duration
        st_cfg = CONFIG.strategy_types
        sl_mult = st_cfg.get_sl_mult(strategy_type)
        tp_mult = st_cfg.get_tp_mult(strategy_type)

        # REGIME-SPECIFIC SL/TP: volatility overrides take priority
        # Only widen for high vol or tighten for low vol — don't narrow
        # a swing trade's stops below the strategy-type minimum
        if vol_ratio > CONFIG.analyzer.high_vol_threshold:
            # High volatility: use the wider of strategy-type or high-vol setting
            sl_mult = max(sl_mult, CONFIG.analyzer.high_vol_sl_mult)
            tp_mult = max(tp_mult, CONFIG.analyzer.high_vol_tp_mult)
        elif vol_ratio < CONFIG.analyzer.low_vol_threshold:
            # Low volatility: use strategy-type setting (already tuned for duration)
            pass  # keep strategy_type defaults
        elif regime_sl_override is not None and regime_tp_override is not None:
            # RANGE/CHOP regime: only tighten if it's a scalp/intraday trade
            if strategy_type in ("scalp", "intraday"):
                sl_mult, tp_mult = regime_sl_override, regime_tp_override

        stop_loss = entry - sl_mult * atr if direction == Direction.LONG else entry + sl_mult * atr
        take_profit = entry + tp_mult * atr if direction == Direction.LONG else entry - tp_mult * atr

        # Apply limit entry if a better price was found
        if limit_entry is not None and limit_entry != entry:
            # Shift SL/TP by the same offset so R:R stays the same
            entry_shift = limit_entry - entry
            entry = limit_entry
            stop_loss = stop_loss + entry_shift
            take_profit = take_profit + entry_shift
            order_type = "limit"

        # Guard against negative SL/TP from extreme ATR values
        if direction == Direction.LONG and stop_loss <= 0:
            stop_loss = entry * 0.01  # floor at 1% of entry
        elif direction == Direction.SHORT and take_profit <= 0:
            take_profit = entry * 0.01

        # ── Leverage-aware SL cap ──────────────────────────────────
        # Prevent outsized losses when leverage is used.
        # max_margin_risk_pct caps the max loss as % of margin (cost).
        # SL distance % × leverage = margin risk %.
        # If it exceeds the cap, tighten SL (keeping R:R ratio intact).
        leverage = CONFIG.exchange.default_leverage
        if leverage > 1 and entry > 0:
            sl_dist_pct = abs(entry - stop_loss) / entry
            margin_risk_pct = sl_dist_pct * leverage * 100  # % of margin at risk
            max_margin_risk = CONFIG.risk.max_margin_risk_pct
            if margin_risk_pct > max_margin_risk:
                # Compute the tighter SL distance
                max_sl_dist_pct = max_margin_risk / (leverage * 100)
                old_sl_dist = abs(entry - stop_loss)
                new_sl_dist = entry * max_sl_dist_pct
                # Preserve R:R ratio by scaling TP proportionally
                old_tp_dist = abs(take_profit - entry)
                rr_ratio = old_tp_dist / old_sl_dist if old_sl_dist > 0 else 1.2
                new_tp_dist = new_sl_dist * rr_ratio
                if direction == Direction.LONG:
                    stop_loss = entry - new_sl_dist
                    take_profit = entry + new_tp_dist
                else:
                    stop_loss = entry + new_sl_dist
                    take_profit = entry - new_tp_dist
                audit(trade_log,
                      f"SL tightened for leverage: {margin_risk_pct:.1f}% margin risk → {max_margin_risk:.1f}% (SL dist {sl_dist_pct*100:.2f}% → {max_sl_dist_pct*100:.2f}%)",
                      action="leverage_sl_cap", result="TIGHTENED")

        # ── RSI hard block: reject trades into overbought/oversold ──
        rsi_val = indicators.get("rsi", 50)
        if direction == Direction.LONG and rsi_val >= CONFIG.analyzer.rsi_overbought_block:
            audit(trade_log,
                  f"LONG rejected: RSI {rsi_val:.1f} >= {CONFIG.analyzer.rsi_overbought_block} (overbought)",
                  action="rsi_block", result="BLOCKED")
            return None
        if direction == Direction.SHORT and rsi_val <= CONFIG.analyzer.rsi_oversold_block:
            audit(trade_log,
                  f"SHORT rejected: RSI {rsi_val:.1f} <= {CONFIG.analyzer.rsi_oversold_block} (oversold)",
                  action="rsi_block", result="BLOCKED")
            return None

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

        # ── Classify signal type for hold-time profiling ──
        signal_type = self._classify_signal_type(
            indicators, signal, regime, thesis.get("source", "")
        )

        # Guard: on very-low-priced assets the ATR-derived stop/target distance
        # can fall below tick precision, so rounding collapses SL/TP onto the
        # entry. That produces a TradeIdea the directional-sanity validator
        # rejects (raising and aborting the whole analysis/backtest run). Skip
        # the degenerate idea instead — no trade is the safe outcome.
        _r_entry = round(entry, price_decimals)
        _r_sl = round(stop_loss, price_decimals)
        _r_tp = round(take_profit, price_decimals)
        _valid_long = direction == Direction.LONG and _r_sl < _r_entry < _r_tp
        _valid_short = direction == Direction.SHORT and _r_tp < _r_entry < _r_sl
        if _r_entry <= 0 or not (_valid_long or _valid_short):
            audit(trade_log,
                  f"Idea skipped: degenerate levels after rounding "
                  f"(entry={_r_entry}, sl={_r_sl}, tp={_r_tp}, {direction.value})",
                  action="analyze", result="SKIP",
                  data={"symbol": signal.symbol})
            return None

        idea = TradeIdea(
            id=f"TI-{uuid.uuid4().hex[:8]}",
            asset=signal.symbol,
            direction=direction,
            entry_price=_r_entry,
            stop_loss=_r_sl,
            take_profit=_r_tp,
            confidence=blended_confidence,
            reasoning=(
                f"[{source}|{regime.value}|{mode_tag}|{strategy_type}|C={confluence:.2f}"
                f"{mtf_tag}{sm_tag}] {thesis.get('reasoning', '')}"
            ),
            signals_used=list(indicators.keys()),
            timestamp=datetime.now(UTC),
            order_type=order_type,
            strategy_type=strategy_type,
            signal_type=signal_type,
        )

        # Phase B: attach the per-voter breakdown so the decision recorder can
        # persist it (joined to outcome for voter-weight learning later).
        try:
            idea._confluence_votes = _confluence_votes
        except Exception:
            pass

        # Store VWAP at entry for reversion exit tracking
        if signal_type == "vwap_reversion" and indicators.get("vwap"):
            idea._entry_vwap = indicators["vwap"]

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

    # -- Strategy Type Classification --

    @staticmethod
    def _classify_strategy_type(
        strategy_mode, regime, mode_tag: str,
        indicators: dict = None,
        mtf_result=None,
        order_flow=None,
        smart_money_score=None,
        signal=None,
    ) -> str:
        """Scoring-based strategy type classifier.

        Returns one of: "scalp", "intraday", "swing", "position"

        Uses 9 weighted factors (strategy mode, regime, ADX, RSI, Bollinger
        squeeze, ATR volatility, MTF alignment, smart money, volume spike)
        to score each type and pick the winner.

        The /scalp, /intraday, /swing, /momentum commands can override this
        by setting strategy_type directly on the TradeIdea.
        """
        from bot.core.strategy_modes import StrategyMode
        from bot.core.ta_utils import Regime

        scores = {"scalp": 0.0, "intraday": 0.0, "swing": 0.0, "position": 0.0}

        # ── Factor 1: Strategy mode (strongest signal, weight 3.0) ──
        mode_map = {
            StrategyMode.MEAN_REVERSION: {"scalp": 3.0},
            StrategyMode.LIQUIDITY_SWEEP: {"scalp": 2.5, "intraday": 0.5},
            StrategyMode.BREAKOUT: {"intraday": 1.5, "swing": 1.5},
            StrategyMode.TREND_CONTINUATION: {"swing": 3.0},
            StrategyMode.TURTLE_BREAKOUT: {"swing": 2.0, "position": 1.0},
            StrategyMode.CONSERVATIVE: {"intraday": 2.0, "swing": 1.0},
        }
        for k, v in mode_map.get(strategy_mode, {"intraday": 1.0}).items():
            scores[k] += v

        # ── Factor 2: Regime (weight 2.0) ──
        if regime in (Regime.RANGE, Regime.CHOP):
            scores["scalp"] += 1.0
            scores["intraday"] += 1.0
        elif regime == Regime.EXPANSION:
            scores["intraday"] += 1.0
            scores["swing"] += 1.0
        elif regime in (Regime.TREND_UP, Regime.TREND_DOWN):
            scores["swing"] += 1.5
            scores["position"] += 0.5

        # ── Factor 3: ADX trend strength (weight 1.5) ──
        if indicators:
            adx = indicators.get("adx", 0)
            if adx < 20:
                scores["scalp"] += 1.5  # no trend -> scalp
            elif adx < 30:
                scores["intraday"] += 1.0
                scores["swing"] += 0.5
            elif adx < 40:
                scores["swing"] += 1.5
            else:
                scores["swing"] += 1.0
                scores["position"] += 1.5  # strong trend -> position

            # ── Factor 4: RSI extremes (weight 1.0) ──
            rsi = indicators.get("rsi", 50)
            if rsi > 75 or rsi < 25:
                scores["scalp"] += 1.0  # extreme RSI -> quick fade
            elif rsi > 65 or rsi < 35:
                scores["intraday"] += 0.5

            # ── Factor 5: Bollinger squeeze (weight 0.8) ──
            kc_squeeze = indicators.get("kc_squeeze", False)
            if kc_squeeze:
                scores["intraday"] += 0.8  # squeeze = imminent breakout

            # ── Factor 6: ATR-based volatility (weight 1.0) ──
            atr = indicators.get("atr", 0)
            # AN-3: indicators never carries "close" (not stored by
            # _compute_indicators), so the old lookup was always 0 and fell
            # through to signal.price. Use signal.price directly.
            price = signal.price if signal else 0
            if price > 0 and atr > 0:
                atr_pct = (atr / price) * 100
                if atr_pct > 5:  # high vol
                    scores["scalp"] += 1.0  # high vol = quick in/out
                elif atr_pct < 1.5:  # low vol
                    scores["swing"] += 0.5
                    scores["position"] += 0.5

        # ── Factor 7: Multi-timeframe alignment (weight 1.5) ──
        if mtf_result is not None:
            alignment = getattr(mtf_result, "alignment_score", 0)
            aligned_count = len(getattr(mtf_result, "aligned_timeframes", []))
            if abs(alignment) > 0.6 and aligned_count >= 2:
                scores["swing"] += 1.0
                scores["position"] += 1.0  # strong MTF = longer hold
            elif abs(alignment) < 0.3:
                scores["scalp"] += 0.5  # weak MTF = quick trade
            # Break of structure or change of character -> swing
            if getattr(mtf_result, "bos_detected", False):
                scores["swing"] += 0.5
            if getattr(mtf_result, "choch_detected", False):
                scores["intraday"] += 0.5

        # ── Factor 8: Smart money / whale activity (weight 1.5) ──
        if smart_money_score is not None:
            composite = abs(getattr(smart_money_score, "composite_score", 0))
            if composite > 0.6:
                scores["position"] += 1.5  # strong smart money -> ride it
                scores["swing"] += 0.5
            elif composite > 0.3:
                scores["swing"] += 1.0

        # ── Factor 9: Volume spike (weight 0.5) ──
        if signal is not None and getattr(signal, "volume_spike", False):
            scores["scalp"] += 0.5  # volume = liquidity for quick exit

        # ── Pick winner ──
        best_type = max(scores, key=scores.get)
        return best_type

    @staticmethod
    def _classify_signal_type(indicators: dict, signal: MarketSignal, regime, thesis_source: str) -> str:
        """Classify the primary signal type that drove this trade idea.

        Maps to optimal hold-time profiles:
        - momentum_confluence: core LLM + 10-voter signal (2-8h)
        - vwap_reversion: mean-reversion near VWAP (30min-2h)
        - regime_trend: strong trend regime play (1-3 days)
        - volume_spike: OBV/taker volume breakout (15-90min)
        - funding_arb: funding rate driven (avoid unless swing)
        """
        from bot.core.ta_utils import Regime

        # Volume spike is highest priority — it time-decays fastest
        if signal.volume_spike:
            vol_osc = indicators.get("vol_oscillator", 0)
            if vol_osc > 20 or indicators.get("capitulation_sell") or indicators.get("capitulation_buy"):
                return "volume_spike"

        # VWAP reversion: price near VWAP in RANGE/CHOP regime
        vwap = indicators.get("vwap")
        if vwap and vwap > 0:
            vwap_dist_pct = abs(signal.price - vwap) / vwap * 100
            if vwap_dist_pct < 0.5 and regime in (Regime.RANGE, Regime.CHOP):
                return "vwap_reversion"

        # Regime trend: strong trend with ADX > 30
        adx = indicators.get("adx", 0)
        if regime in (Regime.TREND_UP, Regime.TREND_DOWN) and adx > 30:
            return "regime_trend"

        # Default: momentum confluence (the core LLM signal)
        return "momentum_confluence"

    # -- Technical Indicators --

    @staticmethod
    @staticmethod
    def _session_anchor_index(times: Optional[np.ndarray]) -> int:
        """Index of the first bar in the SAME UTC day as the latest bar (the
        session open). Returns 0 when timestamps are unavailable/degenerate, so
        the caller falls back to the full window."""
        if times is None or len(times) == 0:
            return 0
        try:
            day = int(times[-1] // 86_400_000)  # UTC day number from epoch ms
            idx = len(times) - 1
            while idx > 0 and int(times[idx - 1] // 86_400_000) == day:
                idx -= 1
            return idx
        except (ValueError, TypeError, OverflowError):
            return 0

    @staticmethod
    def _session_vwap(typical_price: np.ndarray, volumes: np.ndarray,
                      times: Optional[np.ndarray]) -> Optional[float]:
        """Session-anchored VWAP over bars from the current UTC day's open to now.
        Returns None when it can't be computed (no timestamps, zero volume in the
        session) so the caller keeps the full-window VWAP."""
        idx = Analyzer._session_anchor_index(times)
        seg_tp = typical_price[idx:]
        seg_vol = volumes[idx:]
        sv = float(np.sum(seg_vol))
        if sv <= 0:
            return None
        return float(np.sum(seg_tp * seg_vol) / sv)

    @staticmethod
    def _compute_indicators(
        highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        volumes: Optional[np.ndarray] = None,
        opens: Optional[np.ndarray] = None,
        times: Optional[np.ndarray] = None,
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

        # ── VWAP (if volume available) ──
        # NOTE: this "vwap" is a FULL-WINDOW cumulative VWAP — anchored to bar[0]
        # of the fetched window (~100 bars), so it drifts as the window slides and
        # is NOT the session VWAP traders mean. The session-anchored value (from
        # the current UTC day's first bar) is exposed below as "vwap_session";
        # when CONFIG.analyzer.vwap_session_anchored is ON it replaces "vwap".
        if volumes is not None and len(volumes) > 0:
            typical_price = (highs + lows + closes) / 3
            cum_tp_vol = np.cumsum(typical_price * volumes)
            cum_vol = np.cumsum(volumes)
            vwap = cum_tp_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else closes[-1]
            results["vwap"] = round(float(vwap), 6)

            # Session-anchored VWAP (anchored to the current UTC day's first bar).
            session_vwap = Analyzer._session_vwap(typical_price, volumes, times)
            if session_vwap is not None:
                results["vwap_session"] = round(float(session_vwap), 6)
                if getattr(CONFIG.analyzer, "vwap_session_anchored", False):
                    results["vwap"] = results["vwap_session"]

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
            vp = compute_volume_profile(highs, lows, closes, volumes)
            if vp is not None:
                results["poc_price"] = vp.poc
                results["value_area_high"] = vp.vah
                results["value_area_low"] = vp.val
                results["price_vs_poc"] = vp.price_vs_poc
                poc_dist = abs(closes[-1] - vp.poc) / closes[-1] * 100 if closes[-1] > 0 else 0
                results["poc_distance_pct"] = round(poc_dist, 2)

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

        # ── Stochastic Oscillator (14, 3, 3) — momentum + overbought/oversold ──
        stoch_k_period = 14
        stoch_smooth = 3
        if len(closes) >= stoch_k_period + stoch_smooth:
            # Raw %K: (Close - Lowest Low) / (Highest High - Lowest Low) * 100.
            # #40: vectorized rolling max/min over the period window instead of a
            # per-bar Python loop over the whole array — numerically identical
            # (same windowed max/min and the hh<=ll → 50.0 fallback).
            from numpy.lib.stride_tricks import sliding_window_view
            hh = sliding_window_view(highs, stoch_k_period).max(axis=1)
            ll = sliding_window_view(lows, stoch_k_period).min(axis=1)
            last_close = closes[stoch_k_period - 1:]
            denom = hh - ll
            raw_k = np.full(len(denom), 50.0)
            _ok = denom > 0
            raw_k[_ok] = (last_close[_ok] - ll[_ok]) / denom[_ok] * 100
            # Smooth %K (3-period SMA of raw %K)
            if len(raw_k) >= stoch_smooth:
                smooth_k = np.convolve(raw_k, np.ones(stoch_smooth) / stoch_smooth, mode='valid')
                # %D = 3-period SMA of smooth %K
                if len(smooth_k) >= stoch_smooth:
                    smooth_d = np.convolve(smooth_k, np.ones(stoch_smooth) / stoch_smooth, mode='valid')
                    results["stoch_k"] = round(float(smooth_k[-1]), 2)
                    results["stoch_d"] = round(float(smooth_d[-1]), 2)
                    # Crossover detection
                    if len(smooth_k) >= 2 and len(smooth_d) >= 2:
                        results["stoch_cross_up"] = (smooth_k[-1] > smooth_d[-1] and
                                                      smooth_k[-2] <= smooth_d[-2])
                        results["stoch_cross_down"] = (smooth_k[-1] < smooth_d[-1] and
                                                        smooth_k[-2] >= smooth_d[-2])
                    # Divergence: price makes new low but stoch makes higher low (bullish)
                    if len(smooth_k) >= 20:
                        price_low_10 = float(np.min(closes[-10:]))
                        price_low_prev = float(np.min(closes[-20:-10]))
                        stoch_low_10 = float(np.min(smooth_k[-10:]))
                        stoch_low_prev = float(np.min(smooth_k[-20:-10]))
                        results["stoch_bull_div"] = (price_low_10 < price_low_prev and
                                                      stoch_low_10 > stoch_low_prev)
                        price_high_10 = float(np.max(closes[-10:]))
                        price_high_prev = float(np.max(closes[-20:-10]))
                        stoch_high_10 = float(np.max(smooth_k[-10:]))
                        stoch_high_prev = float(np.max(smooth_k[-20:-10]))
                        results["stoch_bear_div"] = (price_high_10 > price_high_prev and
                                                      stoch_high_10 < stoch_high_prev)

        # ── Donchian Channels (20-period) — Turtle Breakout ──
        dc_period = 20
        if len(closes) >= dc_period:
            dc_high = float(np.max(highs[-dc_period:]))
            dc_low = float(np.min(lows[-dc_period:]))
            dc_mid = (dc_high + dc_low) / 2
            results["dc_upper"] = round(dc_high, 6)
            results["dc_lower"] = round(dc_low, 6)
            results["dc_mid"] = round(dc_mid, 6)
            results["dc_width"] = round((dc_high - dc_low) / dc_low * 100 if dc_low > 0 else 0, 4)
            # Breakout detection
            results["dc_breakout_high"] = float(closes[-1]) >= dc_high
            results["dc_breakout_low"] = float(closes[-1]) <= dc_low
            # Position within channel (0=bottom, 1=top)
            results["dc_position"] = round(
                (closes[-1] - dc_low) / (dc_high - dc_low) if dc_high > dc_low else 0.5, 4
            )
            # 55-period Donchian for Turtle system confirmation
            dc55_period = min(55, len(closes))
            if dc55_period >= 40:
                dc55_high = float(np.max(highs[-dc55_period:]))
                dc55_low = float(np.min(lows[-dc55_period:]))
                results["dc55_upper"] = round(dc55_high, 6)
                results["dc55_lower"] = round(dc55_low, 6)
                results["dc55_breakout_high"] = float(closes[-1]) >= dc55_high
                results["dc55_breakout_low"] = float(closes[-1]) <= dc55_low

        # ── Reversal Signal Detection ──
        if opens is not None and len(opens) >= 3 and len(closes) >= 3:
            # Pin bar: long wick, small body, wick >= 2x body
            body = abs(closes[-1] - opens[-1])
            upper_wick = highs[-1] - max(closes[-1], opens[-1])
            lower_wick = min(closes[-1], opens[-1]) - lows[-1]
            candle_range = highs[-1] - lows[-1]
            if candle_range > 0:
                body_ratio = body / candle_range
                results["pin_bar_bullish"] = (lower_wick >= 2 * body and
                                               body_ratio < 0.35 and
                                               upper_wick < body)
                results["pin_bar_bearish"] = (upper_wick >= 2 * body and
                                               body_ratio < 0.35 and
                                               lower_wick < body)
            # Inside bar: current bar fully contained within previous bar
            results["inside_bar"] = (highs[-1] <= highs[-2] and lows[-1] >= lows[-2])
            # Capitulation volume: extreme volume + large red candle
            if volumes is not None and len(volumes) >= 20:
                avg_vol_20 = float(np.mean(volumes[-20:]))
                cur_vol = float(volumes[-1])
                # C2-19 FIX: Explicit guard-first to prevent division by zero.
                # Previously the ternary bound to only the right operand.
                is_large_red = (
                    candle_range > 0
                    and closes[-1] < opens[-1]
                    and body / candle_range > 0.6
                )
                is_large_green = (
                    candle_range > 0
                    and closes[-1] > opens[-1]
                    and body / candle_range > 0.6
                )
                results["capitulation_sell"] = (cur_vol >= 3 * avg_vol_20 and is_large_red)
                results["capitulation_buy"] = (cur_vol >= 3 * avg_vol_20 and is_large_green)
                results["vol_capitulation_ratio"] = round(cur_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0, 2)

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

        # ── Post-computation NaN/Inf sanitizer ──
        # Guard against NaN or Inf leaking from any indicator computation
        # (e.g. division by zero edge cases, empty-window statistics).
        _RSI_DEFAULT = 50.0
        for key, val in results.items():
            if isinstance(val, float) and not math.isfinite(val):
                results[key] = _RSI_DEFAULT if key == "rsi" else 0.0
            elif isinstance(val, (np.floating,)) and not np.isfinite(val):
                results[key] = _RSI_DEFAULT if key == "rsi" else 0.0

        return results

    # -- Regime Detection --

    def _blend_weights(self) -> tuple[float, float]:
        """Return the (llm_weight, confluence_weight) used to blend confidence.

        Normally the configured weights (0.6 / 0.4). When the uncalibrated-LLM
        guard is ON *and* confidence calibration is OFF, the LLM's confidence is
        unproven against realized outcomes, so its weight is capped at
        ``uncalibrated_llm_weight_cap`` and the freed weight is shifted to the
        deterministic, auditable confluence score (the total is preserved). Once
        calibration is enabled the cap lifts automatically. Pure / side-effect
        free so it is unit-testable.
        """
        cfg = CONFIG.analyzer
        llm_w = cfg.llm_weight
        conf_w = cfg.confluence_weight
        if (getattr(cfg, "uncalibrated_llm_weight_cap_enabled", False)
                and not cfg.confidence_calibration_enabled):
            cap = cfg.uncalibrated_llm_weight_cap
            if llm_w > cap:
                conf_w += (llm_w - cap)  # preserve the total weight
                llm_w = cap
        return llm_w, conf_w

    def _regime_hard_gate_reason(
        self, regime: Regime, direction: Direction, adx: float
    ) -> Optional[str]:
        """Return a no-trade reason if the regime HARD gate blocks this entry,
        else None. Pure decision logic (no side effects) so it is unit-testable.

        Gates (only consulted when CONFIG.analyzer.regime_hard_gates_enabled):
          - CHOP / UNKNOWN regime → block (no directional edge).
          - Counter-trend entry into a STRONG trend (ADX >= regime_strong_adx):
            SHORT in TREND_UP, or LONG in TREND_DOWN → block.
        """
        strong = CONFIG.analyzer.regime_strong_adx
        if regime == Regime.CHOP:
            return "CHOP regime — no directional edge"
        if regime == Regime.UNKNOWN:
            return "UNKNOWN regime — insufficient regime signal"
        if regime == Regime.TREND_UP and direction == Direction.SHORT and adx >= strong:
            return f"counter-trend SHORT in strong TREND_UP (ADX {adx:.0f} >= {strong:.0f})"
        if regime == Regime.TREND_DOWN and direction == Direction.LONG and adx >= strong:
            return f"counter-trend LONG in strong TREND_DOWN (ADX {adx:.0f} >= {strong:.0f})"
        return None

    def _detect_regime(self, indicators: dict, symbol: str) -> Regime:
        """
        Classify market regime using ADX + directional indicators + squeeze,
        with a 3-reading confirmation window to prevent whipsaw flips.

        Raw classification logic (unchanged):
        ADX > 25 + DI+ > DI- → TREND_UP
        ADX > 25 + DI- > DI+ → TREND_DOWN
        ADX 20-30 + squeeze releasing → EXPANSION (breakout from compression)
        ADX < 20             → RANGE (mean-reversion favorable)
        ADX 20-25            → CHOP (no clear structure)

        Smoothing: requires 2/3 of the last readings for a symbol to agree
        before switching regime. If no consensus, keep the previous regime.
        """
        adx = indicators.get("adx", 0)
        plus_di = indicators.get("plus_di", 0)
        minus_di = indicators.get("minus_di", 0)
        squeeze = indicators.get("kc_squeeze", False)

        # -- Raw regime classification (same logic as before) --
        if squeeze and 18 <= adx <= 35:
            raw = Regime.EXPANSION
        elif adx > 25:
            raw = Regime.TREND_UP if plus_di > minus_di else Regime.TREND_DOWN
        elif adx < 20:
            raw = Regime.RANGE
        else:
            raw = Regime.CHOP

        # -- Persistence / smoothing --
        self._regime_history.append((symbol, raw.value))
        # Keep history bounded (max 3 entries per symbol is enough;
        # cap total list at 300 to avoid unbounded growth across symbols)
        if len(self._regime_history) > 300:
            self._regime_history = self._regime_history[-200:]

        # Gather last 3 readings for this symbol
        recent = [v for s, v in self._regime_history if s == symbol][-3:]

        # Check for consensus: 2 out of last 3 readings must agree
        from collections import Counter
        counts = Counter(recent)
        consensus_value, consensus_count = counts.most_common(1)[0]

        if consensus_count >= 2:
            smoothed = Regime(consensus_value)
        else:
            # No consensus — keep previous regime if we have one
            smoothed = self._current_regimes.get(symbol, raw)

        self._current_regimes[symbol] = smoothed
        return smoothed

    # -- Confluence Scoring --

    @staticmethod
    def _order_flow_opposition(order_flow, direction) -> tuple[float, float, float]:
        """Measure how strongly order flow opposes a chosen direction.

        Returns (opposition, of_confidence, of_bias):
          - of_bias: mean microstructure bias in [-1, 1] (+ = bullish)
          - opposition: in [0, 1]; >0 only when flow opposes ``direction``
          - of_confidence: the snapshot's own data confidence
        Aligned or absent flow returns opposition 0.0 — the guard never
        manufactures opposition where the evidence doesn't support it.
        """
        of_conf = float(getattr(order_flow, "confidence", 0.0) or 0.0)
        if order_flow is None or of_conf <= 0.0:
            return 0.0, 0.0, 0.0
        comps = getattr(order_flow, "components_ok", set()) or set()
        of_dir, n = 0.0, 0
        if "book" in comps:
            _book_val = float(np.clip(getattr(order_flow, "book_imbalance", 0.0), -1, 1))
            if math.isfinite(_book_val):
                of_dir += _book_val; n += 1
        if "trades" in comps:
            _cvd_val = {"rising": 1.0, "falling": -1.0, "flat": 0.0}.get(
                getattr(order_flow, "cvd_trend", "flat"), 0.0)
            if math.isfinite(_cvd_val):
                of_dir += _cvd_val; n += 1
        div = getattr(order_flow, "cvd_price_divergence", "none")
        if div == "bullish_div":
            of_dir += 1.0; n += 1
        elif div == "bearish_div":
            of_dir += -1.0; n += 1
        if n == 0:
            return 0.0, of_conf, 0.0
        of_dir /= n
        dir_sign = 1.0 if direction == Direction.LONG else -1.0
        opposition = max(0.0, -of_dir * dir_sign)
        return opposition, of_conf, of_dir

    @staticmethod
    def _score_confluence(indicators: dict, regime: Regime, signal: MarketSignal,
                          order_flow=None, mtf_result=None, smart_money_score=None,
                          mode_config=None, sentiment_engine=None,
                          strategy_type: str = "swing",
                          breakdown: "Optional[list]" = None) -> float:
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
        # Indices (into votes/weights) of the mean-reversion OSCILLATOR family:
        # RSI, Bollinger %B, Stochastic, Fibonacci. They all read "price is
        # low/high in its recent range", so they co-fire and over-count one
        # signal. CONFIG.confluence can cap their combined weight (see below).
        mr_osc_idx: list[int] = []

        def _mark_mr_osc() -> None:
            mr_osc_idx.append(len(votes) - 1)

        # Phase B: named per-voter capture. `aw` appends the SAME weight as
        # before plus a parallel name, so votes/weights/confluence are
        # byte-identical; `names` lets a learner attribute outcomes to voters.
        names: list[str] = []

        # Phase B2: optional learned per-voter weight multiplier. Resolved once
        # per call. Default OFF (or no fitted learner) -> `_vw_mult` is None and
        # `aw` is byte-identical. When ON, each voter's weight is scaled by its
        # bounded ([0.5,1.5]) learned multiplier.
        _vw_mult = None
        if CONFIG.analyzer.voter_weight_learning_enabled:
            try:
                from bot.learning.voter_weights import get_voter_learner
                _lw = get_voter_learner()
                if _lw is not None and _lw.is_ready():
                    _vw_mult = _lw.multiplier
            except Exception:
                _vw_mult = None

        def aw(weight: float, name: str) -> None:
            if _vw_mult is not None:
                weight = weight * _vw_mult(name)
            weights.append(weight)
            names.append(name)

        # IMPROVEMENT #1: activate strategy-mode confluence boosts.
        # Each mode amplifies the weight of the factors that matter for it
        # (e.g. MEAN_REVERSION boosts rsi/bb/cvd-divergence). When no mode or
        # no boost is configured the factor is 1.0 — i.e. behaviour is
        # identical to before, so this can never silently change the default.
        boost_map: dict[str, float] = (
            mode_config.confluence_boost
            if (mode_config is not None and getattr(mode_config, "confluence_boost", None))
            else {}
        )

        def _boost(weight: float, label: str) -> float:
            return weight * boost_map.get(label, 1.0)

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
        aw(_boost(1.5, "rsi"), "rsi")
        _mark_mr_osc()

        # MACD vote (weight 1.0)
        macd_hist = indicators.get("macd_histogram", 0)
        if macd_hist > 0:
            votes.append(1.0)
        elif macd_hist < 0:
            votes.append(-1.0)
        else:
            votes.append(0.0)
        aw(1.0, "macd")

        # Bollinger %B vote (weight 1.0)
        pct_b = indicators.get("bb_pct_b", 0.5)
        if pct_b < 0.2:
            votes.append(1.0)   # near lower band → bullish
        elif pct_b > 0.8:
            votes.append(-1.0)  # near upper band → bearish
        else:
            votes.append(0.0)
        aw(_boost(1.0, "bb_pct_b"), "bb_pct_b")
        _mark_mr_osc()

        # Volume spike vote (weight 0.8 — confirms directional moves)
        if signal.volume_spike:
            # Volume spike confirms the direction of the price move
            votes.append(1.0 if signal.change_pct_24h > 0 else -1.0)
        else:
            votes.append(0.0)
        aw(0.8, "volume_spike")

        # ADX trend strength vote (weight 0.7)
        adx = indicators.get("adx", 0)
        if adx > 30:
            votes.append(1.0 if indicators.get("plus_di", 0) > indicators.get("minus_di", 0) else -1.0)
        elif adx > 20:
            votes.append(0.3 if indicators.get("plus_di", 0) > indicators.get("minus_di", 0) else -0.3)
        else:
            votes.append(0.0)
        aw(0.7, "adx")

        # VWAP vote (weight 0.5 — institutional bias)
        vwap = indicators.get("vwap")
        if vwap is not None:
            if signal.price > vwap * 1.005:
                votes.append(1.0)   # above VWAP → bullish
            elif signal.price < vwap * 0.995:
                votes.append(-1.0)  # below VWAP → bearish
            else:
                votes.append(0.0)
            aw(0.5, "vwap")

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
            aw(0.6, "obv")

        # Candlestick pattern vote (weight 0.8 — price action signal)
        bull_count = indicators.get("candle_bullish_count", 0)
        bear_count = indicators.get("candle_bearish_count", 0)
        if bull_count > bear_count:
            votes.append(1.0)
            aw(0.8, "candlestick")
        elif bear_count > bull_count:
            votes.append(-1.0)
            aw(0.8, "candlestick")
        elif bull_count > 0 or bear_count > 0:
            votes.append(0.0)
            aw(0.4, "candlestick")

        # Fibonacci zone vote (weight 0.5 — mean-reversion near key levels)
        fib_zone = indicators.get("fib_zone")
        _fib_before = len(votes)
        if fib_zone in ("618_786", "below_786"):
            votes.append(1.0)   # deep retracement → bullish bounce potential
            aw(0.5, "fibonacci")
        elif fib_zone == "500_618":
            votes.append(0.5)   # moderate retracement → mildly bullish
            aw(0.5, "fibonacci")
        elif fib_zone == "above_236":
            votes.append(-0.3)  # near swing high → mildly bearish
            aw(0.5, "fibonacci")
        elif fib_zone is not None:
            votes.append(0.0)
            aw(0.3, "fibonacci")
        if len(votes) > _fib_before:
            _mark_mr_osc()

        # Chart patterns voter (weight 0.7 — geometric patterns from chart_patterns.py)
        # Votes based on bullish vs bearish pattern count, scaled by confidence
        geo_bull_w = indicators.get("chart_patterns_bullish_weight", 0)
        geo_bear_w = indicators.get("chart_patterns_bearish_weight", 0)
        geo_total = geo_bull_w + geo_bear_w
        if geo_total > 0:
            # Net vote scaled by imbalance: ranges from -1 to +1
            geo_vote = (geo_bull_w - geo_bear_w) / geo_total
            votes.append(geo_vote)
            # Scale weight by number of patterns detected (more patterns = stronger signal)
            geo_count = indicators.get("chart_patterns_bullish_count", 0) + \
                        indicators.get("chart_patterns_bearish_count", 0)
            scaled_weight = min(1.0, 0.7 * min(geo_count, 3))  # cap at 3 patterns
            aw(scaled_weight, "chart_patterns")

        # ── Divergence Scanner voter ──
        div_votes = indicators.get("_div_votes")
        div_weights = indicators.get("_div_weights")
        if div_votes and div_weights:
            votes.extend(div_votes)
            weights.extend(div_weights)
            names.extend(["divergence"] * len(div_weights))

        # ── Volume Profile voter ──
        vp = indicators.get("_vp_result")
        if vp is not None:
            try:
                # Use net bullish/bearish vote based on price vs POC
                vp_vote = 0.0
                if hasattr(vp, 'price_vs_poc'):
                    if vp.price_vs_poc == "above":
                        vp_vote = 0.5   # price above POC → bullish bias
                    elif vp.price_vs_poc == "below":
                        vp_vote = -0.5  # price below POC → bearish bias
                if hasattr(vp, 'price_in_value_area') and vp.price_in_value_area:
                    # Inside value area = mean-reversion zone, dampen signal
                    vp_vote *= 0.5
                if abs(vp_vote) > 0:
                    votes.append(vp_vote)
                    aw(0.6, "volume_profile")
            except Exception as _vp_exc:
                logger.warning("Volume profile confluence vote failed: %s", _vp_exc)

        # Stochastic voter (weight 1.2 — momentum + mean-reversion)
        stoch_k = indicators.get("stoch_k")
        stoch_d = indicators.get("stoch_d")
        if stoch_k is not None and stoch_d is not None:
            stoch_vote = 0.0
            if stoch_k < 20 and stoch_d < 20:
                stoch_vote = 1.0   # oversold → bullish
            elif stoch_k > 80 and stoch_d > 80:
                stoch_vote = -1.0  # overbought → bearish
            elif indicators.get("stoch_cross_up"):
                stoch_vote = 0.7   # bullish crossover
            elif indicators.get("stoch_cross_down"):
                stoch_vote = -0.7  # bearish crossover
            elif indicators.get("stoch_bull_div"):
                stoch_vote = 0.8   # bullish divergence
            elif indicators.get("stoch_bear_div"):
                stoch_vote = -0.8  # bearish divergence
            elif stoch_k < 40:
                stoch_vote = 0.3
            elif stoch_k > 60:
                stoch_vote = -0.3
            votes.append(stoch_vote)
            aw(_boost(1.2, "stoch"), "stoch")
            _mark_mr_osc()

        # Donchian Channel voter (weight 1.0 — Turtle Breakout)
        dc_breakout_high = indicators.get("dc_breakout_high", False)
        dc_breakout_low = indicators.get("dc_breakout_low", False)
        dc_position = indicators.get("dc_position")
        if dc_position is not None:
            dc_vote = 0.0
            if dc_breakout_high:
                dc_vote = 1.0    # 20-bar high breakout → strong bullish
            elif dc_breakout_low:
                dc_vote = -1.0   # 20-bar low breakout → strong bearish
            elif dc_position > 0.8:
                dc_vote = 0.5    # near top of channel
            elif dc_position < 0.2:
                dc_vote = -0.5   # near bottom of channel
            # Boost if 55-period confirms
            if indicators.get("dc55_breakout_high"):
                dc_vote = min(1.0, dc_vote + 0.3)
            elif indicators.get("dc55_breakout_low"):
                dc_vote = max(-1.0, dc_vote - 0.3)
            votes.append(dc_vote)
            aw(_boost(1.0, "donchian"), "donchian")

        # Reversal signal voter (weight 0.9 — pin bars, inside bars, capitulation)
        rev_vote = 0.0
        rev_has_signal = False
        if indicators.get("pin_bar_bullish"):
            rev_vote += 0.7
            rev_has_signal = True
        if indicators.get("pin_bar_bearish"):
            rev_vote -= 0.7
            rev_has_signal = True
        if indicators.get("capitulation_sell"):
            rev_vote += 0.9  # capitulation selling = contrarian bullish
            rev_has_signal = True
        if indicators.get("capitulation_buy"):
            rev_vote -= 0.9  # euphoric buying = contrarian bearish
            rev_has_signal = True
        if indicators.get("inside_bar"):
            # Inside bar: neutral, but indicates compression → breakout coming
            rev_has_signal = True
        if rev_has_signal:
            votes.append(max(-1.0, min(1.0, rev_vote)))
            aw(_boost(0.9, "reversal"), "reversal")

        # Wyckoff phase voter (weight 0.8 — accumulation/distribution cycle)
        wyckoff = indicators.get("wyckoff_pattern")
        if wyckoff:
            w_signal = wyckoff.get("signal", "neutral")
            w_conf = wyckoff.get("confidence", 0.5)
            if w_signal == "bullish":
                votes.append(w_conf)
                aw(0.8, "wyckoff")
            elif w_signal == "bearish":
                votes.append(-w_conf)
                aw(0.8, "wyckoff")
            else:
                votes.append(0.0)
                aw(0.4, "wyckoff")

        # Harmonic pattern voter (weight 0.75 — Gartley/Butterfly/Bat/Crab)
        harmonic = indicators.get("harmonic_pattern")
        if harmonic:
            h_signal = harmonic.get("signal", "neutral")
            h_conf = harmonic.get("confidence", 0.5)
            if h_signal == "bullish":
                votes.append(h_conf)
                aw(0.75, "harmonic")
            elif h_signal == "bearish":
                votes.append(-h_conf)
                aw(0.75, "harmonic")
            else:
                votes.append(0.0)
                aw(0.35, "harmonic")

        # Elliott Wave voters — separate votes for each wave type detected.
        # Impulse (strongest trend signal), Corrective (context), Diagonal (reversal),
        # WXY (extended consolidation). Each gets its own weight so multiple wave
        # structures reinforce rather than overwrite each other.
        _elliott_types = [
            ("elliott_impulse", 0.75, "ew_impulse"),      # 5-wave impulse = strong trend
            ("elliott_corrective", 0.60, "ew_corrective"), # ABC correction = context
            ("elliott_diagonal", 0.65, "ew_diagonal"),     # diagonal = reversal warning
            ("elliott_wxy", 0.55, "ew_wxy"),               # complex correction = consolidation
        ]
        for ew_key, ew_weight, ew_label in _elliott_types:
            ew = indicators.get(ew_key)
            if ew:
                e_signal = ew.get("signal", "neutral")
                e_conf = ew.get("confidence", 0.5)
                if e_signal == "bullish":
                    votes.append(e_conf)
                    aw(_boost(ew_weight, ew_label), ew_label)
                elif e_signal == "bearish":
                    votes.append(-e_conf)
                    aw(_boost(ew_weight, ew_label), ew_label)
                else:
                    votes.append(0.0)
                    aw(0.25, ew_label)

        # Legacy fallback: if no typed Elliott found, use generic key
        if not any(indicators.get(k) for k, _, _ in _elliott_types):
            elliott = indicators.get("elliott_pattern")
            if elliott:
                e_signal = elliott.get("signal", "neutral")
                e_conf = elliott.get("confidence", 0.5)
                if e_signal == "bullish":
                    votes.append(e_conf)
                    aw(0.65, "elliott")
                elif e_signal == "bearish":
                    votes.append(-e_conf)
                    aw(0.65, "elliott")
                else:
                    votes.append(0.0)
                    aw(0.3, "elliott")

        # Order flow votes (if available)
        if order_flow is not None:
            of_votes, of_weights, of_labels = OrderFlowAnalyzer.to_confluence_votes(order_flow)
            votes += of_votes
            weights += [_boost(w, l) for w, l in zip(of_weights, of_labels)]
            names.extend(of_labels)

        # Multi-timeframe votes (if available)
        if mtf_result is not None:
            mtf_votes, mtf_weights, mtf_labels = MTFConfluence.to_confluence_votes(mtf_result)
            votes += mtf_votes
            weights += [_boost(w, l) for w, l in zip(mtf_weights, mtf_labels)]
            names.extend(mtf_labels)

        # Smart money votes (if available)
        if smart_money_score is not None:
            sm_votes, sm_weights, sm_labels = SmartMoneyEngine.to_confluence_votes(smart_money_score)
            # Per-strategy-type weight adjustment for smart money signals
            sm_weight_mult = CONFIG.strategy_types.get_smart_money_weight(strategy_type)
            votes += sm_votes
            weights += [_boost(w * sm_weight_mult, l) for w, l in zip(sm_weights, sm_labels)]
            names.extend(sm_labels)

        # Sentiment voter
        if sentiment_engine is not None:
            try:
                sentiment_votes = sentiment_engine.to_confluence_votes()
                for _name, vote_val, vote_weight in sentiment_votes:
                    votes.append(vote_val)
                    aw(vote_weight, "sentiment")
            except Exception as _sent_exc:
                logger.warning("Sentiment engine vote failed: %s", _sent_exc)

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
                aw(0.6, "poc_magnet")

        # Liquidity Sweep voter (weight up to 1.2 — high-probability reversal)
        sweep_v = indicators.get("_sweep_votes")
        sweep_w = indicators.get("_sweep_weights")
        if sweep_v and sweep_w:
            votes.extend(sweep_v)
            weights.extend(sweep_w)
            names.extend(["liquidity_sweep"] * len(sweep_w))

        # Supply/Demand Zone voter
        sd_zones = indicators.get("_sd_zones")
        if sd_zones:
            try:
                # Need to determine trade direction from current votes
                pre_sum = sum(v * w for v, w in zip(votes, weights))
                approx_dir = "LONG" if pre_sum >= 0 else "SHORT"
                sd_v, sd_w = zones_to_confluence(sd_zones, signal.price, approx_dir)
                votes.extend(sd_v)
                weights.extend(sd_w)
                names.extend(["supply_demand"] * len(sd_w))
            except Exception as _sd_exc:
                logger.warning("Supply/demand zone confluence vote failed: %s", _sd_exc)

        # Volatility Squeeze voter (boost for fired squeeze)
        squeeze_sig = indicators.get("_squeeze_signal")
        if squeeze_sig is not None and hasattr(squeeze_sig, 'squeeze_fired') and squeeze_sig.squeeze_fired:
            sq_vote = 0.8 if squeeze_sig.fire_direction == "bullish" else -0.8
            votes.append(sq_vote)
            aw(0.9, "squeeze")
        elif squeeze_sig is not None and hasattr(squeeze_sig, 'is_squeezing') and squeeze_sig.is_squeezing:
            # Currently squeezing — breakout imminent, mild directional bias from momentum
            if hasattr(squeeze_sig, 'momentum'):
                sq_vote = 0.3 if squeeze_sig.momentum > 0 else -0.3
                votes.append(sq_vote)
                aw(0.5, "squeeze")

        # EMA ribbon voter
        ema9 = indicators.get("ema_9")
        ema21 = indicators.get("ema_21")
        if ema9 is not None and ema21 is not None:
            if ema9 > ema21:
                votes.append(0.6)
                aw(0.5, "ema_ribbon")
            elif ema9 < ema21:
                votes.append(-0.6)
                aw(0.5, "ema_ribbon")
            else:
                votes.append(0.0)
                aw(0.5, "ema_ribbon")

        # Keltner squeeze voter (volatility compression = breakout imminent)
        squeeze = indicators.get("kc_squeeze", False)
        if squeeze:
            # Squeeze detected — direction from MACD histogram
            macd_hist_val = indicators.get("macd_histogram", 0)
            if macd_hist_val > 0:
                votes.append(0.5)
                aw(0.7, "keltner")
            elif macd_hist_val < 0:
                votes.append(-0.5)
                aw(0.7, "keltner")
            else:
                votes.append(0.0)
                aw(0.7, "keltner")

        # Taker buy/sell imbalance voter
        taker_buy_ratio = indicators.get("taker_buy_ratio", 0.5)
        if taker_buy_ratio > 0.55:
            votes.append(0.5)
            aw(0.5, "taker")
        elif taker_buy_ratio < 0.45:
            votes.append(-0.5)
            aw(0.5, "taker")

        # IMPROVEMENT #1: boosts are now applied inline at each voter via
        # _boost(weight, label) using the labels the sub-engines return.

        # LB-2 FIX: votes/weights must be aligned before computation.
        # zip() silently truncates to the shorter list, hiding mismatches.
        if len(votes) != len(weights):
            raise ValueError(
                f"Confluence votes/weights desync: {len(votes)} votes vs {len(weights)} weights"
            )

        # ── Mean-reversion oscillator de-correlation (CONFIG.confluence) ──
        # RSI/%B/Stoch/Fib co-fire on the same "price low/high in range" signal.
        # Cap the COMBINED weight of the ones that ACTUALLY cast a directional
        # vote so a cluster of correlated oscillators counts as ~one strong voter
        # instead of inflating confluence as four independent confirmations.
        # Only the actively-voting members are considered (a lone signalling
        # oscillator over-counts nothing, so it is never penalised), and the cap
        # only ever REDUCES weight.
        if CONFIG.confluence.family_cap_enabled:
            active = [i for i in mr_osc_idx if abs(votes[i]) > 1e-9]
            if len(active) > 1:
                fam_weight = sum(weights[i] for i in active)
                cap = CONFIG.confluence.mr_oscillator_weight_cap
                if fam_weight > cap > 0:
                    scale = cap / fam_weight
                    for i in active:
                        weights[i] *= scale

        # Phase B: emit the named per-voter breakdown (best-effort; only when
        # fully aligned). Does not affect the confluence value.
        if breakdown is not None and len(names) == len(votes) == len(weights):
            breakdown.extend((names[i], votes[i], weights[i]) for i in range(len(votes)))

        # Weighted confluence
        total_weight = sum(weights)
        if total_weight == 0:
            return 0.5

        weighted_sum = sum(v * w for v, w in zip(votes, weights))
        # Normalize to [0, 1]: -total_weight → 0, +total_weight → 1
        confluence = (weighted_sum / total_weight + 1) / 2
        return round(max(0.0, min(1.0, confluence)), 4)

    # -- LLM Reasoning --

    async def _llm_thesis(self, signal: MarketSignal, indicators: dict, order_flow=None, is_admin: bool = False, user_id=None, user_tier=None, as_of=None) -> Optional[dict]:
        """Ask the LLM for a directional call with reasoning.

        Token optimization pipeline:
          1. Semantic cache check -- return cached response if available
          2. Adaptive frequency -- skip LLM for quiet markets
          3. Tiered pipeline -- route to rules/mini/full based on signal quality
          4. Budget guards -- fall back to rules if limits exceeded
          5. LLM call with rate limiting
          6. Cache the response for future use
        """
        # Offline thesis hook (backtest parity): when set, replay a recorded LLM
        # thesis deterministically instead of calling the network, so the
        # backtest exercises the SAME blended path live uses. Returns the
        # recorded thesis, or falls back to the rule engine when no record
        # exists for this (symbol, time).
        if getattr(self, "_offline_thesis_fn", None) is not None:
            rec = self._offline_thesis_fn(signal, indicators, as_of)
            if rec is not None:
                return rec
            result = self._rule_based_thesis(signal, indicators)
            if result is not None:
                result["source"] = "RULE_ENGINE_NO_RECORD"
            return result

        if self._llm is None:
            result = self._rule_based_thesis(signal, indicators)
            if result is None:
                return None
            result["source"] = "RULE_ENGINE"
            return result

        # ── Optimization 1: Semantic Cache ──
        # Scope the key by the answering model's routing identity (default OFF →
        # byte-identical) so an admin/premium/BYOK thesis (or a tier-1 rule
        # result) is never served to a basic user with the same buckets.
        cache_scope = ""
        if getattr(CONFIG.analyzer, "llm_cache_scoped_key", False):
            cache_scope = self._llm_cache_scope(
                signal, indicators, is_admin, user_id, user_tier)
        cache_key = SemanticLLMCache.build_cache_key(
            signal.symbol, indicators, scope=cache_scope)
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
            if result is None:
                return None
            result["source"] = "RULE_ENGINE_ADAPTIVE"
            return result

        # ── Optimization 3: Tiered Pipeline ──
        tier = TieredPipeline.classify_tier(indicators, signal)
        self._opt_stats.record_tier(tier)

        if tier == 1:
            # Tier 1: Rule engine handles clear-cut signals (FREE)
            result = self._rule_based_thesis(signal, indicators)
            if result is None:
                return None
            result["source"] = "RULE_ENGINE_TIER1"
            # #42: do NOT cache the tier-1 rule result under the LLM cache key.
            # The key is built from coarse indicator buckets, so a later signal
            # that buckets to the same key but classifies tier 2/3 would hit this
            # entry and be served a cheap rule thesis as "LLM_CACHED" instead of
            # running the LLM. Rule computation is free, so recomputing on the
            # rare tier-1 collision costs nothing and keeps the cache LLM-only.
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
            if result is None:
                return None
            result["source"] = "RULE_ENGINE_BUDGET"
            return result

        # Dollar budget guard: fall back to rules when daily spend exceeded
        if self._cost is not None:
            snap = self._cost.snapshot()
            if snap.llm_cost_usd >= CONFIG.llm.daily_budget_usd:
                audit(trade_log, f"LLM daily dollar budget exhausted (${snap.llm_cost_usd:.4f} >= ${CONFIG.llm.daily_budget_usd}), using rules",
                      action="analyze", result="LLM_BUDGET_USD")
                result = self._rule_based_thesis(signal, indicators)
                if result is None:
                    return None
                result["source"] = "RULE_ENGINE_BUDGET"
                return result

        prompt = self._build_prompt(signal, indicators, order_flow)

        # Hash prompt for calibration replay (cheap — ~0.01ms)
        import hashlib as _hl
        _prompt_hash = _hl.sha256(prompt.encode()).hexdigest()[:16]

        # Tier-based model routing:
        #   Tier 2 → scan model (cheap/fast — e.g. Qwen for non-admin, Sonnet for admin)
        #   Tier 3 → thesis model (strong reasoning — e.g. Sonnet for all)
        use_full_model = tier == 3
        # Multi-tier routing: select client based on admin status
        if is_admin:
            # Admin: use premium clients for all tiers
            if use_full_model and self._admin_thesis_client is not None:
                active_client = self._admin_thesis_client
                active_cfg = self._admin_thesis_config
                model = self._admin_thesis_config.model
            elif not use_full_model and self._admin_scan_client is not None:
                active_client = self._admin_scan_client
                active_cfg = self._admin_scan_config
                model = self._admin_scan_config.model
            else:
                active_client = self._llm
                active_cfg = self._resolve_llm_config()
                model = self.THESIS_MODEL if use_full_model else self.SCAN_MODEL
        else:
            # Non-admin: use cheap tier-specific clients
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

        # Per-user BYOK routing (opt-in, default OFF): for a command the user ran
        # by hand, route the thesis through THEIR own provider key. Fail-open —
        # (None, None) leaves the operator client selected above untouched.
        _user_client, _user_cfg = self._maybe_user_client(user_id)
        if _user_client is not None:
            active_client = _user_client
            active_cfg = _user_cfg
            model = _user_cfg.model
            audit(trade_log,
                  f"Per-user LLM routing: user={user_id} "
                  f"{_user_cfg.provider.value}/{model}",
                  action="per_user_llm", result="ROUTED",
                  data={"user_id": str(user_id), "provider": _user_cfg.provider.value})
        else:
            # No BYOK key → route by the user's TIER to operator-funded premium
            # models (elite/pro/admin); basic/free keep the default. Fail-open.
            _tier_client, _tier_cfg, _tier_model = self._maybe_tier_client(user_tier, use_full_model)
            if _tier_client is not None:
                active_client = _tier_client
                active_cfg = _tier_cfg
                model = _tier_model
                audit(trade_log,
                      f"Tier LLM routing: tier={user_tier} "
                      f"{_tier_cfg.provider.value}/{model}",
                      action="tier_llm", result="ROUTED",
                      data={"user_tier": str(user_tier), "provider": _tier_cfg.provider.value})

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

            # Enhanced system prompt for Opus/Sonnet — structured for best trade analysis
            _TRADING_SYSTEM_PROMPT = (
                "You are RUNECLAW, an elite crypto trading analyst operating on Bitget Futures.\n\n"
                "## Your Role\n"
                "Analyze market data and produce actionable trade ideas with precise entries, "
                "stop-losses, and take-profit levels. You are risk-first: capital preservation "
                "always outweighs potential gains.\n\n"
                "## Analysis Framework\n"
                "1. TREND: Identify the dominant trend on the given timeframe using price action, "
                "SMAs, and momentum indicators\n"
                "2. STRUCTURE: Find key support/resistance levels, VWAP, and Fibonacci zones\n"
                "3. CONFLUENCE: Count how many independent signals align (RSI, volume, patterns, "
                "order flow). Require 3+ for a trade idea.\n"
                "4. RISK: Calculate R:R ratio. Minimum 1.2:1. SL must be at a logical invalidation "
                "point, not an arbitrary percentage.\n"
                "5. CONVICTION: Score 0.0-1.0 based on confluence strength, not gut feeling.\n\n"
                "## Output Format\n"
                "Return a JSON object with these exact keys:\n"
                "- direction: \"LONG\" or \"SHORT\"\n"
                "- confidence: float 0.0-1.0\n"
                "- entry_price: float (current market price or limit entry)\n"
                "- stop_loss: float (below entry for LONG, above for SHORT)\n"
                "- take_profit: float (above entry for LONG, below for SHORT)\n"
                "- reasoning: string (2-3 sentences citing specific indicators and levels)\n"
                "- signals_used: array of strings (indicator names that contributed)\n"
                "- order_type: \"market\" or \"limit\"\n\n"
                "If no clear setup exists, return: {\"direction\": null, \"confidence\": 0.0, "
                "\"reasoning\": \"No actionable setup — [specific reason]\"}\n\n"
                "## Rules\n"
                "- Never force a trade. \"No trade\" is a valid and often correct answer.\n"
                "- Use exact prices from the data provided, not rounded approximations.\n"
                "- SL distance should be ATR-based (1.5-3x ATR from entry).\n"
                "- TP distance should be at least 1.2x the SL distance.\n"
                "- Confidence below 0.55 means skip the trade.\n"
            )

            sys_content = (
                "You are RUNECLAW, a risk-first crypto analyst. "
                "Return concise analysis in json format with keys: direction, confidence, reasoning."
                if use_json_format else
                _TRADING_SYSTEM_PROMPT
            )

            if sdk_type == "anthropic":
                # Use unified llm_complete for Anthropic (different API format)
                # Track usage from Anthropic response
                messages = [{"role": "user", "content": prompt}]

                # Apply prompt caching to system prompt — saves 90% on input costs
                # for repeated calls with the same system prompt
                system_content = [
                    {
                        "type": "text",
                        "text": sys_content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]

                # Use extended thinking for Opus on thesis-tier calls
                create_kwargs = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system_content,
                    "messages": messages,
                }

                # Enable adaptive thinking for Opus 4.8+ (thesis tier)
                # Opus 4.8 ONLY supports adaptive thinking; manual budget_tokens
                # returns 400.  Adaptive lets the model decide how much to think.
                if use_full_model and "opus" in model.lower():
                    create_kwargs["thinking"] = {"type": "adaptive"}
                    # Structured output: guarantee valid JSON from the LLM
                    create_kwargs["output_config"] = {
                        "format": {
                            "type": "json_schema",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "direction": {
                                        "type": "string",
                                        "enum": ["LONG", "SHORT"],
                                    },
                                    "confidence": {"type": "number"},
                                    "reasoning": {"type": "string"},
                                },
                                "required": ["direction", "confidence", "reasoning"],
                                "additionalProperties": False,
                            },
                        }
                    }

                response = await active_client.messages.create(**create_kwargs)
                # Handle extended thinking response — extract text block (skip thinking blocks)
                raw_text = ""
                if response.content:
                    for block in response.content:
                        if getattr(block, "type", "") == "text":
                            raw_text = block.text
                            break
                    if not raw_text:
                        raw_text = response.content[0].text if hasattr(response.content[0], "text") else ""
                self._llm_calls_today += 1
                # Anthropic returns usage in response.usage
                _usage = getattr(response, "usage", None)
                if _usage is not None and self._cost is not None:
                    self._cost.record_llm(
                        model=model,
                        prompt_tokens=getattr(_usage, "input_tokens", 0) or 0,
                        completion_tokens=getattr(_usage, "output_tokens", 0) or 0,
                        symbol=signal.symbol,
                        category=category,
                    )
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
                audit(trade_log, "LLM response could not be parsed, blocking trade",
                      action="analyze", result="LLM_PARSE_FAIL",
                      data={"raw_text": raw_text[:200]})
                return None  # C-07 FIX: do not default to LONG on parse failure
            result["source"] = f"LLM_{tier_label}"
            result["model_used"] = model
            result["prompt_hash"] = _prompt_hash

            # ── Cache the LLM response ──
            self._llm_cache.put(cache_key, result, signal.symbol)

            return result
        except Exception as exc:
            audit(trade_log, f"LLM error on primary provider, trying fallback: {exc}",
                  action="analyze", result="LLM_FAIL")
            # ── Cascading fallback: try alternate providers before rules ──
            # Pass the actual provider that failed (may differ from global primary
            # when tier routing is active, e.g. SCAN tier → Gemini, not Anthropic)
            failed_provider = None
            if active_cfg and hasattr(active_cfg, 'provider'):
                failed_provider = (
                    active_cfg.provider.value
                    if isinstance(active_cfg.provider, LLMProvider)
                    else str(active_cfg.provider)
                )
            fallback_result = await self._try_llm_fallback(prompt, signal, use_full_model, failed_provider=failed_provider)
            if fallback_result is not None:
                fallback_result["source"] = f"LLM_FALLBACK_{fallback_result.get('_fallback_provider', 'UNKNOWN')}"
                fallback_result.pop("_fallback_provider", None)
                self._llm_cache.put(cache_key, fallback_result, signal.symbol)
                return fallback_result
            result = self._rule_based_thesis(signal, indicators)
            if result is None:
                return None
            result["source"] = "RULE_ENGINE_FALLBACK"
            return result

    async def _try_llm_fallback(
        self,
        prompt: str,
        signal: MarketSignal,
        use_full_model: bool,
        failed_provider: Optional[str] = None,
    ) -> Optional[dict]:
        """Try alternate LLM providers when the primary fails (rate limit, error).

        Cascading order:
          1. Gemini (free tier, high quota)
          2. Groq (free tier, fast)
          3. Anthropic (paid, high quality)
          4. DeepSeek (cheap, good quality)

        Skips the provider that actually failed (which may differ from the global
        primary when tier routing is active — e.g. SCAN tier uses Gemini while
        global primary is Anthropic).
        Returns parsed result dict or None if all fallbacks fail.
        """
        import os as _os

        # Use the explicitly-passed failed provider if available,
        # otherwise fall back to global primary (backward compat)
        skip_provider = failed_provider
        if not skip_provider and self._llm_config:
            skip_provider = (
                self._llm_config.provider.value
                if isinstance(self._llm_config.provider, LLMProvider)
                else str(self._llm_config.provider)
            )

        # Build fallback chain — skip the provider that actually failed
        fallback_chain = [
            (LLMProvider.ALIBABA, "ALIBABA_API_KEY", "qwen3.6-flash"),
            (LLMProvider.GEMINI, "GEMINI_API_KEY", "gemini-2.0-flash"),
            (LLMProvider.GROQ, "GROQ_API_KEY", "llama-3.3-70b-versatile"),
            (LLMProvider.ANTHROPIC, "ANTHROPIC_API_KEY", "claude-sonnet-4-6"),
            (LLMProvider.DEEPSEEK, "DEEPSEEK_API_KEY", "deepseek-chat"),
        ]

        for provider, key_env, default_model in fallback_chain:
            if provider.value == skip_provider:
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
                    # llm_complete discards usage → estimate from char length so
                    # the dollar guard still sees (approximate) fallback spend.
                    _pt = self._estimate_tokens(sys_content + prompt)
                    _ct = self._estimate_tokens(raw_text or "")
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
                    _u = getattr(resp, "usage", None)
                    _pt = (getattr(_u, "prompt_tokens", 0) or 0) if _u is not None \
                        else self._estimate_tokens(sys_content + prompt)
                    _ct = (getattr(_u, "completion_tokens", 0) or 0) if _u is not None \
                        else self._estimate_tokens(raw_text or "")

                # Account this billable fallback call against the daily budgets
                # (deep-audit medium). Done here — after a successful API response,
                # before the parse check — so every billable round-trip counts,
                # mirroring the primary path. Gated default-OFF → byte-identical.
                if getattr(CONFIG.llm, "fallback_cost_accounting_enabled", False):
                    self._llm_calls_today += 1
                    if self._cost is not None:
                        self._cost.record_llm(
                            model=default_model,
                            prompt_tokens=_pt,
                            completion_tokens=_ct,
                            symbol=signal.symbol,
                            category="thesis" if use_full_model else "analyze",
                        )

                result = self._parse_llm_response(raw_text or "")
                # C-07 FIX (fallback path): block trade on parse failure,
                # same as the primary provider path.  Without this check,
                # direction=None slips through and implicitly becomes SHORT.
                if not result.pop("_parsed", False):
                    audit(trade_log,
                          f"LLM fallback {provider.value} returned unparseable response, blocking trade",
                          action="llm_fallback", result="LLM_PARSE_FAIL",
                          data={"provider": provider.value, "model": default_model,
                                "raw_text": (raw_text or "")[:200]})
                    continue  # try next fallback provider
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

    # -- E8: VWAP Resilience Ranker --

    @staticmethod
    def rank_vwap_resilience(results: list[dict], direction: str = "long") -> list[dict]:
        """E8: Rank symbols by VWAP resilience score.

        resilience_score = (price - VWAP) / VWAP * 100

        For LONG candidates: sort descending (closest to/above VWAP = first to flip)
        For SHORT candidates: sort ascending (most below VWAP = most damaged)

        Args:
            results: list of dicts with at least {symbol, price, vwap}
            direction: "long" or "short"

        Returns:
            Ranked list of dicts with: symbol, price, vwap, vwap_gap_pct, rank
        """
        ranked = []
        for r in results:
            price = r.get("price", 0)
            vwap = r.get("vwap", 0)
            symbol = r.get("symbol", "???")
            if not vwap or vwap <= 0 or not price or price <= 0:
                continue
            gap_pct = (price - vwap) / vwap * 100
            ranked.append({
                "symbol": symbol,
                "price": round(price, 6),
                "vwap": round(vwap, 6),
                "vwap_gap_pct": round(gap_pct, 2),
                "book_bias": r.get("book_imbalance", 0),
                "rsi": r.get("rsi", None),
            })

        reverse = direction.lower() == "long"  # descending for longs, ascending for shorts
        ranked.sort(key=lambda x: x["vwap_gap_pct"], reverse=reverse)

        for i, item in enumerate(ranked):
            item["rank"] = i + 1

        return ranked

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

        # Add regime-aware instruction to guide the LLM toward trend-aligned trades
        regime_str = indicators.get('regime', 'UNKNOWN')
        regime_hint = ""
        if regime_str == "TREND_UP":
            regime_hint = "IMPORTANT: Regime is TREND_UP — strongly prefer LONG setups. Only suggest SHORT if overwhelming bearish evidence exists."
        elif regime_str == "TREND_DOWN":
            regime_hint = "IMPORTANT: Regime is TREND_DOWN — strongly prefer SHORT setups. Only suggest LONG if overwhelming bullish evidence exists."
        elif regime_str in ("RANGE", "CHOP"):
            regime_hint = "Regime is RANGE/CHOP — look for mean-reversion setups at extremes. Avoid trend-following."

        if regime_hint:
            parts.append(regime_hint)

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
        result: dict = {"direction": None, "confidence": 0.0, "reasoning": "", "_parsed": False}

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
                d = str(data.get("direction", data.get("DIRECTION", ""))).upper()
                if "SHORT" in d:
                    result["direction"] = "SHORT"
                elif "LONG" in d:
                    result["direction"] = "LONG"
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
                if "SHORT" in rest.upper():
                    result["direction"] = "SHORT"
                elif "LONG" in rest.upper():
                    result["direction"] = "LONG"
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
        result["_parsed"] = parsed_fields >= 2 and result["direction"] is not None
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
        # EXPANSION regime narrows the ambiguous zone — these are high-probability setups
        is_expansion = regime == "EXPANSION"
        bull_thresh = 0.52 if is_expansion else 0.55
        bear_thresh = 0.48 if is_expansion else 0.45

        if confluence > bull_thresh:
            direction = "LONG"
        elif confluence < bear_thresh:
            direction = "SHORT"
        elif rsi < 35:
            direction = "LONG"
        elif rsi > 65:
            direction = "SHORT"
        elif macd_hist > 0 and confluence >= 0.50:
            direction = "LONG"   # MACD tiebreaker: positive histogram + slight bullish lean
        elif macd_hist < 0 and confluence <= 0.50:
            direction = "SHORT"  # MACD tiebreaker: negative histogram + slight bearish lean
        else:
            return None  # ambiguous confluence + neutral RSI + no MACD signal -- no signal

        # Confidence from confluence strength + regime clarity
        conf_base = abs(confluence - 0.5) * 2  # 0-1 scale of confluence strength
        regime_bonus = 0.1 if regime in ("TREND_UP", "TREND_DOWN", "EXPANSION") else 0
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


