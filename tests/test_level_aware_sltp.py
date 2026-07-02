"""Tier 3: level-aware SL/TP snapping (bot/core/levels.py).

SL/TP were pure ATR multiples, blind to every level the bot detects — a LONG
stop could sit one tick above a triple-tested wick low (the sweep magnet the
bot's own liquidity module models) and a TP routinely parked just past a
resistance and missed. The snap is tighten-only for SL (sizing and the
leverage margin-risk cap assume at most the ATR distance) and clips TP just
inside an opposing wall at 50-105% of the target distance.
"""
from __future__ import annotations

import numpy as np

from bot.config import CONFIG
from bot.core.levels import Level, gather_levels, snap_sl_tp


def _series_with_support(support=95.0, n=60, price=100.0):
    """Flat-ish series with three wick touches of `support`."""
    closes = np.full(n, price)
    highs = closes + 0.5
    lows = closes - 0.5
    for i in (20, 30, 40):   # three separated wick touches -> a scored level
        lows[i] = support
    return highs, lows, closes


class TestGatherLevels:
    def test_swing_wicks_clustered_and_touch_scored(self):
        highs, lows, closes = _series_with_support()
        levels = gather_levels(highs, lows, closes, atr=1.0)
        sup = [lv for lv in levels if abs(lv.price - 95.0) < 0.3]
        assert sup, "the triple-tested wick low must be a level"
        assert sup[0].touches >= 3
        assert sup[0].score >= 1.5

    def test_vp_and_pdh_pdl_included(self):
        highs, lows, closes = _series_with_support()
        day_ms = 86_400_000
        # last bar in day D, first half in day D-1
        times = np.array([(2 * day_ms) + i * 3_600_000 for i in range(len(closes))],
                         dtype=float)
        levels = gather_levels(highs, lows, closes, atr=1.0, times=times,
                               vp={"poc": 99.0, "vah": 101.0, "val": 97.0})
        kinds = {lv.kind for lv in levels}
        assert "poc" in kinds
        assert "pdh" in kinds or "pdl" in kinds

    def test_empty_on_no_atr(self):
        highs, lows, closes = _series_with_support()
        assert gather_levels(highs, lows, closes, atr=0.0) == []


class TestSnapSlTp:
    def test_long_sl_tightens_under_support(self):
        levels = [Level(95.0, "swing", touches=3, score=2.0)]
        sl, tp, note = snap_sl_tp("LONG", 100.0, 93.0, 108.0, levels, atr=1.0)
        # ATR stop 93 -> snapped just UNDER the 95 support (95 - 0.25 ATR).
        assert sl == 94.75 and "SL snapped" in note
        assert tp == 108.0

    def test_long_sl_never_widens(self):
        # Support BELOW the ATR stop must not pull the stop wider.
        levels = [Level(90.0, "swing", touches=3, score=2.0)]
        sl, tp, _ = snap_sl_tp("LONG", 100.0, 93.0, 108.0, levels, atr=1.0)
        assert sl == 93.0

    def test_long_tp_clips_inside_wall(self):
        levels = [Level(107.0, "pdh", touches=2, score=2.0)]
        sl, tp, note = snap_sl_tp("LONG", 100.0, 93.0, 108.0, levels, atr=1.0)
        assert tp == 106.9 and "TP clipped" in note

    def test_long_tp_ignores_far_or_near_walls(self):
        # A wall at 30% of the distance (too near) or 120% (beyond) is ignored.
        for wall in (102.0, 110.0):
            _, tp, _ = snap_sl_tp("LONG", 100.0, 93.0, 108.0,
                                  [Level(wall, "swing", score=2.0)], atr=1.0)
            assert tp == 108.0

    def test_short_mirrors(self):
        levels = [Level(105.0, "swing", touches=3, score=2.0),
                  Level(93.0, "pdl", touches=2, score=2.0)]
        sl, tp, note = snap_sl_tp("SHORT", 100.0, 107.0, 92.0, levels, atr=1.0)
        assert sl == 105.25          # over the 105 resistance
        assert tp == 93.1            # inside the 93 support wall
        assert "SL snapped" in note and "TP clipped" in note

    def test_weak_levels_ignored(self):
        levels = [Level(95.0, "round", touches=1, score=0.8)]
        sl, tp, _ = snap_sl_tp("LONG", 100.0, 93.0, 108.0, levels, atr=1.0)
        assert (sl, tp) == (93.0, 108.0)


class TestFlagDefault:
    def test_default_on(self):
        assert CONFIG.analyzer.level_aware_sltp_enabled is True
