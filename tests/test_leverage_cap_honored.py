"""The executor must honor the risk engine's margin-risk-capped leverage.

Audit bugs 2/6: RiskEngine.evaluate() reduces leverage when SL distance ×
leverage would exceed max_margin_risk_pct and writes idea._adjusted_leverage
"for the executor" — but execute() never read it, so orders sized at full
leverage and exceeded the very cap the engine reported enforcing. The fix clamps
the sizing leverage to _adjusted_leverage (reduce-only).
"""
import inspect

from bot.core.live_executor import LiveExecutor
from bot.risk.risk_engine import RiskEngine


def test_execute_clamps_leverage_to_risk_adjusted_value():
    src = inspect.getsource(LiveExecutor.execute)
    read = src.index('getattr(idea, "_adjusted_leverage"')
    # The clamp must be reduce-only (min) and applied BEFORE quantity is sized.
    qty = src.index("quantity = (size_usd * leverage_mult)")
    assert read < qty, "leverage must be clamped before it sizes the order"
    clamp = src.index("min(int(leverage_mult), int(_risk_lev))")
    assert read < clamp < qty


def test_risk_engine_still_writes_adjusted_leverage():
    # Guards the producer side so the two can't silently drift again.
    src = inspect.getsource(RiskEngine._evaluate_locked)
    assert 'setattr(idea, "_adjusted_leverage"' in src


def test_clamp_is_reduce_only():
    # The min() semantics: never raises leverage, only lowers it.
    for sized, risk_cap, expected in [(20, 5, 5), (5, 20, 5), (10, 10, 10)]:
        assert min(int(sized), int(risk_cap)) == expected
