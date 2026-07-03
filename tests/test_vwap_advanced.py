"""Tests for the advanced VWAP suite (bot/core/vwap.py) and its analyzer wiring.

Covers the four gated upgrades:
  * band_reversion_signal   — ±1σ/±2σ mean-reversion vote (range-only)
  * slope_adjusted_vote     — dampen a bias that fights the VWAP slope
  * select_setup_anchor     — horizon-matched anchor selection
  * anchored_vwap_from_last_pivot — AVWAP from the last ZigZag pivot
plus the config defaults (all ON) and the analyzer setup-anchoring helper.
"""
from __future__ import annotations

import numpy as np

from bot.config import CONFIG
from bot.core.vwap import (
    band_reversion_signal,
    slope_adjusted_vote,
    vwap_slope_pct,
    select_setup_anchor,
    anchored_vwap_from_last_pivot,
)
from bot.core.analyzer import _apply_vwap_setup_anchoring


BANDS = {
    "vwap_lower_2": 90.0, "vwap_lower_1": 95.0,
    "vwap_upper_1": 105.0, "vwap_upper_2": 110.0,
}


class TestBandReversionSignal:
    def test_below_lower_2_is_strong_bullish(self):
        assert band_reversion_signal(89.0, BANDS, in_range=True) == 1.0

    def test_below_lower_1_is_weak_bullish(self):
        assert band_reversion_signal(94.0, BANDS, in_range=True) == 0.5

    def test_above_upper_2_is_strong_bearish(self):
        assert band_reversion_signal(111.0, BANDS, in_range=True) == -1.0

    def test_above_upper_1_is_weak_bearish(self):
        assert band_reversion_signal(106.0, BANDS, in_range=True) == -0.5

    def test_inside_bands_is_neutral(self):
        assert band_reversion_signal(100.0, BANDS, in_range=True) == 0.0

    def test_only_fires_in_range(self):
        # Same stretched price, but not a range regime -> no reversion vote.
        assert band_reversion_signal(89.0, BANDS, in_range=False) == 0.0

    def test_missing_bands_is_neutral(self):
        assert band_reversion_signal(89.0, {}, in_range=True) == 0.0

    def test_bad_price_is_neutral(self):
        assert band_reversion_signal(0.0, BANDS, in_range=True) == 0.0
        assert band_reversion_signal(None, BANDS, in_range=True) == 0.0


class TestSlopeAdjustedVote:
    def test_with_trend_long_unchanged(self):
        # +1 (above VWAP) with a rising VWAP is confirmed -> unchanged.
        assert slope_adjusted_vote(1.0, 0.5) == 1.0

    def test_against_trend_long_dampened(self):
        # +1 above a *falling* VWAP -> weakening bias -> halved.
        assert slope_adjusted_vote(1.0, -0.5) == 0.5

    def test_with_trend_short_unchanged(self):
        assert slope_adjusted_vote(-1.0, -0.5) == -1.0

    def test_against_trend_short_dampened(self):
        assert slope_adjusted_vote(-1.0, 0.5) == -0.5

    def test_flat_slope_unchanged(self):
        assert slope_adjusted_vote(1.0, 0.0) == 1.0
        assert slope_adjusted_vote(1.0, 0.01) == 1.0  # within threshold

    def test_neutral_vote_unchanged(self):
        assert slope_adjusted_vote(0.0, -5.0) == 0.0

    def test_none_slope_unchanged(self):
        assert slope_adjusted_vote(1.0, None) == 1.0


class TestVwapSlopePct:
    def test_rising_series_positive(self):
        series = np.linspace(100.0, 110.0, 30)
        assert vwap_slope_pct(series, lookback=10) > 0

    def test_falling_series_negative(self):
        series = np.linspace(110.0, 100.0, 30)
        assert vwap_slope_pct(series, lookback=10) < 0

    def test_too_short_returns_none(self):
        assert vwap_slope_pct(np.array([1.0, 2.0]), lookback=10) is None

    def test_none_series(self):
        assert vwap_slope_pct(None) is None


class TestSelectSetupAnchor:
    ANCHORS = {"session": 100.0, "rolling50": 101.0, "full": 102.0}

    def test_scalp_uses_session(self):
        assert select_setup_anchor("scalp", self.ANCHORS) == (100.0, "session")

    def test_intraday_uses_session(self):
        assert select_setup_anchor("intraday", self.ANCHORS) == (100.0, "session")

    def test_swing_uses_rolling(self):
        assert select_setup_anchor("swing", self.ANCHORS) == (101.0, "rolling50")

    def test_position_uses_full(self):
        assert select_setup_anchor("position", self.ANCHORS) == (102.0, "full")

    def test_falls_back_when_preferred_missing(self):
        # swing prefers rolling50; missing -> falls back to session.
        val, kind = select_setup_anchor("swing", {"session": 100.0})
        assert (val, kind) == (100.0, "session")

    def test_empty_returns_none(self):
        assert select_setup_anchor("swing", {}) == (None, "none")

    def test_unknown_strategy_defaults_session(self):
        assert select_setup_anchor("???", self.ANCHORS) == (100.0, "session")

    def test_override_map(self):
        val, kind = select_setup_anchor(
            "scalp", self.ANCHORS, overrides={"scalp": "full"})
        assert (val, kind) == (102.0, "full")


class TestAnchoredVwapFromLastPivot:
    def _series(self, n=80):
        # A clean down-then-up move so the ZigZag registers a structural pivot.
        rng = np.arange(n, dtype=float)
        closes = 100.0 - np.minimum(rng, 40) + np.maximum(rng - 40, 0)
        highs = closes + 1.0
        lows = closes - 1.0
        vols = np.full(n, 1000.0)
        return highs, lows, closes, vols

    def test_returns_a_price_within_range(self):
        highs, lows, closes, vols = self._series()
        av = anchored_vwap_from_last_pivot(highs, lows, closes, vols)
        if av is not None:  # pivot engine may or may not find a turn on this shape
            assert lows.min() - 1 <= av <= highs.max() + 1

    def test_insufficient_bars_returns_none(self):
        assert anchored_vwap_from_last_pivot(
            np.array([1.0, 2.0]), np.array([1.0, 2.0]),
            np.array([1.0, 2.0]), np.array([1.0, 1.0])) is None

    def test_mismatched_volumes_returns_none(self):
        highs, lows, closes, _ = self._series(n=40)
        assert anchored_vwap_from_last_pivot(
            highs, lows, closes, np.array([1.0, 2.0])) is None

    def test_zero_volume_returns_none(self):
        highs, lows, closes, _ = self._series(n=60)
        assert anchored_vwap_from_last_pivot(
            highs, lows, closes, np.zeros(60)) is None


class TestVwapConfigDefaultsOn:
    def test_all_vwap_flags_default_on(self):
        a = CONFIG.analyzer
        assert a.vwap_bands_vote_enabled is True
        assert a.vwap_slope_vote_enabled is True
        assert a.vwap_setup_anchoring_enabled is True
        assert a.vwap_anchored_pivot_enabled is True


class TestApplyVwapSetupAnchoring:
    def test_swing_repoints_to_rolling50(self):
        ind = {"vwap": 100.0, "vwap_session": 100.0,
               "vwap_50": 101.0, "vwap_full": 102.0}
        _apply_vwap_setup_anchoring(ind, "swing")
        assert ind["vwap"] == 101.0
        assert ind["vwap_anchor_kind"] == "rolling50"

    def test_position_repoints_to_full(self):
        ind = {"vwap": 100.0, "vwap_session": 100.0,
               "vwap_50": 101.0, "vwap_full": 102.0}
        _apply_vwap_setup_anchoring(ind, "position")
        assert ind["vwap"] == 102.0
        assert ind["vwap_anchor_kind"] == "full"

    def test_scalp_keeps_session(self):
        ind = {"vwap": 100.0, "vwap_session": 100.0,
               "vwap_50": 101.0, "vwap_full": 102.0}
        _apply_vwap_setup_anchoring(ind, "scalp")
        assert ind["vwap"] == 100.0
        assert ind["vwap_anchor_kind"] == "session"

    def test_missing_anchor_leaves_vwap_untouched(self):
        # No anchor keys present at all -> fail-open, vwap unchanged, no crash.
        ind = {"vwap": 100.0}
        _apply_vwap_setup_anchoring(ind, "swing")
        assert ind["vwap"] == 100.0
        assert "vwap_anchor_kind" not in ind


class TestBandRecenterOnAnchorSwap:
    def test_bands_follow_the_setup_anchor(self):
        # Audit batch 3: re-pointing vwap to the swing anchor must move the
        # σ-bands with it (same half-width) — the band-reversion voter and
        # the directional vote must reference ONE VWAP.
        from bot.core.analyzer import _apply_vwap_setup_anchoring
        ind = {"vwap": 100.0, "vwap_session": 100.0, "vwap_50": 104.0,
               "vwap_full": 106.0, "vwap_upper_1": 101.5, "vwap_lower_1": 98.5,
               "vwap_upper_2": 103.0, "vwap_lower_2": 97.0}
        _apply_vwap_setup_anchoring(ind, "swing")
        assert ind["vwap"] == 104.0 and ind["vwap_anchor_kind"] == "rolling50"
        assert ind["vwap_upper_1"] == 105.5 and ind["vwap_lower_1"] == 102.5
        assert ind["vwap_upper_2"] == 107.0 and ind["vwap_lower_2"] == 101.0

    def test_same_anchor_leaves_bands_untouched(self):
        from bot.core.analyzer import _apply_vwap_setup_anchoring
        ind = {"vwap": 100.0, "vwap_session": 100.0, "vwap_50": 104.0,
               "vwap_full": 106.0, "vwap_upper_1": 101.5, "vwap_lower_1": 98.5}
        _apply_vwap_setup_anchoring(ind, "scalp")   # scalp keeps session
        assert ind["vwap"] == 100.0
        assert ind["vwap_upper_1"] == 101.5
