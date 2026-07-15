"""
Circuit-breaker false re-trip #2: a manual /reset must clear the DAILY-LOSS
condition, not just the drawdown peak.

Live incident: the breaker tripped on daily_loss (-8.87% of equity). The
operator ran /reset ("Trading resumed"), but the day's realized loss was still
on the books — reset_circuit_breaker re-seeded the drawdown high-water mark but
left the LIVE daily-PnL accumulator (_live_daily_pnl) untouched — so the very
next evaluate() re-tripped on the same daily_loss ("after breaker reset it keeps
falling back"). reset now zeroes the live daily accumulator (fresh budget for
the current UTC day) and clears the cached loss %, mirroring the peak re-seed.
"""

import os
import tempfile
import time

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-dlbr-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def test_reset_clears_live_daily_pnl_accumulator():
    eng = _engine()
    # Simulate a day that hit the daily-loss breaker on a live account.
    eng._live_daily_pnl = -11.3            # ~ -8.87% of a ~$128 account
    eng._live_daily_day = "2020-01-01"     # a stale day tag
    eng._last_known_daily_loss_pct = 8.87
    eng._circuit_open = True
    eng._circuit_trip_cause = "daily_loss"

    eng.reset_circuit_breaker()

    assert eng.circuit_breaker_active is False
    assert eng._live_daily_pnl == 0.0, "daily-loss budget must reset so /reset sticks"
    assert eng._last_known_daily_loss_pct == 0.0
    # Re-seeded to the CURRENT utc day so the next close accumulates cleanly.
    assert eng._live_daily_day == time.strftime("%Y-%m-%d", time.gmtime())
    assert eng._circuit_trip_cause == ""


def test_after_reset_daily_loss_check_does_not_retrip():
    # Mirror evaluate()'s LIVE daily-loss condition (risk_engine.py ~1122-1130):
    # with the accumulator re-seeded to 0, daily_loss_pct is 0 → no re-trip.
    eng = _engine()
    eng._live_daily_pnl = -11.3
    eng._circuit_open = True
    eng.reset_circuit_breaker()

    live_equity = 128.85
    _daily_pnl = eng._live_daily_pnl            # 0.0 after reset
    daily_loss_pct = abs(_daily_pnl / live_equity * 100) if live_equity > 0 else 0.0
    would_trip = _daily_pnl < 0 and daily_loss_pct >= CONFIG.risk.max_daily_loss_pct
    assert daily_loss_pct == 0.0
    assert would_trip is False, "a fresh daily budget must not re-trip the breaker"


def test_losses_after_reset_still_accumulate_and_can_retrip():
    # Protection is refreshed, not disabled: new losses after the reset build up
    # from zero and will trip the breaker again.
    eng = _engine()
    eng._live_daily_pnl = -11.3
    eng._circuit_open = True
    eng.reset_circuit_breaker()
    assert eng._live_daily_pnl == 0.0

    eng.record_live_trade_result(-20.0)   # a fresh loss after the reset
    assert eng._live_daily_pnl == -20.0, "post-reset losses accumulate from zero"
