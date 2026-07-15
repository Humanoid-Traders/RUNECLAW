"""
Circuit-breaker false-halt: a manual /resume must re-seed the live drawdown
high-water mark.

Live incident: during an auth blip the live-equity reading was briefly wrong-high
(stale / paper-fallback), which seeded `_live_equity_peak` far above the real
account. Current drawdown = (peak - equity)/peak then stayed pinned above the
cap, so the breaker re-tripped on the very next evaluate() and a manual /resume
never stuck — the engine kept announcing "halted" while still filling orders, and
the alert read non-existent attributes so it showed "Drawdown N/A, Daily P&L N/A,
Open Positions 0". reset_circuit_breaker now zeroes the peak so the next live
evaluation re-seeds it from CURRENT equity; the breaker + alert also expose the
real trip cause.
"""

import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-cbpr-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def test_reset_reseeds_live_equity_peak():
    eng = _engine()
    # Simulate a corrupted high-water mark + an open breaker.
    eng._live_equity_peak = 10_000.0   # bogus peak from a bad reading
    eng._circuit_open = True
    eng._circuit_trip_cause = "drawdown"

    eng.reset_circuit_breaker()

    assert eng.circuit_breaker_active is False
    assert eng._live_equity_peak == 0.0, "peak must re-seed from the next live equity"
    assert eng._circuit_trip_cause == ""


def test_peak_reseeds_to_current_after_reset():
    # After reset (peak=0), the drawdown formula the engine uses on the next
    # live evaluation seeds the peak to current equity → drawdown 0 (no re-trip).
    eng = _engine()
    eng._live_equity_peak = 10_000.0
    eng._circuit_open = True
    eng.reset_circuit_breaker()

    live_equity = 122.21  # the real account
    # Mirror the engine's peak/drawdown update (risk_engine.py drawdown gate).
    if live_equity > eng._live_equity_peak:
        eng._live_equity_peak = live_equity
    cur_dd = (100.0 * (eng._live_equity_peak - live_equity) / eng._live_equity_peak
              if eng._live_equity_peak > 0 else 0.0)
    assert cur_dd == 0.0, "a fresh peak = current equity → no phantom drawdown"


def test_trip_cause_and_daily_loss_are_exposed():
    eng = _engine()
    eng._circuit_trip_cause = "daily_loss"
    eng._last_known_daily_loss_pct = 6.5
    assert eng.circuit_trip_cause == "daily_loss"
    assert eng.last_known_daily_loss_pct == 6.5
    # Not tripped → empty cause.
    eng._circuit_trip_cause = ""
    assert eng.circuit_trip_cause == ""
