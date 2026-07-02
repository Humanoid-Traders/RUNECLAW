"""
Unit tests for bot/core/elliott.py — the advanced Elliott Wave helpers.

These are pure functions with no pipeline side effects; the gated wiring into
the analyzer is tested separately. Covers: the ATR-ZigZag pivot engine's shape
and noise-filtering, the strategy_type -> timeframe degree map, wave-position ->
action translation, and Fibonacci target/invalidation projection.
"""

import numpy as np

from bot.core.elliott import (
    atr_zigzag_pivots,
    timeframe_for_strategy,
    wave_action,
    project_targets,
)


def _synthetic_zigzag():
    """A clean up-down-up-down price path with small intrabar noise, so a
    structural ZigZag should find the four big turns and ignore the wiggles."""
    seg = []
    # 0->100 up, ->50 down, ->150 up, ->80 down (big structural moves)
    legs = [(0, 100, 20), (100, 50, 20), (50, 150, 20), (150, 80, 20)]
    price = 0.0
    for start, end, steps in legs:
        for k in range(steps):
            price = start + (end - start) * (k + 1) / steps
            seg.append(price)
    closes = np.array(seg, dtype=float)
    # small noise band for highs/lows
    highs = closes + 0.5
    lows = closes - 0.5
    return highs, lows, closes


class TestAtrZigzagPivots:
    def test_shape_matches_find_swings(self):
        highs, lows, closes = _synthetic_zigzag()
        piv = atr_zigzag_pivots(highs, lows, closes)
        assert set(piv.keys()) == {"swing_highs", "swing_lows"}
        for lst in (piv["swing_highs"], piv["swing_lows"]):
            for item in lst:
                assert isinstance(item, tuple) and len(item) == 2
                idx, price = item
                assert isinstance(idx, int) and isinstance(price, float)

    def test_finds_structural_turns(self):
        highs, lows, closes = _synthetic_zigzag()
        piv = atr_zigzag_pivots(highs, lows, closes, atr_mult=1.0)
        # The path has 2 major highs (~100, ~150) and at least 1 major low (~50).
        assert len(piv["swing_highs"]) >= 2
        assert len(piv["swing_lows"]) >= 1
        # Highs should be near 100 and 150, not the noise.
        high_prices = sorted(p for _, p in piv["swing_highs"])
        assert any(abs(p - 100) < 5 for p in high_prices)
        assert any(abs(p - 150) < 5 for p in high_prices)

    def test_noise_only_series_yields_few_pivots(self):
        # Flat price with tiny noise below the ATR threshold -> no structural pivots.
        base = np.full(60, 100.0)
        noise = np.array([0.05 * ((-1) ** i) for i in range(60)])
        closes = base + noise
        highs = closes + 0.02
        lows = closes - 0.02
        piv = atr_zigzag_pivots(highs, lows, closes, atr_mult=3.0)
        assert len(piv["swing_highs"]) + len(piv["swing_lows"]) <= 2

    def test_too_short_returns_empty(self):
        piv = atr_zigzag_pivots(np.array([1.0, 2.0]), np.array([1.0, 2.0]), np.array([1.0, 2.0]))
        assert piv == {"swing_highs": [], "swing_lows": []}

    def test_max_points_truncation(self):
        highs, lows, closes = _synthetic_zigzag()
        piv = atr_zigzag_pivots(highs, lows, closes, atr_mult=0.5, max_points=1)
        assert len(piv["swing_highs"]) <= 1
        assert len(piv["swing_lows"]) <= 1


class TestTimeframeForStrategy:
    def test_default_degree_map(self):
        assert timeframe_for_strategy("scalp") == "15m"
        assert timeframe_for_strategy("intraday") == "1h"
        assert timeframe_for_strategy("swing") == "4h"
        assert timeframe_for_strategy("position") == "1d"

    def test_unknown_falls_back_to_1h(self):
        assert timeframe_for_strategy("nonsense") == "1h"

    def test_overrides_apply(self):
        assert timeframe_for_strategy("scalp", {"scalp": "5m"}) == "5m"
        # empty override value is ignored, default kept
        assert timeframe_for_strategy("swing", {"swing": ""}) == "4h"


class TestWaveAction:
    def test_none_pattern_is_wait(self):
        a = wave_action(None)
        assert a["action"] == "wait" and a["weight_mult"] == 1.0

    def test_wave3_impulse_is_strong_enter(self):
        a = wave_action({"name": "Elliott 5-Wave Impulse", "signal": "bullish",
                         "key_levels": {"current_wave": "3"}})
        assert a["action"] == "enter" and a["bias"] == "with"
        assert a["weight_mult"] > 1.0

    def test_wave4_impulse_enters_for_wave5(self):
        a = wave_action({"name": "Elliott 5-Wave Impulse", "signal": "bullish",
                         "key_levels": {"current_wave": "4"}})
        assert a["action"] == "enter" and a["weight_mult"] > 1.0

    def test_wave5_impulse_is_avoid_or_exit(self):
        a = wave_action({"name": "Elliott 5-Wave Impulse", "signal": "bullish",
                         "key_levels": {"current_wave": "5"}})
        assert a["action"] in ("exit", "avoid")
        assert a["weight_mult"] < 0.5  # conviction dampened, not amplified

    def test_ending_diagonal_is_avoid(self):
        a = wave_action({"name": "Elliott Ending Diagonal", "signal": "bullish", "key_levels": {}})
        assert a["action"] == "avoid" and a["weight_mult"] < 0.5

    def test_truncated_fifth_is_avoid(self):
        a = wave_action({"name": "Elliott Truncated 5th", "signal": "bullish", "key_levels": {}})
        assert a["action"] == "avoid"

    def test_leading_diagonal_enters_small(self):
        a = wave_action({"name": "Elliott Leading Diagonal", "signal": "bullish", "key_levels": {}})
        assert a["action"] == "enter" and a["weight_mult"] < 1.0

    def test_complex_correction_waits(self):
        a = wave_action({"name": "Elliott WXY Double Combination", "signal": "bullish", "key_levels": {}})
        assert a["action"] == "wait"

    def test_abc_complete_enters(self):
        a = wave_action({"name": "Elliott ABC Zigzag", "signal": "bullish", "key_levels": {}})
        assert a["action"] == "enter"

    def test_abc_partial_waits(self):
        a = wave_action({"name": "Elliott ABC Zigzag (partial)", "signal": "bullish", "key_levels": {}})
        assert a["action"] == "wait"

    def test_current_wave_inferred_from_description(self):
        a = wave_action({"name": "Elliott 5-Wave Impulse", "signal": "bullish",
                         "description": "Bullish impulse: wave 5 | ...", "key_levels": {}})
        assert a["action"] in ("exit", "avoid")


class TestProjectTargets:
    def test_bullish_projection_and_invalidation(self):
        pat = {"signal": "bullish", "key_levels": {
            "w1_start": 100.0, "w1_top": 120.0, "w2_low": 110.0, "w3_top": 150.0}}
        t = project_targets(pat)
        assert t["invalidation"] == 110.0
        # tp2 = w2_low + 1.618 * (w1_top - w1_start) = 110 + 1.618*20 = 142.36
        assert abs(t["tp2"] - 142.36) < 0.1
        # tp1 = w3_top + 0.618*(w3_top - w2_low) = 150 + 0.618*40 = 174.72
        assert abs(t["tp1"] - 174.72) < 0.1
        assert t["basis"] == "elliott_impulse_bull"

    def test_bearish_projection_and_invalidation(self):
        pat = {"signal": "bearish", "key_levels": {
            "w1_start": 200.0, "w1_low": 180.0, "w2_high": 190.0, "w3_low": 150.0}}
        t = project_targets(pat)
        assert t["invalidation"] == 190.0
        # tp2 = w2_high - 1.618*(w1_start - w1_low) = 190 - 1.618*20 = 157.64
        assert abs(t["tp2"] - 157.64) < 0.1
        assert t["basis"] == "elliott_impulse_bear"

    def test_missing_levels_returns_empty(self):
        assert project_targets({"signal": "bullish", "key_levels": {"w1_start": 100.0}}) == {}
        assert project_targets(None) == {}
        assert project_targets({"signal": "neutral", "key_levels": {}}) == {}

    def test_degenerate_w1_returns_empty(self):
        # w1_top == w1_start -> zero-length wave 1 -> no projection
        pat = {"signal": "bullish", "key_levels": {
            "w1_start": 100.0, "w1_top": 100.0, "w2_low": 95.0}}
        assert project_targets(pat) == {}


# ── Gated analyzer wiring (module functions) ────────────────────────────

from bot.config import CONFIG  # noqa: E402
from bot.core.analyzer import (  # noqa: E402
    _apply_elliott_wave_targets,
    _apply_timeframe_matched_elliott,
    _run_elliott_detectors,
)
from bot.utils.models import Direction  # noqa: E402


class TestWaveAnchoredTargets:
    def _long_impulse(self):
        return {"elliott_impulse": {"signal": "bullish", "key_levels": {
            "w1_start": 100.0, "w1_top": 120.0, "w2_low": 110.0, "w3_top": 150.0}}}

    def test_sl_tightens_to_invalidation_long(self):
        ind = self._long_impulse()
        # ATR stop at 105 (below the 110 invalidation) -> should tighten UP to 110.
        sl, tp = _apply_elliott_wave_targets(Direction.LONG, 130.0, 105.0, 140.0, ind)
        assert sl == 110.0
        assert tp >= 140.0  # tp only extends or stays

    def test_sl_never_loosens_long(self):
        ind = self._long_impulse()
        # ATR stop already TIGHTER (115 > 110 invalidation) -> keep the tighter stop.
        sl, tp = _apply_elliott_wave_targets(Direction.LONG, 130.0, 115.0, 140.0, ind)
        assert sl == 115.0  # not loosened to 110

    def test_tp_extends_to_projection_long(self):
        ind = self._long_impulse()
        # tp2 = 110 + 1.618*20 = 142.36; ATR tp at 135 -> should extend to 142.36.
        sl, tp = _apply_elliott_wave_targets(Direction.LONG, 130.0, 108.0, 135.0, ind)
        assert abs(tp - 142.36) < 0.1

    def test_direction_mismatch_is_noop(self):
        ind = self._long_impulse()  # bullish impulse
        # SHORT trade against a bullish impulse -> untouched.
        sl, tp = _apply_elliott_wave_targets(Direction.SHORT, 130.0, 140.0, 120.0, ind)
        assert (sl, tp) == (140.0, 120.0)

    def test_no_impulse_is_noop(self):
        sl, tp = _apply_elliott_wave_targets(Direction.LONG, 130.0, 105.0, 140.0, {})
        assert (sl, tp) == (105.0, 140.0)


class TestTimeframeMatchedElliott:
    def _series(self, n=60):
        # minimal OHLCV rows [ts, o, h, l, c, v]
        rows = []
        price = 100.0
        for i in range(n):
            price += (1.0 if (i // 5) % 2 == 0 else -1.0)
            rows.append([i, price, price + 0.5, price - 0.5, price, 100.0])
        return rows

    def test_picks_matched_timeframe_and_tags_degree(self):
        ind = {}
        mtf = {"4h": self._series()}  # swing -> 4h
        _apply_timeframe_matched_elliott(ind, "swing", mtf)
        # It ran on the 4h series (tagged), regardless of whether a pattern formed.
        assert ind.get("elliott_degree_tf") == "4h"

    def test_missing_matched_tf_is_noop(self):
        ind = {"elliott_impulse": {"sentinel": True}}
        _apply_timeframe_matched_elliott(ind, "scalp", {"4h": self._series()})  # scalp wants 15m
        # 15m absent -> no recompute, existing indicators untouched, no degree tag.
        assert ind == {"elliott_impulse": {"sentinel": True}}

    def test_short_series_is_noop(self):
        ind = {}
        _apply_timeframe_matched_elliott(ind, "swing", {"4h": self._series(n=10)})
        assert "elliott_degree_tf" not in ind


class TestGatedConfigDefaultsOff:
    def test_all_elliott_flags_default_off(self):
        a = CONFIG.analyzer
        assert a.elliott_zigzag_enabled is False
        assert a.elliott_wave_action_enabled is False
        assert a.elliott_fib_targets_enabled is False
        assert a.elliott_mtf_enabled is False


class TestRunElliottDetectorsReplaces:
    def test_replaces_elliott_set(self):
        import numpy as _np
        ind = {"elliott_impulse": {"stale": True}, "elliott_pattern": {"stale": True}}
        # empty swings -> detectors find nothing -> stale keys cleared
        _run_elliott_detectors(ind, _np.array([1.0]), _np.array([1.0]), _np.array([1.0]),
                               {"swing_highs": [], "swing_lows": []})
        assert "elliott_impulse" not in ind
        assert "elliott_pattern" not in ind
