"""Backtest time-stop (gated) — the measurable mirror of the live time-stop.

Default OFF so baseline backtests stay byte-identical. When on, a position past
its per-strategy time-close horizon that is NOT in profit is closed; winners
ride. This makes time-stop behavior A/B-able on the honest benchmark (it never
was before).
"""
from types import SimpleNamespace

from bot.backtest.engine import BacktestEngine
from bot.utils.models import Direction


def _engine_stub(timeframe: str = "1h", time_stop: bool = True):
    """A minimal object exposing just what _time_stop_hit touches."""
    stub = SimpleNamespace(
        config=SimpleNamespace(timeframe=timeframe),
        _time_stop_enabled=time_stop,
    )
    return stub


def _pos(direction=Direction.LONG, entry=100.0):
    return SimpleNamespace(direction=direction, entry_price=entry)


def _bar(close):
    return SimpleNamespace(open=close, high=close, low=close, close=close)


def _meta(strategy="intraday", bars_held=0, adj_entry=100.0):
    return {
        "idea": SimpleNamespace(strategy_type=strategy),
        "bars_held": bars_held,
        "adjusted_entry": adj_entry,
    }


def test_not_hit_before_horizon():
    # intraday close = 4h => 4 bars on a 1h run. 3 bars held, losing → no cut yet.
    stub = _engine_stub()
    hit = BacktestEngine._time_stop_hit(
        stub, _meta("intraday", bars_held=3), _pos(), _bar(99.0)
    )
    assert hit is False


def test_hit_after_horizon_when_losing():
    stub = _engine_stub()
    hit = BacktestEngine._time_stop_hit(
        stub, _meta("intraday", bars_held=4), _pos(), _bar(99.0)
    )
    assert hit is True


def test_winner_rides_even_past_horizon():
    # Past horizon but in profit (close 101 > entry 100) → NOT cut.
    stub = _engine_stub()
    hit = BacktestEngine._time_stop_hit(
        stub, _meta("intraday", bars_held=10), _pos(), _bar(101.0)
    )
    assert hit is False


def test_short_profit_gate_is_direction_aware():
    stub = _engine_stub()
    meta = _meta("intraday", bars_held=6, adj_entry=100.0)
    short = _pos(direction=Direction.SHORT)
    # SHORT in profit when price below entry → rides.
    assert BacktestEngine._time_stop_hit(stub, meta, short, _bar(98.0)) is False
    # SHORT losing when price above entry → cut.
    assert BacktestEngine._time_stop_hit(stub, meta, short, _bar(102.0)) is True


def test_swing_horizon_is_longer_than_intraday():
    # swing close = 48h => 48 bars. 10 bars held, losing → not yet.
    stub = _engine_stub()
    assert BacktestEngine._time_stop_hit(
        stub, _meta("swing", bars_held=10), _pos(), _bar(99.0)
    ) is False
    assert BacktestEngine._time_stop_hit(
        stub, _meta("swing", bars_held=48), _pos(), _bar(99.0)
    ) is True


def test_default_flag_is_off():
    import inspect
    src = inspect.getsource(BacktestEngine.__init__)
    assert 'BACKTEST_TIME_STOP", False' in src
