"""
Trailing-stop logic shared between paper/live trading and backtesting.

Multi-stage trailing stop system:

  Stage 0 (inactive)   — No trailing, use original SL.
                          Transitions to Stage 1 when profit >= 1R.
  Stage 1 (breakeven)  — SL floor at entry price. Trail at 2.0x ATR.
                          Transitions to Stage 2 when profit >= 2R.
  Stage 2 (lock profit) — SL floor at entry + 0.5R. Trail at 1.5x ATR.
                          Transitions to Stage 3 when profit >= 3R.
  Stage 3 (aggressive)  — SL floor at entry + 1.5R. Trail at 1.0x ATR.
                          No further transitions.

The trail only tightens, never widens.
"""

from __future__ import annotations

import os


def _stage_mult(stage: int, default: float) -> float:
    """Per-stage trailing ATR multiplier, env-overridable.

    The multi-stage trail TIGHTENS as profit grows (2.0 -> 1.5 -> 1.0 xATR by
    default), which can choke a big runner into a small trailing exit. These
    knobs let the late-stage distance be widened so winners breathe. Defaults
    reproduce the original table exactly (baseline byte-identical).
    """
    try:
        return float(os.getenv(f"TRAIL_STAGE{stage}_ATR_MULT", str(default)))
    except (TypeError, ValueError):
        return default


# Stage definitions: (R-threshold to enter, SL floor in R-units, ATR trail multiplier)
_STAGES: dict[int, dict[str, float | None]] = {
    0: {"r_threshold": 0.0, "floor_r": None, "atr_mult": None},
    1: {"r_threshold": 1.0, "floor_r": 0.0,  "atr_mult": _stage_mult(1, 2.0)},
    2: {"r_threshold": 2.0, "floor_r": 0.5,  "atr_mult": _stage_mult(2, 1.5)},
    3: {"r_threshold": 3.0, "floor_r": 1.5,  "atr_mult": _stage_mult(3, 1.0)},
}


def make_trailing_state(
    entry_price: float,
    direction: str,
    initial_risk: float,
    atr_value: float,
) -> dict:
    """Create initial trailing-stop state for a new position.

    Args:
        entry_price: The entry price of the position.
        direction: "LONG" or "SHORT".
        initial_risk: The 1R risk amount (absolute price distance).
        atr_value: Current ATR value for trail distance calculation.

    Returns:
        A mutable state dict to be passed into ``update_trailing_stop``.
    """
    return {
        "entry_price": entry_price,
        "best_price": entry_price,
        "trailing_active": False,
        "initial_risk": initial_risk,
        "atr": atr_value,
        "stage": 0,
    }


def _compute_profit(entry_price: float, best_price: float, direction: str) -> float:
    """Return the favorable profit distance from entry to best price."""
    if direction == "LONG":
        return best_price - entry_price
    return entry_price - best_price


def _apply_floor_sl(
    entry_price: float,
    floor_r: float,
    initial_risk: float,
    direction: str,
    current_sl: float,
) -> float:
    """Apply a stage's SL floor and return the tighter of floor vs current SL.

    The floor is expressed in R-units from entry. For LONG positions it is
    added; for SHORT positions it is subtracted.
    """
    if direction == "LONG":
        floor_sl = entry_price + floor_r * initial_risk
        return max(current_sl, floor_sl)
    else:
        floor_sl = entry_price - floor_r * initial_risk
        return min(current_sl, floor_sl)


def _apply_atr_trail(
    best_price: float,
    atr: float,
    atr_mult: float,
    direction: str,
    current_sl: float,
) -> float:
    """Compute the ATR-based trailing SL and return the tighter value.

    For LONG: trailing SL is below best price.
    For SHORT: trailing SL is above best price.
    """
    if atr <= 0:
        return current_sl

    if direction == "LONG":
        trailing_sl = best_price - atr_mult * atr
        return max(current_sl, trailing_sl)
    else:
        trailing_sl = best_price + atr_mult * atr
        return min(current_sl, trailing_sl)


def update_trailing_stop(
    state: dict,
    current_price: float,
    original_sl: float,
    direction: str,
    trail_atr_mult: float = 1.5,
    rule: str = "multistage",
    playbook_atr_mult: float = 2.0,
) -> tuple[float, bool]:
    """Update trailing-stop state and return the effective stop-loss.

    Implements a four-stage trailing stop that progressively tightens as
    profit increases.  Each stage sets a minimum SL floor and then applies
    an ATR-based trail on top.  The SL only ever tightens, never widens.

    Backward compatible: if ``state`` lacks a ``"stage"`` key it falls back
    to the legacy single-stage behaviour (activate at 1R, trail at
    ``trail_atr_mult`` x ATR).

    Args:
        state: Mutable trailing-stop state dict (modified in place).
        current_price: Latest market price.
        original_sl: The original (or previously tightened) stop-loss.
        direction: "LONG" or "SHORT".
        trail_atr_mult: ATR multiplier for trailing distance. Only used in
            legacy mode (state without ``"stage"`` key); multi-stage mode
            uses per-stage multipliers.

    Returns:
        (effective_sl, trailing_active) -- the adjusted SL and whether
        the trailing stop is currently active.
    """
    # Reject invalid prices -- return state unchanged.
    if current_price <= 0:
        return original_sl, state.get("trailing_active", False)

    # --- Playbook rule (opt-in): trail playbook_atr_mult·ATR behind the MARK,
    #     tighten-only, no 1R activation gate. Matches the external Playbook. ---
    if rule == "playbook":
        return _playbook_update(state, current_price, original_sl, direction, playbook_atr_mult)

    # --- Legacy fallback (no "stage" key) ---
    if "stage" not in state:
        return _legacy_update(state, current_price, original_sl, direction, trail_atr_mult)

    # --- Multi-stage logic ---
    atr = state.get("atr", 0)
    initial_risk = state.get("initial_risk", 0)
    entry_price = state.get("entry_price", current_price)
    stage = state["stage"]

    # Update best price.
    if direction == "LONG":
        if current_price > state["best_price"]:
            state["best_price"] = current_price
    else:
        if current_price < state["best_price"]:
            state["best_price"] = current_price

    # Check for stage upgrades (can jump multiple stages in one tick).
    if initial_risk > 0:
        profit = _compute_profit(entry_price, state["best_price"], direction)
        profit_in_r = profit / initial_risk
        for next_stage in range(stage + 1, max(_STAGES.keys()) + 1):
            if next_stage in _STAGES and profit_in_r >= _STAGES[next_stage]["r_threshold"]:
                stage = next_stage
            else:
                break
        state["stage"] = stage

    # Stage 0: no trailing, use original SL.
    if stage == 0:
        state["trailing_active"] = False
        return original_sl, False

    # Stages 1-3: trailing is active.
    state["trailing_active"] = True
    stage_def = _STAGES[stage]
    sl = original_sl

    # Apply the SL floor for this stage.
    if stage_def["floor_r"] is not None and initial_risk > 0:
        sl = _apply_floor_sl(entry_price, stage_def["floor_r"], initial_risk, direction, sl)

    # Apply the ATR trail.
    if stage_def["atr_mult"] is not None:
        sl = _apply_atr_trail(state["best_price"], atr, stage_def["atr_mult"], direction, sl)

    return sl, True


def _playbook_update(
    state: dict,
    current_price: float,
    original_sl: float,
    direction: str,
    atr_mult: float,
) -> tuple[float, bool]:
    """Playbook trail rule: keep the SL exactly atr_mult·ATR behind the mark,
    tighten-only, with NO 1R activation gate.

        LONG:  candidate = mark − atr_mult·ATR ; ratchet UP   if candidate > SL
        SHORT: candidate = mark + atr_mult·ATR ; ratchet DOWN if candidate < SL

    The ratchet "fires" (and trailing_active flips True) the moment the mark has
    moved atr_mult·ATR past the SL — exactly the Playbook's
    ``mark ± atr_mult·ATR vs SL`` geometry. The SL never widens.
    """
    atr = state.get("atr", 0)
    sl = original_sl
    state.setdefault("best_price", state.get("entry_price", current_price))

    # Track the favorable extreme (informational / display parity).
    if direction == "LONG":
        if current_price > state["best_price"]:
            state["best_price"] = current_price
    else:
        if current_price < state["best_price"]:
            state["best_price"] = current_price

    if atr <= 0:
        return sl, state.get("trailing_active", False)

    if direction == "LONG":
        candidate = current_price - atr_mult * atr
        if candidate > sl:
            sl = candidate
            state["trailing_active"] = True
    else:  # SHORT
        candidate = current_price + atr_mult * atr
        if candidate < sl:
            sl = candidate
            state["trailing_active"] = True

    return sl, state.get("trailing_active", False)


def _legacy_update(
    state: dict,
    current_price: float,
    original_sl: float,
    direction: str,
    trail_atr_mult: float,
) -> tuple[float, bool]:
    """Original single-stage trailing-stop logic for backward compatibility."""
    atr = state.get("atr", 0)
    initial_risk = state.get("initial_risk", 0)
    sl = original_sl

    if direction == "LONG":
        if current_price > state["best_price"]:
            state["best_price"] = current_price
        if not state["trailing_active"] and initial_risk > 0:
            if state["best_price"] - state.get("entry_price", state["best_price"]) >= initial_risk:
                state["trailing_active"] = True
        if state["trailing_active"] and atr > 0:
            trailing_sl = state["best_price"] - trail_atr_mult * atr
            if trailing_sl > sl:
                sl = trailing_sl
    else:
        if current_price < state["best_price"]:
            state["best_price"] = current_price
        if not state["trailing_active"] and initial_risk > 0:
            if state.get("entry_price", state["best_price"]) - state["best_price"] >= initial_risk:
                state["trailing_active"] = True
        if state["trailing_active"] and atr > 0:
            trailing_sl = state["best_price"] + trail_atr_mult * atr
            if trailing_sl < sl:
                sl = trailing_sl

    return sl, state["trailing_active"]


def structure_ratchet(
    highs: list[float],
    lows: list[float],
    direction: str,
    current_sl: float,
    buffer: float,
    fractal: int = 3,
) -> float:
    """Ratchet the stop to just beyond the most recent CONFIRMED swing.

    For LONG: the newest swing low (a low lower than ``fractal`` neighbors on
    each side, so the last ``fractal`` bars can never qualify — no repaint)
    minus ``buffer``; adopted only if TIGHTER than current_sl. SHORT mirrors
    with swing highs. Tighten-only by construction.
    """
    n = len(lows)
    if n < 2 * fractal + 1 or buffer < 0:
        return current_sl
    if direction == "LONG":
        for i in range(n - fractal - 1, fractal - 1, -1):
            if all(lows[i] <= lows[i - j] for j in range(1, fractal + 1)) and \
               all(lows[i] <= lows[i + j] for j in range(1, fractal + 1)):
                candidate = lows[i] - buffer
                return max(current_sl, candidate)
        return current_sl
    else:
        for i in range(n - fractal - 1, fractal - 1, -1):
            if all(highs[i] >= highs[i - j] for j in range(1, fractal + 1)) and \
               all(highs[i] >= highs[i + j] for j in range(1, fractal + 1)):
                candidate = highs[i] + buffer
                return min(current_sl, candidate)
        return current_sl
