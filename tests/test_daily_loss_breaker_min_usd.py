"""
Absolute-dollar floor on the daily-loss breaker (micro-account noise guard).

On a tiny live account the 5% daily-loss cap is only a few dollars (5% of $128 =
$6.40), so a couple of normal stop-outs + fees halt the whole day. The breaker
now trips only when the day's loss exceeds BOTH max_daily_loss_pct AND
daily_loss_breaker_min_usd. Default floor 0 → pure-% behaviour (unchanged); a
funded account's % cap dominates so the floor is a no-op there.
"""

import os
import tempfile
from contextlib import contextmanager

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


@contextmanager
def _min_usd(val):
    # RiskConfig is a frozen dataclass — bypass to set the floor for the test.
    old = CONFIG.risk.daily_loss_breaker_min_usd
    object.__setattr__(CONFIG.risk, "daily_loss_breaker_min_usd", val)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.risk, "daily_loss_breaker_min_usd", old)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-dlmin-"), "risk.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    return TradeIdea(id="TI-min", asset="BTC/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                     confidence=0.9, risk_reward_ratio=2.0, reasoning="t")


def test_default_floor_zero_preserves_pure_pct_behaviour():
    # 8.87% of a $128 account with the default floor (0) still trips.
    eng = _engine()
    eng.record_live_trade_result(-11.35)  # ~8.87% of 128
    assert CONFIG.risk.daily_loss_breaker_min_usd == 0.0
    eng.evaluate(_idea(), live_equity=128.0)
    assert eng.circuit_breaker_active is True


def test_floor_prevents_trip_on_micro_account_noise():
    # Same 8.87% loss, but a $25 floor → the $11.35 loss is below the floor,
    # so the day is NOT halted.
    eng = _engine()
    eng.record_live_trade_result(-11.35)
    with _min_usd(25.0):
        eng.evaluate(_idea(), live_equity=128.0)
    assert eng.circuit_breaker_active is False


def test_floor_still_trips_once_the_dollar_loss_is_real():
    # A loss that clears BOTH the % cap and the $25 floor still halts.
    eng = _engine()
    eng.record_live_trade_result(-30.0)  # 23% of $128 and > $25 floor
    with _min_usd(25.0):
        eng.evaluate(_idea(), live_equity=128.0)
    assert eng.circuit_breaker_active is True


def test_floor_is_noop_for_a_funded_account():
    # $10k account, 6% loss ($600) with a $25 floor → % cap dominates, trips
    # exactly as before (floor never changes a real account's protection).
    eng = _engine()
    eng.record_live_trade_result(-600.0)
    with _min_usd(25.0):
        eng.evaluate(_idea(), live_equity=10_000.0)
    assert eng.circuit_breaker_active is True
