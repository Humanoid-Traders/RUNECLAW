"""
Session-anchored VWAP (deep-audit medium).

The "vwap" indicator was a FULL-WINDOW cumulative VWAP (anchored to bar[0] of the
~100-bar fetch), which drifts as the window slides and is not the session VWAP
traders mean. The proper session VWAP — anchored to the current UTC day's first
bar — is now always exposed as "vwap_session"; when VWAP_SESSION_ANCHORED is on,
the "vwap" key consumers read is set to that value. Default OFF is byte-identical.
"""

import inspect

import numpy as np

from bot.core.analyzer import Analyzer

_DAY_MS = 86_400_000
_anchor = Analyzer._session_anchor_index
_svwap = Analyzer._session_vwap


class TestSessionAnchorIndex:
    def test_none_times_is_zero(self):
        assert _anchor(None) == 0

    def test_empty_times_is_zero(self):
        assert _anchor(np.array([])) == 0

    def test_all_same_day_is_zero(self):
        t = np.array([0.0, 60_000.0, 120_000.0])  # all within UTC day 0
        assert _anchor(t) == 0

    def test_day_boundary_returns_first_bar_of_latest_day(self):
        # 3 bars in day 0, then 2 bars in day 1 → anchor at index 3.
        t = np.array([0.0, 60_000.0, 120_000.0, float(_DAY_MS), _DAY_MS + 60_000.0])
        assert _anchor(t) == 3


class TestSessionVwap:
    def test_session_segment_only(self):
        tp = np.array([100.0, 100.0, 100.0, 200.0, 200.0])
        vol = np.ones(5)
        t = np.array([0.0, 60_000.0, 120_000.0, float(_DAY_MS), _DAY_MS + 60_000.0])
        # Session = last two bars (day 1) → VWAP 200, not the full-window 140.
        assert _svwap(tp, vol, t) == 200.0

    def test_zero_session_volume_is_none(self):
        tp = np.array([100.0, 200.0])
        vol = np.array([0.0, 0.0])
        t = np.array([0.0, float(_DAY_MS)])
        assert _svwap(tp, vol, t) is None

    def test_none_times_uses_full_window(self):
        tp = np.array([100.0, 200.0])
        vol = np.ones(2)
        # No timestamps → anchor 0 → full window VWAP 150 (not None).
        assert _svwap(tp, vol, None) == 150.0


def _bars():
    n = 40
    closes = np.array([100.0] * 30 + [200.0] * 10)
    highs = closes * 1.001
    lows = closes * 0.999
    vols = np.ones(n)
    # First 30 bars in UTC day 0, last 10 in day 1.
    times = np.array([i * 60_000.0 for i in range(30)] +
                     [float(_DAY_MS) + j * 60_000.0 for j in range(10)])
    return highs, lows, closes, vols, times


class TestComputeIndicatorsIntegration:
    def test_exposes_session_vwap_distinct_from_full_window(self):
        highs, lows, closes, vols, times = _bars()
        res = Analyzer._compute_indicators(highs, lows, closes, vols, times=times)
        assert res is not None
        # typical_price == close here, so session VWAP (day-1 bars) == 200.
        assert res["vwap_session"] == 200.0
        # Default OFF: "vwap" stays the full-window cumulative (~125), not 200.
        assert res["vwap"] == 125.0


class TestGating:
    def test_override_is_gated(self):
        src = inspect.getsource(Analyzer._compute_indicators)
        assert 'results["vwap"] = results["vwap_session"]' in src
        assert 'vwap_session_anchored' in src

    def test_config_flag_defaults_off(self):
        from bot.config import AnalyzerConfig
        assert AnalyzerConfig().vwap_session_anchored is False
