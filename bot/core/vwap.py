"""bot/core/vwap.py — pure VWAP analytics.

Institutional-grade VWAP helpers that go beyond the single "above/below VWAP"
bias. Everything here is a pure function of arrays / scalars (no config reads, no
I/O) so it is trivially unit-testable and safe to call from the analyzer's hot
path. The analyzer decides *whether* to use each one via gated config flags.

Four capabilities, matched to how traders actually read VWAP:

  * band_reversion_signal   — fade statistical extremes (±1σ / ±2σ) back to VWAP
                              in range/chop regimes.
  * slope_adjusted_vote     — a directional VWAP bias is stronger when it agrees
                              with the VWAP's own slope; dampen it when it fights
                              a rising/falling anchor.
  * select_setup_anchor     — pick the VWAP anchor whose horizon matches the
                              setup (scalp→session, swing→rolling, position→full).
  * anchored_vwap_from_last_pivot — AVWAP reset at the most recent structural
                              ZigZag pivot (the institutional "anchor from the
                              swing" level), reusing the Elliott pivot engine.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# Which VWAP anchor best fits each strategy_type's hold horizon. A UTC-day
# session VWAP is meaningful for a scalp and near-useless for a multi-day swing,
# so longer setups anchor to a rolling / full-window mean instead.
_DEFAULT_ANCHOR_FOR_STRATEGY = {
    "scalp": "session",
    "intraday": "session",
    "swing": "rolling50",
    "position": "full",
}


def band_reversion_signal(price: Optional[float], bands: dict, in_range: bool) -> float:
    """Volatility-adaptive mean-reversion vote from the VWAP σ-bands.

    Only fires in a range/chop regime (``in_range``) — fading extremes into a
    trend is how you get run over. Returns a signed strength in [-1, 1]:

      * price at/below the −2σ band → +1.0 (stretched low, expect reversion up)
      * price at/below the −1σ band → +0.5
      * price at/above the +2σ band → −1.0 (stretched high, expect reversion down)
      * price at/above the +1σ band → −0.5
      * otherwise → 0.0

    The bands are the volatility-adaptive version of a fixed "% from VWAP"
    trigger: they widen for a volatile alt and tighten for BTC automatically.
    """
    if not in_range or price is None or price <= 0:
        return 0.0
    l1 = bands.get("vwap_lower_1")
    l2 = bands.get("vwap_lower_2")
    u1 = bands.get("vwap_upper_1")
    u2 = bands.get("vwap_upper_2")
    if l2 is not None and price <= l2:
        return 1.0
    if u2 is not None and price >= u2:
        return -1.0
    if l1 is not None and price <= l1:
        return 0.5
    if u1 is not None and price >= u1:
        return -0.5
    return 0.0


def slope_adjusted_vote(base_vote: float, slope_pct: Optional[float],
                        threshold: float = 0.02) -> float:
    """Scale a directional VWAP vote by whether it agrees with the VWAP slope.

    Price holding above a *rising* VWAP is trend continuation; holding above a
    *falling* VWAP is a weakening bias that often mean-reverts. Same directional
    vote, very different conviction — so:

      * with-trend (vote and slope agree in sign) → unchanged (confirmed)
      * against-trend (vote fights the slope)      → halved (dampened)
      * flat slope (|slope| ≤ threshold) or unknown → unchanged

    ``threshold`` is in percent (the unit of :func:`vwap_slope_pct`).
    """
    if base_vote == 0.0 or slope_pct is None:
        return base_vote
    if abs(slope_pct) <= threshold:
        return base_vote
    rising = slope_pct > 0
    with_trend = (base_vote > 0 and rising) or (base_vote < 0 and not rising)
    return base_vote if with_trend else base_vote * 0.5


def vwap_slope_pct(vwap_series, lookback: int = 10) -> Optional[float]:
    """Percent change of the (cumulative) VWAP series over the last ``lookback``
    bars. Returns None when the series is too short or the reference is zero."""
    if vwap_series is None or len(vwap_series) < lookback + 1:
        return None
    prev = float(vwap_series[-lookback - 1])
    if prev <= 0:
        return None
    return float((float(vwap_series[-1]) - prev) / prev * 100.0)


def select_setup_anchor(strategy_type: str, anchors: dict,
                        overrides: Optional[dict] = None) -> tuple:
    """Pick the VWAP anchor matching the setup's hold horizon.

    ``anchors`` maps anchor-kind → value, e.g.
    ``{"session": .., "rolling50": .., "full": ..}``. Returns ``(value, kind)``
    for the best available anchor, falling back through session → rolling → full
    if the preferred one is missing. Returns ``(None, "none")`` when nothing is
    available (caller then leaves the existing ``vwap`` untouched — fail-open).
    """
    mapping = dict(_DEFAULT_ANCHOR_FOR_STRATEGY)
    if overrides:
        mapping.update(overrides)
    preferred = mapping.get(strategy_type, "session")
    for kind in (preferred, "session", "rolling50", "full"):
        val = anchors.get(kind)
        if val is not None and val > 0:
            return float(val), kind
    return None, "none"


def anchored_vwap_from_last_pivot(highs, lows, closes, volumes,
                                  atr_mult: float = 1.5,
                                  min_bars: int = 5) -> Optional[float]:
    """Anchored VWAP (AVWAP) reset at the most recent structural pivot.

    Reuses the Elliott ATR-ZigZag engine to find the last genuine swing turn,
    then computes the volume-weighted average price from that pivot to now — the
    institutional "average price since the swing" level that acts as dynamic
    support/resistance. Returns None if the pivot engine finds nothing usable or
    data is insufficient / mismatched (fail-open).
    """
    n = len(closes)
    if n < min_bars or volumes is None or len(volumes) != n:
        return None
    try:
        from bot.core.elliott import atr_zigzag_pivots
        piv = atr_zigzag_pivots(highs, lows, closes, atr_mult=atr_mult)
    except Exception:
        return None
    idxs = [i for i, _ in piv.get("swing_highs", [])]
    idxs += [i for i, _ in piv.get("swing_lows", [])]
    idxs = [i for i in idxs if 0 <= i < n - 1]
    if not idxs:
        return None
    anchor = max(idxs)  # most recent structural turn
    seg_tp = (np.asarray(highs[anchor:], dtype=float)
              + np.asarray(lows[anchor:], dtype=float)
              + np.asarray(closes[anchor:], dtype=float)) / 3.0
    seg_vol = np.asarray(volumes[anchor:], dtype=float)
    sv = float(np.sum(seg_vol))
    if sv <= 0:
        return None
    return float(np.sum(seg_tp * seg_vol) / sv)
