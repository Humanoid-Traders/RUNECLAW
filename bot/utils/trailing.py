"""
Trailing-stop logic shared between paper/live trading and backtesting.

Strategy: trailing stop activates after 1R profit, then trails at 1.5x ATR
behind the best favorable price. Only tightens, never widens.
"""

from __future__ import annotations

from typing import Optional


def make_trailing_state(
    entry_price: float,
    direction: str,
    initial_risk: float,
    atr_value: float,
) -> dict:
    """Create initial trailing-stop state for a new position."""
    return {
        "entry_price": entry_price,
        "best_price": entry_price,
        "trailing_active": False,
        "initial_risk": initial_risk,
        "atr": atr_value,
    }


def update_trailing_stop(
    state: dict,
    current_price: float,
    original_sl: float,
    direction: str,
) -> tuple[float, bool]:
    """
    Update trailing-stop state and return the effective stop-loss.

    Args:
        state: Mutable trailing-stop state dict (modified in place).
        current_price: Latest market price.
        original_sl: The original (or previously tightened) stop-loss.
        direction: "LONG" or "SHORT".

    Returns:
        (effective_sl, trailing_active) — the adjusted SL and whether
        the trailing stop is currently active.
    """
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
            trailing_sl = state["best_price"] - 1.5 * atr
            if trailing_sl > sl:
                sl = trailing_sl
    else:
        if current_price < state["best_price"]:
            state["best_price"] = current_price
        if not state["trailing_active"] and initial_risk > 0:
            if state.get("entry_price", state["best_price"]) - state["best_price"] >= initial_risk:
                state["trailing_active"] = True
        if state["trailing_active"] and atr > 0:
            trailing_sl = state["best_price"] + 1.5 * atr
            if trailing_sl < sl:
                sl = trailing_sl

    return sl, state["trailing_active"]
