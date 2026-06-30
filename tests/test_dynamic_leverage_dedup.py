"""
Unified dynamic-leverage computation (deep-audit medium).

The set-leverage path (_ensure_leverage) and the sizing path (execute) each had
their own copy of the dynamic-leverage logic, and they had DIVERGED: the set
path still scaled leverage UP ×1.4 in low vol while sizing was reduce-only, so
the leverage SET on the exchange disagreed with the leverage used to SIZE the
order. Both now call the single reduce-only _compute_target_leverage, which
never exceeds the configured default.
"""

import inspect
from types import SimpleNamespace

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor


class _AnyATR(dict):
    """dict whose .get() always yields a fixed ATR regardless of the symbol key."""
    def __init__(self, val):
        super().__init__()
        self._val = val

    def get(self, key, default=None):
        return self._val


def _exec(monkeypatch, *, default_lev=10, min_lev=2, enabled=True, atr=0.02):
    executor = LiveExecutor()
    executor._last_atr_pct = _AnyATR(atr)
    monkeypatch.setattr(live_executor_mod, "CONFIG", SimpleNamespace(
        exchange=SimpleNamespace(
            default_leverage=default_lev, min_leverage=min_lev,
            dynamic_leverage_enabled=enabled)))
    return executor


class TestComputeTargetLeverage:
    def test_disabled_returns_default(self, monkeypatch):
        ex = _exec(monkeypatch, enabled=False, atr=0.005)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_high_vol_halves(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.05)  # >4% ATR
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 5

    def test_elevated_vol_scales_07(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.035)  # 3–4% ATR → int(10*0.7)=7
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 7

    def test_low_vol_does_not_upscale(self, monkeypatch):
        # THE divergence regression: low vol must KEEP the default, never ×1.4.
        ex = _exec(monkeypatch, atr=0.005)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_normal_vol_keeps_default(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.02)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 10

    def test_never_exceeds_default(self, monkeypatch):
        # Across a sweep of ATRs the result is always ≤ default and ≥ 1.
        for atr in (0.0, 0.005, 0.02, 0.031, 0.05, 0.2):
            ex = _exec(monkeypatch, atr=atr)
            lev = ex._compute_target_leverage("BTC/USDT:USDT")
            assert 1 <= lev <= 10

    def test_min_leverage_floor(self, monkeypatch):
        # default 3, high vol → 3//2=1 → floored to min_lev=2.
        ex = _exec(monkeypatch, default_lev=3, min_lev=2, atr=0.05)
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 2

    def test_exception_falls_back_to_one(self, monkeypatch):
        ex = _exec(monkeypatch, atr=0.02)
        monkeypatch.setattr(live_executor_mod, "normalize_symbol",
                            lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ex._compute_target_leverage("BTC/USDT:USDT") == 1


class TestBothPathsUseHelper:
    def test_set_path_uses_helper(self):
        src = inspect.getsource(LiveExecutor._ensure_leverage)
        assert "_target_leverage = self._compute_target_leverage(symbol)" in src
        # The old up-scaling branch is gone.
        assert "* 1.4" not in src

    def test_size_path_uses_helper(self):
        src = inspect.getsource(LiveExecutor.execute)
        assert "leverage_mult = self._compute_target_leverage(symbol)" in src
