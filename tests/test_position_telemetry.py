"""
Position telemetry (read-only): the Playbook-aligned trail read, ATR%, liq
distance, limit-expiry countdown, and the engine's authoritative trail-fired
flag. Pure functions, verified directly.

The trail threshold uses the Playbook's exact formula — SL / (1 ± 2·ATR_pct),
ATR_pct as a fraction of the live mark — confirmed against the live WLD short.
"""

import pytest

from bot.core import position_telemetry as pt


class TestPlaybookThreshold:
    def test_short_formula_matches_playbook(self):
        # WLD short: SL 0.4521, ATR_pct 0.0178 → threshold 0.4521 / 1.0356.
        thr = pt.playbook_trail_threshold("SHORT", 0.4521, 0.0178)
        assert thr == pytest.approx(0.4521 / 1.0356, abs=1e-6)
        assert thr == pytest.approx(0.4366, abs=0.0003)  # the Playbook's ~0.4367

    def test_long_formula(self):
        # LONG: threshold = SL / (1 − 2·ATR_pct).
        thr = pt.playbook_trail_threshold("LONG", 100.0, 0.02)
        assert thr == pytest.approx(100.0 / 0.96, abs=1e-6)

    def test_degenerate_long_atr_returns_none(self):
        # 2·ATR_pct ≥ 1 → denominator ≤ 0 → undefined.
        assert pt.playbook_trail_threshold("LONG", 100.0, 0.6) is None

    def test_none_atr_returns_none(self):
        assert pt.playbook_trail_threshold("SHORT", 0.4521, None) is None


class TestPlaybookGap:
    def test_short_gap_positive_when_mark_above_threshold(self):
        # mark 0.4385 above threshold 0.4366 → not demanded → gap positive.
        assert pt.playbook_gap("SHORT", 0.4385, 0.4366) == pytest.approx(0.0019, abs=1e-4)

    def test_long_gap_positive_when_mark_below_threshold(self):
        assert pt.playbook_gap("LONG", 100.0, 104.0) == pytest.approx(4.0)

    def test_none_threshold(self):
        assert pt.playbook_gap("SHORT", 0.4385, None) is None


class TestWldShortFromPlaybook:
    # WLD SHORT: entry 0.4413, SL 0.4521, mark 0.4385, ATR ≈ 0.0078 abs (~1.78%).
    E, SL, MARK, ATR = 0.4413, 0.4521, 0.4385, 0.0078

    def test_trail_read_matches_playbook_geometry(self):
        read = pt.trail_read("SHORT", self.E, self.SL, self.MARK, atr=self.ATR)
        assert read["atr_pct"] == pytest.approx(1.78, abs=0.05)
        # Threshold per the Playbook formula (~0.4366), gap positive ≈ Playbook's +0.0017.
        assert read["threshold"] == pytest.approx(0.4366, abs=0.0005)
        assert read["gap"] > 0
        assert read["gap"] == pytest.approx(0.0019, abs=0.0006)
        assert read["demanded"] is False
        assert "GEOMETRY NOT DEMANDED" in read["verdict"]

    def test_profit_in_r_context(self):
        read = pt.trail_read("SHORT", self.E, self.SL, self.MARK, atr=self.ATR)
        assert read["profit_r"] == pytest.approx(0.259, abs=0.01)


class TestRatchetDemandedAndFired:
    def test_demanded_but_not_fired(self):
        # Push the mark below the short threshold → demanded; engine flag False.
        read = pt.trail_read("SHORT", 0.4413, 0.4521, 0.4300, atr=0.0078,
                             trailing_active=False)
        assert read["demanded"] is True
        assert read["fired"] is False
        assert "NOT fired" in read["verdict"]

    def test_demanded_and_fired(self):
        read = pt.trail_read("SHORT", 0.4413, 0.4521, 0.4300, atr=0.0078,
                             trailing_active=True)
        assert read["demanded"] is True
        assert read["fired"] is True
        assert "trail fired" in read["verdict"]

    def test_atr_unavailable(self):
        read = pt.trail_read("SHORT", 0.4413, 0.4521, 0.4385, atr=0.0)
        assert read["threshold"] is None
        assert "ATR unavailable" in read["verdict"]


class TestTrailEngineRead:
    def test_no_state(self):
        r = pt.trail_engine_read(None)
        assert r["fired"] is None

    def test_active_state(self):
        r = pt.trail_engine_read({"trailing_active": True, "stage": 2, "best_price": 0.41})
        assert r["fired"] is True
        assert r["stage"] == 2
        assert r["best_price"] == 0.41

    def test_armed_not_fired(self):
        r = pt.trail_engine_read({"trailing_active": False, "stage": 0})
        assert r["fired"] is False


class TestRollingAtr:
    def test_atr_from_candles_constant_range(self):
        # Each bar has a true range of exactly 2.0 → ATR = 2.0.
        n = 30
        highs = [101.0] * n
        lows = [99.0] * n
        closes = [100.0] * n
        assert pt.atr_from_candles(highs, lows, closes) == pytest.approx(2.0, abs=1e-9)

    def test_short_series_falls_back_to_mean(self):
        # Fewer than `period` TRs → simple mean of available TRs (non-zero).
        highs = [10.0, 11.0, 12.0]
        lows = [9.0, 9.5, 10.0]
        closes = [9.5, 10.5, 11.5]
        assert pt.atr_from_candles(highs, lows, closes, period=14) > 0

    def test_degenerate_input(self):
        assert pt.atr_from_candles([], [], []) == 0.0
        assert pt.atr_from_candles([1.0], [1.0], [1.0]) == 0.0

    def test_feeds_trail_read_threshold(self):
        # A rolling ATR drives the same Playbook threshold formula.
        atr = pt.atr_from_candles([0.45] * 30, [0.43] * 30, [0.44] * 30)
        read = pt.trail_read("SHORT", 0.4413, 0.4521, 0.4385, atr=atr)
        assert read["threshold"] is not None
        assert read["atr_pct"] is not None


class TestScalars:
    def test_atr_pct(self):
        assert pt.atr_pct(1.0, 50.0) == pytest.approx(2.0)
        assert pt.atr_pct(0.0, 50.0) is None

    def test_liq_distance(self):
        assert pt.liq_distance_pct(0.4385, 0.5483) == pytest.approx(25.04, abs=0.1)
        assert pt.liq_distance_pct(0.4385, None) is None
        assert pt.liq_distance_pct(0.4385, 0.0) is None

    def test_expiry_remaining_and_format(self):
        rem = pt.expiry_remaining_seconds(0.0, 14400.0, 3 * 3600 + 26 * 60)
        assert rem == pytest.approx(34 * 60, abs=1)
        assert "34m to expiry" in pt.format_expiry(rem)
        assert "EXPIRED" in pt.format_expiry(-5)

    def test_sl_trail_fired(self):
        assert pt.sl_trail_fired(1_000_060_000, 1_000_000_000) is True
        assert pt.sl_trail_fired(1_000_000_004, 1_000_000_000) is False
        assert pt.sl_trail_fired(None, 1_000_000_000) is None


class TestFormatters:
    def test_trail_read_lines(self):
        read = pt.trail_read("SHORT", 0.4413, 0.4521, 0.4385, atr=0.0078,
                             trailing_active=False)
        text = "\n".join(pt.format_trail_read(read))
        assert "TRAIL READ" in text
        assert "Est. threshold" in text
        assert "Gap:" in text
        assert "ABOVE threshold" in text
        assert "VERDICT" in text

    def test_trail_fired_format(self):
        assert "NOT FIRED" in pt.format_trail_fired(False)
        assert "re-issued" in pt.format_trail_fired(True)
        assert "unavailable" in pt.format_trail_fired(None)
