"""QC-3 pattern-detector correctness round (quant audit, each re-verified).

1. Rectangle: the fade vote had no inside-range check — a confirmed
   breakdown below support emitted a BULLISH vote (price < mid) and a
   breakout above resistance a BEARISH one. A broken range now returns
   no pattern at all.
2. Elliott impulse: after a COMPLETE 5-wave count, a close back beyond
   the wave-4 extreme is an invalidated/finished impulse — the old code
   labeled it "wave 4" and returned a with-trend vote anyway.
3. Elliott symmetry: the bullish count earned golden-ratio and
   W2-retracement confidence bonuses that the bearish mirror never got,
   systematically under-weighting downtrend impulses. Mirrored inputs
   now score identically.
4. Divergence recency: every adjacent pivot pair in the window voted —
   a divergence whose second pivot was 30+ bars old (long resolved)
   still emitted at full weight each scan. Stale pairs are now gated.
"""

from __future__ import annotations

import numpy as np

from bot.core.chart_patterns import detect_elliott_impulse, detect_rectangle
from bot.core.divergence import _check_divergence

# Dummy OHLC arrays — every call below injects precomputed swings, so these
# only need to exist and carry the close used by the detector.
def _arrs(last_close: float, n: int = 60):
    highs = np.full(n, 999.0)
    lows = np.full(n, 1.0)
    closes = np.full(n, last_close)
    return highs, lows, closes


RANGE_SWINGS = {
    "swing_highs": [(10, 110.0), (20, 110.4), (30, 109.8)],
    "swing_lows": [(15, 100.0), (25, 100.3), (35, 99.9)],
}


class TestRectangleInsideRangeOnly:
    def test_fade_votes_inside_the_range(self):
        h, l, c = _arrs(102.0)
        r = detect_rectangle(h, l, c, swings=RANGE_SWINGS)
        assert r and r["signal"] == "bullish"          # near support -> fade up
        h, l, c = _arrs(108.0)
        r = detect_rectangle(h, l, c, swings=RANGE_SWINGS)
        assert r and r["signal"] == "bearish"          # near resistance -> fade down

    def test_breakdown_no_longer_votes_bullish(self):
        h, l, c = _arrs(97.0)                          # closed BELOW support
        assert detect_rectangle(h, l, c, swings=RANGE_SWINGS) is None

    def test_breakout_no_longer_votes_bearish(self):
        h, l, c = _arrs(113.0)                         # closed ABOVE resistance
        assert detect_rectangle(h, l, c, swings=RANGE_SWINGS) is None


BULL_5WAVE = {
    "swing_lows": [(0, 100.0), (10, 104.0), (20, 118.0)],
    "swing_highs": [(5, 110.0), (15, 126.0), (25, 130.0)],
}
# Exact reflection of BULL_5WAVE around 115 — identical wave lengths.
BEAR_5WAVE = {
    "swing_highs": [(0, 130.0), (10, 126.0), (20, 112.0)],
    "swing_lows": [(5, 120.0), (15, 104.0), (25, 100.0)],
}


class TestElliottInvalidation:
    def test_intact_bullish_count_is_wave_5(self):
        h, l, c = _arrs(125.0)                         # above the wave-4 low
        r = detect_elliott_impulse(h, l, c, swings=BULL_5WAVE)
        assert r and r["signal"] == "bullish" and "wave 5" in r["description"]

    def test_close_below_wave4_low_invalidates_bullish_count(self):
        h, l, c = _arrs(115.0)                         # back below w4 low 118
        assert detect_elliott_impulse(h, l, c, swings=BULL_5WAVE) is None

    def test_close_above_wave4_high_invalidates_bearish_count(self):
        h, l, c = _arrs(115.0)                         # back above w4 high 112
        assert detect_elliott_impulse(h, l, c, swings=BEAR_5WAVE) is None


class TestElliottSymmetry:
    def test_mirrored_full_counts_score_identically(self):
        h, l, c = _arrs(125.0)
        bull = detect_elliott_impulse(h, l, c, swings=BULL_5WAVE)
        h, l, c = _arrs(105.0)                         # mirror of 125 around 115
        bear = detect_elliott_impulse(h, l, c, swings=BEAR_5WAVE)
        assert bull and bear
        assert bull["confidence"] == bear["confidence"]

    def test_bearish_partial_earns_the_w2_retrace_bonus(self):
        # W1 130->120 (len 10), W2 retraces to 126.18 (0.618 of W1), W3 -> 103.
        # Three swing lows are required to enter the bearish branch at all;
        # with only two swing highs the FULL count can't form, so this lands
        # on the partial (waves 1-3) return.
        swings = {"swing_highs": [(0, 130.0), (10, 126.18)],
                  "swing_lows": [(5, 120.0), (15, 103.0), (25, 105.0)]}
        h, l, c = _arrs(104.0)
        r = detect_elliott_impulse(h, l, c, swings=swings)
        assert r and r["signal"] == "bearish"
        assert r["confidence"] == 0.55                 # 0.50 + 0.05 bonus
        assert "w2_retrace" in r["key_levels"]


def _v_series(centers, depths, length=60, base=110.0):
    arr = np.full(length, base, dtype=float)
    for c, d in zip(centers, depths):
        for k in range(-7, 8):
            i = c + k
            if 0 <= i < length:
                arr[i] = min(arr[i], base - d * (1 - abs(k) / 8))
    return arr


class TestDivergenceRecency:
    def test_stale_second_pivot_no_longer_votes(self):
        # Price lower low + indicator higher low — a textbook regular bullish
        # divergence, but its second pivot is ~34 bars old: long resolved.
        price = _v_series([10, 25], [10.0, 15.0])
        ind = _v_series([10, 25], [8.0, 4.0])
        signals = _check_divergence(price, ind, lookback=60)
        assert [s for s in signals if s.div_type == "regular_bullish"] == []

    def test_fresh_second_pivot_still_votes(self):
        price = _v_series([30, 50], [10.0, 15.0])
        ind = _v_series([30, 50], [8.0, 4.0])
        signals = _check_divergence(price, ind, lookback=60)
        assert any(s.div_type == "regular_bullish" for s in signals)
