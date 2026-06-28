"""
RUNECLAW — Quant Skill
bot/skills/quant_skill.py

A deep statistical analysis layer that sits between analyze_asset and the risk engine.
Adds: regime detection, volatility modeling, factor scoring, and edge statistics.

Usage (CLI):  runeclaw> quant_analyze symbol=BTC/USDT
Usage (code): await Quant AnalyzeSkill().execute(engine, symbol="ETH/USDT", timeframe="4h")

Author:    Humanoid Traders / RuneMule
Project:   RUNECLAW — Bitget GetClaw Hackathon
Safety:    Read-only. Does not execute trades. Outputs a QuantReport passed to risk engine.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ── Compatibility shim: use pydantic if available, else plain dataclass ──────
try:
    from pydantic import BaseModel, Field  # noqa: F401  (availability probe)

    _USE_PYDANTIC = True
except ImportError:  # pragma: no cover
    _USE_PYDANTIC = False

# ── Local imports (graceful if run standalone) ────────────────────────────────
try:
    from bot.skills.skill_registry import BaseSkill
    from bot.core.engine import RuneClawEngine
    from bot.utils.logger import audit, system_log, trade_log
except ImportError:  # pragma: no cover — standalone / test mode

    class BaseSkill:  # type: ignore
        name: str = ""
        description: str = ""

        async def execute(self, engine: Any, **kwargs: Any) -> str:
            raise NotImplementedError

    RuneClawEngine = Any  # type: ignore

    def audit(channel: Any, msg: str, **kw: Any) -> None:  # type: ignore
        print(f"[AUDIT] {msg}")

    system_log = trade_log = None  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  ENUMS & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

class MarketRegime(str, Enum):
    STRONG_TREND_UP   = "STRONG_TREND_UP"
    WEAK_TREND_UP     = "WEAK_TREND_UP"
    RANGING           = "RANGING"
    WEAK_TREND_DOWN   = "WEAK_TREND_DOWN"
    STRONG_TREND_DOWN = "STRONG_TREND_DOWN"
    HIGH_VOLATILITY   = "HIGH_VOLATILITY"   # overrides trend label when vol is extreme
    CHOPPY            = "CHOPPY"


class VolatilityState(str, Enum):
    LOW      = "LOW"      # ATR% < 1.0
    NORMAL   = "NORMAL"   # 1.0 – 2.5
    ELEVATED = "ELEVATED" # 2.5 – 4.0
    EXTREME  = "EXTREME"  # > 4.0


class EdgeStrength(str, Enum):
    STRONG   = "STRONG"    # quant_score >= 0.70
    MODERATE = "MODERATE"  # 0.45 – 0.69
    WEAK     = "WEAK"      # 0.25 – 0.44
    NONE     = "NONE"      # < 0.25  → recommend skip


# Minimum quant score to pass to risk engine (matches hackathon demo threshold)
QUANT_SCORE_GATE: float = 0.40

# Rolling window sizes (bars)
ADX_PERIOD:         int = 14
ATR_PERIOD:         int = 14
HURST_WINDOW:       int = 40
ZSCORE_WINDOW:      int = 20
MOMENTUM_FAST:      int = 5
MOMENTUM_SLOW:      int = 20
VOLUME_AVG_WINDOW:  int = 20


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FactorScores:
    """Individual factor contributions to the composite quant score (0–1 each)."""
    trend_factor:     float = 0.0   # ADX-derived directional strength
    momentum_factor:  float = 0.0   # price momentum vs recent baseline
    mean_reversion:   float = 0.0   # Z-score distance from rolling mean
    volume_confirm:   float = 0.0   # volume surge vs 20-bar average
    volatility_fit:   float = 0.0   # reward for normal vol, penalty for extreme
    hurst_factor:     float = 0.0   # Hurst exponent: trending vs mean-reverting
    vol_forecast:     float = 0.0   # GARCH(1,1) volatility forecast factor


@dataclass
class QuantReport:
    """
    Output of the Quant Skill.  Consumed by the risk engine as an additional gate.
    Fully auditable: every field has a plain-English explanation.
    """
    # Identity
    symbol:        str = ""
    timeframe:     str = "4h"
    timestamp:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bars_analyzed: int = 0

    # Regime
    regime:          MarketRegime    = MarketRegime.RANGING
    volatility_state: VolatilityState = VolatilityState.NORMAL

    # Raw indicator values (for auditability)
    adx:              float = 0.0   # Average Directional Index (0–100)
    atr_pct:          float = 0.0   # ATR as % of price
    hurst_exponent:   float = 0.5   # 0.5 = random walk, >0.5 = trending
    price_zscore:     float = 0.0   # standard deviations from 20-bar mean
    volume_ratio:     float = 1.0   # current volume / 20-bar avg volume
    momentum_ratio:   float = 0.0   # fast EMA / slow EMA - 1

    # Rolling Hurst & GARCH outputs
    hurst_trend:      str  = "STABLE"          # TRENDING_UP / TRENDING_DOWN / STABLE
    garch_forecast:   dict = field(default_factory=lambda: {
        "current_vol": 0.0, "forecast_vol": 0.0, "vol_expanding": False, "vol_ratio": 1.0
    })

    # Factor breakdown
    factors: FactorScores = field(default_factory=FactorScores)

    # Composite
    quant_score:    float       = 0.0   # 0.0 – 1.0
    edge_strength:  EdgeStrength = EdgeStrength.NONE

    # Gate decision
    passes_quant_gate: bool = False
    rejection_reason:  str  = ""

    # Human-readable explanation
    explanation: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol":             self.symbol,
            "timeframe":          self.timeframe,
            "timestamp":          self.timestamp.isoformat(),
            "bars_analyzed":      self.bars_analyzed,
            "regime":             self.regime.value,
            "volatility_state":   self.volatility_state.value,
            "adx":                round(self.adx, 2),
            "atr_pct":            round(self.atr_pct, 3),
            "hurst_exponent":     round(self.hurst_exponent, 3),
            "price_zscore":       round(self.price_zscore, 3),
            "volume_ratio":       round(self.volume_ratio, 2),
            "momentum_ratio":     round(self.momentum_ratio, 4),
            "hurst_trend":        self.hurst_trend,
            "garch_forecast":     self.garch_forecast,
            "factors": {
                "trend":         round(self.factors.trend_factor, 3),
                "momentum":      round(self.factors.momentum_factor, 3),
                "mean_reversion":round(self.factors.mean_reversion, 3),
                "volume_confirm":round(self.factors.volume_confirm, 3),
                "vol_fit":       round(self.factors.volatility_fit, 3),
                "hurst":         round(self.factors.hurst_factor, 3),
                "vol_forecast":  round(self.factors.vol_forecast, 3),
            },
            "quant_score":        round(self.quant_score, 3),
            "edge_strength":      self.edge_strength.value,
            "passes_quant_gate":  self.passes_quant_gate,
            "rejection_reason":   self.rejection_reason,
            "explanation":        self.explanation,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  PURE MATH FUNCTIONS  (no external dependencies — works anywhere)
# ═══════════════════════════════════════════════════════════════════════════════

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average.  Returns same-length list (head filled with SMA)."""
    if not values or period <= 0:
        return values[:]
    k = 2.0 / (period + 1)
    result: list[float] = []
    sma_seed = statistics.mean(values[:period]) if len(values) >= period else values[0]
    result.append(sma_seed)
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range (last value)."""
    if len(closes) < 2:
        return 0.0
    trs = [_true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, len(closes))]
    window = trs[-period:] if len(trs) >= period else trs
    return statistics.mean(window) if window else 0.0


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """
    Simplified ADX (Average Directional Index).
    Returns 0–100.  > 25 = trending, < 20 = ranging.
    """
    if len(closes) < period + 1:
        return 0.0

    dm_plus, dm_minus, trs = [], [], []
    for i in range(1, len(closes)):
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        dm_plus.append(up   if up > down and up > 0 else 0.0)
        dm_minus.append(down if down > up and down > 0 else 0.0)
        trs.append(_true_range(highs[i], lows[i], closes[i - 1]))

    def _smooth(data: list[float], p: int) -> list[float]:
        if len(data) < p:
            return data[:]
        smoothed = [sum(data[:p])]
        for v in data[p:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / p + v)
        return smoothed

    atr_s   = _smooth(trs, period)
    dmp_s   = _smooth(dm_plus, period)
    dmm_s   = _smooth(dm_minus, period)

    dx_list = []
    for a, p_, m in zip(atr_s, dmp_s, dmm_s):
        if a == 0:
            continue
        di_plus  = 100 * p_ / a
        di_minus = 100 * m / a
        denom    = di_plus + di_minus
        dx_list.append(100 * abs(di_plus - di_minus) / denom if denom else 0.0)

    return statistics.mean(dx_list[-period:]) if dx_list else 0.0


def _hurst_exponent(prices: list[float], window: int = 40) -> float:
    """
    Simplified Hurst Exponent via R/S analysis.
    H > 0.55 → trending  |  H ≈ 0.5 → random walk  |  H < 0.45 → mean-reverting
    """
    data = prices[-window:]
    n = len(data)
    if n < 10:
        return 0.5

    mean_val = statistics.mean(data)
    deviations = [v - mean_val for v in data]
    cumulative = [sum(deviations[:i+1]) for i in range(n)]
    r = max(cumulative) - min(cumulative)
    s = statistics.stdev(data) if n > 1 else 1e-9
    if s == 0 or r == 0:
        return 0.5

    # R/S at multiple sub-windows (simplified two-point log-log regression)
    half_n = n // 2
    half_data = data[:half_n]
    half_mean = statistics.mean(half_data)
    half_dev  = [v - half_mean for v in half_data]
    half_cum  = [sum(half_dev[:i+1]) for i in range(half_n)]
    r2 = max(half_cum) - min(half_cum)
    s2 = statistics.stdev(half_data) if half_n > 1 else 1e-9

    rs1 = r / s if s else 0.5
    rs2 = r2 / s2 if s2 and r2 else 0.5
    if rs1 <= 0 or rs2 <= 0:
        return 0.5

    try:
        h = math.log(rs1 / rs2) / math.log(n / half_n)
    except (ValueError, ZeroDivisionError):
        h = 0.5

    return max(0.0, min(1.0, h))


def _zscore(prices: list[float], window: int = 20) -> float:
    """Z-score of the last price relative to the rolling window."""
    data = prices[-window:]
    if len(data) < 3:
        return 0.0
    mean_val = statistics.mean(data)
    std_val  = statistics.stdev(data)
    if std_val == 0:
        return 0.0
    return (prices[-1] - mean_val) / std_val


def _momentum_ratio(closes: list[float], fast: int = 5, slow: int = 20) -> float:
    """Fast EMA / Slow EMA - 1.  Positive = upward momentum."""
    if len(closes) < slow:
        return 0.0
    fast_ema = _ema(closes, fast)[-1]
    slow_ema = _ema(closes, slow)[-1]
    return (fast_ema / slow_ema - 1.0) if slow_ema else 0.0


def _volume_ratio(volumes: list[float], window: int = 20) -> float:
    """Current volume / rolling average volume."""
    if len(volumes) < 2:
        return 1.0
    avg = statistics.mean(volumes[-window - 1 : -1]) if len(volumes) > window else statistics.mean(volumes[:-1])
    return (volumes[-1] / avg) if avg else 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  FACTOR SCORING  (each factor → 0.0 – 1.0)
# ═══════════════════════════════════════════════════════════════════════════════

def _score_trend(adx: float) -> float:
    """ADX 0→25 = 0.0, 25→40 = linear 0.0→1.0, 40+ = 1.0."""
    if adx < 25:
        return 0.0
    if adx >= 40:
        return 1.0
    return (adx - 25) / 15.0


def _score_momentum(mom_ratio: float) -> float:
    """Sigmoid-like: strong momentum in either direction → high score."""
    abs_mom = abs(mom_ratio)
    # Cap at ±5% change (0.05); beyond that = max score
    return min(1.0, abs_mom / 0.03)


def _score_mean_reversion(zscore: float) -> float:
    """
    For mean-reversion strategies: large Z-score = strong edge.
    Returns 0 near mean, 1.0 at |Z| >= 2.0.
    """
    return min(1.0, abs(zscore) / 2.0)


def _score_volume(vol_ratio: float) -> float:
    """Volume 1x = 0.4, 2x = 0.8, 3x+ = 1.0."""
    if vol_ratio < 1.0:
        return 0.1
    if vol_ratio >= 3.0:
        return 1.0
    return 0.1 + (vol_ratio - 1.0) / 2.0 * 0.9


def _score_volatility_fit(atr_pct: float) -> VolatilityState:
    """Classify volatility and return the state enum."""
    if atr_pct < 1.0:
        return VolatilityState.LOW
    if atr_pct < 2.5:
        return VolatilityState.NORMAL
    if atr_pct < 4.0:
        return VolatilityState.ELEVATED
    return VolatilityState.EXTREME


def _vol_fit_score(state: VolatilityState) -> float:
    """NORMAL = full score (reward clean conditions); extremes = penalty."""
    return {
        VolatilityState.LOW:      0.5,
        VolatilityState.NORMAL:   1.0,
        VolatilityState.ELEVATED: 0.6,
        VolatilityState.EXTREME:  0.2,
    }[state]


def _score_hurst(hurst: float) -> float:
    """H > 0.55 = trending edge; H < 0.45 = mean-reversion edge; H ≈ 0.5 = noise."""
    distance_from_random = abs(hurst - 0.5)
    return min(1.0, distance_from_random / 0.2)  # full score at 0.3 or 0.7


# ── Rolling Hurst & GARCH helpers ────────────────────────────────────────────

def _rolling_hurst(prices: list[float], window: int = 100) -> list[float]:
    """
    Compute Hurst exponent on rolling windows of size `window`.
    Returns a list of Hurst values, one per valid window position.
    """
    results: list[float] = []
    if len(prices) < window:
        # Not enough data: compute a single Hurst on whatever we have
        results.append(_hurst_exponent(prices, min(len(prices), HURST_WINDOW)))
        return results
    for i in range(window, len(prices) + 1):
        segment = prices[i - window : i]
        results.append(_hurst_exponent(segment, window))
    return results


def _hurst_trend(rolling_hurst: list[float]) -> str:
    """
    Detect Hurst regime-shift signals based on 0.5 crossings.
    - "TRENDING_UP": Hurst crossing above 0.5 from below
    - "TRENDING_DOWN": Hurst crossing below 0.5 from above
    - "STABLE": no recent crossing
    Looks at the last 5 values (or fewer if the list is short).
    """
    if len(rolling_hurst) < 2:
        return "STABLE"
    lookback = min(5, len(rolling_hurst))
    recent = rolling_hurst[-lookback:]
    # Scan for crossings in the recent window (newest first)
    for i in range(len(recent) - 1, 0, -1):
        prev_val = recent[i - 1]
        curr_val = recent[i]
        if prev_val < 0.5 and curr_val >= 0.5:
            return "TRENDING_UP"
        if prev_val >= 0.5 and curr_val < 0.5:
            return "TRENDING_DOWN"
    return "STABLE"


def _garch_forecast(
    returns: list[float],
    omega: float = 0.00001,
    alpha: float = 0.1,
    beta: float = 0.85,
) -> dict:
    """
    Simple GARCH(1,1) variance forecast with fixed parameters.
    sigma2(t+1) = omega + alpha * r(t)^2 + beta * sigma2(t)
    Returns dict with: current_vol, forecast_vol, vol_expanding, vol_ratio.
    """
    if len(returns) < 2:
        return {
            "current_vol": 0.0,
            "forecast_vol": 0.0,
            "vol_expanding": False,
            "vol_ratio": 1.0,
        }
    # Initialize variance with sample variance
    sigma2 = statistics.variance(returns) if len(returns) > 1 else omega
    if sigma2 == 0:
        sigma2 = omega

    # Run GARCH filter through all returns
    for r in returns:
        sigma2 = omega + alpha * (r * r) + beta * sigma2

    current_vol = math.sqrt(sigma2)
    # One-step-ahead forecast
    last_r = returns[-1]
    forecast_sigma2 = omega + alpha * (last_r * last_r) + beta * sigma2
    forecast_vol = math.sqrt(forecast_sigma2)

    vol_ratio = forecast_vol / current_vol if current_vol > 0 else 1.0

    return {
        "current_vol": round(current_vol, 6),
        "forecast_vol": round(forecast_vol, 6),
        "vol_expanding": forecast_vol > current_vol,
        "vol_ratio": round(vol_ratio, 4),
    }


def _vol_regime_forecast(ohlcv: list[list[float]]) -> dict:
    """Run GARCH(1,1) on close-to-close returns from OHLCV data."""
    if len(ohlcv) < 3:
        return _garch_forecast([])
    closes = [bar[4] for bar in ohlcv]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]
    return _garch_forecast(returns)


def _score_vol_forecast(garch: dict) -> float:
    """
    Score the GARCH forecast factor (0-1).
    Expanding vol with high ratio = high score (opportunity for vol-based edges).
    Contracting vol = moderate score. Flat = low score.
    """
    ratio = garch.get("vol_ratio", 1.0)
    # Distance from 1.0 indicates change; cap at 1.0
    return min(1.0, abs(ratio - 1.0) / 0.3)


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  REGIME CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════════

def _classify_regime(
    adx: float,
    momentum_ratio: float,
    atr_pct: float,
    hurst: float,
) -> MarketRegime:
    if atr_pct >= 4.0:
        return MarketRegime.HIGH_VOLATILITY
    if adx < 20 and hurst < 0.50:
        return MarketRegime.CHOPPY
    if adx < 25:
        return MarketRegime.RANGING
    direction_up = momentum_ratio >= 0
    if adx >= 35:
        return MarketRegime.STRONG_TREND_UP if direction_up else MarketRegime.STRONG_TREND_DOWN
    return MarketRegime.WEAK_TREND_UP if direction_up else MarketRegime.WEAK_TREND_DOWN


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  COMPOSITE SCORER
# ═══════════════════════════════════════════════════════════════════════════════

# Factor weights — must sum to 1.0
FACTOR_WEIGHTS = {
    "trend":          0.25,
    "momentum":       0.20,
    "mean_reversion": 0.15,
    "volume_confirm": 0.20,
    "vol_fit":        0.10,
    "hurst":          0.05,
    "vol_forecast":   0.05,
}


def _composite_score(factors: FactorScores) -> float:
    return (
        factors.trend_factor     * FACTOR_WEIGHTS["trend"]
        + factors.momentum_factor  * FACTOR_WEIGHTS["momentum"]
        + factors.mean_reversion   * FACTOR_WEIGHTS["mean_reversion"]
        + factors.volume_confirm   * FACTOR_WEIGHTS["volume_confirm"]
        + factors.volatility_fit   * FACTOR_WEIGHTS["vol_fit"]
        + factors.hurst_factor     * FACTOR_WEIGHTS["hurst"]
        + factors.vol_forecast     * FACTOR_WEIGHTS["vol_forecast"]
    )


def _edge_strength(score: float) -> EdgeStrength:
    if score >= 0.70:
        return EdgeStrength.STRONG
    if score >= 0.45:
        return EdgeStrength.MODERATE
    if score >= 0.25:
        return EdgeStrength.WEAK
    return EdgeStrength.NONE


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  CORE ANALYSIS FUNCTION  (pure, no I/O, fully testable)
# ═══════════════════════════════════════════════════════════════════════════════

def run_quant_analysis(
    symbol:    str,
    timeframe: str,
    ohlcv:     list[list[float]],  # [[ts, open, high, low, close, volume], ...]
) -> QuantReport:
    """
    Run the full quant pipeline on raw OHLCV data.
    Returns a QuantReport suitable for risk engine consumption.
    No side effects. Safe to call from any context.
    """
    report = QuantReport(symbol=symbol, timeframe=timeframe, bars_analyzed=len(ohlcv))

    if len(ohlcv) < HURST_WINDOW + 5:
        report.rejection_reason = f"Insufficient bars: {len(ohlcv)} < {HURST_WINDOW + 5}"
        report.explanation = f"⚠️ Not enough data to compute quant metrics (need {HURST_WINDOW + 5}+ bars)."
        return report

    # Unpack OHLCV columns
    opens   = [b[1] for b in ohlcv]
    highs   = [b[2] for b in ohlcv]
    lows    = [b[3] for b in ohlcv]
    closes  = [b[4] for b in ohlcv]
    volumes = [b[5] for b in ohlcv]

    last_close = closes[-1]

    # ── Raw indicators ────────────────────────────────────────────────────────
    atr_abs             = _atr(highs, lows, closes, ATR_PERIOD)
    report.atr_pct      = (atr_abs / last_close * 100) if last_close else 0.0
    report.adx          = _adx(highs, lows, closes, ADX_PERIOD)
    report.hurst_exponent = _hurst_exponent(closes, HURST_WINDOW)
    report.price_zscore = _zscore(closes, ZSCORE_WINDOW)
    report.volume_ratio = _volume_ratio(volumes, VOLUME_AVG_WINDOW)
    report.momentum_ratio = _momentum_ratio(closes, MOMENTUM_FAST, MOMENTUM_SLOW)

    # ── Rolling Hurst & trend detection ──────────────────────────────────────
    r_hurst = _rolling_hurst(closes, window=100)
    h_trend = _hurst_trend(r_hurst)

    # ── GARCH(1,1) volatility forecast ───────────────────────────────────────
    garch = _vol_regime_forecast(ohlcv)

    # ── Regime & volatility classification ───────────────────────────────────
    report.volatility_state = _score_volatility_fit(report.atr_pct)
    report.regime = _classify_regime(
        report.adx, report.momentum_ratio, report.atr_pct, report.hurst_exponent
    )

    # ── Individual factor scores ──────────────────────────────────────────────
    f = FactorScores(
        trend_factor    = _score_trend(report.adx),
        momentum_factor = _score_momentum(report.momentum_ratio),
        mean_reversion  = _score_mean_reversion(report.price_zscore),
        volume_confirm  = _score_volume(report.volume_ratio),
        volatility_fit  = _vol_fit_score(report.volatility_state),
        hurst_factor    = _score_hurst(report.hurst_exponent),
        vol_forecast    = _score_vol_forecast(garch),
    )
    report.factors = f
    report.hurst_trend = h_trend
    report.garch_forecast = garch

    # ── Composite score ───────────────────────────────────────────────────────
    report.quant_score   = round(_composite_score(f), 4)
    report.edge_strength = _edge_strength(report.quant_score)

    # ── Gate decision ─────────────────────────────────────────────────────────
    if report.volatility_state == VolatilityState.EXTREME:
        report.passes_quant_gate = False
        report.rejection_reason  = "EXTREME_VOLATILITY: ATR% > 4.0 — risk is unquantifiable"
    elif report.regime == MarketRegime.CHOPPY:
        report.passes_quant_gate = False
        report.rejection_reason  = "CHOPPY_MARKET: ADX < 20 and Hurst < 0.50 — no directional edge"
    elif report.quant_score < QUANT_SCORE_GATE:
        report.passes_quant_gate = False
        report.rejection_reason  = f"LOW_QUANT_SCORE: {report.quant_score:.3f} < {QUANT_SCORE_GATE} threshold"
    else:
        report.passes_quant_gate = True

    # ── Human-readable explanation ────────────────────────────────────────────
    regime_emoji = {
        MarketRegime.STRONG_TREND_UP:   "📈 Strong Uptrend",
        MarketRegime.WEAK_TREND_UP:     "↗️  Weak Uptrend",
        MarketRegime.RANGING:           "↔️  Ranging",
        MarketRegime.WEAK_TREND_DOWN:   "↘️  Weak Downtrend",
        MarketRegime.STRONG_TREND_DOWN: "📉 Strong Downtrend",
        MarketRegime.HIGH_VOLATILITY:   "⚡ High Volatility",
        MarketRegime.CHOPPY:            "🌊 Choppy / No Edge",
    }[report.regime]

    gate_line = (
        f"✅ QUANT GATE: PASS (score {report.quant_score:.2f})"
        if report.passes_quant_gate
        else f"❌ QUANT GATE: REJECTED — {report.rejection_reason}"
    )

    report.explanation = (
        f"RUNECLAW QUANT REPORT — {symbol} [{timeframe}]\n"
        f"{'─' * 42}\n"
        f"Regime:       {regime_emoji}\n"
        f"Volatility:   {report.volatility_state.value} (ATR {report.atr_pct:.2f}%)\n"
        f"ADX:          {report.adx:.1f}  {'(trending)' if report.adx > 25 else '(weak/ranging)'}\n"
        f"Hurst (H):    {report.hurst_exponent:.3f}  "
        f"{'→ trending memory' if report.hurst_exponent > 0.55 else '→ mean-reverting' if report.hurst_exponent < 0.45 else '→ near random walk'}\n"
        f"Hurst Trend:  {h_trend}\n"
        f"GARCH Vol:    curr={garch['current_vol']:.4f}  fcast={garch['forecast_vol']:.4f}  "
        f"{'EXPANDING' if garch['vol_expanding'] else 'CONTRACTING'}\n"
        f"Price Z-Score:{report.price_zscore:+.2f}  {'(extended)' if abs(report.price_zscore) > 2 else ''}\n"
        f"Vol Ratio:    {report.volume_ratio:.2f}x  {'🔥 SPIKE' if report.volume_ratio >= 2.0 else ''}\n"
        f"Momentum:     {report.momentum_ratio:+.3f}\n"
        f"{'─' * 42}\n"
        f"Factors (0–1):\n"
        f"  Trend:          {f.trend_factor:.2f}  (weight {FACTOR_WEIGHTS['trend']:.0%})\n"
        f"  Momentum:       {f.momentum_factor:.2f}  (weight {FACTOR_WEIGHTS['momentum']:.0%})\n"
        f"  Mean Reversion: {f.mean_reversion:.2f}  (weight {FACTOR_WEIGHTS['mean_reversion']:.0%})\n"
        f"  Volume Confirm: {f.volume_confirm:.2f}  (weight {FACTOR_WEIGHTS['volume_confirm']:.0%})\n"
        f"  Vol Fit:        {f.volatility_fit:.2f}  (weight {FACTOR_WEIGHTS['vol_fit']:.0%})\n"
        f"  Hurst Edge:     {f.hurst_factor:.2f}  (weight {FACTOR_WEIGHTS['hurst']:.0%})\n"
        f"  Vol Forecast:   {f.vol_forecast:.2f}  (weight {FACTOR_WEIGHTS['vol_forecast']:.0%})\n"
        f"{'─' * 42}\n"
        f"Composite Score: {report.quant_score:.3f} → {report.edge_strength.value}\n"
        f"{gate_line}"
    )

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# 7b. LIVE DATA PIPELINE & TELEGRAM FORMATTER
# ═══════════════════════════════════════════════════════════════════════════════

async def analyze_live(symbol: str, exchange=None) -> dict:
    """
    Live data pipeline: fetch OHLCV and run quant analysis.
    - If exchange is provided, fetches 1h OHLCV (500 bars) via exchange.fetch_ohlcv
    - If exchange is None, falls back to synthetic data for demo mode
    Returns the full quant analysis result as a dict.
    """
    ohlcv: list[list[float]] = []

    if exchange is not None:
        try:
            raw = await exchange.fetch_ohlcv(symbol, '1h', limit=500)
            ohlcv = [[float(c) for c in bar] for bar in raw]
        except Exception:
            ohlcv = []

    if not ohlcv:
        sym_seed = hash(symbol) % (2**31)
        ohlcv = _generate_synthetic_ohlcv(500, seed=sym_seed)

    report = run_quant_analysis(symbol, '1h', ohlcv)
    return report.to_dict()


def format_quant_for_telegram(result: dict) -> str:
    """
    Format quant analysis result as a Telegram HTML message with War Room styling.
    Uses horizontal bars, dots for confidence, regime indicators.
    """
    symbol = result.get("symbol", "???")
    regime = result.get("regime", "UNKNOWN")
    score = result.get("quant_score", 0.0)
    edge = result.get("edge_strength", "NONE")
    vol_state = result.get("volatility_state", "NORMAL")
    passes = result.get("passes_quant_gate", False)
    factors = result.get("factors", {})
    hurst_t = result.get("hurst_trend", "STABLE")
    garch = result.get("garch_forecast", {})

    # Confidence dots: 1 dot per 0.2 of score (max 5)
    filled = int(score / 0.2)
    filled = min(filled, 5)
    dots = "\u25cf " * filled + "\u25cb " * (5 - filled)

    # Regime indicator
    regime_map = {
        "STRONG_TREND_UP": "\u2191\u2191 STRONG UP",
        "WEAK_TREND_UP": "\u2191 WEAK UP",
        "RANGING": "\u2194 RANGING",
        "WEAK_TREND_DOWN": "\u2193 WEAK DOWN",
        "STRONG_TREND_DOWN": "\u2193\u2193 STRONG DOWN",
        "HIGH_VOLATILITY": "\u26a1 HIGH VOL",
        "CHOPPY": "\u223c CHOPPY",
    }
    regime_label = regime_map.get(regime, regime)

    # Gate
    gate_icon = "\u2705" if passes else "\u274c"
    gate_text = "PASS" if passes else "FAIL"

    # GARCH line
    garch_vol = garch.get("vol_expanding", False)
    garch_dir = "EXPANDING" if garch_vol else "CONTRACTING"

    # Factor bars: short horizontal bar scaled 0-1
    def _bar(val: float) -> str:
        filled_b = int(val * 8)
        return "\u2588" * filled_b + "\u2591" * (8 - filled_b)

    lines = [
        "<b>\u2501\u2501 RUNECLAW QUANT \u2501\u2501</b>",
        f"<b>{symbol}</b>",
        "",
        f"<b>Regime:</b> {regime_label}",
        f"<b>Hurst Trend:</b> {hurst_t}",
        f"<b>Vol State:</b> {vol_state}",
        f"<b>GARCH:</b> {garch_dir}",
        "",
        "\u2501\u2501 Factors \u2501\u2501",
        f"Trend:     {_bar(factors.get('trend', 0))} {factors.get('trend', 0):.2f}",
        f"Momentum:  {_bar(factors.get('momentum', 0))} {factors.get('momentum', 0):.2f}",
        f"MeanRev:   {_bar(factors.get('mean_reversion', 0))} {factors.get('mean_reversion', 0):.2f}",
        f"Volume:    {_bar(factors.get('volume_confirm', 0))} {factors.get('volume_confirm', 0):.2f}",
        f"VolFit:    {_bar(factors.get('vol_fit', 0))} {factors.get('vol_fit', 0):.2f}",
        f"Hurst:     {_bar(factors.get('hurst', 0))} {factors.get('hurst', 0):.2f}",
        f"VolFcast:  {_bar(factors.get('vol_forecast', 0))} {factors.get('vol_forecast', 0):.2f}",
        "",
        f"<b>Score:</b> {score:.3f} {dots.strip()}",
        f"<b>Edge:</b> {edge}",
        f"<b>Gate:</b> {gate_icon} {gate_text}",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  SKILL CLASS  (plugs into SkillRegistry)
# ═══════════════════════════════════════════════════════════════════════════════

class QuantAnalyzeSkill(BaseSkill):
    """
    Quant Skill — deep statistical layer.

    Command:  quant_analyze [symbol=BTC/USDT] [timeframe=4h]
    Output:   QuantReport (printed + returned as attribute for chaining)
    Safety:   READ ONLY. No trades. No state mutation.
    """

    name        = "quant_analyze"
    description = "quant_analyze [symbol=X/USDT] [timeframe=4h] -- Deep quant: regime, volatility model, factor scoring, Hurst exponent, edge gate"

    # Store last report for chaining with risk engine
    last_report: QuantReport | None = None

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        symbol    = kwargs.get("symbol", "BTC/USDT").upper()
        timeframe = kwargs.get("timeframe", "4h")

        # ── Fetch OHLCV data ──────────────────────────────────────────────────
        ohlcv: list[list[float]] = []

        try:
            # Use engine's public exchange accessor
            if hasattr(engine, "get_exchange"):
                exchange = await engine.get_exchange()
                if exchange is not None:
                    raw = await exchange.fetch_ohlcv(symbol, timeframe, limit=150)
                    ohlcv = [[float(c) for c in bar] for bar in raw]
            elif hasattr(engine, "exchange") and engine.exchange is not None:
                raw = await engine.exchange.fetch_ohlcv(symbol, timeframe, limit=150)
                ohlcv = [[float(c) for c in bar] for bar in raw]
        except Exception as exc:
            # Graceful fallback: generate synthetic data for demo/test
            audit(system_log, f"[QUANT] Exchange fetch failed ({exc}), using synthetic data",
                  action="quant_data_fallback")
            sym_seed = hash(symbol) % (2**31)
            ohlcv = _generate_synthetic_ohlcv(150, seed=sym_seed)

        if not ohlcv:
            sym_seed = hash(symbol) % (2**31)
            ohlcv = _generate_synthetic_ohlcv(150, seed=sym_seed)

        # ── Run analysis ──────────────────────────────────────────────────────
        report = run_quant_analysis(symbol, timeframe, ohlcv)
        self.last_report = report

        # ── Audit log ─────────────────────────────────────────────────────────
        audit(
            system_log,
            f"[QUANT] {symbol} score={report.quant_score:.3f} "
            f"regime={report.regime.value} gate={'PASS' if report.passes_quant_gate else 'FAIL'}",
            action="quant_analyze",
            data=report.to_dict(),
        )

        return report.explanation


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  SYNTHETIC DATA GENERATOR  (for demo / offline / test)
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_synthetic_ohlcv(bars: int = 150, seed: int = 42) -> list[list[float]]:
    """
    Generate realistic-looking OHLCV bars using a seeded random walk.
    Used when the exchange is unavailable (demo mode, CI, offline).
    """
    import random
    rng = random.Random(seed)

    price  = 65_000.0
    volume = 500.0
    ts     = 1_700_000_000_000  # ms timestamp base
    result = []

    for _ in range(bars):
        change_pct = rng.gauss(0.001, 0.018)  # slight upward drift
        close  = price * (1 + change_pct)
        high   = max(price, close) * (1 + abs(rng.gauss(0, 0.005)))
        low    = min(price, close) * (1 - abs(rng.gauss(0, 0.005)))
        vol    = volume * rng.lognormvariate(0, 0.5)

        result.append([float(ts), float(price), float(high), float(low), float(close), float(vol)])
        price  = close
        volume = vol * 0.7 + 500 * 0.3  # mean-revert volume
        ts    += 14_400_000  # 4h in ms

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 10. REGISTRATION HELPER  (call from skill_registry.py)
# ═══════════════════════════════════════════════════════════════════════════════

def register_quant_skill(registry: Any) -> None:
    """
    Call this from build_default_registry() in skill_registry.py:

        from bot.skills.quant_skill import register_quant_skill
        register_quant_skill(registry)
    """
    registry.register(QuantAnalyzeSkill())


# ═══════════════════════════════════════════════════════════════════════════════
# 11. STANDALONE TEST  (python bot/skills/quant_skill.py)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio

    async def _demo() -> None:
        print("=" * 60)
        print("  RUNECLAW QUANT SKILL — Standalone Demo")
        print("  Humanoid Traders | Bitget GetClaw Hackathon")
        print("=" * 60, "\n")

        ohlcv = _generate_synthetic_ohlcv(150, seed=7)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)

        print(report.explanation)
        print("\n── JSON (audit log format) ──")
        import json
        print(json.dumps(report.to_dict(), indent=2))

    asyncio.run(_demo())
