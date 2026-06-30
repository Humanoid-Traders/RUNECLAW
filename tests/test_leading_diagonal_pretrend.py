"""
Leading-diagonal pre-trend window fix (deep-audit medium).

A leading diagonal is a wave-1 structure that should be PRECEDED by a move in
the opposite direction. The detector gauged that prior move from the first 10
bars of the whole lookback window (`closes[0] - closes[10]`), which is
disconnected from where the pattern actually starts (swing_lows[0]). The
precondition was therefore meaningless.

The fix (gated by LEADING_DIAGONAL_PRETREND_FIX, default OFF) measures the move
over up to 10 bars IMMEDIATELY BEFORE the pattern start, and declines to
classify when there isn't enough pre-pattern history.
"""

import numpy as np

from bot.core.chart_patterns import (
    _LEADING_DIAG_MIN_PRE_BARS,
    _env_bool,
    _leading_diagonal_pre_trend,
)

_pt = _leading_diagonal_pre_trend


def _closes():
    # First 10 bars RISE (100→110); the 10 bars before the pattern start at
    # index 40 FALL (130→100). Legacy and fixed windows give opposite signs.
    c = np.full(60, 115.0)
    c[0], c[10], c[30], c[40] = 100.0, 110.0, 130.0, 100.0
    return c


class TestLegacyPath:
    def test_legacy_reads_window_head(self):
        c = _closes()
        # closes[0] - closes[10] = 100 - 110 = -10 (window head rose).
        assert _pt(c, [(40, 100.0)], use_fix=False) == -10.0

    def test_legacy_ignores_swings(self):
        c = _closes()
        assert _pt(c, [], use_fix=False) == _pt(c, [(40, 100.0)], use_fix=False)


class TestFixedPath:
    def test_reads_bars_before_pattern_start(self):
        c = _closes()
        # start=40, pre_start=30 → closes[30]-closes[40] = 130-100 = 30 (fell in).
        assert _pt(c, [(40, 100.0)], use_fix=True) == 30.0

    def test_opposite_sign_to_legacy(self):
        # The whole point: the legacy head-of-window read disagrees with the
        # real pre-pattern move (rose vs fell).
        c = _closes()
        assert _pt(c, [(40, 100.0)], use_fix=False) < 0 < _pt(c, [(40, 100.0)], use_fix=True)

    def test_no_swings_is_zero(self):
        assert _pt(_closes(), [], use_fix=True) == 0.0

    def test_too_little_history_is_zero(self):
        # Pattern starts at index 2 → fewer than the minimum pre-bars → 0.0.
        c = _closes()
        assert (2 - max(0, 2 - 10)) < _LEADING_DIAG_MIN_PRE_BARS
        assert _pt(c, [(2, 100.0)], use_fix=True) == 0.0

    def test_partial_but_sufficient_window(self):
        # start=6, pre_start=max(0,6-10)=0 → 6 bars ≥ min(5) → closes[0]-closes[6].
        c = np.full(60, 115.0)
        c[0], c[6] = 120.0, 100.0
        assert _pt(c, [(6, 100.0)], use_fix=True) == 20.0


class TestGatingDefault:
    def test_flag_defaults_off(self, monkeypatch):
        monkeypatch.delenv("LEADING_DIAGONAL_PRETREND_FIX", raising=False)
        assert _env_bool("LEADING_DIAGONAL_PRETREND_FIX", False) is False

    def test_flag_enables(self, monkeypatch):
        monkeypatch.setenv("LEADING_DIAGONAL_PRETREND_FIX", "1")
        assert _env_bool("LEADING_DIAGONAL_PRETREND_FIX", False) is True
