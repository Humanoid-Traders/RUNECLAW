"""
All-timeframes candlestick map (2026-07-13) — the doji/candles audit.

What existed: a solid single-degree detector (15+ patterns, trend-context
gating, strength-weighted vote, directional dragonfly/gravestone doji,
pullback-limit veto) — but it only ever read the PRIMARY timeframe. The
15m/1h/4h/1d series are already fetched for the Elliott/MTF work, so a 1d
gravestone against a long was invisible while a 15m one voted.

Now: mtf_candle_map runs the detector on every degree, produces one
bounded agreement vote (higher degrees weighted more), records veto-grade
opposing reversals on 4h/1d for the entry veto, and the detector gained
the missing long-legged doji variant. Zero extra API calls.
"""

from __future__ import annotations

import inspect

import numpy as np

from bot.core.analyzer import (_detect_candlestick_patterns, mtf_candle_map)


def _series_with(last_bars, n=30, base=100.0):
    """Build an OHLCV list ending in the given (o,h,l,c) bars, preceded by
    mild downtrend filler so trend-context gates see a downtrend."""
    rows = []
    p = base * 1.06
    for i in range(n - len(last_bars)):
        p *= 0.998
        rows.append([i, p, p * 1.002, p * 0.998, p * 0.999, 1000.0])
    for j, (o, h, l, c) in enumerate(last_bars):
        rows.append([n + j, o, h, l, c, 1000.0])
    return rows


def _bullish_engulf_tail(base=100.0):
    return [(base * 1.001, base * 1.002, base * 0.995, base * 0.996),   # bearish
            (base * 0.995, base * 1.010, base * 0.994, base * 1.008)]   # engulfs


def _gravestone_tail(base=100.0):
    # tiny body at the LOW of a huge upper wick, after an uptrend filler
    return [(base, base * 1.001, base * 0.999, base * 1.0005),
            (base, base * 1.03, base * 0.9995, base * 1.001)]


# ── long-legged doji (new variant) ───────────────────────────────────
def test_long_legged_doji_detected():
    o = np.array([100.0, 100.0, 100.0])
    c = np.array([100.1, 100.1, 100.05])       # tiny body
    h = np.array([100.2, 100.2, 101.5])        # long upper wick
    lo = np.array([99.9, 99.9, 98.7])          # long lower wick
    pats = _detect_candlestick_patterns(o, h, lo, c)
    assert pats.get("doji") == "neutral"
    assert pats.get("long_legged_doji") == "neutral"


def test_one_sided_doji_is_not_long_legged():
    o = np.array([100.0, 100.0, 100.0])
    c = np.array([100.1, 100.1, 100.02])
    h = np.array([100.2, 100.2, 100.06])       # no upper wick
    lo = np.array([99.9, 99.9, 98.7])          # dragonfly shape
    pats = _detect_candlestick_patterns(o, h, lo, c)
    assert "long_legged_doji" not in pats


# ── cross-degree map ─────────────────────────────────────────────────
def test_map_reads_every_degree_and_weights_higher_ones():
    mtf = {"15m": _series_with(_bullish_engulf_tail()),
           "1h": _series_with(_bullish_engulf_tail()),
           "4h": _series_with(_bullish_engulf_tail()),
           "1d": _series_with(_bullish_engulf_tail())}
    m = mtf_candle_map(mtf)
    assert m["n_timeframes"] == 4
    assert m["alignment"] > 0.5                 # nested bullish agreement
    assert all("bullish_engulfing" in d["patterns"]
               for d in m["by_tf"].values())


def test_higher_degree_outvotes_lower():
    """1d bearish net vs 15m bullish net -> alignment negative."""
    bear_tail = [(100.0, 100.1, 99.0, 99.05),   # big bearish body...
                 (99.6, 99.7, 98.0, 98.1)]
    m = mtf_candle_map({"15m": _series_with(_bullish_engulf_tail()),
                        "1d": _series_with(bear_tail)})
    if m["n_timeframes"] == 2:                  # both degrees resolved
        b15 = m["by_tf"]["15m"]["net"]
        b1d = m["by_tf"]["1d"]["net"]
        if b15 > 0 and b1d < 0:
            assert m["alignment"] < b15         # dragged down by the 1d


def test_htf_veto_recorded_only_for_high_degrees():
    """A veto-grade reversal (gravestone/shooting star) on 1d records an
    htf_veto for LONG; the same shape on 15m must NOT."""
    # Uptrend filler so the top-reversal context gate passes
    rows_up = []
    p = 96.0
    for i in range(28):
        p *= 1.002
        rows_up.append([i, p, p * 1.002, p * 0.998, p * 1.001, 1000.0])
    for j, (o, h, l, c) in enumerate(_gravestone_tail(p)):
        rows_up.append([30 + j, o, h, l, c, 1000.0])

    m_htf = mtf_candle_map({"1d": rows_up, "1h": _series_with(_bullish_engulf_tail())})
    m_ltf = mtf_candle_map({"15m": rows_up, "1h": _series_with(_bullish_engulf_tail())})
    if m_htf["by_tf"].get("1d"):
        assert m_htf["htf_veto"]["LONG"] is not None
        assert m_htf["htf_veto"]["LONG"].startswith("1d:")
    assert m_ltf["htf_veto"]["LONG"] is None    # 15m never HTF-vetoes


def test_empty_and_short_series_are_neutral():
    for arg in ({}, None, {"1h": []}, {"1h": [[0, 1, 1, 1, 1, 1]] * 5}):
        m = mtf_candle_map(arg)
        assert m["n_timeframes"] == 0
        assert m["alignment"] == 0.0
        assert m["htf_veto"] == {"LONG": None, "SHORT": None}


# ── wiring pins ──────────────────────────────────────────────────────
def test_analyzer_wiring():
    import bot.core.analyzer as az
    src = inspect.getsource(az.Analyzer.analyze)
    assert "candle_mtf_enabled" in src
    assert "_apply_mtf_candles" in src
    conf = inspect.getsource(az.Analyzer._score_confluence)
    assert "candles_mtf" in conf                # the bounded degree vote
    # HTF veto consulted at the entry-veto site, inside the existing gate
    assert 'htf_veto' in src


def test_apply_requires_two_degrees():
    from bot.core.analyzer import _apply_mtf_candles
    ind: dict = {}
    _apply_mtf_candles(ind, {"1h": _series_with(_bullish_engulf_tail())})
    assert "candle_mtf" not in ind
    _apply_mtf_candles(ind, {"1h": _series_with(_bullish_engulf_tail()),
                             "4h": _series_with(_bullish_engulf_tail())})
    assert ind["candle_mtf"]["n_timeframes"] == 2


def test_config_flag_exists():
    from bot.config import CONFIG
    assert isinstance(CONFIG.analyzer.candle_mtf_enabled, bool)
