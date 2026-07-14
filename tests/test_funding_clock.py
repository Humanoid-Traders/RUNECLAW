"""
Funding-clock gate + liquidation-cascade chase veto (Fable-5 round 5).

The funding gate blocks ONLY the narrow triple: pre-settlement window +
extreme rate + paying side. The cascade veto blocks only entries that
CHASE a range+volume explosion; fading is always allowed. Both fail open
on missing data.
"""

import inspect

from bot.risk.funding_clock import (
    SETTLEMENT_INTERVAL_SEC,
    cascade_state,
    cascade_veto,
    funding_clock_verdict,
    pays_funding,
    seconds_to_settlement,
)

# 2026-07-14 00:00:00 UTC — exactly on a settlement boundary.
T_SETTLE = 1_784_073_600.0
assert T_SETTLE % SETTLEMENT_INTERVAL_SEC == 0


# ── the clock ─────────────────────────────────────────────────────────

def test_seconds_to_settlement():
    assert seconds_to_settlement(T_SETTLE - 600) == 600.0
    assert seconds_to_settlement(T_SETTLE + 3600) == SETTLEMENT_INTERVAL_SEC - 3600


def test_pays_funding_sides():
    assert pays_funding("LONG", 0.001) is True      # positive: longs pay
    assert pays_funding("SHORT", 0.001) is False
    assert pays_funding("SHORT", -0.001) is True    # negative: shorts pay
    assert pays_funding("LONG", -0.001) is False
    assert pays_funding("LONG", 0.0) is False


# ── the gate: blocks only the narrow triple ───────────────────────────

class TestFundingVerdict:
    def test_blocks_paying_side_of_extreme_rate_near_settle(self):
        blocked, reason = funding_clock_verdict(
            "LONG", 0.001, T_SETTLE - 900)     # 15m out, +0.10% extreme
        assert blocked is True
        assert "pay extreme funding" in reason

    def test_receiving_side_passes(self):
        blocked, reason = funding_clock_verdict("SHORT", 0.001, T_SETTLE - 900)
        assert blocked is False
        assert "paid TO this side" in reason

    def test_far_from_settlement_passes(self):
        blocked, reason = funding_clock_verdict("LONG", 0.001, T_SETTLE - 7200)
        assert blocked is False
        assert "away" in reason

    def test_mild_rate_passes(self):
        blocked, _ = funding_clock_verdict("LONG", 0.0001, T_SETTLE - 900)
        assert blocked is False

    def test_missing_funding_fails_open(self):
        blocked, reason = funding_clock_verdict("LONG", None, T_SETTLE - 900)
        assert blocked is False and "skip" in reason

    def test_negative_extreme_blocks_short(self):
        blocked, _ = funding_clock_verdict("SHORT", -0.002, T_SETTLE - 600)
        assert blocked is True


# ── cascade detection ─────────────────────────────────────────────────

def _series(n=30, price=100.0, vol=1000.0):
    highs = [price + 1.0] * n
    lows = [price - 1.0] * n
    closes = [price] * n
    volumes = [vol] * n
    return highs, lows, closes, volumes


class TestCascadeState:
    def test_quiet_series_has_no_cascade(self):
        h, low, c, v = _series()
        assert cascade_state(h, low, c, v, atr=2.0)["cascade"] is False

    def test_range_and_volume_explosion_detected_down(self):
        h, low, c, v = _series()
        # last closed bar: 6x ATR range, 5x volume, closing down
        h[-1], low[-1], c[-1], v[-1] = 100.0, 88.0, 89.0, 5000.0
        st = cascade_state(h, low, c, v, atr=2.0)
        assert st == {"cascade": True, "direction": "DOWN", "bars_ago": 1}

    def test_range_without_volume_is_not_a_cascade(self):
        h, low, c, v = _series()
        h[-1], low[-1], c[-1] = 100.0, 88.0, 89.0   # big range, normal volume
        assert cascade_state(h, low, c, v, atr=2.0)["cascade"] is False

    def test_volume_without_range_is_not_a_cascade(self):
        h, low, c, v = _series()
        v[-1] = 9000.0                               # volume spike, tight range
        assert cascade_state(h, low, c, v, atr=2.0)["cascade"] is False

    def test_up_flush_direction(self):
        h, low, c, v = _series()
        h[-1], low[-1], c[-1], v[-1] = 112.0, 100.0, 111.0, 5000.0
        assert cascade_state(h, low, c, v, atr=2.0)["direction"] == "UP"

    def test_cascade_seen_within_recent_window_only(self):
        h, low, c, v = _series()
        h[-3], low[-3], c[-3], v[-3] = 100.0, 88.0, 89.0, 5000.0
        st = cascade_state(h, low, c, v, atr=2.0, recent_bars=3)
        assert st["cascade"] is True and st["bars_ago"] == 3
        st2 = cascade_state(h, low, c, v, atr=2.0, recent_bars=2)
        assert st2["cascade"] is False

    def test_degenerate_input_never_raises(self):
        assert cascade_state([], [], [], [], atr=0.0)["cascade"] is False
        assert cascade_state([1], [1], [1], [1], atr=None)["cascade"] is False


# ── cascade veto: chase blocked, fade allowed ─────────────────────────

class TestCascadeVeto:
    def test_chasing_the_flush_is_vetoed(self):
        st = {"cascade": True, "direction": "DOWN", "bars_ago": 1}
        assert cascade_veto("SHORT", st) is not None   # shorting INTO the flush
        assert cascade_veto("LONG", st) is None        # fading is allowed

    def test_up_flush_mirrors(self):
        st = {"cascade": True, "direction": "UP", "bars_ago": 2}
        assert cascade_veto("LONG", st) is not None
        assert cascade_veto("SHORT", st) is None

    def test_no_cascade_no_veto(self):
        assert cascade_veto("LONG", {"cascade": False}) is None
        assert cascade_veto("LONG", {}) is None


# ── wiring pins ───────────────────────────────────────────────────────

class TestWiring:
    def test_risk_engine_has_funding_clock_check(self):
        from bot.risk.risk_engine import RiskEngine
        src = inspect.getsource(RiskEngine)
        assert "FUNDING_CLOCK" in src
        assert "funding_clock_verdict" in src

    def test_analyzer_has_cascade_veto(self):
        from bot.core import analyzer as an_mod
        src = inspect.getsource(an_mod)
        assert "cascade_veto_enabled" in src
        assert "cascade_state(" in src

    def test_config_defaults(self):
        import os
        import pytest
        if any(os.environ.get(k) for k in
               ("FUNDING_CLOCK_GATE_ENABLED", "CASCADE_VETO_ENABLED")):
            pytest.skip("env override present")
        from bot.config import CONFIG
        assert CONFIG.risk.funding_clock_gate_enabled is True   # narrow + shadow-priced
        assert CONFIG.analyzer.cascade_veto_enabled is False    # pending A/B