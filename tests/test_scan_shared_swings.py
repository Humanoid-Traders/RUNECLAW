"""
scan_all_chart_patterns computes swings once and threads them in (#41).

Each detector used to call _find_swings(highs, lows, lookback) itself — ~14× per
symbol per scan. scan_all_chart_patterns now computes the swings ONCE and passes
them via the optional `swings` kwarg; detectors recompute only when it isn't
supplied. The shared swings is exactly what each detector would have computed, so
results are byte-identical.
"""

import numpy as np
import pytest

import bot.core.chart_patterns as cp
from bot.core.chart_patterns import (
    _find_swings,
    detect_head_and_shoulders,
    scan_all_chart_patterns,
)

_DETECTORS = [
    cp.detect_head_and_shoulders, cp.detect_double_top_bottom, cp.detect_triangles,
    cp.detect_wedges, cp.detect_rectangle, cp.detect_cup_and_handle, cp.detect_sr_flip,
    cp.detect_elliott_impulse, cp.detect_elliott_corrective, cp.detect_elliott_diagonal,
    cp.detect_elliott_wxy, cp.detect_liquidity_sweep, cp.detect_harmonic_pattern,
    cp.detect_fibonacci_extensions,
]


def _ohlc(seed, n=120):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, n))
    closes = np.abs(closes) + 1.0
    highs = closes + rng.uniform(0.2, 2.0, n)
    lows = np.maximum(closes - rng.uniform(0.2, 2.0, n), 0.5)
    opens = closes + rng.normal(0, 0.5, n)
    return opens, highs, lows, closes


class TestPerDetectorEquivalence:
    @pytest.mark.parametrize("seed", [1, 7, 42, 99, 2024])
    def test_shared_swings_matches_recompute(self, seed):
        _, highs, lows, closes = _ohlc(seed)
        shared = _find_swings(highs, lows, 5)
        for det in _DETECTORS:
            recompute = det(highs, lows, closes, 5, swings=None)
            with_shared = det(highs, lows, closes, 5, swings=shared)
            assert recompute == with_shared, f"{det.__name__} diverged at seed {seed}"


class TestScanEquivalence:
    @pytest.mark.parametrize("seed", [3, 11, 77])
    def test_scan_matches_manual_recompute_loop(self, seed):
        opens, highs, lows, closes = _ohlc(seed)
        got = scan_all_chart_patterns(opens, highs, lows, closes)
        # Reference: each detector recomputing its own swings, same order + sort.
        ref = []
        for det in [cp.detect_head_and_shoulders, cp.detect_double_top_bottom,
                    cp.detect_flags, cp.detect_triangles, cp.detect_wedges,
                    cp.detect_rectangle, cp.detect_cup_and_handle, cp.detect_sr_flip,
                    cp.detect_elliott_impulse, cp.detect_elliott_corrective,
                    cp.detect_elliott_diagonal, cp.detect_elliott_wxy,
                    cp.detect_liquidity_sweep, cp.detect_wyckoff_phases,
                    cp.detect_harmonic_pattern, cp.detect_fibonacci_extensions]:
            try:
                p = det(highs, lows, closes, 5, swings=None)
                if p:
                    ref.append(p)
            except Exception:
                continue
        ref.sort(key=lambda p: p.get("confidence", 0), reverse=True)
        assert got == ref


class TestSharedSwingsActuallyUsed:
    def test_provided_swings_bypasses_find_swings(self, monkeypatch):
        # If the detector recomputed, this would raise; passing swings must skip it.
        monkeypatch.setattr(cp, "_find_swings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("recomputed")))
        swings = {"swing_highs": [(10, 100.0), (20, 110.0), (30, 103.0)],
                  "swing_lows": [(15, 98.0), (25, 99.0)]}
        closes = np.full(60, 100.0)
        highs = closes + 1
        lows = closes - 1
        # Should not raise — uses the provided swings.
        res = detect_head_and_shoulders(highs, lows, closes, 5, swings=swings)
        assert res is not None and res["name"] == "Head & Shoulders"
