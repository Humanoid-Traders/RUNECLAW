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
import os
import re
import time
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


# ── Advanced Elliott Wave recompute (gated) ──────────────────────
_ELLIOTT_KEYS = ("elliott_impulse", "elliott_corrective", "elliott_diagonal",
                 "elliott_wxy", "elliott_pattern")


# Analyzer schema/version tag stamped into every TradeIdea (audit fix #18).
# Bump when the signal pipeline changes in a way that affects comparability
# of recorded decisions (voters, blending, SL/TP logic).
_ANALYSIS_VERSION = "2026.07-audit25"


def _is_gated_signal_type(signal_type: str, skip_config: str) -> bool:
    """True if ``signal_type`` is in the comma-separated ``SKIP_SIGNAL_TYPES``
    set — an evidence-gated skip for a negative-edge family. Empty config skips
    nothing (trade every family)."""
    if not skip_config:
        return False
    return signal_type in {s.strip() for s in skip_config.split(",") if s.strip()}


def _floor_stop_distance(entry: float, stop_loss: float, direction,
                         min_frac: float) -> float:
    """Widen a too-tight stop to a minimum placeable distance from entry.

    ``|entry - stop_loss|`` is floored to ``min_frac * entry`` (safety —
    prevents an ATR stop that the venue rejects or noise trips instantly).
    Never tightens; a stop already beyond the floor is returned unchanged.
    ``min_frac <= 0`` disables it. See ``AnalyzerConfig.min_stop_distance_pct``.
    """
    if min_frac <= 0 or entry <= 0:
        return stop_loss
    min_dist = min_frac * entry
    if abs(entry - stop_loss) >= min_dist:
        return stop_loss
    return (entry - min_dist) if direction == Direction.LONG else (entry + min_dist)


def _apply_elliott_wave_targets(direction, entry: float, stop_loss: float,
                                take_profit: float, indicators: dict) -> tuple:
    """Wave-anchor the stop and (optionally) extend the target using the
    Elliott impulse's Fibonacci projections. SAFETY: SL is only ever *tightened*
    (moved closer to entry, reducing risk), never loosened; TP is only extended
    further in-favour, never pulled in. Direction must match the impulse signal.
    Returns the (possibly adjusted) ``(stop_loss, take_profit)``. Fail-open.
    """
    from bot.core.elliott import project_targets
    imp = indicators.get("elliott_impulse")
    if not imp:
        return stop_loss, take_profit
    is_long = str(getattr(direction, "value", direction)).upper() == "LONG"
    sig = imp.get("signal")
    if (is_long and sig != "bullish") or (not is_long and sig != "bearish"):
        return stop_loss, take_profit  # impulse disagrees with the trade side
    t = project_targets(imp)
    if not t:
        return stop_loss, take_profit

    inval = t.get("invalidation")
    if inval is not None and inval > 0:
        # Only tighten: for a LONG, a higher invalidation than the ATR stop is
        # tighter (less risk); it must still sit below entry and not hug it.
        if is_long and stop_loss < inval < entry * 0.999:
            stop_loss = float(inval)
        elif (not is_long) and entry * 1.001 < inval < stop_loss:
            stop_loss = float(inval)

    tp2 = t.get("tp2")
    if tp2 is not None and tp2 > 0:
        # Only extend further in-favour than the ATR target.
        if is_long and tp2 > take_profit:
            take_profit = float(tp2)
        elif (not is_long) and tp2 < take_profit:
            take_profit = float(tp2)
    return stop_loss, take_profit


def _run_elliott_detectors(indicators: dict, highs, lows, closes, swings) -> None:
    """Run the 4 Elliott detectors with the given swings and replace the
    elliott_* indicator set. Shared by the ZigZag and timeframe-matched paths.
    """
    from bot.core.chart_patterns import (
        detect_elliott_impulse, detect_elliott_corrective,
        detect_elliott_diagonal, detect_elliott_wxy,
    )
    for k in _ELLIOTT_KEYS:
        indicators.pop(k, None)
    detectors = (
        (detect_elliott_impulse, "elliott_impulse"),
        (detect_elliott_corrective, "elliott_corrective"),
        (detect_elliott_diagonal, "elliott_diagonal"),
        (detect_elliott_wxy, "elliott_wxy"),
    )
    for fn, key in detectors:
        try:
            pat = fn(highs, lows, closes, swings=swings)
        except Exception:
            pat = None
        if pat:
            indicators[key] = pat
            indicators["elliott_pattern"] = pat


def _apply_timeframe_matched_elliott(indicators: dict, strategy_type: str,
                                     mtf_candles: dict) -> None:
    """Recompute the elliott_* indicators on the timeframe whose wave degree
    matches ``strategy_type``. ``mtf_candles`` maps a timeframe string to an
    OHLCV list. No-op (fail-open) when the matched timeframe wasn't supplied or
    the series is too short. Mutates ``indicators``.
    """
    from bot.core.elliott import timeframe_for_strategy, atr_zigzag_pivots
    from bot.core.multi_timeframe import _find_swings

    tf = timeframe_for_strategy(strategy_type)
    series = mtf_candles.get(tf)
    if not series or len(series) < 30:
        return
    arr = np.asarray(series, dtype=float)
    # OHLCV rows: [ts, open, high, low, close, volume]
    highs = arr[:, 2]
    lows = arr[:, 3]
    closes = arr[:, 4]
    if CONFIG.analyzer.elliott_zigzag_enabled:
        swings = atr_zigzag_pivots(highs, lows, closes,
                                   atr_mult=CONFIG.analyzer.elliott_zigzag_atr_mult)
        if not (swings["swing_highs"] or swings["swing_lows"]):
            swings = _find_swings(highs, lows, 5)
    else:
        swings = _find_swings(highs, lows, 5)
    _run_elliott_detectors(indicators, highs, lows, closes, swings)
    indicators["elliott_degree_tf"] = tf  # observability: which TF the waves came from


def _apply_mtf_elliott_alignment(indicators: dict, mtf_candles: dict) -> None:
    """Run the wave detectors on EVERY supplied timeframe and store the
    cross-degree alignment map as ``indicators["elliott_mtf"]``.

    The degree-matched read (_apply_timeframe_matched_elliott) answers
    "what wave is THIS setup's degree in?"; this answers "do the degrees
    AGREE?" — nested with-trend structure across 15m/1h/4h/1d is the
    textbook Elliott entry, while a terminal higher-degree wave argues
    against chasing a lower-degree signal. Uses only the series already
    fetched for mtf (zero extra API calls). Fail-open; mutates
    ``indicators`` only on success.
    """
    from bot.core.elliott import atr_zigzag_pivots, mtf_wave_map
    from bot.core.multi_timeframe import _find_swings

    patterns_by_tf: dict = {}
    for tf, series in (mtf_candles or {}).items():
        if not series or len(series) < 30:
            continue
        try:
            arr = np.asarray(series, dtype=float)
            highs, lows, closes = arr[:, 2], arr[:, 3], arr[:, 4]
            if CONFIG.analyzer.elliott_zigzag_enabled:
                swings = atr_zigzag_pivots(
                    highs, lows, closes,
                    atr_mult=CONFIG.analyzer.elliott_zigzag_atr_mult)
                if not (swings["swing_highs"] or swings["swing_lows"]):
                    swings = _find_swings(highs, lows, 5)
            else:
                swings = _find_swings(highs, lows, 5)
            scratch: dict = {}
            _run_elliott_detectors(scratch, highs, lows, closes, swings)
            patterns_by_tf[tf] = scratch.get("elliott_pattern")
        except Exception:  # noqa: BLE001 — one bad series never voids the map
            continue
    ew_map = mtf_wave_map(patterns_by_tf)
    if ew_map.get("n_timeframes", 0) >= 2:
        indicators["elliott_mtf"] = ew_map


def _recompute_elliott_indicators(indicators: dict, highs, lows, closes,
                                   atr_mult: float) -> None:
    """Recompute the elliott_* indicators from structural ATR-ZigZag pivots,
    replacing the fixed-fractal read. Gated by the caller; fail-open (leaves
    existing indicators untouched on any error). Mutates ``indicators``.
    """
    from bot.core.elliott import atr_zigzag_pivots
    swings = atr_zigzag_pivots(highs, lows, closes, atr_mult=atr_mult)
    if not (swings["swing_highs"] or swings["swing_lows"]):
        return  # zigzag found nothing structural — keep the fractal read
    # Clear + replace the fractal-derived Elliott set wholesale (clean A/B).
    _run_elliott_detectors(indicators, highs, lows, closes, swings)


def _apply_scalp_session_vwap(indicators: dict, strategy_type: str,
                              mtf_candles: dict) -> None:
    """For scalp/intraday setups, rebuild ``vwap_session`` from the 15m series
    (audit follow-up). The primary 1h session VWAP is a UTC-day average of
    <=24 hourly points — too coarse for a scalp; the 15m series gives ~96
    intraday points. No-op (fail-open) for higher-horizon setups or when 15m
    isn't supplied. Mutates ``indicators``.
    """
    if strategy_type not in ("scalp", "intraday"):
        return
    series = (mtf_candles or {}).get("15m")
    if not series or len(series) < 8:
        return
    arr = np.asarray(series, dtype=float)
    highs, lows, closes, volumes = arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5]
    times = arr[:, 0]
    tp = (highs + lows + closes) / 3.0
    sv = Analyzer._session_vwap(tp, volumes, times)
    if sv is not None and sv > 0:
        indicators["vwap_session"] = round(float(sv), 6)
        indicators["vwap_session_tf"] = "15m"  # observability


def _apply_vwap_setup_anchoring(indicators: dict, strategy_type: str) -> None:
    """Re-point the ``vwap`` value consumers read to the anchor whose horizon
    matches this setup (scalp/intraday→session, swing→rolling-50, position→full
    window). A UTC-day session VWAP is meaningful for a scalp but near-useless
    for a multi-day swing. Fail-open: leaves ``vwap`` untouched when the chosen
    anchor is missing. Records ``vwap_anchor_kind`` for transparency. Mutates
    ``indicators``.
    """
    from bot.core.vwap import select_setup_anchor
    anchors = {
        "session": indicators.get("vwap_session"),
        "rolling50": indicators.get("vwap_50"),
        "full": indicators.get("vwap_full"),
    }
    val, kind = select_setup_anchor(strategy_type, anchors)
    if val is not None:
        # Re-center the σ-bands on the new anchor (audit: bands were built
        # around the session VWAP in _compute_indicators; re-pointing the
        # center without moving the bands left the band-reversion voter and
        # the directional vote referencing two different VWAPs). The band
        # half-width (dispersion) is preserved — only the center moves.
        _old_center = indicators.get("vwap")
        _old_u1 = indicators.get("vwap_upper_1")
        if (_old_center is not None and _old_u1 is not None
                and val != _old_center):
            _dev = float(_old_u1) - float(_old_center)
            if _dev > 0:
                indicators["vwap_upper_1"] = round(val + _dev, 6)
                indicators["vwap_lower_1"] = round(val - _dev, 6)
                indicators["vwap_upper_2"] = round(val + 2 * _dev, 6)
                indicators["vwap_lower_2"] = round(val - 2 * _dev, 6)
        indicators["vwap"] = val
        indicators["vwap_anchor_kind"] = kind


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

    # 2b. Anchored VWAP (AVWAP from the last structural pivot) — dynamic S/R
    # since the most recent swing. Present only when the gated feature is on.
    vwap_anchored = indicators.get("vwap_anchored")
    if vwap_anchored is not None and vwap_anchored > 0:
        candidates.append(float(vwap_anchored))

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
        # LLM health tracking (proactive-monitor degrade alert). A thesis that
        # ATTEMPTED the LLM but exhausted every provider and fell to the rule
        # engine (RULE_ENGINE_FALLBACK) is the live "quota exhausted → brain
        # offline" signature. Rule-engine-by-design (tier 1, never called the
        # LLM) does NOT touch these counters, so the streak only rises on real
        # provider failure. The streak resets to 0 on the next LLM success.
        self._llm_degraded_streak: int = 0        # consecutive all-provider fails
        self._llm_last_ok_monotonic: float = 0.0  # last successful LLM thesis
        self._llm_degraded_since_monotonic: float = 0.0  # when the streak began
        self._llm_last_error: str = ""            # last primary-provider error
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
        # Structured no-trade reasons per symbol (audit fix #8): every analyzer
        # skip path records WHY, so callers (/whynot, dashboard, learning) can
        # enumerate skip reasons instead of scraping the audit log.
        self._no_trade_reasons: dict[str, dict] = {}
        # Regime persistence: smooth out single-bar whipsaw changes
        self._regime_history: list[tuple[str, str]] = []  # (symbol, regime_value)
        self._current_regimes: dict[str, Regime] = {}  # per-symbol smoothed regime

    def _record_no_trade(self, symbol: str, stage: str, reason: str, **data) -> None:
        """Record a structured no-trade reason (audit fix #8). Best-effort —
        never raises into the analysis path."""
        try:
            entry = {
                "symbol": symbol,
                "stage": stage,
                "reason": reason,
                "ts": datetime.now(UTC).isoformat(),
                **data,
            }
            self._no_trade_reasons[symbol] = entry
            self._last_rejection_diag = entry
        except Exception:
            pass

    def get_no_trade_reason(self, symbol: str) -> Optional[dict]:
        """Structured reason why the last analysis of `symbol` produced no
        trade, or None if the last analysis produced an idea."""
        return self._no_trade_reasons.get(symbol)

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
                       as_of: Optional[datetime] = None, user_id=None, user_tier=None,
                       mtf_candles: Optional[dict] = None,
                       timeframe: str = "1h") -> Optional[TradeIdea]:
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
            self._record_no_trade(signal.symbol, "data", "not enough candles",
                                  candles=len(candles))
            return None

        # Validate candle data integrity before processing (audit fix #9):
        # field count, NaN/positivity on OHLC, volume/timestamp finiteness,
        # ascending timestamp order and duplicate-bar removal — an out-of-order
        # or duplicated exchange batch silently skews cumulative VWAP and the
        # session anchor otherwise.
        try:
            for i, c in enumerate(candles):
                if len(c) < 5:
                    raise ValueError(f"Candle {i} has {len(c)} fields (need >=5)")
            times = np.array([c[0] for c in candles], dtype=float)  # epoch ms (ccxt)
            if not np.all(np.isfinite(times)):
                raise ValueError("Non-finite timestamps")
            # Sort ascending + de-duplicate by timestamp (keep the LAST record
            # for a duplicated bar — the freshest snapshot of that candle).
            order = np.argsort(times, kind="stable")
            if not np.array_equal(order, np.arange(len(times))):
                candles = [candles[int(i)] for i in order]
                times = times[order]
            if len(times) > 1:
                dup = np.concatenate([times[1:] == times[:-1], [False]])
                if np.any(dup):
                    keep = ~dup
                    candles = [c for c, k in zip(candles, keep) if k]
                    times = times[keep]
            if len(candles) < CONFIG.analyzer.min_candles:
                raise ValueError("Too few candles after de-duplication")
            opens = np.array([c[1] for c in candles], dtype=float)
            highs = np.array([c[2] for c in candles], dtype=float)
            lows = np.array([c[3] for c in candles], dtype=float)
            closes = np.array([c[4] for c in candles], dtype=float)
            volumes = np.array([c[5] if len(c) > 5 else 0 for c in candles], dtype=float)
            # Reject NaN/Inf in OHLC data; volume gets sanitized (a missing
            # volume must not kill analysis, but NaN must not reach VWAP).
            for name, arr in [("opens", opens), ("highs", highs), ("lows", lows), ("closes", closes)]:
                if not np.all(np.isfinite(arr)):
                    raise ValueError(f"Non-finite values in {name}")
                if np.any(arr <= 0):
                    raise ValueError(f"Non-positive values in {name}")
            volumes = np.where(np.isfinite(volumes) & (volumes >= 0), volumes, 0.0)
        except (ValueError, IndexError, TypeError) as exc:
            audit(trade_log, f"Invalid candle data: {exc}", action="analyze",
                  result="SKIP", data={"symbol": signal.symbol, "error": str(exc)})
            self._record_no_trade(signal.symbol, "data", f"invalid candle data: {exc}")
            return None

        indicators = self._compute_indicators(highs, lows, closes, volumes, opens=opens, times=times)
        if indicators is None:
            audit(trade_log, "Indicator computation failed (insufficient data)", action="analyze",
                  result="SKIP", data={"symbol": signal.symbol, "candles": len(candles)})
            self._record_no_trade(signal.symbol, "indicators",
                                  "indicator computation failed", candles=len(candles))
            return None

        # Data-quality stamp (audit fix #10): below 50 bars the SMA-50 trend
        # check, vwap_50 anchor and the full 50-bar fib window are silently
        # unavailable/shrunken — record it so downstream (and the operator)
        # can see a thin-window signal for what it is.
        indicators["data_bars"] = int(len(closes))
        indicators["data_thin"] = bool(len(closes) < 50)

        # Candlestick pattern detection (needs opens)
        candle_patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        if candle_patterns:
            indicators["candle_patterns"] = candle_patterns
            # Summarize bullish/bearish pattern counts for confluence
            bullish_patterns = [k for k, v in candle_patterns.items() if v == "bullish"]
            bearish_patterns = [k for k, v in candle_patterns.items() if v == "bearish"]
            indicators["candle_bullish_count"] = len(bullish_patterns)
            indicators["candle_bearish_count"] = len(bearish_patterns)
            # Strength-weighted sums (audit fix #14): a three-candle formation
            # is stronger evidence than a lone small-bar pattern.
            indicators["candle_bullish_strength"] = round(
                sum(_CANDLE_STRENGTH.get(k, 1.0) for k in bullish_patterns), 2)
            indicators["candle_bearish_strength"] = round(
                sum(_CANDLE_STRENGTH.get(k, 1.0) for k in bearish_patterns), 2)

        # ── Geometric chart pattern detection (H&S, double top/bottom, flags, etc.) ──
        chart_patterns = scan_all_chart_patterns(opens, highs, lows, closes)
        if chart_patterns:
            indicators["chart_patterns_geo"] = chart_patterns
            # De-correlation (audit fix #5): Wyckoff / Harmonic / Elliott /
            # Fib-extension results vote through their own DEDICATED voters
            # below — counting them in the aggregate chart_patterns vote too
            # double-counted the same evidence. The aggregate now covers only
            # the purely-geometric patterns (H&S, double top, flags, ...).
            def _has_dedicated_voter(p: dict) -> bool:
                n = p.get("name", "")
                return ("Wyckoff" in n or "Harmonic" in n or "Elliott" in n
                        or any(h in n for h in ("Gartley", "Butterfly", "Bat", "Crab"))
                        or "Liquidity Sweep" in n
                        or ("Fibonacci" in n and "ext" in n.lower()))
            if CONFIG.analyzer.pattern_dedup_enabled:
                agg_patterns = [p for p in chart_patterns if not _has_dedicated_voter(p)]
            else:
                agg_patterns = chart_patterns
            bullish_geo = [p for p in agg_patterns if p.get("signal") == "bullish"]
            bearish_geo = [p for p in agg_patterns if p.get("signal") == "bearish"]
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
                elif "Liquidity Sweep" in name:
                    # Fallback evidence for the sweep voter when the
                    # dedicated module detects nothing (audit: this result
                    # was excluded from the aggregate AND had no consumer).
                    indicators["chart_sweep"] = p

        # ── Advanced Elliott: structural ATR-ZigZag pivots (gated, default ON) ──
        # Replaces the fixed 5-bar fractal Elliott read with one built on
        # ATR-normalized ZigZag pivots, which filter out the noise wiggles the
        # fractal keeps. Fail-open; byte-identical when the flag is off.
        if CONFIG.analyzer.elliott_zigzag_enabled:
            try:
                _recompute_elliott_indicators(
                    indicators, highs, lows, closes,
                    atr_mult=CONFIG.analyzer.elliott_zigzag_atr_mult)
            except Exception as exc:
                system_log.debug("Elliott ZigZag recompute skipped: %s", exc)

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
            # Reuse the histogram _compute_indicators already built when the
            # parameters are equivalent (default 50 bins; ticker within 0.1%
            # of the last close, so the categorical fields match). Otherwise
            # recompute with the configured bins — behavior identical.
            _vp_cached = indicators.get("_vp_basic")
            _lc = float(closes[-1]) if len(closes) else 0.0
            if (_vp_cached is not None
                    and CONFIG.analyzer.volume_profile_bins == 50
                    and _lc > 0
                    and abs(signal.price - _lc) / _lc < 0.001):
                vp = _vp_cached
            else:
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

        # ── Smart-money concepts: FVG / equal-level pools / premium-discount ──
        if CONFIG.analyzer.smc_voters_enabled:
            try:
                from bot.core.smc import (equal_level_pools, find_fvgs, fvg_vote,
                                          premium_discount)
                _smc_atr = indicators.get("atr", 0.0) or 0.0
                if _smc_atr > 0 and len(closes) >= 30:
                    _fvgs = find_fvgs(highs, lows, closes)
                    _fv, _fw = fvg_vote(_fvgs, float(closes[-1]), _smc_atr)
                    _pools = equal_level_pools(highs, lows, _smc_atr)
                    _pd = premium_discount(highs, lows, closes,
                                           window=min(100, len(closes)))
                    indicators["_smc"] = {
                        "fvg_vote": _fv, "fvg_weight": _fw,
                        "eqh": _pools["eqh"], "eql": _pools["eql"],
                        "premium_discount": _pd,
                    }
                    indicators["fvg_count_unfilled"] = sum(
                        1 for g in _fvgs if not g.filled)
            except Exception as exc:
                system_log.debug("SMC feature computation failed: %s", exc)

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
        # MTF_CONFLUENCE_ENABLED (default ON): the engine ships its cached,
        # forming-candle-cleaned 4h/1d fetches in mtf_candles — use them when
        # the explicit params weren't given. (Before this the module was dead
        # code: no caller ever supplied candles_4h/candles_1d.)
        mtf_result = None
        if CONFIG.analyzer.mtf_confluence_enabled and mtf_candles:
            if candles_4h is None:
                candles_4h = mtf_candles.get("4h")
            if candles_1d is None:
                candles_1d = mtf_candles.get("1d")
        if candles_4h or candles_1d:
            # Only feed the primary window into the "1h" slot when it actually
            # IS 1h data (or a true 1h series is available) — a 5m/4h primary
            # mislabeled as 1h would be weighted and structured as the wrong TF.
            _c1h = (mtf_candles or {}).get("1h") or (candles if timeframe == "1h" else None)
            mtf_result = self._mtf.analyze(
                candles_1h=_c1h,
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
            # the last/None value → zero external adjustment). Default ON;
            # backtests force it off (today's sentiment is lookahead there).
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

        # ── Timeframe-matched Elliott (gated, default ON) ───────────────
        # Re-read the wave structure on the timeframe whose degree matches this
        # setup's strategy_type (scalp<intraday<swing<position), so a scalp sees
        # its lower-degree sub-wave and a swing its higher-degree wave -- rather
        # than every setup getting the same primary-timeframe read. Overrides the
        # elliott_* indicators BEFORE confluence voting + SL/TP use them. Fail-open.
        if CONFIG.analyzer.elliott_mtf_enabled and mtf_candles:
            try:
                _apply_timeframe_matched_elliott(indicators, strategy_type, mtf_candles)
            except Exception as exc:
                system_log.debug("Timeframe-matched Elliott skipped: %s", exc)

        # ── Cross-degree Elliott alignment (gated, default ON) ──────────
        # Run the wave detectors across ALL fetched timeframes and expose the
        # degree-agreement map; _score_confluence adds one bounded vote from
        # it. Zero extra API calls (reuses mtf_candles). Fail-open.
        if CONFIG.analyzer.elliott_mtf_alignment_enabled and mtf_candles:
            try:
                _apply_mtf_elliott_alignment(indicators, mtf_candles)
            except Exception as exc:
                system_log.debug("MTF Elliott alignment skipped: %s", exc)

        # ── All-timeframes candlestick map (gated, default ON) ──────────
        # Candle formations on every fetched degree (a 1d engulfing outranks
        # a 15m one); one bounded agreement vote + HTF veto input. Zero
        # extra API calls (reuses mtf_candles). Fail-open.
        if getattr(CONFIG.analyzer, "candle_mtf_enabled", False) and mtf_candles:
            try:
                _apply_mtf_candles(indicators, mtf_candles)
            except Exception as exc:
                system_log.debug("MTF candle map skipped: %s", exc)

        # ── Scalp session VWAP from 15m (gated, default ON) ─────────────
        # Rebuild the session anchor scalps read from the 15m series BEFORE
        # setup anchoring re-points "vwap" to it. Fail-open to the 1h session.
        if (CONFIG.analyzer.scalp_session_vwap_enabled and mtf_candles):
            try:
                _apply_scalp_session_vwap(indicators, strategy_type, mtf_candles)
            except Exception as exc:
                system_log.debug("Scalp session VWAP skipped: %s", exc)

        # ── Setup-matched VWAP anchoring (gated, default ON) ────────────
        # Re-point the "vwap" consumers read (confluence vote, S/R candidate,
        # reversion classifier) to the anchor whose horizon matches this setup,
        # BEFORE confluence voting + SL/TP use it. Fail-open.
        if CONFIG.analyzer.vwap_setup_anchoring_enabled:
            try:
                _apply_vwap_setup_anchoring(indicators, strategy_type)
            except Exception as exc:
                system_log.debug("VWAP setup anchoring skipped: %s", exc)

        # Phase B: capture the named per-voter breakdown alongside the score so
        # downstream recording can persist it for voter-weight learning. The
        # breakdown out-param does not affect the returned confluence value.
        # On-chain snapshot (gated): the provider is inert unless ONCHAIN_* is
        # enabled, so this await is a no-op in the default configuration; when
        # enabled it serves a 10-min cache and fails open to None.
        _onchain_snapshot = None
        try:
            from bot.core.onchain import (
                get_onchain_provider, onchain_enabled, onchain_flow_enabled)
            if onchain_enabled() or onchain_flow_enabled():
                _onchain_snapshot = await get_onchain_provider().fetch(
                    str(signal.symbol).split("/")[0])
        except Exception as _oc_exc:
            system_log.debug("On-chain snapshot skipped: %s", _oc_exc)

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
            onchain_snapshot=_onchain_snapshot,
        )
        # Voter-grounded thesis (audit UX item): expose the top signed votes
        # to the LLM prompt so the narrative cites the actual electorate that
        # drove the score instead of re-deriving (or inventing) its own read.
        if _confluence_votes:
            indicators["_top_votes"] = sorted(
                ((n, v, w) for n, v, w in _confluence_votes if abs(v * w) > 1e-9),
                key=lambda t: abs(t[1] * t[2]), reverse=True)[:6]

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
            self._record_no_trade(
                signal.symbol, "thesis",
                thesis.get("reasoning") or "thesis returned no valid direction")
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

        # ── LLM direction guard (gated, default ON; audit fix #1) ──────────
        # The thesis (LLM) picks the direction, but the deterministic voters
        # are the decider of record: when the confluence score CLEARLY opposes
        # the thesis direction (0.5 = neutral; >0.5 bullish, <0.5 bearish), the
        # LLM does not get to overrule them unchecked. Opposition beyond the
        # veto margin rejects the idea; beyond the haircut margin it halves the
        # thesis confidence before blending. Rule-engine theses derive their
        # direction FROM confluence and can never trip this guard.
        if CONFIG.analyzer.llm_direction_guard_enabled:
            _guard = self._direction_guard_action(
                direction, confluence,
                CONFIG.analyzer.llm_direction_haircut_margin,
                CONFIG.analyzer.llm_direction_veto_margin)
            if _guard == "veto":
                self._record_no_trade(
                    signal.symbol, "llm_direction_guard",
                    "thesis direction opposes strong deterministic consensus",
                    direction=direction.value, confluence=round(confluence, 4))
                audit(trade_log,
                      f"LLM direction guard: {signal.symbol} {direction.value} vetoed — "
                      f"confluence {confluence:.2f} opposes",
                      action="analyze", result="REJECTED_DIRECTION_GUARD",
                      data={"symbol": signal.symbol, "direction": direction.value,
                            "confluence": round(confluence, 4)})
                return None
            if _guard == "haircut":
                counter_trend_penalty = min(counter_trend_penalty, 0.5)
                audit(trade_log,
                      f"LLM direction guard: {signal.symbol} {direction.value} haircut — "
                      f"confluence {confluence:.2f} leans against",
                      action="analyze", result="PENALTY",
                      data={"symbol": signal.symbol, "direction": direction.value,
                            "confluence": round(confluence, 4)})

        confidence = max(0.0, min(1.0, thesis.get("confidence", 0.0))) * counter_trend_penalty
        # C2-20 FIX: Cap combined penalty — confidence never drops below 25% of raw.
        # Without this, counter_trend (0.5x) + regime_penalty (-0.15) = ~70-80% total
        # reduction, eliminating legitimate mean-reversion setups entirely.

        # Blend LLM/rule-based confidence with confluence score. The weights are
        # capped if the uncalibrated-LLM guard is active (see _blend_weights).
        _llm_w, _conf_w = self._blend_weights()
        # Orient confluence to the trade direction before blending. `confluence`
        # is a bullishness score (0.5 neutral, >0.5 long, <0.5 short); blending it
        # RAW treated a strongly-confirmed SHORT (a low value) as weak, so shorts
        # were systematically under-scored/suppressed and longs over-credited. For
        # a SHORT the confirming strength is (1 - confluence). Honest 6-fold
        # walk-forward A/B: mean OOS -1.48% -> -1.12%, better on 4/6 folds, worse
        # on none.
        conf_term = confluence if direction == Direction.LONG else 1.0 - confluence
        blended_confidence = confidence * _llm_w + conf_term * _conf_w

        # ── LLM Calibration Log ──────────────────────────────────────
        # Captures raw LLM confidence vs confluence BEFORE any post-blend
        # adjustments.  Enables offline calibration study: correlation,
        # direction agreement, precision/recall at thresholds per model.
        # LIVE ONLY: backtest replays (as_of set) must not append thousands
        # of rule-engine rows to the operator's calibration study file.
        if as_of is None:
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

        # Data-quality penalty (audit fix #10): a thin (<50 bar) window lacks
        # the SMA-50 trend check, vwap_50 anchor and full fib window — the
        # signal has structurally less confirmation, so it needs a bounded
        # confidence haircut instead of passing as a fully-confirmed setup.
        if (CONFIG.analyzer.data_quality_penalty_enabled
                and indicators.get("data_thin")):
            blended_confidence -= CONFIG.analyzer.data_thin_penalty
            audit(trade_log, "Data-quality penalty: thin window",
                  action="analyze", result="PENALTY",
                  data={"symbol": signal.symbol,
                        "bars": indicators.get("data_bars"),
                        "penalty": CONFIG.analyzer.data_thin_penalty})

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

        # #35: snapshot the blended confidence the calibrator is APPLIED to, BEFORE
        # the calibration remap and the setup-expectancy nudge below mutate it. The
        # decision record persists this exact value so the calibrator trains on the
        # same field it remaps (otherwise it fits post-adjustment confidence and
        # applies the curve to this pre-adjustment value — a systematic mismatch).
        _blended_confidence_raw = blended_confidence

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
        # Per-strategy-type confidence threshold, RAISED (never lowered) by
        # the selected strategy mode's own bar — a BREAKOUT setup demands
        # 0.65, a LIQUIDITY_SWEEP 0.68 (these per-mode bars were dead fields
        # until the signal-stack audit).
        min_conf = CONFIG.strategy_types.get_min_confidence(strategy_type)
        # The mode floor applies only to SPECIFIC setup modes (BREAKOUT 0.65,
        # LIQUIDITY_SWEEP 0.68...) — a breakout that can't clear a breakout
        # bar isn't one. CONSERVATIVE is the uncertain-regime catch-all that
        # most scans land in; applying its 0.65 raised the effective global
        # gate from 0.55 for nearly every idea and collapsed trade flow
        # (measured: 44 -> 10 trades, +3.66% -> -0.20%).
        if (CONFIG.analyzer.mode_min_confidence_enabled
                and mode_config is not None
                and getattr(getattr(mode_config, "mode", None), "value", "")
                not in ("", "CONSERVATIVE")):
            min_conf = max(min_conf, getattr(mode_config, "min_confidence", 0.0))
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
            self._record_no_trade(
                signal.symbol, "confidence",
                self._last_rejection_diag.get("reason", "below threshold")
                if self._last_rejection_diag else "below threshold",
                blended=round(blended_confidence, 3), threshold=min_conf)
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

        # STRATEGY: adaptive ATR multipliers based on volatility regime.
        # SL/TP baselines come from CONFIG.strategy_types (per scalp/intraday/
        # swing/position); volatility/regime can override, then level-aware
        # snapping refines. (Strategy MODES do not set SL/TP — their live
        # knobs are confluence_boost and min_confidence.)
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

        # ── Wave-anchored SL/TP from Elliott Fib projections (gated, default ON) ──
        # Tighten the stop to the wave-invalidation level and extend the target
        # to the projected wave objective. Only ever reduces risk / increases
        # reward; never loosens the stop. Applied on absolute levels after the
        # limit-entry shift so the invalidation isn't distorted. Disabled → no-op.
        if CONFIG.analyzer.elliott_fib_targets_enabled:
            try:
                stop_loss, take_profit = _apply_elliott_wave_targets(
                    direction, entry, stop_loss, take_profit, indicators)
            except Exception as exc:
                system_log.debug("Elliott wave-target anchoring skipped: %s", exc)

        # ── Level-aware SL/TP (gated, default ON) ────────────────────
        # Snap the ATR stop just beyond real structure (tighten-only) and
        # clip the target inside an opposing wall — a stop one tick above a
        # triple-tested wick low is the sweep magnet the bot's own liquidity
        # module models, and a TP just past resistance never fills. Applied
        # after the Elliott anchoring on absolute levels; the leverage
        # margin-risk cap below still runs after and only tightens further.
        if CONFIG.analyzer.level_aware_sltp_enabled and atr > 0:
            try:
                from bot.core.levels import gather_levels, snap_sl_tp
                # Feed the bot's own derived objectives into the level map
                # (audit upgrade): fib retracements of the dominant leg and
                # the Elliott impulse's projected targets are real magnets —
                # the SL/TP snap should know about them.
                _extras = []
                for _fk in ("fib_382", "fib_500", "fib_618"):
                    _fv = indicators.get(_fk)
                    if _fv:
                        _extras.append((float(_fv), "fib"))
                _imp = indicators.get("elliott_impulse")
                if _imp:
                    try:
                        from bot.core.elliott import project_targets
                        _t = project_targets(_imp) or {}
                        for _tk in ("tp1", "tp2"):
                            if _t.get(_tk):
                                _extras.append((float(_t[_tk]), "ew_target"))
                    except Exception:
                        pass
                _lvls = gather_levels(
                    highs, lows, closes, atr,
                    times=times,
                    vp=indicators.get("volume_profile"),
                    extra_levels=_extras)
                _sl2, _tp2, _note = snap_sl_tp(
                    direction.value, entry, stop_loss, take_profit, _lvls, atr)
                if _note:
                    stop_loss, take_profit = _sl2, _tp2
                    indicators["level_snap"] = _note
            except Exception as exc:
                system_log.debug("Level-aware SL/TP skipped: %s", exc)

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
            self._record_no_trade(signal.symbol, "rsi_block",
                                  f"LONG into overbought RSI {rsi_val:.1f}")
            return None
        if direction == Direction.SHORT and rsi_val <= CONFIG.analyzer.rsi_oversold_block:
            audit(trade_log,
                  f"SHORT rejected: RSI {rsi_val:.1f} <= {CONFIG.analyzer.rsi_oversold_block} (oversold)",
                  action="rsi_block", result="BLOCKED")
            self._record_no_trade(signal.symbol, "rsi_block",
                                  f"SHORT into oversold RSI {rsi_val:.1f}")
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

        # ── Evidence-gated signal-family skip (SKIP_SIGNAL_TYPES) ──
        # The frozen-benchmark attribution under the LIVE partial-TP exit can flag
        # a family as a persistent drag; skip it rather than trade known-negative
        # edge. Default empty = trade all families.
        if _is_gated_signal_type(signal_type, CONFIG.analyzer.skip_signal_types):
            self._record_no_trade(signal.symbol, "signal_type",
                                  f"{signal_type} gated (SKIP_SIGNAL_TYPES)")
            return None

        # ── Minimum stop-distance floor (safety, MIN_STOP_DISTANCE_PCT) ──
        # A pathologically-tight ATR stop (low-vol / low-priced asset) gets
        # rejected by the venue as a conditional order (position left UNPROTECTED,
        # the live TAG case) or is tripped instantly by noise. Widen |entry - SL|
        # to a sane, placeable floor here — after every tighten-only adjustment
        # (leverage cap, structure snap) and before sizing, so position sizing
        # measures risk on the real (floored) stop. Never tightens; leaves TP,
        # so a stop that has to be widened to be placeable naturally lowers R:R
        # and the risk engine can reject it — a trade needing an un-placeable
        # stop for good R:R is a bad trade.
        stop_loss = _floor_stop_distance(
            entry, stop_loss, direction, CONFIG.analyzer.min_stop_distance_pct)

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
            self._record_no_trade(signal.symbol, "levels",
                                  "degenerate SL/TP after rounding",
                                  entry=_r_entry, sl=_r_sl, tp=_r_tp)
            return None

        # Candle-pattern entry veto (opt-in, default OFF). When a PULLBACK LIMIT
        # is about to be placed and the last closed bar prints a strong reversal
        # OPPOSING the trade, skip — the "pullback" may be a breakdown through
        # the fill zone. Only for limit entries; market orders are unaffected.
        if (getattr(CONFIG.analyzer, "candle_entry_veto_enabled", False) is True
                and order_type == "limit"):
            _veto = candle_entry_veto(candle_patterns, direction)
            # Higher-degree extension: a veto-grade opposing reversal on the
            # 4h/1d last CLOSED bar (from the all-timeframes candle map) also
            # vetoes — the degree ABOVE the trade printing "reversal" is
            # stronger evidence than the primary bar alone. Only active when
            # the map exists (candle_mtf_enabled) and the veto feature is on.
            if not _veto:
                _htf = (indicators.get("candle_mtf") or {}).get("htf_veto") or {}
                _veto = _htf.get(direction.value)
            if _veto:
                audit(trade_log, f"Idea vetoed by candle pattern: {_veto}",
                      action="analyze", result="SKIP", data={"symbol": signal.symbol})
                self._record_no_trade(signal.symbol, "candle_veto", _veto)
                return None

        # Liquidation-cascade chase veto (opt-in, default OFF pending A/B).
        # A recent bar whose range AND volume both exploded is forced-flow
        # territory: entering IN the flush direction fills at the extreme of
        # a move that mean-reverts once the liquidations are spent. Fading
        # the cascade is never vetoed. Applies to both order types — chasing
        # is bad at market and worse as a limit that fills on continuation.
        if getattr(CONFIG.analyzer, "cascade_veto_enabled", False) is True:
            try:
                from bot.risk.funding_clock import cascade_state, cascade_veto
                _cs = cascade_state(
                    highs, lows, closes, volumes, atr=atr_val,
                    range_atr_mult=CONFIG.analyzer.cascade_range_atr_mult,
                    vol_mult=CONFIG.analyzer.cascade_vol_mult,
                    recent_bars=CONFIG.analyzer.cascade_recent_bars)
                _cv = cascade_veto(direction.value, _cs)
                if _cv:
                    audit(trade_log, f"Idea vetoed by cascade: {_cv}",
                          action="analyze", result="SKIP",
                          data={"symbol": signal.symbol})
                    self._record_no_trade(signal.symbol, "cascade_veto", _cv)
                    return None
            except Exception as _cs_exc:
                logger.debug("cascade veto skipped: %s", _cs_exc)

        self._no_trade_reasons.pop(signal.symbol, None)
        idea = TradeIdea(
            id=f"TI-{uuid.uuid4().hex[:8]}",
            asset=signal.symbol,
            direction=direction,
            entry_price=_r_entry,
            stop_loss=_r_sl,
            take_profit=_r_tp,
            confidence=blended_confidence,
            blended_confidence_raw=_blended_confidence_raw,
            reasoning=(
                f"[{source}|{regime.value}|{mode_tag}|{strategy_type}|C={confluence:.2f}"
                f"{mtf_tag}{sm_tag}] {thesis.get('reasoning', '')}"
            ),
            signals_used=list(indicators.keys()),
            # Higher-timeframe trend (daily-weighted) for the risk MTF gate.
            # "" when no MTF data was fed this bar (gate then skips).
            htf_trend=(mtf_result.htf_trend if mtf_result is not None else ""),
            timestamp=datetime.now(UTC),
            order_type=order_type,
            strategy_type=strategy_type,
            signal_type=signal_type,
            # Provenance + evidence (audit fix #18)
            timeframe=timeframe,
            llm_confidence=round(float(thesis.get("confidence", 0.0)), 4),
            confluence_score=round(float(confluence), 4),
            model_provider=thesis.get("model") or thesis.get("source"),
            prompt_hash=thesis.get("prompt_hash") or None,
            analysis_version=_ANALYSIS_VERSION,
            data_bars=indicators.get("data_bars"),
            data_thin=indicators.get("data_thin"),
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
            # Guardian Flight Recorder: carry the deterministic explanation onto
            # the idea (like _confluence_votes above) so the decision seal can
            # persist it as provenance. Best-effort — never blocks the idea.
            try:
                idea._explain_report = explain_report.model_dump(mode="json")
            except Exception:
                pass
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

        # VWAP reversion: price near VWAP in RANGE/CHOP regime. When the band
        # feature is on, "near" is volatility-adaptive (within ±0.5σ of VWAP)
        # instead of a fixed 0.5% — which is too wide for BTC and too tight for
        # a volatile alt.
        vwap = indicators.get("vwap")
        if vwap and vwap > 0 and regime in (Regime.RANGE, Regime.CHOP):
            u1 = indicators.get("vwap_upper_1")
            if CONFIG.analyzer.vwap_bands_vote_enabled and u1 and u1 > vwap:
                near = abs(signal.price - vwap) <= 0.5 * (u1 - vwap)
            else:
                near = abs(signal.price - vwap) / vwap * 100 < 0.5
            if near:
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
            if avg_gain <= 1e-12 and avg_loss <= 1e-12:
                # Perfectly flat market: no gains AND no losses. rs would be
                # 0/epsilon → RSI=0 (max oversold, a spurious bullish vote);
                # the neutral reading is 50 (audit fix #11).
                results["rsi"] = 50.0
            else:
                rs = avg_gain / max(avg_loss, 1e-10)
                results["rsi"] = round(100 - 100 / (1 + rs), 2)
        else:
            avg_gain = np.mean(gain) if len(gain) > 0 else 0
            avg_loss = np.mean(loss) if len(loss) > 0 else 1e-10
            if avg_gain <= 1e-12 and avg_loss <= 1e-12:
                results["rsi"] = 50.0
            else:
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
            # Preserve the full-window value even if session-anchoring overwrites
            # "vwap" below — setup-matched anchoring (position setups) reads it.
            results["vwap_full"] = round(float(vwap), 6)

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

            # VWAP slope (gated): % change of the cumulative VWAP over the last
            # ~10 bars, so a directional bias can be dampened when it fights a
            # rising/falling anchor (see slope_adjusted_vote).
            if getattr(CONFIG.analyzer, "vwap_slope_vote_enabled", False):
                try:
                    from bot.core.vwap import vwap_slope_pct
                    vwap_series = cum_tp_vol / np.maximum(cum_vol, 1e-10)
                    sp = vwap_slope_pct(vwap_series, lookback=10)
                    if sp is not None:
                        results["vwap_slope_pct"] = round(sp, 6)
                except Exception:
                    pass

            # Anchored VWAP (gated): reset at the most recent structural ZigZag
            # pivot — the institutional "average price since the swing" level.
            if getattr(CONFIG.analyzer, "vwap_anchored_pivot_enabled", False):
                try:
                    from bot.core.vwap import anchored_vwap_from_last_pivot
                    av = anchored_vwap_from_last_pivot(highs, lows, closes, volumes)
                    if av is not None and av > 0:
                        results["vwap_anchored"] = round(float(av), 6)
                except Exception:
                    pass

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
                # Cache for analyze()'s VP block (audit: the full 50-bin
                # histogram ran twice per symbol per scan).
                results["_vp_basic"] = vp
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

        # ── Money Flow Index (14) — volume-weighted RSI (advertised but never
        # implemented until the signal-stack audit). Typical-price money flow
        # split by direction, Wilder-style ratio. ──
        if volumes is not None and len(closes) >= 15:
            tp = (highs + lows + closes) / 3.0
            mf = tp * volumes
            d_tp = np.diff(tp[-15:])
            mf_win = mf[-14:]
            pos = float(np.sum(mf_win[d_tp > 0]))
            neg = float(np.sum(mf_win[d_tp < 0]))
            if pos + neg > 0:
                results["mfi"] = round(100.0 * pos / (pos + neg), 2)

        # ── Per-bar volume spike: last CLOSED bar vs SMA-20 of prior volumes.
        # The scanner-level signal.volume_spike compares the rolling 24h sum
        # between scans — a quantity that essentially never doubles in 5
        # minutes, leaving the voter inert. This is the bar-level read. ──
        if volumes is not None and len(volumes) >= 21:
            _v_prior = volumes[-21:-1]
            _v_avg = float(np.mean(_v_prior))
            if _v_avg > 0:
                results["vol_spike_bar_ratio"] = round(float(volumes[-1]) / _v_avg, 3)
                results["vol_spike_bar"] = bool(volumes[-1] >= 2.0 * _v_avg)
                results["vol_spike_bar_dir"] = 1 if closes[-1] >= closes[-2] else -1

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
                # Previous-bar squeeze state, so the regime can detect the
                # RELEASE transition (on -> off) instead of compression itself.
                # Prior-bar Keltner width reuses the current ATR (it moves
                # little bar-to-bar and avoids a second full ATR pass).
                if len(closes) >= 21:
                    _pc = closes[:-1]
                    _bb_mid_p = float(np.mean(_pc[-20:]))
                    _bb_sd_p = float(np.std(_pc[-20:]))
                    _kc_mid_p = float(_ema(_pc, 20)[-1])
                    results["kc_squeeze_prev"] = (
                        _bb_mid_p + 2 * _bb_sd_p < _kc_mid_p + 2 * kc_atr
                        and _bb_mid_p - 2 * _bb_sd_p > _kc_mid_p - 2 * kc_atr)

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
        # Audit fix: the channel EXCLUDES the current bar. Including it made
        # the breakout self-defeating — closes[-1] >= max(highs incl. own
        # high) requires closing exactly at the 20-bar max's own high, so the
        # donchian voter and TURTLE_BREAKOUT trigger almost never fired.
        # Standard Turtle compares price to the PRIOR N-bar channel.
        dc_period = 20
        if len(closes) >= dc_period + 1:
            dc_high = float(np.max(highs[-(dc_period + 1):-1]))
            dc_low = float(np.min(lows[-(dc_period + 1):-1]))
            dc_mid = (dc_high + dc_low) / 2
            results["dc_upper"] = round(dc_high, 6)
            results["dc_lower"] = round(dc_low, 6)
            results["dc_mid"] = round(dc_mid, 6)
            results["dc_width"] = round((dc_high - dc_low) / dc_low * 100 if dc_low > 0 else 0, 4)
            # Breakout detection: close beyond the prior channel
            results["dc_breakout_high"] = float(closes[-1]) >= dc_high
            results["dc_breakout_low"] = float(closes[-1]) <= dc_low
            # Position within channel (0=bottom, 1=top)
            results["dc_position"] = round(
                (closes[-1] - dc_low) / (dc_high - dc_low) if dc_high > dc_low else 0.5, 4
            )
            # 55-period Donchian for Turtle system confirmation (same
            # prior-channel convention)
            dc55_period = min(55, len(closes) - 1)
            if dc55_period >= 40:
                dc55_high = float(np.max(highs[-(dc55_period + 1):-1]))
                dc55_low = float(np.min(lows[-(dc55_period + 1):-1]))
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
        # Audit fix #22: deviation is measured around the SAME anchor the bands
        # are centered on (results["vwap"], which is session-anchored under the
        # default). Previously the dispersion was computed against the
        # cumulative full-window VWAP series while the center was the session
        # value — a center/reference mismatch that skewed band widths.
        if volumes is not None and len(volumes) >= 20 and "vwap" in results:
            typical_price = (highs + lows + closes) / 3
            _band_center = float(results["vwap"])
            vwap_dev = np.sqrt(np.mean((typical_price[-20:] - _band_center) ** 2))
            results["vwap_upper_1"] = round(_band_center + vwap_dev, 6)
            results["vwap_lower_1"] = round(_band_center - vwap_dev, 6)
            results["vwap_upper_2"] = round(_band_center + 2 * vwap_dev, 6)
            results["vwap_lower_2"] = round(_band_center - 2 * vwap_dev, 6)

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

    @staticmethod
    def _direction_guard_action(direction, confluence: float,
                                haircut_margin: float,
                                veto_margin: float) -> Optional[str]:
        """Pure decision for the LLM direction guard (audit fix #1).

        Returns "veto" when the thesis direction opposes the deterministic
        confluence consensus by >= veto_margin, "haircut" when it opposes by
        >= haircut_margin, else None. Confluence 0.5 is neutral; deviation
        above/below is the bullish/bearish consensus strength.
        """
        dev = confluence - 0.5
        opposes = ((direction == Direction.LONG and dev < 0)
                   or (direction == Direction.SHORT and dev > 0))
        if not opposes:
            return None
        if abs(dev) >= veto_margin:
            return "veto"
        if abs(dev) >= haircut_margin:
            return "haircut"
        return None

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
        squeeze_prev = indicators.get("kc_squeeze_prev", False)

        # -- Raw regime classification --
        # EXPANSION is the squeeze RELEASE (was on, now off) per its own
        # definition in ta_utils. The old test fired while the squeeze was
        # still ON — i.e. during compression, the opposite condition — and
        # granted the expansion confidence bonus in the middle of chop.
        if (not squeeze) and squeeze_prev and 18 <= adx <= 35:
            raw = Regime.EXPANSION
        elif adx > 25:
            raw = Regime.TREND_UP if plus_di > minus_di else Regime.TREND_DOWN
        elif adx < 20:
            raw = Regime.RANGE
        else:
            raw = Regime.CHOP

        # -- Persistence / smoothing --
        # EXPANSION bypasses smoothing (audit: EXPANSION starved). It is a
        # single-bar squeeze-RELEASE event by construction — requiring 2-of-3
        # consecutive readings meant the release bar could never reach
        # consensus, so the boost/1.3x sizing/narrowed thresholds were dead in
        # live. The release latches immediately for this read; the next
        # non-expansion bar resolves through normal smoothing again.
        if raw == Regime.EXPANSION:
            self._regime_history.append((symbol, raw.value))
            self._current_regimes[symbol] = raw
            return raw
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
    def _ablated_voters() -> frozenset:
        """Voter names to mute this process (ABLATE_VOTERS env, comma-list).
        Cached on first read; re-read when the env value changes so an
        ablation sweep that sets it per-run picks up each new value."""
        raw = os.getenv("ABLATE_VOTERS", "")
        cache = getattr(Analyzer, "_ablate_cache", None)
        if cache is None or cache[0] != raw:
            parsed = frozenset(n.strip() for n in raw.split(",") if n.strip())
            Analyzer._ablate_cache = (raw, parsed)
            return parsed
        return cache[1]

    @staticmethod
    def _score_confluence(indicators: dict, regime: Regime, signal: MarketSignal,
                          order_flow=None, mtf_result=None, smart_money_score=None,
                          mode_config=None, sentiment_engine=None,
                          strategy_type: str = "swing",
                          breakdown: "Optional[list]" = None,
                          onchain_snapshot=None) -> float:
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

        # Learned per-voter multiplier for the direct-append voter blocks
        # (OF/MTF/SM/divergence/sweep bypassed aw(), so the voter-weight
        # learner could never adjust them — audit fix).
        def _lw(weight: float, label: str) -> float:
            return weight * (_vw_mult(label) if _vw_mult is not None else 1.0)

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

        # Dilution guard (audit fix #16): when ON, a voter whose input is
        # MISSING is skipped entirely (no weight appended) instead of casting
        # a 0-vote that inflates the denominator and compresses real signals
        # toward 0.5. A voter whose input is present-but-neutral still votes 0
        # — genuine neutrality is information; absence is not.
        _skip_missing = CONFIG.analyzer.voter_skip_missing_enabled

        # RSI vote (weight 1.5 — strong mean-reversion signal)
        if not (_skip_missing and "rsi" not in indicators):
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
        if not (_skip_missing and "macd_histogram" not in indicators):
            macd_hist = indicators.get("macd_histogram", 0)
            if macd_hist > 0:
                votes.append(1.0)
            elif macd_hist < 0:
                votes.append(-1.0)
            else:
                votes.append(0.0)
            aw(1.0, "macd")

        # Bollinger %B vote (weight 1.0)
        if not (_skip_missing and "bb_pct_b" not in indicators):
            pct_b = indicators.get("bb_pct_b", 0.5)
            if pct_b < 0.2:
                votes.append(1.0)   # near lower band → bullish
            elif pct_b > 0.8:
                votes.append(-1.0)  # near upper band → bearish
            else:
                votes.append(0.0)
            aw(_boost(1.0, "bb_pct_b"), "bb_pct_b")
            _mark_mr_osc()

        # MFI vote (weight 0.9): volume-weighted RSI — same contrarian bands
        # as RSI but confirmed by money flow. Joins the MR-oscillator family
        # cap so it can't stack with RSI/stoch/BB beyond the family budget.
        if CONFIG.analyzer.mfi_voter_enabled and "mfi" in indicators:
            mfi = indicators["mfi"]
            _mfi_vote = None
            if mfi < 20:
                _mfi_vote = 1.0
            elif mfi > 80:
                _mfi_vote = -1.0
            elif mfi < 35:
                _mfi_vote = 0.5
            elif mfi > 65:
                _mfi_vote = -0.5
            # Skip-don't-dilute (audit #16): the neutral 35-65 band casts NO
            # vote. Voting 0 at weight 0.9 on nearly every bar dragged the
            # confluence toward 0.5 across the board and starved the 0.55
            # confidence gate (measured: 44 -> ~10 trades).
            if _mfi_vote is not None:
                votes.append(_mfi_vote)
                aw(_boost(0.9, "mfi"), "mfi")
                _mark_mr_osc()

        # Volume spike vote (weight 0.8 — confirms directional moves). The
        # bar-level spike (last closed bar >= 2x SMA-20 volume) is the live
        # trigger; the scanner's rolling-24h flag is kept as a secondary OR
        # (it essentially never fires between 5-minute scans — audit fix).
        # With the dilution guard on, "no spike" is skipped rather than
        # voting 0 on every quiet bar.
        _bar_spike = (CONFIG.analyzer.vol_spike_bar_vote_enabled
                      and bool(indicators.get("vol_spike_bar")))
        if _bar_spike or signal.volume_spike:
            # Spike confirms the direction of the move: bar direction for the
            # bar-level spike, 24h change for the scanner flag.
            if _bar_spike:
                votes.append(float(indicators.get("vol_spike_bar_dir", 1)))
            else:
                votes.append(1.0 if (signal.change_pct_24h or 0) > 0 else -1.0)
            aw(0.8, "volume_spike")
        elif not _skip_missing:
            votes.append(0.0)
            aw(0.8, "volume_spike")

        # ADX trend strength vote (weight 0.7)
        if not (_skip_missing and "adx" not in indicators):
            adx = indicators.get("adx", 0)
            if adx > 30:
                votes.append(1.0 if indicators.get("plus_di", 0) > indicators.get("minus_di", 0) else -1.0)
            elif adx > 20:
                votes.append(0.3 if indicators.get("plus_di", 0) > indicators.get("minus_di", 0) else -0.3)
            else:
                votes.append(0.0)
            aw(0.7, "adx")

        # VWAP vote (weight 0.5 — institutional bias; optional slope damping)
        vwap = indicators.get("vwap")
        if vwap is not None:
            if signal.price > vwap * 1.005:
                vwap_vote = 1.0   # above VWAP → bullish
            elif signal.price < vwap * 0.995:
                vwap_vote = -1.0  # below VWAP → bearish
            else:
                vwap_vote = 0.0
            # Dampen a bias that fights the VWAP's own slope (gated). When the
            # flag is off or no slope is available this is a no-op, so the vote
            # stays byte-identical to the legacy above/below read.
            if CONFIG.analyzer.vwap_slope_vote_enabled:
                from bot.core.vwap import slope_adjusted_vote
                vwap_vote = slope_adjusted_vote(vwap_vote, indicators.get("vwap_slope_pct"))
            votes.append(vwap_vote)
            aw(0.5, "vwap")

        # VWAP band mean-reversion vote (weight 0.6 — volatility-adaptive
        # extremes). Only appended when it actually fires (price at a ±1σ/±2σ
        # band in a range/chop regime), so a neutral read never dilutes the
        # confluence denominator. Gated, default ON.
        if CONFIG.analyzer.vwap_bands_vote_enabled:
            from bot.core.ta_utils import Regime as _Regime
            from bot.core.vwap import band_reversion_signal
            _in_range = regime in (_Regime.RANGE, _Regime.CHOP)
            band_vote = band_reversion_signal(signal.price, indicators, _in_range)
            if band_vote != 0.0:
                votes.append(band_vote)
                aw(0.6, "vwap_bands")

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

        # Candlestick pattern vote (weight 0.8 — price action signal). With
        # the strength flag on (audit fix #14) the vote is the NET pattern
        # strength (three-candle formations outrank single bars) instead of a
        # raw key count where a lone hammer equalled three white soldiers.
        bull_count = indicators.get("candle_bullish_count", 0)
        bear_count = indicators.get("candle_bearish_count", 0)
        _bull_s = indicators.get("candle_bullish_strength")
        _bear_s = indicators.get("candle_bearish_strength")
        if (CONFIG.analyzer.candle_strength_vote_enabled
                and _bull_s is not None and _bear_s is not None
                and (_bull_s > 0 or _bear_s > 0)):
            _net = (_bull_s - _bear_s) / max(_bull_s + _bear_s, 1e-9)
            votes.append(max(-1.0, min(1.0, _net)))
            aw(0.8 if abs(_net) > 1e-9 else 0.4, "candlestick")
        elif bull_count > bear_count:
            votes.append(1.0)
            aw(0.8, "candlestick")
        elif bear_count > bull_count:
            votes.append(-1.0)
            aw(0.8, "candlestick")
        elif bull_count > 0 or bear_count > 0:
            votes.append(0.0)
            aw(0.4, "candlestick")

        # Cross-degree candlestick vote (gated, default ON). One bounded
        # vote from the all-timeframes candle map: degree agreement (15m/1h/
        # 4h/1d, higher degrees weighted more) votes its signed strength.
        # Dead-zone below |0.05| so mixed/neutral maps never vote.
        _cmtf = indicators.get("candle_mtf")
        if _cmtf and abs(float(_cmtf.get("alignment", 0.0) or 0.0)) > 0.05:
            _calign = max(-1.0, min(1.0, float(_cmtf["alignment"])))
            votes.append(_calign)
            aw(_boost(0.5, "candles_mtf"), "candles_mtf")

        # Fibonacci zone vote (weight 0.5 — mean-reversion near key levels).
        # Direction-aware (audit fix #4): the zone means "how deep price has
        # retraced the dominant leg", so its trade meaning flips with the leg:
        # up-leg deep retrace = bullish bounce; down-leg deep retrace (price
        # bounced far up a downtrend) = bearish continuation. Symmetric votes
        # replace the old always-bullish framing.
        fib_zone = indicators.get("fib_zone")
        fib_trend = indicators.get("fib_trend", "up")
        _fib_sign = 1.0 if fib_trend == "up" else -1.0
        _fib_before = len(votes)
        if fib_zone in ("618_786", "below_786"):
            votes.append(1.0 * _fib_sign)   # deep retracement of the leg
            aw(0.5, "fibonacci")
        elif fib_zone == "500_618":
            votes.append(0.5 * _fib_sign)   # moderate retracement
            aw(0.5, "fibonacci")
        elif fib_zone == "above_236":
            votes.append(-0.3 * _fib_sign)  # barely retraced → fade the leg end
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
            # Scale weight by the number of patterns AGREEING with the net
            # vote (audit fix: counting both sides let contradictory patterns
            # from one structure INCREASE conviction weight).
            if geo_vote > 0:
                geo_count = indicators.get("chart_patterns_bullish_count", 0)
            elif geo_vote < 0:
                geo_count = indicators.get("chart_patterns_bearish_count", 0)
            else:
                geo_count = 1
            scaled_weight = min(1.0, 0.7 * min(max(geo_count, 1), 3))  # cap at 3 patterns
            aw(scaled_weight, "chart_patterns")

        # ── Divergence Scanner voter ──
        div_votes = indicators.get("_div_votes")
        div_weights = indicators.get("_div_weights")
        if div_votes and div_weights:
            votes.extend(div_votes)
            weights.extend(_lw(w, "divergence") for w in div_weights)
            names.extend(["divergence"] * len(div_weights))

        # ── Volume Profile voter ──
        vp = indicators.get("_vp_result")
        if vp is not None:
            try:
                # Direction-aware VP votes (audit: the richer
                # volume_profile_to_confluence was dead code while this path
                # used a crude above/below-POC bias that ignored VAH/VAL and
                # the momentum-vs-contrarian split). Direction approximated
                # from the running vote sum, same convention as the
                # supply/demand voter below.
                from bot.core.volume_profile import volume_profile_to_confluence
                _pre = sum(v * w for v, w in zip(votes, weights))
                vp_v, vp_w = volume_profile_to_confluence(
                    vp, "LONG" if _pre >= 0 else "SHORT")
                votes.extend(vp_v)
                weights.extend(_lw(w, "volume_profile") for w in vp_w)
                names.extend(["volume_profile"] * len(vp_w))
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
        # Wave-position action multiplier (gated, default ON): scale each EW
        # voter's weight by where in the wave structure we are. A terminal wave
        # 5 / ending diagonal returns a <1 multiplier so it stops adding trend
        # conviction; a wave-3/4 pullback returns >1. Off → multiplier is 1.0
        # for every pattern, so the vote weights are byte-identical to before.
        _ew_action_on = CONFIG.analyzer.elliott_wave_action_enabled
        _wave_action = None
        if _ew_action_on:
            try:
                from bot.core.elliott import wave_action as _wave_action
            except Exception:
                _wave_action = None
        for ew_key, ew_weight, ew_label in _elliott_types:
            ew = indicators.get(ew_key)
            if ew:
                e_signal = ew.get("signal", "neutral")
                e_conf = ew.get("confidence", 0.5)
                _amult = 1.0
                _flip = False
                if _wave_action is not None:
                    try:
                        _act = _wave_action(ew)
                        _amult = float(_act.get("weight_mult", 1.0))
                        # Audit fix: a COMPLETED corrective (ABC / WXY) is
                        # labeled with the correction's own direction, but the
                        # doctrine ("correction complete — trend resumes",
                        # bias "with" the prior trend) means the tradeable
                        # signal is the RESUMPTION — the opposite direction.
                        # The old code took only weight_mult and cast the vote
                        # exactly backwards for these two voters.
                        if (ew_label in ("ew_corrective", "ew_wxy")
                                and _act.get("bias") == "with"
                                and _act.get("action") == "enter"):
                            _flip = True
                    except Exception:
                        _amult = 1.0
                if e_signal == "bullish":
                    votes.append(-e_conf if _flip else e_conf)
                    aw(_boost(ew_weight, ew_label) * _amult, ew_label)
                elif e_signal == "bearish":
                    votes.append(e_conf if _flip else -e_conf)
                    aw(_boost(ew_weight, ew_label) * _amult, ew_label)
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

        # Cross-degree Elliott alignment vote (gated, default ON). One
        # bounded vote from the all-timeframes wave map: agreement of nested
        # degrees (15m/1h/4h/1d, higher degrees weighted more) votes its
        # signed strength; a terminal 4h/1d structure (wave 5 / ending
        # diagonal) HALVES the weight — degree agreement into higher-degree
        # exhaustion is precisely where trend-continuation entries die.
        _ew_mtf = indicators.get("elliott_mtf")
        if _ew_mtf and abs(float(_ew_mtf.get("alignment", 0.0) or 0.0)) > 0.05:
            _align = max(-1.0, min(1.0, float(_ew_mtf["alignment"])))
            _w_align = 0.6
            if _ew_mtf.get("higher_degree_terminal"):
                _w_align *= 0.5
            votes.append(_align)
            aw(_boost(_w_align, "ew_mtf_align"), "ew_mtf_align")

        # Order flow votes (if available)
        if order_flow is not None:
            of_votes, of_weights, of_labels = OrderFlowAnalyzer.to_confluence_votes(order_flow)
            votes += of_votes
            weights += [_lw(_boost(w, l), l) for w, l in zip(of_weights, of_labels)]
            names.extend(of_labels)

        # Multi-timeframe votes (if available)
        if mtf_result is not None:
            mtf_votes, mtf_weights, mtf_labels = MTFConfluence.to_confluence_votes(mtf_result)
            votes += mtf_votes
            weights += [_lw(_boost(w, l), l) for w, l in zip(mtf_weights, mtf_labels)]
            names.extend(mtf_labels)

        # Smart money votes (if available)
        if smart_money_score is not None:
            sm_votes, sm_weights, sm_labels = SmartMoneyEngine.to_confluence_votes(smart_money_score)
            # Per-strategy-type weight adjustment for smart money signals
            sm_weight_mult = CONFIG.strategy_types.get_smart_money_weight(strategy_type)
            votes += sm_votes
            weights += [_lw(_boost(w * sm_weight_mult, l), l) for w, l in zip(sm_weights, sm_labels)]
            names.extend(sm_labels)

        # Sentiment voter
        if sentiment_engine is not None:
            try:
                sentiment_votes = sentiment_engine.to_confluence_votes()
                for _name, vote_val, vote_weight in sentiment_votes:
                    # Dilution guard (audit fix #16): a present-but-dataless
                    # sentiment engine returns a 0.0 vote — skip it rather
                    # than diluting the denominator at weight 0.6.
                    if _skip_missing and vote_val == 0.0:
                        continue
                    votes.append(vote_val)
                    aw(vote_weight, "sentiment")
            except Exception as _sent_exc:
                logger.warning("Sentiment engine vote failed: %s", _sent_exc)

        # On-chain flow voter (PR JJ) — present only when the gated provider
        # (ONCHAIN_ENABLED BYOK or ONCHAIN_FLOW_ENABLED keyless) produced a
        # snapshot upstream; a dataless snapshot votes nothing.
        if onchain_snapshot is not None:
            try:
                for _oc_name, _oc_vote, _oc_weight in onchain_snapshot.to_confluence_votes():
                    votes.append(_oc_vote)
                    aw(_oc_weight, _oc_name)
            except Exception as _oc_exc:
                logger.warning("On-chain vote failed: %s", _oc_exc)

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
            weights.extend(_lw(w, "liquidity_sweep") for w in sweep_w)
            names.extend(["liquidity_sweep"] * len(sweep_w))
        else:
            # Fallback (audit: detected-then-dropped): the chart-pattern
            # sweep detector was excluded from the aggregate because sweeps
            # have a dedicated voter — but when the dedicated module found
            # nothing, its evidence vanished entirely. Vote it here under the
            # SAME name so the pattern family cap treats it as one sweep
            # source and the two can never stack.
            _cs = indicators.get("chart_sweep")
            if _cs and _cs.get("signal") in ("bullish", "bearish"):
                votes.append(1.0 if _cs["signal"] == "bullish" else -1.0)
                aw(_lw(0.6 * float(_cs.get("confidence", 0.5)),
                       "liquidity_sweep"), "liquidity_sweep")

        # Fibonacci extensions voter (audit: detected-then-dropped). The
        # detector fires when an impulse + retracement projects extension
        # targets ahead — a mild continuation vote in the impulse direction,
        # weighted by the detector's own confidence. Skip-don't-dilute.
        _fx = indicators.get("fib_extensions")
        if _fx and _fx.get("signal") in ("bullish", "bearish"):
            votes.append(1.0 if _fx["signal"] == "bullish" else -1.0)
            aw(_lw(0.4 * float(_fx.get("confidence", 0.6)) / 0.6,
                   "fib_extension"), "fib_extension")

        # Smart-money-concept voters (audit Tier 3, default ON):
        #   fvg      — nearest UNFILLED fair value gap within 1 ATR acts as a
        #              magnet/support (bullish gap below) or resistance.
        #   premium_discount — lean toward value in the dealing range: deep
        #              discount (<0.25) is bullish, deep premium (>0.75)
        #              bearish; the middle votes nothing.
        _smc = indicators.get("_smc")
        if _smc:
            if _smc.get("fvg_weight"):
                votes.append(_smc["fvg_vote"])
                aw(_boost(_smc["fvg_weight"], "fvg"), "fvg")
            _pd = _smc.get("premium_discount")
            if _pd is not None and (_pd <= 0.25 or _pd >= 0.75):
                votes.append(1.0 if _pd <= 0.25 else -1.0)
                aw(_boost(0.5, "premium_discount"), "premium_discount")

        # Supply/Demand Zone voter
        sd_zones = indicators.get("_sd_zones")
        if sd_zones:
            try:
                # Need to determine trade direction from current votes
                pre_sum = sum(v * w for v, w in zip(votes, weights))
                approx_dir = "LONG" if pre_sum >= 0 else "SHORT"
                sd_v, sd_w = zones_to_confluence(sd_zones, signal.price, approx_dir)
                votes.extend(sd_v)
                # _lw: learned-weight coverage — every direct-append voter
                # must pass through the multiplier or the learner has a hole.
                weights.extend(_lw(w, "supply_demand") for w in sd_w)
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

            # PATTERN family cap (audit fix #12 extension): candlesticks,
            # geometric patterns, reversal bars, Wyckoff, harmonics and the
            # Elliott voters all read the same price STRUCTURE and can co-fire
            # up to ~7 combined weight. Same rule: only actively-voting
            # members, cap only ever reduces.
            _PATTERN_FAMILY = {
                "candlestick", "chart_patterns", "reversal", "wyckoff",
                "harmonic", "ew_impulse", "ew_corrective", "ew_diagonal",
                "ew_wxy", "elliott", "liquidity_sweep",
            }
            if len(names) == len(votes):
                p_active = [i for i, n in enumerate(names)
                            if n in _PATTERN_FAMILY and abs(votes[i]) > 1e-9]
                if len(p_active) > 1:
                    p_weight = sum(weights[i] for i in p_active)
                    p_cap = CONFIG.confluence.pattern_weight_cap
                    if p_weight > p_cap > 0:
                        p_scale = p_cap / p_weight
                        for i in p_active:
                            weights[i] *= p_scale

        # STRUCTURE + AGGRESSION family caps (audit: BOS double-count,
        # taker/CVD double-count). Same reduce-only discipline as the pattern
        # cap: mtf structure voters describe one structural fact from several
        # angles, and taker + of_cvd_trend are the same buy/sell aggression
        # read from two data sources — cap each family so correlated votes
        # can't stack past a single strong voter's budget.
        if len(names) == len(votes) == len(weights):
            for _fam, _cap in ((("mtf_structure", "mtf_bos", "mtf_choch",
                                 "mtf_alignment"), 1.5),
                               (("taker", "of_cvd_trend", "of_cvd_divergence"),
                                1.0)):
                _active = [i for i, n in enumerate(names)
                           if n in _fam and abs(votes[i]) > 1e-9]
                if len(_active) > 1:
                    _wsum = sum(weights[i] for i in _active)
                    if _wsum > _cap:
                        _scale = _cap / _wsum
                        for i in _active:
                            weights[i] *= _scale

        # Voter ablation hook (measurement only; default no-op). ABLATE_VOTERS
        # is a comma-list of voter names whose weight is zeroed here, so the
        # ablation harness can measure each voter's marginal out-of-sample
        # contribution (drop-one) without touching any voter's code. Empty/
        # unset → byte-identical to normal scoring. Parsed once and cached.
        _ablate = Analyzer._ablated_voters()
        if _ablate and len(names) == len(weights):
            for i, n in enumerate(names):
                if n in _ablate:
                    weights[i] = 0.0

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

        # Cross-layer confirmation bonus (gated, default OFF — measured). The
        # weighted average captures vote MAGNITUDE but not BREADTH: three
        # independent families each nudging +0.5 average to the same net
        # contribution as one strong vote, yet three independent confirmations
        # are more robust. When >=2 distinct families (liquidity / price-action
        # / structure / order-flow) agree with the net direction, nudge a
        # small bounded amount toward it. The family caps already prevent
        # SAME-concept stacking, so this can't re-introduce double-count.
        if (CONFIG.analyzer.cross_layer_confirmation_enabled
                and len(names) == len(votes) and abs(confluence - 0.5) > 1e-6):
            net_dir = 1.0 if confluence > 0.5 else -1.0
            _fam_map = {
                "liquidity": ("liquidity_sweep",),
                "price_action": ("candlestick", "reversal", "chart_patterns"),
                "structure": ("mtf_bos", "mtf_structure", "mtf_choch"),
                "order_flow": ("of_cvd_trend", "of_book_imbalance",
                               "of_whale_bias", "taker"),
            }
            agreeing = 0
            for _members in _fam_map.values():
                fam_vote = sum(votes[i] for i, n in enumerate(names)
                               if n in _members)
                if fam_vote * net_dir > 1e-9:
                    agreeing += 1
            if agreeing >= 2:
                # +0.03 per agreeing family beyond the first, capped at +0.09.
                bonus = min(0.09, 0.03 * (agreeing - 1)) * net_dir
                confluence += bonus

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
            # (required by Groq and some other providers). Audit fix #7: JSON
            # mode is now enforced on BOTH tiers of the OpenAI-compatible path
            # — the thesis tier previously relied on regex parsing alone. The
            # full-model prompt already specifies a JSON output contract.
            use_json_format = sdk_type != "anthropic"

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
                "- Never invent indicator values, patterns, or levels that are not in the "
                "provided data — cite only what was given to you.\n"
                "- Never express certainty about future price; probabilities only. No "
                "guarantees, ever.\n"
                "- SL distance should be ATR-based (1.5-3x ATR from entry).\n"
                "- TP distance should be at least 1.2x the SL distance.\n"
                "- Confidence below 0.55 means skip the trade.\n"
            )

            # Full-model calls always keep the rich prompt (it already carries
            # the JSON output contract, so json_object mode is compatible);
            # the terse prompt covers the cheap scan tier only.
            sys_content = (
                _TRADING_SYSTEM_PROMPT if use_full_model else
                "You are RUNECLAW, a risk-first crypto analyst. "
                "Return concise analysis in json format with keys: direction, confidence, reasoning."
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
                    # Audit fix: the Anthropic path previously omitted the
                    # configured temperature, so provider-default sampling
                    # applied only here. (Ignored when adaptive thinking is
                    # on — thinking requires default temperature.)
                    "temperature": CONFIG.llm.temperature,
                }
                # LIVE INCIDENT 2026-07-16: the Claude 5 family DEPRECATED the
                # temperature parameter — sending one returns 400 and took the
                # whole brain down to the rule engine ("every provider has
                # failed 20 analyses"). Omit it for those models up front.
                from bot.llm.provider import model_accepts_temperature
                if not model_accepts_temperature(model):
                    create_kwargs.pop("temperature", None)

                # Enable adaptive thinking for Opus 4.8+ (thesis tier)
                # Opus 4.8 ONLY supports adaptive thinking; manual budget_tokens
                # returns 400.  Adaptive lets the model decide how much to think.
                if use_full_model and "opus" in model.lower():
                    create_kwargs["thinking"] = {"type": "adaptive"}
                    # Extended thinking requires default sampling — the API
                    # rejects an explicit non-default temperature with it.
                    create_kwargs.pop("temperature", None)
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

                try:
                    response = await active_client.messages.create(**create_kwargs)
                except Exception as _temp_exc:
                    # Future-proof net: if a model we didn't anticipate rejects
                    # the temperature parameter, strip it and retry ONCE rather
                    # than failing every analysis until a code change ships.
                    _msg = str(_temp_exc).lower()
                    if ("temperature" in create_kwargs and "temperature" in _msg
                            and ("deprecated" in _msg or "unsupported" in _msg
                                 or "invalid_request" in _msg)):
                        audit(trade_log,
                              f"Anthropic rejected temperature for {model} — "
                              f"retrying without it",
                              action="llm_temperature_retry", result="RETRY")
                        create_kwargs.pop("temperature", None)
                        response = await active_client.messages.create(**create_kwargs)
                    else:
                        raise
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

            self._note_llm_ok()
            # Shadow A/B (opt-in): fire the same prompt at the challenger
            # model in the background. Records only — the shadow answer is
            # never read by the trading path, and errors are swallowed.
            if as_of is None:
                try:
                    from bot.llm.shadow_eval import SHADOW
                    SHADOW.maybe_spawn(self, prompt, _prompt_hash,
                                       signal.symbol, result)
                except Exception:
                    pass
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
            fallback_result = await self._try_llm_fallback(
                prompt, signal, use_full_model, failed_provider=failed_provider,
                is_admin=is_admin)
            if fallback_result is not None:
                fallback_result["source"] = f"LLM_FALLBACK_{fallback_result.get('_fallback_provider', 'UNKNOWN')}"
                fallback_result.pop("_fallback_provider", None)
                self._llm_cache.put(cache_key, fallback_result, signal.symbol)
                self._note_llm_ok()   # a fallback provider answered — brain is up
                return fallback_result
            # Every provider failed and we're running on the rule engine — the
            # live "quota exhausted → brain offline" signature. Record it so the
            # proactive monitor can alert once the streak crosses the threshold.
            self._note_llm_degraded(str(exc))
            # Auth failures condemn the KEY, not the provider: mark this
            # config's key invalid in the key-health registry so the next
            # tier resolution auto-heals onto the next candidate key instead
            # of failing forever (recurring live incident 2026-07-11).
            try:
                from bot.llm import key_health as _kh
                if (_kh.looks_like_auth_error(str(exc))
                        and active_cfg is not None
                        and getattr(active_cfg, "api_key", "")):
                    _kh.mark_invalid(active_cfg.api_key, str(exc),
                                     source="runtime-call")
            except Exception:
                pass
            result = self._rule_based_thesis(signal, indicators)
            if result is None:
                return None
            result["source"] = "RULE_ENGINE_FALLBACK"
            return result

    # ── LLM health tracking (proactive degrade alert) ────────────────
    def _note_llm_ok(self) -> None:
        """A live LLM (primary or fallback provider) answered. Clears the
        degrade streak so the monitor sees the brain as healthy again."""
        self._llm_degraded_streak = 0
        self._llm_degraded_since_monotonic = 0.0
        self._llm_last_error = ""
        self._llm_last_ok_monotonic = time.monotonic()

    def _note_llm_degraded(self, reason: str = "") -> None:
        """Every provider failed for one thesis and we fell to the rule engine.
        Advance the consecutive-fail streak; stamp when it began. ``reason`` is
        the primary provider's error — surfaced in /llmstatus and the degraded
        alert so the operator sees WHY (401 bad key / 404 model / 429 quota),
        not just that it failed (live incident: key swapped by the .env
        precedence flip, and nothing showed the auth error)."""
        if self._llm_degraded_streak == 0:
            self._llm_degraded_since_monotonic = time.monotonic()
        self._llm_degraded_streak += 1
        if reason:
            self._llm_last_error = str(reason)[:200]

    def llm_health(self) -> dict:
        """Snapshot of LLM brain health for the proactive monitor / /status.

        degraded_streak: consecutive theses where every provider failed and the
        rule engine answered instead (0 = healthy or LLM-by-design-off).
        degraded_seconds: how long the current all-fail streak has persisted.
        """
        now = time.monotonic()
        since = self._llm_degraded_since_monotonic
        return {
            "degraded_streak": self._llm_degraded_streak,
            "degraded_seconds": (now - since) if since > 0 else 0.0,
            "last_ok_seconds_ago": (
                (now - self._llm_last_ok_monotonic)
                if self._llm_last_ok_monotonic > 0 else None),
            "last_error": self._llm_last_error,
        }

    async def _try_llm_fallback(
        self,
        prompt: str,
        signal: MarketSignal,
        use_full_model: bool,
        failed_provider: Optional[str] = None,
        is_admin: bool = False,
    ) -> Optional[dict]:
        """Try alternate LLM providers when the primary fails (rate limit, error).

        Cascading order:
          1. Gemini (free tier, high quota)
          2. Groq (free tier, fast)
          3. Anthropic (paid, high quality) — admin callers only; the
             operator's Claude key is reserved for admin use, matching
             resolve_tier_config()'s hard non-admin guard for the primary path
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
            (LLMProvider.DEEPSEEK, "DEEPSEEK_API_KEY", "deepseek-chat"),
        ]
        if is_admin:
            fallback_chain.insert(
                3, (LLMProvider.ANTHROPIC, "ANTHROPIC_API_KEY", "claude-sonnet-5"))

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

        # Ground the thesis in the actual confluence electorate: the top
        # signed votes (name, vote, weight) that drove the score. The model
        # is told to cite these rather than invent its own indicator reads.
        _tv = indicators.get("_top_votes") or []
        if _tv:
            parts.append("TopVotes: " + " | ".join(
                f"{n} {v:+.2f}x{w:.2f}" for n, v, w in _tv))
            parts.append("Cite TopVotes names when reasoning; do not contradict their signs without saying why.")

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

    async def scan_read(self, signal: MarketSignal,
                        candles: list) -> Optional[dict]:
        """Directional read for the market SCANNER, driven by the SAME engine
        that decides trades — real indicators, regime detection, the full
        confluence electorate and the analyzer's own rule-based thesis — so the
        /scan card's direction and score agree with the per-asset analysis
        instead of a divergent naive RSI heuristic.

        Deliberately runs WITHOUT the LLM (fast, free, deterministic — it calls
        _rule_based_thesis directly, exactly the fallback the live analyzer uses
        when no LLM is configured) and WITHOUT side effects: no audit rows, no
        _record_no_trade, no _last_rejection_diag mutation, no learning writes.
        The only stateful call, _detect_regime, mutates per-symbol smoothing
        state the LIVE path relies on, so its two containers are snapshotted and
        restored — the scan leaves zero footprint on the trading analyzer.

        Returns {direction, score, regime, confluence} (direction may be None
        when the thesis is ambiguous), or None on insufficient/invalid data.
        Fail-safe: any error returns None so the caller falls back to its own
        heuristic rather than dropping the symbol.
        """
        try:
            if not candles or len(candles) < CONFIG.analyzer.min_candles:
                return None
            arr = np.asarray(candles, dtype=float)
            if arr.ndim != 2 or arr.shape[1] < 6:
                return None
            times = arr[:, 0]; opens = arr[:, 1]; highs = arr[:, 2]
            lows = arr[:, 3]; closes = arr[:, 4]; volumes = arr[:, 5]

            indicators = self._compute_indicators(
                highs, lows, closes, volumes, opens=opens, times=times)
            if indicators is None:
                return None

            # Same candlestick + geometric pattern enrichment the full pipeline
            # feeds to the confluence voters and the thesis (these are among the
            # most influential voters, so omitting them would reintroduce drift).
            try:
                cp = _detect_candlestick_patterns(opens, highs, lows, closes)
                if cp:
                    indicators["candle_patterns"] = cp
                    bull = [k for k, v in cp.items() if v == "bullish"]
                    bear = [k for k, v in cp.items() if v == "bearish"]
                    indicators["candle_bullish_count"] = len(bull)
                    indicators["candle_bearish_count"] = len(bear)
                    indicators["candle_bullish_strength"] = round(
                        sum(_CANDLE_STRENGTH.get(k, 1.0) for k in bull), 2)
                    indicators["candle_bearish_strength"] = round(
                        sum(_CANDLE_STRENGTH.get(k, 1.0) for k in bear), 2)
            except Exception:
                pass
            try:
                gp = scan_all_chart_patterns(opens, highs, lows, closes)
                if gp:
                    indicators["chart_patterns_geo"] = gp
            except Exception:
                pass

            # Regime: snapshot + restore the smoothing state so the scanner
            # never perturbs the live analyzer's per-symbol regime history.
            _hist_snapshot = list(self._regime_history)
            _regimes_snapshot = dict(self._current_regimes)
            try:
                regime = self._detect_regime(indicators, signal.symbol)
            finally:
                self._regime_history = _hist_snapshot
                self._current_regimes = _regimes_snapshot
            indicators["regime"] = regime.value

            confluence = self._score_confluence(indicators, regime, signal)
            indicators["confluence"] = confluence

            thesis = self._rule_based_thesis(signal, indicators)
            score_from_confluence = round(abs(confluence - 0.5) * 2, 3)
            if not thesis or not thesis.get("direction"):
                return {"direction": None, "score": score_from_confluence,
                        "regime": regime.value, "confluence": round(confluence, 3)}

            direction = thesis["direction"]
            conf = float(thesis.get("confidence", score_from_confluence))
            # Same counter-trend awareness the analyzer applies to confidence:
            # fading a strong trend is penalised (analyze() lines ~1118-1129).
            if (regime == Regime.TREND_UP and direction == "SHORT") or \
               (regime == Regime.TREND_DOWN and direction == "LONG"):
                conf *= 0.5
            return {"direction": direction,
                    "score": round(max(0.0, min(1.0, conf)), 3),
                    "regime": regime.value, "confluence": round(confluence, 3)}
        except Exception as exc:
            system_log.debug("scan_read failed for %s: %s", signal.symbol, exc)
            return None

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

        # Fib level support — direction-aware (audit fix #4): the zone reads
        # against the dominant leg, so a deep retrace supports LONG only on an
        # up-leg and SHORT on a down-leg.
        fib_trend = ind.get("fib_trend", "up")
        fib_bonus = 0.0
        if fib_zone in ("618_786", "below_786"):
            if fib_trend == "up" and direction == "LONG":
                fib_bonus = 0.08  # deep retracement of an up-leg supports long
            elif fib_trend == "down" and direction == "SHORT":
                fib_bonus = 0.08  # deep bounce in a down-leg supports short
        elif fib_zone == "above_236":
            if fib_trend == "up" and direction == "SHORT":
                fib_bonus = 0.05  # near swing high supports short
            elif fib_trend == "down" and direction == "LONG":
                fib_bonus = 0.05  # near swing low supports long

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
    Direction-aware Fibonacci retracement over the last 50 bars (audit fix #4).

    The dominant leg is inferred from the ORDER of the extremes in the window:
    swing low before swing high = an up-leg (retracement measured DOWN from the
    high — the legacy behaviour); swing high before swing low = a down-leg
    (retracement measured UP from the low). ``fib_zone`` always describes how
    deep price has retraced the dominant leg, so ``618_786``/``below_786`` mean
    "deep retracement" in BOTH framings and consumers interpret the zone
    together with ``fib_trend`` ("up"/"down").

    With FIB_DIRECTION_AWARE_ENABLED=false the legacy bullish-only framing is
    used regardless of leg order.
    """
    lookback = min(50, len(highs))
    seg_h = highs[-lookback:]
    seg_l = lows[-lookback:]

    swing_high = float(np.max(seg_h))
    swing_low = float(np.min(seg_l))
    diff = swing_high - swing_low

    if diff <= 0:
        return {"fib_swing_high": swing_high, "fib_swing_low": swing_low}

    trend = "up"
    if getattr(CONFIG.analyzer, "fib_direction_aware_enabled", False):
        idx_high = int(np.argmax(seg_h))
        idx_low = int(np.argmin(seg_l))
        if idx_high < idx_low:
            trend = "down"

    if trend == "up":
        # Up-leg: levels descend from the swing high (legacy formula).
        levels = {r: swing_high - r * diff for r in (0.236, 0.382, 0.5, 0.618, 0.786)}
    else:
        # Down-leg: price retraces UP from the swing low.
        levels = {r: swing_low + r * diff for r in (0.236, 0.382, 0.5, 0.618, 0.786)}

    fib_levels = {
        "fib_swing_high": round(swing_high, 6),
        "fib_swing_low": round(swing_low, 6),
        "fib_trend": trend,
        "fib_236": round(levels[0.236], 6),
        "fib_382": round(levels[0.382], 6),
        "fib_500": round(levels[0.5], 6),
        "fib_618": round(levels[0.618], 6),
        "fib_786": round(levels[0.786], 6),
    }

    # Zone = how deep price has retraced the dominant leg (0 = at the leg's
    # end, 1 = fully retraced). Identical to the legacy ladder for up-legs.
    price = float(closes[-1])
    if trend == "up":
        retrace = (swing_high - price) / diff
    else:
        retrace = (price - swing_low) / diff
    if retrace <= 0.236:
        fib_levels["fib_zone"] = "above_236"
    elif retrace <= 0.382:
        fib_levels["fib_zone"] = "236_382"
    elif retrace <= 0.500:
        fib_levels["fib_zone"] = "382_500"
    elif retrace <= 0.618:
        fib_levels["fib_zone"] = "500_618"
    elif retrace <= 0.786:
        fib_levels["fib_zone"] = "618_786"
    else:
        fib_levels["fib_zone"] = "below_786"

    return fib_levels


# Relative evidential strength per candlestick pattern (audit fix #14):
# three-candle formations outrank two-candle ones, which outrank single bars.
_CANDLE_STRENGTH: dict = {
    "three_white_soldiers": 1.5, "three_black_crows": 1.5,
    "morning_star": 1.4, "evening_star": 1.4,
    "bullish_engulfing": 1.2, "bearish_engulfing": 1.2,
    "marubozu": 1.1,
    "bullish_harami": 1.0, "bearish_harami": 1.0,
    "tweezer_top": 1.0, "tweezer_bottom": 1.0,
    "hammer": 1.0, "shooting_star": 1.0,
    "dragonfly_doji": 1.0, "gravestone_doji": 1.0,
}


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

    # Trend context (audit fix #13): reversal patterns need something to
    # reverse — a hammer in an uptrend or a shooting star in a downtrend is
    # geometry without meaning. Slope of the closes preceding the pattern
    # decides: -1 downtrend, +1 uptrend, 0 flat/unknown. When the flag is off
    # (or history is too short) context stays 0 and behaviour is legacy.
    trend_ctx = 0
    if (getattr(CONFIG.analyzer, "candle_trend_context_enabled", False)
            and len(closes) >= 10):
        _ctx = closes[-10:-2]  # bars before the pattern's final two candles
        _c0, _c1 = float(_ctx[0]), float(_ctx[-1])
        if _c0 > 0:
            if _c1 < _c0 * 0.995:
                trend_ctx = -1
            elif _c1 > _c0 * 1.005:
                trend_ctx = 1
    _ctx_on = getattr(CONFIG.analyzer, "candle_trend_context_enabled", False)

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
            # Long-legged doji: violent two-sided auction (both wicks long).
            # Neutral like the plain doji (display/LLM context, no vote) but
            # named — on higher degrees it marks a battle zone, not drift.
            if (last_upper >= 0.35 * last_range
                    and last_lower >= 0.35 * last_range):
                patterns["long_legged_doji"] = "neutral"

        # Directional doji (audit fix): a plain doji is neutral (display only),
        # but the dragonfly/gravestone variants carry direction — a dragonfly
        # at a downtrend low is buyers rejecting lower prices; a gravestone at
        # an uptrend high is the mirror. Gated by the same trend context as
        # hammer/shooting star so the geometry appears where it means reversal.
        if body_pct <= 0.10:
            if (last_lower >= 0.6 * last_range and last_upper <= 0.10 * last_range
                    and (not _ctx_on or trend_ctx == -1)):
                patterns["dragonfly_doji"] = "bullish"
            if (last_upper >= 0.6 * last_range and last_lower <= 0.10 * last_range
                    and (not _ctx_on or trend_ctx == 1)):
                patterns["gravestone_doji"] = "bearish"

        # Hammer: small body at top, long lower wick (>= 2x body). With trend
        # context on, it must appear in a DOWNTREND (it is a bottom reversal).
        if last_lower >= 2 * last_body and last_upper < last_body and body_pct < 0.4:
            if not _ctx_on or trend_ctx == -1:
                patterns["hammer"] = "bullish"

        # Shooting Star: small body at bottom, long upper wick (>= 2x body).
        # With trend context on, it must appear in an UPTREND (top reversal).
        if last_upper >= 2 * last_body and last_lower < last_body and body_pct < 0.4:
            if not _ctx_on or trend_ctx == 1:
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

    # Trend-context suppression for reversal patterns (audit fix): a bullish
    # reversal (engulfing / morning star / soldiers) printed INTO an
    # established uptrend has nothing to reverse — suppress only when the
    # context is OPPOSITE (lenient: flat/unknown context keeps legacy firing).
    _suppress_bull = _ctx_on and trend_ctx == 1
    _suppress_bear = _ctx_on and trend_ctx == -1

    # Bullish Engulfing: prev bearish, current bullish wraps prev entirely
    if prev_body < 0 and curr_body > 0 and curr_abs > prev_abs and not _suppress_bull:
        if float(c[-1]) > float(o[-2]) and float(o[-1]) < float(c[-2]):
            patterns["bullish_engulfing"] = "bullish"

    # Bearish Engulfing: prev bullish, current bearish wraps prev entirely
    if prev_body > 0 and curr_body < 0 and curr_abs > prev_abs and not _suppress_bear:
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
    # Morning/Evening Star. Crypto trades 24/7 so the textbook "gap" almost
    # never prints; the meaningful confirmation (audit fix #13) is that the
    # third candle closes back INTO the first candle's body (beyond its
    # midpoint) — without it, any small middle bar plus a bounce qualifies.
    b0, b1, b2 = float(body[-3]), float(body[-2]), float(body[-1])
    a0, a1, a2 = float(abs_body[-3]), float(abs_body[-2]), float(abs_body[-1])
    _body0_mid = (float(o[-3]) + float(c[-3])) / 2.0
    if b0 < 0 and a0 > 0 and a1 < a0 * 0.3 and b2 > 0 and a2 > a0 * 0.5 and not _suppress_bull:
        if not _ctx_on or float(c[-1]) > _body0_mid:
            patterns["morning_star"] = "bullish"

    if b0 > 0 and a0 > 0 and a1 < a0 * 0.3 and b2 < 0 and a2 > a0 * 0.5 and not _suppress_bear:
        if not _ctx_on or float(c[-1]) < _body0_mid:
            patterns["evening_star"] = "bearish"

    # Three White Soldiers / Black Crows (audit fix): monotone closes alone
    # let three +0.01% drifts fire the TOP-strength (1.5) pattern in any slow
    # trend. Standard definition requires three LONG bodies — each body >= 60%
    # of its own bar range AND >= the average body of the preceding ~10 bars —
    # with each candle opening within the previous candle's body.
    _avg_hist = np.abs(closes[:-3] - opens[:-3])[-10:]
    _avg_body = float(np.mean(_avg_hist)) if len(_avg_hist) > 0 else 0.0

    def _long_body(i: int) -> bool:
        rng = float(candle_range[i])
        ab = float(abs_body[i])
        if rng <= 0 or ab < 0.6 * rng:
            return False
        return _avg_body <= 0 or ab >= _avg_body

    def _opens_in_prev_body(i: int) -> bool:
        lo = min(float(o[i - 1]), float(c[i - 1]))
        hi = max(float(o[i - 1]), float(c[i - 1]))
        return lo <= float(o[i]) <= hi

    _long3 = _long_body(-3) and _long_body(-2) and _long_body(-1)
    _staircase = _opens_in_prev_body(-2) and _opens_in_prev_body(-1)

    # Three White Soldiers: three consecutive long bullish candles with higher
    # closes, each opening within the prior body (a reversal from a downtrend
    # — suppressed when the context is already an uptrend).
    if b0 > 0 and b1 > 0 and b2 > 0 and _long3 and _staircase and not _suppress_bull:
        if float(c[-2]) > float(c[-3]) and float(c[-1]) > float(c[-2]):
            patterns["three_white_soldiers"] = "bullish"

    # Three Black Crows: three consecutive long bearish candles with lower
    # closes, each opening within the prior body (top reversal — suppressed
    # when the context is already a downtrend).
    if b0 < 0 and b1 < 0 and b2 < 0 and _long3 and _staircase and not _suppress_bear:
        if float(c[-2]) < float(c[-3]) and float(c[-1]) < float(c[-2]):
            patterns["three_black_crows"] = "bearish"

    return patterns


# Strong single/two-bar reversal patterns that oppose a pullback-limit entry.
# Kept deliberately narrow (engulfing + pin bar + directional doji + marubozu)
# — the classic "reversal at the fill zone" shapes the veto targets.
_CANDLE_VETO_LONG = ("bearish_engulfing", "shooting_star", "gravestone_doji")
_CANDLE_VETO_SHORT = ("bullish_engulfing", "hammer", "dragonfly_doji")


def candle_entry_veto(patterns: dict, direction) -> Optional[str]:
    """Return a veto reason when the last closed bar prints a strong reversal
    pattern OPPOSING a pullback-limit entry, else None. Pure + harness-sweepable
    — takes the pattern dict from ``_detect_candlestick_patterns`` and the trade
    direction. LONG is vetoed by bearish reversals (and a bearish marubozu);
    SHORT by the bullish mirror. Empty/None patterns → no veto."""
    if not patterns:
        return None
    is_long = getattr(direction, "value", str(direction)).upper() == "LONG"
    opposing = _CANDLE_VETO_LONG if is_long else _CANDLE_VETO_SHORT
    hits = [p for p in opposing if p in patterns]
    maru = patterns.get("marubozu")
    if is_long and maru == "bearish":
        hits.append("bearish_marubozu")
    elif (not is_long) and maru == "bullish":
        hits.append("bullish_marubozu")
    if hits:
        return f"CANDLE_VETO: opposing reversal {', '.join(sorted(hits))}"
    return None


# Higher wave degrees carry more evidential weight for candle formations too:
# a 1d engulfing summarizes 24x the order flow of a 1h one.
_CANDLE_DEGREE_WEIGHT = {"15m": 0.7, "1h": 1.0, "4h": 1.3, "1d": 1.5}


def mtf_candle_map(mtf_candles: dict) -> dict:
    """Run the candlestick detector on the last CLOSED bars of EVERY supplied
    timeframe and produce a cross-degree agreement read.

    Per timeframe: net = strength-weighted (bull − bear) / (bull + bear)
    using ``_CANDLE_STRENGTH`` — the same normalization the primary-degree
    confluence vote uses. Degrees are then combined with
    ``_CANDLE_DEGREE_WEIGHT`` (a 1d formation outranks a 15m one) into an
    ``alignment`` in [-1, 1].

    Also records, for the HIGHER degrees (4h/1d), any veto-grade opposing
    reversal via ``candle_entry_veto`` so the entry veto can consult the
    degree above the trade, not just the primary bar.

    Returns ``{"by_tf": {tf: {"patterns", "net"}}, "alignment", "n_timeframes",
    "htf_veto": {"LONG": reason|None, "SHORT": reason|None}}``.
    Pure math; never raises; empty input yields a neutral map.
    """
    by_tf: dict = {}
    num = 0.0
    den = 0.0
    htf_veto: dict = {"LONG": None, "SHORT": None}
    for tf, series in (mtf_candles or {}).items():
        try:
            if not series or len(series) < 12:
                continue
            arr = np.asarray(series, dtype=float)
            opens, highs = arr[:, 1], arr[:, 2]
            lows, closes = arr[:, 3], arr[:, 4]
            pats = _detect_candlestick_patterns(opens, highs, lows, closes)
            if not pats:
                continue
            bull = sum(_CANDLE_STRENGTH.get(k, 1.0)
                       for k, v in pats.items() if v == "bullish")
            bear = sum(_CANDLE_STRENGTH.get(k, 1.0)
                       for k, v in pats.items() if v == "bearish")
            net = (bull - bear) / max(bull + bear, 1e-9) if (bull or bear) else 0.0
            w = _CANDLE_DEGREE_WEIGHT.get(tf, 1.0)
            num += w * net
            den += w
            by_tf[tf] = {"patterns": pats, "net": round(net, 4)}
            if tf in ("4h", "1d"):
                for _dir in ("LONG", "SHORT"):
                    if htf_veto[_dir] is None:
                        _v = candle_entry_veto(pats, _dir)
                        if _v:
                            htf_veto[_dir] = f"{tf}: {_v}"
        except Exception:  # noqa: BLE001 — one bad series never voids the map
            continue
    alignment = max(-1.0, min(1.0, (num / den) if den > 0 else 0.0))
    return {"by_tf": by_tf, "alignment": round(alignment, 4),
            "n_timeframes": len(by_tf), "htf_veto": htf_veto}


def _apply_mtf_candles(indicators: dict, mtf_candles: dict) -> None:
    """Store the cross-degree candle map as ``indicators["candle_mtf"]`` when
    at least two degrees resolved. Fail-open; mutates only on success."""
    cmap = mtf_candle_map(mtf_candles)
    if cmap.get("n_timeframes", 0) >= 2:
        indicators["candle_mtf"] = cmap


