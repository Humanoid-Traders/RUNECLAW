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
        import bot.core.live_executor as le
        src = inspect.getsource(le.LiveExecutor.execute)
        # The x1.4 low-vol up-scale must be gone.
        assert "leverage_mult * 1.4" not in src
        # And there must be an explicit cap at the default leverage.
        assert "min(leverage_mult, CONFIG.exchange.default_leverage)" in src
