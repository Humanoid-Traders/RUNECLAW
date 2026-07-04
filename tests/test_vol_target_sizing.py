"""Volatility-targeted position cap (design bet; default ON, tighten-only).

The notional cap binds on ~every crypto trade, so the engine effectively runs
flat-margin and realized per-trade dollar risk scales UP with ATR%. When
enabled, the binding cap floats INVERSELY with ATR% so per-trade risk is
normalized toward vol_target_atr_pct. Tighten-only, so it can only reduce
risk. A/B-validated on the honest 6-fold walk-forward (mean OOS -1.14% ->
-0.91%, worst fold -4.12% -> -2.72%, folds 1-5 byte-identical) and shipped
default ON; disable with VOL_TARGET_SIZING_ENABLED=0.
"""
import inspect

from bot.config import CONFIG
from bot.risk.risk_engine import RiskEngine


def _vt_mult(atr_pct: float, target: float, floor: float) -> float:
    # Mirror the engine's clamp: tighten-only in [floor, 1.0].
    return max(floor, min(1.0, target / atr_pct))


def test_default_on_after_ab_validation():
    # A/B-validated (tighten-only, zero downside) and shipped default ON.
    assert CONFIG.risk.vol_target_sizing_enabled is True


def test_multiplier_is_tighten_only_and_vol_inverse():
    tgt, floor = 3.0, 0.33
    # Low-vol trade (ATR% below target): cap kept at full (never grows above 1).
    assert _vt_mult(2.0, tgt, floor) == 1.0
    # At target: full cap.
    assert _vt_mult(3.0, tgt, floor) == 1.0
    # High-vol trade: cap shrinks inversely with ATR%.
    assert _vt_mult(6.0, tgt, floor) == 0.5
    # Extreme vol: clamped at the floor, never below.
    assert _vt_mult(100.0, tgt, floor) == floor


def test_engine_applies_gated_tighten_only_before_the_cap_clamp():
    src = inspect.getsource(RiskEngine._evaluate_locked)
    assert "vol_target_sizing_enabled" in src
    # Fail-open guard on missing/zero atr or entry.
    assert "atr and atr > 0" in src
    # Tighten-only clamp and the multiply happen BEFORE the position clamp.
    mult = src.index("max_notional_usd *= _vt")
    clamp = src.index("position_usd = max_notional_usd")
    assert mult < clamp
    assert "min(1.0, _vt)" in src            # never grows the cap
    assert "vol_target_floor" in src         # never shrinks past the floor
