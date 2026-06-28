"""
Position telemetry (read-only): trail-read geometry, ATR%, liq distance,
limit-expiry countdown, and the SL-uTime trail-fired check — the signals the
external Playbook surfaces. Pure functions, so they're verified directly.

The headline case is the Playbook's live WLD short, to confirm the bot's trail
geometry lines up with what that readout showed.
"""

import pytest

from bot.core import position_telemetry as pt


class TestWldShortFromPlaybook:
    # WLD SHORT: entry 0.4413, initial SL 0.4521, mark 0.4385.
    E, SL, MARK = 0.4413, 0.4521, 0.4385

    def test_initial_risk_and_profit(self):
        r = pt.initial_risk(self.E, self.SL)
        assert r == pytest.approx(0.0108, abs=1e-6)
        pr = pt.profit_in_r("SHORT", self.E, self.MARK, r)
        assert pr == pytest.approx(0.259, abs=0.01)  # ~0.26R in profit

    def test_trail_read_is_not_yet_demanded(self):
        read = pt.trail_read("SHORT", self.E, self.SL, self.MARK, atr=0.0078)
        assert read["stage"] == 0  # below 1R → trail inactive
        assert read["active"] is False
        # Next trigger = 1R below entry for a short.
        assert read["next_threshold"] == pytest.approx(0.4305, abs=1e-4)
        # Mark is above the (lower) short trigger → gap positive → not demanded.
        assert read["gap"] > 0
        assert "NOT DEMANDED" in read["verdict"]
        assert read["atr_pct"] == pytest.approx(0.0078 / 0.4385 * 100, abs=0.01)


class TestStages:
    def test_stage_boundaries(self):
        assert pt.current_stage(0.0) == 0
        assert pt.current_stage(0.99) == 0
        assert pt.current_stage(1.0) == 1
        assert pt.current_stage(2.5) == 2
        assert pt.current_stage(3.1) == 3

    def test_long_next_threshold_is_above_entry(self):
        # entry 100, SL 98 → 1R=2 → stage-1 trigger at 102.
        thr = pt.next_stage_threshold("LONG", 100.0, 2.0, 0)
        assert thr == pytest.approx(102.0)

    def test_short_next_threshold_is_below_entry(self):
        thr = pt.next_stage_threshold("SHORT", 100.0, 2.0, 0)
        assert thr == pytest.approx(98.0)

    def test_no_threshold_at_max_stage(self):
        assert pt.next_stage_threshold("LONG", 100.0, 2.0, 3) is None

    def test_active_trail_verdict(self):
        # LONG, entry 100, SL 98, mark 103 → +1.5R → stage 1, trailing active.
        read = pt.trail_read("LONG", 100.0, 98.0, 103.0, atr=1.0)
        assert read["stage"] == 1
        assert read["active"] is True
        assert "TRAILING ACTIVE" in read["verdict"]


class TestGapSign:
    def test_long_gap_is_distance_up_to_threshold(self):
        # threshold 102, mark 101 → need +1 → gap +1.
        assert pt.threshold_gap("LONG", 101.0, 102.0) == pytest.approx(1.0)

    def test_short_gap_is_distance_down_to_threshold(self):
        # threshold 98, mark 99 → need −1 → gap +1 (still above the short trigger).
        assert pt.threshold_gap("SHORT", 99.0, 98.0) == pytest.approx(1.0)

    def test_none_threshold(self):
        assert pt.threshold_gap("LONG", 100.0, None) is None


class TestScalars:
    def test_atr_pct(self):
        assert pt.atr_pct(1.0, 50.0) == pytest.approx(2.0)
        assert pt.atr_pct(0.0, 50.0) is None

    def test_liq_distance(self):
        assert pt.liq_distance_pct(0.4385, 0.5483) == pytest.approx(25.04, abs=0.1)
        assert pt.liq_distance_pct(0.4385, None) is None
        assert pt.liq_distance_pct(0.4385, 0.0) is None

    def test_expiry_remaining_and_format(self):
        # opened at t=0, 4h expiry, now at 3h26m → ~34m left.
        rem = pt.expiry_remaining_seconds(0.0, 14400.0, 3 * 3600 + 26 * 60)
        assert rem == pytest.approx(34 * 60, abs=1)
        assert "34m to expiry" in pt.format_expiry(rem)
        assert "EXPIRED" in pt.format_expiry(-5)

    def test_sl_trail_fired(self):
        # SL updated 60s after creation → fired.
        assert pt.sl_trail_fired(1_000_060_000, 1_000_000_000) is True
        # SL updated 4ms after creation → NOT fired (placed at open).
        assert pt.sl_trail_fired(1_000_000_004, 1_000_000_000) is False
        assert pt.sl_trail_fired(None, 1_000_000_000) is None


class TestFormatters:
    def test_trail_read_lines(self):
        read = pt.trail_read("SHORT", 0.4413, 0.4521, 0.4385, atr=0.0078)
        lines = pt.format_trail_read(read)
        text = "\n".join(lines)
        assert "TRAIL READ" in text
        assert "Next trigger" in text
        assert "Gap:" in text
        assert "VERDICT" in text

    def test_trail_fired_format(self):
        assert "NOT FIRED" in pt.format_trail_fired(False)
        assert "re-issued" in pt.format_trail_fired(True)
        assert "unavailable" in pt.format_trail_fired(None)
