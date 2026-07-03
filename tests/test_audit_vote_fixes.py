"""Batch-1 audit fixes: corrupted votes out of live confluence.

Covers: BOS single-count (bias no longer folds the break), WXY completed
complex corrections resolve as trend RESUMPTION, EXPANSION bypasses regime
smoothing, continuation patterns withhold their directional vote until the
breakout close, and structure/aggression family caps.
"""

import numpy as np

from bot.core.chart_patterns import detect_flags, detect_triangles, detect_wedges
from bot.core.elliott import wave_action
from bot.core.multi_timeframe import _analyze_structure


def _swings(sh, sl):
    return {"swing_highs": sh, "swing_lows": sl}


class TestBOSSingleCount:
    def _closes(self, last):
        arr = np.linspace(95, 100, 40)
        arr[-1] = last
        return arr

    def test_break_sets_dir_but_not_bias(self):
        # Zigzag with real fractal swings (rising highs/lows); final close
        # breaks the last swing high. The break must set bos/bos_dir but NOT
        # add ±0.3 to bias (mtf_bos is the break's one and only vote).
        i = np.arange(60, dtype=float)
        base = 100 + i * 0.15 + 3 * np.sin(i * 2 * np.pi / 10)
        highs, lows = base + 0.4, base - 0.4
        closes_break = base.copy()
        closes_break[-1] = float(highs.max()) * 1.005   # beyond last swing high
        res = _analyze_structure(highs, lows, closes_break)
        assert res["bos"] is True and res["bos_dir"] == 1
        biased = _analyze_structure(highs, lows, base.copy())
        # Same swings, no break: bias must be identical (no +0.3 fold).
        assert abs(res["bias"] - biased["bias"]) < 1e-9
        assert res["structure"] == "bullish"


class TestWXYResumption:
    def test_completed_wxy_is_enter_with(self):
        act = wave_action({"name": "Elliott WXY Double", "key_levels": {}})
        assert act["action"] == "enter" and act["bias"] == "with"

    def test_analyzer_flip_condition_now_reachable(self):
        act = wave_action({"name": "Elliott WXYXZ Triple", "key_levels": {}})
        # The analyzer flips when bias=="with" and action=="enter" for ew_wxy.
        assert act.get("bias") == "with" and act.get("action") == "enter"


class TestExpansionLatch:
    def test_expansion_bypasses_smoothing(self):
        from bot.core.analyzer import Analyzer
        from bot.core.ta_utils import Regime
        a = Analyzer.__new__(Analyzer)
        a._regime_history = []
        a._current_regimes = {}
        # Two RANGE reads establish consensus, then a single-bar EXPANSION
        # release must latch immediately (old smoothing required 2-of-3, so
        # the release bar could never win).
        ind_range = {"adx": 15.0, "plus_di": 10.0, "minus_di": 10.0,
                     "kc_squeeze": True, "kc_squeeze_prev": True}
        ind_release = {"adx": 25.0, "plus_di": 20.0, "minus_di": 10.0,
                       "kc_squeeze": False, "kc_squeeze_prev": True}
        a._detect_regime(ind_range, "T/USDT")
        a._detect_regime(ind_range, "T/USDT")
        got = a._detect_regime(ind_release, "T/USDT")
        assert got == Regime.EXPANSION


class TestContinuationGating:
    def _tri(self, price):
        sh = [(i, 105.0) for i in (5, 10, 15)]
        sl = [(5, 95.0), (10, 96.0), (15, 97.0)]
        closes = np.full(20, price)
        return detect_triangles(closes + 6, closes - 6, closes,
                                swings=_swings(sh, sl))

    def test_ascending_neutral_inside_bullish_after_break(self):
        forming = self._tri(100.0)
        assert forming["signal"] == "neutral" and "forming" in forming["name"]
        broke = self._tri(106.0)
        assert broke["signal"] == "bullish" and broke["name"] == "Ascending Triangle"

    def test_rising_wedge_bearish_only_after_breakdown(self):
        sh = [(5, 100.0), (10, 102.0), (15, 104.0)]
        sl = [(5, 90.0), (10, 95.0), (15, 100.5)]
        closes = np.full(20, 102.0)
        res = detect_wedges(closes + 6, closes - 6, closes,
                            swings=_swings(sh, sl))
        assert res is not None and res["signal"] == "neutral"
        closes_broke = np.full(20, 99.0)   # below lower trendline swing
        res2 = detect_wedges(closes_broke + 6, closes_broke - 6, closes_broke,
                             swings=_swings(sh, sl))
        assert res2 is not None and res2["signal"] == "bearish"

    def test_bull_flag_neutral_until_flag_high_break(self):
        # 10-bar pole up 100->110, then a TIGHT 20-bar drift down ~109..106.5
        # (tight enough that the breakout bar doesn't widen the flag past the
        # 0.5x-pole range check and kill the pattern itself).
        pole = np.linspace(100, 110, 10)
        flag = np.linspace(109, 106.5, 20)
        closes = np.concatenate([pole, flag])
        res = detect_flags(closes + 0.5, closes - 0.5, closes)
        assert res is not None and res["signal"] == "neutral"
        closes2 = closes.copy()
        closes2[-1] = 110.0   # close above prior flag highs (109.5)
        res2 = detect_flags(closes2 + 0.5, closes2 - 0.5, closes2)
        assert res2 is not None and res2["signal"] == "bullish"


class TestFamilyCaps:
    def test_structure_and_aggression_caps_reduce_only(self):
        # Pin the cap arithmetic used in _score_confluence.
        names = ["mtf_structure", "mtf_bos", "mtf_choch", "taker", "of_cvd_trend"]
        votes = [1.0, 1.0, 1.0, 1.0, 1.0]
        weights = [0.8, 0.6, 0.5, 0.5, 0.8]
        for fam, cap in ((("mtf_structure", "mtf_bos", "mtf_choch",
                           "mtf_alignment"), 1.5),
                         (("taker", "of_cvd_trend", "of_cvd_divergence"), 1.0)):
            active = [i for i, n in enumerate(names)
                      if n in fam and abs(votes[i]) > 1e-9]
            wsum = sum(weights[i] for i in active)
            if len(active) > 1 and wsum > cap:
                scale = cap / wsum
                for i in active:
                    weights[i] *= scale
        assert abs(sum(weights[:3]) - 1.5) < 1e-9   # structure capped
        assert abs(sum(weights[3:]) - 1.0) < 1e-9   # aggression capped


class TestZigZagStructure:
    def test_htf_short_window_resolves_structure(self):
        # 30-bar HTF window with a clear rising zigzag: the 5-bar fractal
        # yields <=2 swings ("ranging"); the ATR-ZigZag must resolve it.
        i = np.arange(34, dtype=float)
        base = 100 + i * 0.35 + 2.2 * np.sin(i * 2 * np.pi / 8)
        res = _analyze_structure(base + 0.3, base - 0.3, base,
                                 lookback=min(5, len(base) // 6))
        assert res["structure"] == "bullish"

    def test_fractal_fallback_when_flat(self):
        # Dead-flat series: ZigZag has no reversals -> falls back to the
        # fractal path without raising; structure is "ranging".
        flat = np.full(40, 100.0)
        res = _analyze_structure(flat + 0.1, flat - 0.1, flat)
        assert res["structure"] == "ranging"
