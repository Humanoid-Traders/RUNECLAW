"""
Continuous equity-curve throttle — pure math, engine wiring, sizing effect.

The throttle scales position size off the rolling PF of recent realized
closes: full size at/above pf_full, floor_mult at/below pf_floor, linear
ramp between. It never pauses (floor > 0) so the window keeps refreshing
and recovery is observable. Default OFF pending the frozen-benchmark A/B.
"""

import os
from unittest.mock import patch

import pytest

from bot.risk.equity_throttle import rolling_profit_factor, throttle_multiplier


# ── rolling_profit_factor ─────────────────────────────────────────────

class TestRollingPF:
    def test_basic_pf(self):
        # gross wins 30, gross losses 20 -> PF 1.5
        assert rolling_profit_factor([10, 20, -5, -15]) == pytest.approx(1.5)

    def test_no_losses_is_undefined(self):
        assert rolling_profit_factor([10, 20]) is None
        assert rolling_profit_factor([]) is None
        assert rolling_profit_factor([0.0, 0.0]) is None

    def test_all_losses_is_zero(self):
        assert rolling_profit_factor([-10, -20]) == 0.0


# ── throttle_multiplier ───────────────────────────────────────────────

class TestThrottleMultiplier:
    def test_full_size_at_or_above_pf_full(self):
        assert throttle_multiplier(1.2) == 1.0
        assert throttle_multiplier(3.0) == 1.0

    def test_floor_at_or_below_pf_floor(self):
        assert throttle_multiplier(0.8) == 0.25
        assert throttle_multiplier(0.0) == 0.25

    def test_linear_ramp_midpoint(self):
        # PF 1.0 is halfway between 0.8 and 1.2 -> 0.25 + 0.75/2 = 0.625
        assert throttle_multiplier(1.0) == pytest.approx(0.625)

    def test_ramp_is_monotone(self):
        pfs = [0.8, 0.9, 1.0, 1.1, 1.2]
        mults = [throttle_multiplier(p) for p in pfs]
        assert mults == sorted(mults)
        assert mults[0] == 0.25 and mults[-1] == 1.0

    def test_undefined_pf_fails_open(self):
        assert throttle_multiplier(None) == 1.0

    def test_never_zero(self):
        assert throttle_multiplier(0.0, floor_mult=0.05) == 0.05

    def test_inverted_band_degrades_to_step(self):
        # pf_full <= pf_floor is a misconfiguration; behave as a hard step
        assert throttle_multiplier(1.5, pf_full=1.0, pf_floor=1.0) == 1.0
        assert throttle_multiplier(0.5, pf_full=1.0, pf_floor=1.0) == 0.25


# ── engine integration ────────────────────────────────────────────────

def _engine():
    import tempfile

    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    state = os.path.join(tempfile.mkdtemp(prefix="rc-throttle-"),
                         "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=state)


class TestEngineWiring:
    def test_warmup_fails_open(self):
        eng = _engine()
        for pnl in [-10.0] * 5:  # below min_samples (10)
            eng.record_trade_result(pnl)
        assert eng.equity_throttle_multiplier == 1.0
        assert eng.equity_throttle_state()["status"] in ("OFF", "WARMUP")

    def test_losing_window_hits_floor(self):
        eng = _engine()
        for pnl in [10.0] + [-10.0] * 11:  # PF well below 0.8
            eng.record_trade_result(pnl)
        assert eng.equity_throttle_multiplier == pytest.approx(0.25)

    def test_winning_window_full_size(self):
        eng = _engine()
        for pnl in [30.0, 30.0, 30.0, -10.0] * 3:  # PF 9
            eng.record_trade_result(pnl)
        assert eng.equity_throttle_multiplier == 1.0

    def test_recovery_rescales_without_reset(self):
        # The whole point of never pausing: fresh wins push the rolling PF
        # back up and the multiplier recovers on its own.
        eng = _engine()
        for pnl in [-10.0] * 12:
            eng.record_trade_result(pnl)
        floor = eng.equity_throttle_multiplier
        assert floor == pytest.approx(0.25)
        for pnl in [25.0] * 15:  # wins push the 20-window PF above pf_full
            eng.record_trade_result(pnl)
        assert eng.equity_throttle_multiplier == 1.0

    def test_state_shape(self):
        eng = _engine()
        st = eng.equity_throttle_state()
        assert set(st) == {"enabled", "samples", "pf", "multiplier", "status"}

    def test_flag_default_off(self):
        from bot.config import CONFIG
        if os.environ.get("EQUITY_THROTTLE_ENABLED"):
            pytest.skip("env override present")
        assert CONFIG.risk.equity_throttle_enabled is False

    def test_sizing_applies_multiplier_when_enabled(self):
        # Same idea, same engine: with the flag on and the throttle at the
        # floor, the computed position must shrink by exactly that multiplier.
        # The throttle property is patched directly (feeding real losses
        # would trip the consecutive-loss circuit breaker first).
        from datetime import UTC, datetime

        from bot.config import CONFIG
        from bot.utils.models import Direction, TradeIdea

        def _size(enabled):
            eng = _engine()
            idea = TradeIdea(
                id="thr-test", asset="BTC/USDT", direction=Direction.LONG,
                entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                confidence=0.9, risk_reward_ratio=2.0, reasoning="t",
                source="test", timestamp=datetime.now(UTC))
            old = CONFIG.risk.equity_throttle_enabled
            object.__setattr__(CONFIG.risk, "equity_throttle_enabled", enabled)
            try:
                with patch.object(type(eng), "equity_throttle_multiplier",
                                  property(lambda self: 0.25)):
                    chk = eng.evaluate(idea)
            finally:
                object.__setattr__(CONFIG.risk, "equity_throttle_enabled", old)
            return chk.position_size_usd

        base = _size(False)
        throttled = _size(True)
        assert base > 0
        assert throttled == pytest.approx(base * 0.25, rel=0.01)
