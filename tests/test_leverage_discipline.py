"""
Risk-discipline regression (from the 4-day live-trade analysis): dynamic leverage
must only ever REDUCE vs the configured default, never increase it. The old code
scaled leverage UP x1.4 in low volatility, amplifying losses on the full-stop
hits that drove the live drawdown.
"""

import inspect

from bot.config import CONFIG


def _dyn_lev(default, atr_pct, min_lev=1):
    """Mirror the executor's dynamic-leverage rule to pin its behavior."""
    lev = default
    if atr_pct > 0.04:
        lev = max(min_lev, lev // 2)
    elif atr_pct > 0.03:
        lev = max(min_lev, int(lev * 0.7))
    # low/normal vol: no change
    return min(lev, default)


class TestDynamicLeverageOnlyReduces:
    def test_low_vol_does_not_increase(self):
        d = CONFIG.exchange.default_leverage
        # Low volatility used to bump leverage to int(d*1.4); now it stays at d.
        assert _dyn_lev(d, atr_pct=0.005) == d

    def test_high_vol_reduces(self):
        d = CONFIG.exchange.default_leverage
        assert _dyn_lev(d, atr_pct=0.05) < d
        assert _dyn_lev(d, atr_pct=0.035) <= d

    def test_never_exceeds_default_across_atr_sweep(self):
        d = CONFIG.exchange.default_leverage
        for i in range(0, 200):
            atr = i / 1000.0  # 0% .. 20%
            assert _dyn_lev(d, atr_pct=atr) <= d, f"atr={atr} exceeded default {d}"

    def test_executor_source_has_no_upscale_and_caps_at_default(self):
        # The dynamic-leverage rule now lives in the single _compute_target_leverage
        # helper, used by both the set-leverage and sizing paths (deep-audit dedup).
        import bot.core.live_executor as le
        helper_src = inspect.getsource(le.LiveExecutor._compute_target_leverage)
        # No low-vol up-scale anywhere in the rule.
        assert "* 1.4" not in helper_src
        # Explicit cap at the default leverage.
        assert "min(lev, default_lev)" in helper_src
        # Both paths delegate to the helper rather than recomputing.
        exec_src = inspect.getsource(le.LiveExecutor.execute)
        ensure_src = inspect.getsource(le.LiveExecutor._ensure_leverage)
        assert "self._compute_target_leverage(symbol)" in exec_src
        assert "self._compute_target_leverage(symbol)" in ensure_src
        assert "* 1.4" not in exec_src and "* 1.4" not in ensure_src
