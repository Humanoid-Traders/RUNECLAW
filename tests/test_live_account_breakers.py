"""
Live-mode account-level loss breakers (audit CRITICAL, 2026-07-14).

In pure-live mode the paper portfolio is never updated, so the daily-loss,
drawdown, consecutive-loss, governor and throttle protections — all of
which read paper state — could never trip on real losses. `_on_live_
position_closed` now feeds every live realized close into
`record_live_trade_result`, which drives the account-wide breakers AND a
live daily-PnL accumulator + equity high-water mark that the daily-loss
and drawdown gates use on live evaluations.
"""

import inspect
import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-livebrk-"), "risk.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=state)


def _idea():
    return TradeIdea(
        id="TI-lb", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        confidence=0.9, risk_reward_ratio=2.0, reasoning="t")


class TestLiveDailyAccumulator:
    def test_live_close_feeds_streak_breaker(self):
        eng = _engine()
        # record_live_trade_result must drive the account-wide streak counter
        # (the paper close callback never fires in pure-live mode).
        for _ in range(3):
            eng.record_live_trade_result(-5.0)
        assert eng._consecutive_losses == 3

    def test_live_daily_pnl_accumulates_and_resets_on_day_roll(self):
        eng = _engine()
        eng.record_live_trade_result(-10.0)
        eng.record_live_trade_result(-5.0)
        assert eng._live_daily_pnl == -15.0
        # force a UTC-day roll and confirm the accumulator resets
        eng._live_daily_day = "1999-01-01"
        eng.record_live_trade_result(-3.0)
        assert eng._live_daily_pnl == -3.0

    def test_daily_loss_gate_trips_on_live_losses(self):
        eng = _engine()
        # 6% of a $1000 live account lost today, cap is 5% → must reject
        eng.record_live_trade_result(-60.0)
        chk = eng.evaluate(_idea(), live_equity=1000.0)
        assert any("DAILY_LOSS" in f for f in chk.checks_failed), chk.checks_failed

    def test_daily_loss_gate_passes_when_live_losses_small(self):
        eng = _engine()
        eng.record_live_trade_result(-10.0)  # 1% of $1000, under 5% cap
        chk = eng.evaluate(_idea(), live_equity=1000.0)
        assert not any("DAILY_LOSS:" in f for f in chk.checks_failed)

    def test_paper_mode_unchanged_when_no_live_equity(self):
        # Without live_equity the gate still reads the paper snapshot — a live
        # accumulator must never leak into paper/backtest evaluations.
        eng = _engine()
        eng.record_live_trade_result(-500.0)   # would be a huge live loss
        chk = eng.evaluate(_idea())            # paper call, no live_equity
        # paper daily_pnl is 0 → daily-loss gate passes despite the live accum
        assert not any("DAILY_LOSS:" in f for f in chk.checks_failed)


class TestLiveDrawdown:
    def test_drawdown_gate_trips_from_live_high_water_mark(self):
        eng = _engine()
        # first eval sets the peak at 1000
        eng.evaluate(_idea(), live_equity=1000.0)
        # equity fell to 800 → 20% drawdown, well past any sane cap
        chk = eng.evaluate(_idea(), live_equity=800.0)
        assert any("DRAWDOWN" in f for f in chk.checks_failed), chk.checks_failed

    def test_drawdown_recovers_as_equity_climbs(self):
        eng = _engine()
        eng.evaluate(_idea(), live_equity=1000.0)
        eng.evaluate(_idea(), live_equity=800.0)     # deep dip
        chk = eng.evaluate(_idea(), live_equity=1000.0)  # back to the peak
        assert not any("DRAWDOWN:" in f for f in chk.checks_failed)


class TestNeverRaises:
    def test_record_live_trade_result_swallows_garbage(self):
        eng = _engine()
        eng.record_live_trade_result(float("nan"))  # must not raise
        eng.record_live_trade_result(-1.0)


class TestWiring:
    def test_engine_close_callback_feeds_risk(self):
        from bot.core import engine as eng_mod
        src = inspect.getsource(eng_mod._on_live_position_closed) \
            if hasattr(eng_mod, "_on_live_position_closed") else \
            inspect.getsource(eng_mod.RuneClawEngine._on_live_position_closed)
        assert "record_live_trade_result" in src

    def test_preflight_classifies_error_dict_not_exception(self):
        # the preflight must read fetch_balance's {"error": ...} dict, since
        # get_live_equity swallows the venue error and never raises.
        import bot.main as m
        src = inspect.getsource(m._credential_preflight)
        assert "fetch_balance()" in src
        assert 'bal.get("error")' in src
        assert "40006" in src and "40099" in src
