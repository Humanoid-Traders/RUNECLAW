"""
RUNECLAW — continuous equity-curve throttle (pure math).

Scales position size continuously off the rolling profit factor of the
most recent closed trades, instead of the step functions the existing
breakers use (equity-curve MA: 1.0/0.5/0.0; live-perf governor:
1.0/reduce/0.0). A strategy drifting from PF 1.4 to 1.0 to 0.9 gets
proportionally smaller — it doesn't ride full size until it crashes
through a discrete threshold.

The multiplier NEVER reaches zero (floor_mult > 0 by config bounds):
a full pause starves the rolling window of new closed trades, so a
paused strategy can never demonstrate recovery. At the floor, trades
keep flowing at reduced size, the window keeps refreshing, and the
throttle re-scales itself the moment recent PF recovers — while the
shadow book keeps pricing rejected ideas at full pace in parallel.

Tighten-only and fail-open: the result is always in (0, 1], and any
degenerate input (too few samples, no losses yet) returns 1.0.
"""

from __future__ import annotations

from typing import Optional, Sequence


def rolling_profit_factor(pnls: Sequence[float]) -> Optional[float]:
    """Profit factor (gross wins / gross losses) of a PnL window.

    Returns None when the window has no losses — PF is undefined/infinite
    there, and an all-winning window must never be throttled anyway.
    """
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = sum(-p for p in pnls if p < 0)
    if gross_loss <= 0:
        return None
    return gross_win / gross_loss


def throttle_multiplier(pf: Optional[float],
                        pf_full: float = 1.2,
                        pf_floor: float = 0.8,
                        floor_mult: float = 0.25) -> float:
    """Map a rolling PF to a size multiplier in [floor_mult, 1.0].

    PF >= pf_full  -> 1.0 (full size)
    PF <= pf_floor -> floor_mult (minimum size, never zero)
    between        -> linear ramp
    None (undefined PF) -> 1.0 (fail open)
    """
    if pf is None:
        return 1.0
    if pf_full <= pf_floor:  # misconfigured band → act as a hard step
        return 1.0 if pf >= pf_full else floor_mult
    if pf >= pf_full:
        return 1.0
    if pf <= pf_floor:
        return floor_mult
    frac = (pf - pf_floor) / (pf_full - pf_floor)
    return floor_mult + (1.0 - floor_mult) * frac
