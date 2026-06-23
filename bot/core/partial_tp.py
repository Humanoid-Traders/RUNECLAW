"""
RUNECLAW Partial Take-Profit Ladder — institutional-style position scaling.

Instead of all-or-nothing exits at a single TP, this module manages
staged profit-taking:
  Stage 1 (TP1): Close 50% at 1.5R, move SL to breakeven
  Stage 2 (TP2): Close 30% at 2.5R, lock profit with tight trail
  Stage 3 (Runner): Remaining 20% rides with aggressive ATR trail

Benefits:
  - Locks in profits early to reduce variance
  - Lets winners run with a portion of the position
  - Moves SL to breakeven after TP1, making the trade "risk-free"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from bot.config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class PartialTPState:
    """Tracks partial TP progress for a single position."""
    trade_id: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    original_sl: float
    original_tp: float
    initial_risk: float  # abs(entry - sl)
    original_qty: float
    atr: float  # ATR at entry for trailing

    # Stage tracking
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp1_qty_closed: float = 0.0
    tp2_qty_closed: float = 0.0
    remaining_qty: float = 0.0
    current_sl: float = 0.0
    runner_trail_best: float = 0.0

    def __post_init__(self):
        self.remaining_qty = self.original_qty
        self.current_sl = self.original_sl
        self.runner_trail_best = self.entry_price


@dataclass
class PartialTPAction:
    """Action to execute from partial TP check."""
    action: str  # "close_partial", "move_sl", "close_runner", "none"
    qty_to_close: float = 0.0
    new_sl: float = 0.0
    reason: str = ""
    stage: str = ""  # "tp1", "tp2", "runner_sl"


def create_partial_tp_state(
    trade_id: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    quantity: float,
    atr: float,
) -> PartialTPState:
    """Create a new partial TP tracking state for a position."""
    initial_risk = abs(entry_price - stop_loss)
    return PartialTPState(
        trade_id=trade_id,
        direction=direction,
        entry_price=entry_price,
        original_sl=stop_loss,
        original_tp=take_profit,
        initial_risk=initial_risk,
        original_qty=quantity,
        atr=atr if atr > 0 else entry_price * 0.02,
    )


def check_partial_tp(
    state: PartialTPState,
    current_price: float,
) -> list[PartialTPAction]:
    """Check if current price triggers any partial TP actions.

    Returns a list of actions to execute (may be multiple if price
    jumped past multiple levels in one tick).
    """
    if not CONFIG.partial_tp.enabled:
        return []

    actions: list[PartialTPAction] = []
    cfg = CONFIG.partial_tp

    is_long = state.direction == "LONG"

    # Calculate R-multiple of current price move
    if is_long:
        current_r = (current_price - state.entry_price) / state.initial_risk if state.initial_risk > 0 else 0
    else:
        current_r = (state.entry_price - current_price) / state.initial_risk if state.initial_risk > 0 else 0

    # Check TP1: first partial close
    if not state.tp1_hit and current_r >= cfg.tp1_r_multiple:
        close_qty = state.original_qty * (cfg.tp1_close_pct / 100.0)
        close_qty = min(close_qty, state.remaining_qty)

        if close_qty > 0:
            state.tp1_hit = True
            state.tp1_qty_closed = close_qty
            state.remaining_qty -= close_qty

            # Move SL to breakeven (+ small buffer for fees)
            fee_buffer = state.entry_price * 0.001  # 0.1% buffer
            if is_long:
                new_sl = state.entry_price + fee_buffer
            else:
                new_sl = state.entry_price - fee_buffer
            state.current_sl = new_sl

            actions.append(PartialTPAction(
                action="close_partial",
                qty_to_close=close_qty,
                new_sl=new_sl,
                reason=f"TP1 hit at {current_r:.1f}R — closing {cfg.tp1_close_pct:.0f}%, SL→breakeven",
                stage="tp1",
            ))

    # Check TP2: second partial close
    if state.tp1_hit and not state.tp2_hit and current_r >= cfg.tp2_r_multiple:
        close_qty = state.original_qty * (cfg.tp2_close_pct / 100.0)
        close_qty = min(close_qty, state.remaining_qty)

        if close_qty > 0:
            state.tp2_hit = True
            state.tp2_qty_closed = close_qty
            state.remaining_qty -= close_qty

            # Tighten SL to lock profit (1R above entry)
            lock_distance = state.initial_risk
            if is_long:
                new_sl = state.entry_price + lock_distance
            else:
                new_sl = state.entry_price - lock_distance
            state.current_sl = new_sl

            actions.append(PartialTPAction(
                action="close_partial",
                qty_to_close=close_qty,
                new_sl=new_sl,
                reason=f"TP2 hit at {current_r:.1f}R — closing {cfg.tp2_close_pct:.0f}%, locking 1R profit",
                stage="tp2",
            ))

    # Runner: aggressive trailing stop for remaining position
    if state.tp2_hit and state.remaining_qty > 0:
        trail_dist = state.atr * cfg.runner_trail_atr_mult

        if is_long:
            if current_price > state.runner_trail_best:
                state.runner_trail_best = current_price
            trail_sl = state.runner_trail_best - trail_dist
            new_sl = max(trail_sl, state.current_sl)  # never lower SL

            if new_sl > state.current_sl:
                state.current_sl = new_sl
                actions.append(PartialTPAction(
                    action="move_sl",
                    new_sl=new_sl,
                    reason=f"Runner trail: SL→{new_sl:.4f} (best={state.runner_trail_best:.4f})",
                    stage="runner",
                ))

            # Check if runner SL hit
            if current_price <= state.current_sl:
                actions.append(PartialTPAction(
                    action="close_runner",
                    qty_to_close=state.remaining_qty,
                    reason=f"Runner SL hit at {current_price:.4f}",
                    stage="runner_sl",
                ))
        else:
            if current_price < state.runner_trail_best:
                state.runner_trail_best = current_price
            trail_sl = state.runner_trail_best + trail_dist
            new_sl = min(trail_sl, state.current_sl)  # never raise SL for shorts

            if new_sl < state.current_sl:
                state.current_sl = new_sl
                actions.append(PartialTPAction(
                    action="move_sl",
                    new_sl=new_sl,
                    reason=f"Runner trail: SL→{new_sl:.4f} (best={state.runner_trail_best:.4f})",
                    stage="runner",
                ))

            # Check if runner SL hit
            if current_price >= state.current_sl:
                actions.append(PartialTPAction(
                    action="close_runner",
                    qty_to_close=state.remaining_qty,
                    reason=f"Runner SL hit at {current_price:.4f}",
                    stage="runner_sl",
                ))

    return actions


def partial_tp_summary(state: PartialTPState) -> dict:
    """Get a summary of partial TP state for display."""
    pct_closed = ((state.tp1_qty_closed + state.tp2_qty_closed) / state.original_qty * 100) if state.original_qty > 0 else 0
    pct_remaining = (state.remaining_qty / state.original_qty * 100) if state.original_qty > 0 else 0

    return {
        "trade_id": state.trade_id,
        "tp1_hit": state.tp1_hit,
        "tp2_hit": state.tp2_hit,
        "pct_closed": round(pct_closed, 1),
        "pct_remaining": round(pct_remaining, 1),
        "current_sl": state.current_sl,
        "original_sl": state.original_sl,
        "runner_best": state.runner_trail_best if state.tp2_hit else None,
    }
