"""
Vectorized stochastic %K is numerically identical to the old loop (#40).

_compute_indicators computed raw %K with a per-bar Python loop over the whole
array each call. It now uses a vectorized rolling max/min (sliding_window_view).
This test pins byte-for-byte equality of the reported stoch_k / stoch_d against a
reference implementation of the original loop, including the hh<=ll → 50.0 case.
"""

import numpy as np
import pytest

from bot.core.analyzer import Analyzer


def _ref_stoch(highs, lows, closes, period=14, smooth=3):
    """The ORIGINAL per-bar loop, kept here as the reference oracle."""
    raw_k = np.zeros(len(closes) - period + 1)
    for si in range(len(raw_k)):
        hh = np.max(highs[si:si + period])
        ll = np.min(lows[si:si + period])
        raw_k[si] = ((closes[si + period - 1] - ll) / (hh - ll) * 100
                     if hh > ll else 50.0)
    smooth_k = np.convolve(raw_k, np.ones(smooth) / smooth, mode='valid')
    smooth_d = np.convolve(smooth_k, np.ones(smooth) / smooth, mode='valid')
    return round(float(smooth_k[-1]), 2), round(float(smooth_d[-1]), 2)


def _ohlc(n, seed):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    highs = closes + rng.uniform(0.1, 2.0, n)
    lows = closes - rng.uniform(0.1, 2.0, n)
    return highs, lows, closes


class TestStochEquivalence:
    @pytest.mark.parametrize("seed", [1, 7, 42, 123, 2024])
    def test_matches_reference_loop(self, seed):
        highs, lows, closes = _ohlc(80, seed)
        res = Analyzer._compute_indicators(highs, lows, closes)
        ref_k, ref_d = _ref_stoch(highs, lows, closes)
        assert res["stoch_k"] == ref_k
        assert res["stoch_d"] == ref_d

    def test_flat_window_uses_50_fallback(self):
        # highs == lows == closes over the whole series → every window hh == ll,
        # so raw %K is the 50.0 fallback for every bar (and so are the smooths).
        flat = np.full(60, 100.0)
        res = Analyzer._compute_indicators(flat.copy(), flat.copy(), flat.copy())
        assert res["stoch_k"] == 50.0
        assert res["stoch_d"] == 50.0
