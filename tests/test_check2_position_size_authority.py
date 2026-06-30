"""
Check #2 (POSITION_SIZE) compares against the per-trade cap (deep-audit medium).

position_usd is clamped to max_position_pct (13% default) just before check #2,
but check #2 used to compare it against max_symbol_exposure_pct (20% default) —
so a single capped trade (≤13%) could never reach 20% and the reject branch was
UNREACHABLE, making its "real authority" docstring false. The aggregate
per-symbol limit (max_symbol_exposure_pct) is the job of check #15.

Check #2 now verifies the post-cap margin against max_position_pct: a true
fail-closed invariant that passes in normal operation but rejects if the clamp
is ever bypassed/miscomputed.
"""

import inspect

from bot.risk.risk_engine import RiskEngine

_within = RiskEngine._position_within_cap


class TestPositionWithinCap:
    def test_below_cap_passes(self):
        ok, pct = _within(80.0, 1000.0, 13.0)  # 8% margin
        assert ok is True
        assert round(pct, 4) == 8.0

    def test_exactly_at_cap_passes(self):
        # The clamp produces exactly the cap; the epsilon must let it pass.
        ok, pct = _within(130.0, 1000.0, 13.0)  # 13.0%
        assert ok is True
        assert round(pct, 4) == 13.0

    def test_above_cap_rejects(self):
        # Simulated clamp bypass: 20% margin must now REJECT (was unreachable).
        ok, pct = _within(200.0, 1000.0, 13.0)  # 20%
        assert ok is False
        assert round(pct, 4) == 20.0

    def test_just_over_cap_rejects(self):
        ok, _ = _within(131.0, 1000.0, 13.0)  # 13.1%
        assert ok is False

    def test_zero_equity_rejects(self):
        ok, pct = _within(100.0, 0.0, 13.0)
        assert ok is False and pct == 0.0

    def test_negative_equity_rejects(self):
        ok, _ = _within(100.0, -50.0, 13.0)
        assert ok is False


class TestComparedAgainstPerTradeCap:
    def test_check2_uses_max_position_pct_not_symbol_exposure(self):
        src = inspect.getsource(RiskEngine._evaluate_locked)
        # The check-#2 block compares the post-cap margin to the per-trade cap…
        assert "max_margin_pct = CONFIG.risk.max_position_pct" in src
        # …and the old (unreachable) comparison against the symbol-exposure limit
        # is gone from check #2.
        assert "max_margin_pct = CONFIG.risk.max_symbol_exposure_pct" not in src

    def test_reject_branch_is_reachable_at_symbol_exposure_level(self):
        # At the OLD threshold (20%) a 15% trade passed; against the per-trade cap
        # (13%) the same trade is correctly rejected — proving the branch is live.
        assert _within(150.0, 1000.0, 13.0)[0] is False   # 15% > 13% cap → reject
        assert _within(150.0, 1000.0, 20.0)[0] is True    # would have passed at 20%
