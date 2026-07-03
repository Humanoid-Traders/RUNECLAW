"""Shared close-reason labelling for stop-side exits.

A LONG stop that sits ABOVE entry (or a SHORT stop BELOW entry) is a
profit-locking stop — it can only get there by trailing/breakeven ratcheting,
because the entry-time validators (``bot/utils/models.py``) forbid an inverted
ORIGINAL stop. When such a stop fills, the position closes in PROFIT, so
recording it as a bare "SL HIT" (which every dashboard/tally reads as a loss)
is wrong. This helper is the single place both close/reconcile paths
(``live_executor`` and ``exchange_sync``) use to tell a profit-locking
(trailing) stop apart from a protective loss stop.

Pure functions, no imports — safe to import from anywhere without a cycle.

Incident: TI-a4ba8a82 — a LONG opened at 0.5638 whose trailing stop had
ratcheted to 0.5679 (above entry) filled for a GAIN, but the reconcile path
labelled it "SL HIT (inferred)".
"""

from __future__ import annotations

from typing import Optional


def is_profit_locking_stop(
    is_long: bool,
    entry_price: float,
    stop_loss: float,
    exit_price: Optional[float] = None,
    trailing_active: bool = False,
    realized_pnl: Optional[float] = None,
) -> bool:
    """True when a stop-side exit is a profit-lock (trailing/breakeven), not a loss.

    Signals, strongest first:
      1. ``trailing_active`` — the position explicitly had a live trailing stop.
      2. ``realized_pnl`` > 0 — the exchange says the close booked a profit; a
         genuine protective stop can only fill at a loss.
      3. Geometry — the stop is on the PROFIT side of entry (LONG: at/above
         entry; SHORT: at/below entry). This is the load-bearing test for
         adopted positions that have no ``trailing_state``.
      4. Fallback — the exit price itself is a gain versus entry.
    """
    if trailing_active:
        return True
    if realized_pnl is not None and realized_pnl > 0:
        return True
    if entry_price and stop_loss and stop_loss > 0:
        if is_long and stop_loss >= entry_price:
            return True
        if (not is_long) and stop_loss <= entry_price:
            return True
    if exit_price is not None and entry_price:
        move = (exit_price - entry_price) if is_long else (entry_price - exit_price)
        if move > 0:
            return True
    return False


def stop_exit_label(
    is_long: bool,
    entry_price: float,
    stop_loss: float,
    exit_price: Optional[float] = None,
    trailing_active: bool = False,
    realized_pnl: Optional[float] = None,
) -> str:
    """Return "TRAILING SL HIT" for a profit-locking stop, else "SL HIT".

    Callers append their own provenance suffix (e.g. " (inferred)",
    " (exchange)") to preserve the existing label format.
    """
    if is_profit_locking_stop(
        is_long, entry_price, stop_loss, exit_price, trailing_active, realized_pnl
    ):
        return "TRAILING SL HIT"
    return "SL HIT"
