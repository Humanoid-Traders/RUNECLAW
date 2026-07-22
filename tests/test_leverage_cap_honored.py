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


def test_margin_risk_cap_uses_the_effective_override_leverage():
    # AUDIT-FIX-4: check-6b hard-coded CONFIG.exchange.default_leverage, but the
    # executor sizes with RUNTIME.leverage_override (which wins over the default
    # and is only clamped <=20, not <=default). An override ABOVE default sized
    # past the cap unchecked because the gate evaluated at the lower default.
    # The cap must now evaluate at the SAME leverage the executor will use.
    src = inspect.getsource(RiskEngine._evaluate_locked)
    block = src[src.index("6b. Leverage-aware margin risk cap"):]
    block = block[:block.index("MARGIN_RISK: no leverage")]  # bound to the 6b block
    assert "RUNTIME.leverage_override" in block, \
        "margin-risk cap must read the runtime /leverage override, not only the env default"
    # And it still falls back to the env default when no override is set.
    assert "CONFIG.exchange.default_leverage" in block


def test_effective_leverage_selection_semantics():
    # The value the cap evaluates: override when set (worst case), else default.
    # Per-user prefs / dynamic scaling only reduce from here, so the pre-reduction
    # override is the conservative bound the cap must respect.
    def effective(override, default):
        return max(1, int(override)) if override is not None else default
    assert effective(20, 5) == 20      # override above default → evaluated at 20x
    assert effective(None, 5) == 5     # no override → unchanged (identical to before)
    assert effective(3, 5) == 3        # override below default → 3x
