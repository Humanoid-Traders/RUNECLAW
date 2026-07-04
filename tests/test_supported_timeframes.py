"""Shared SUPPORTED_TIMEFRAMES constant + the on-demand multi-timeframe sweep.

The bot's understood timeframes live in one place (bot.utils.candles) so command
arg-validation and the /deepscan "all" sweep agree. The autonomous loop is
deliberately NOT part of this — it stays single-timeframe (1h) per the design.
"""
from bot.utils.candles import (
    SUPPORTED_TIMEFRAMES,
    is_supported_timeframe,
    resolve_timeframes,
    timeframe_to_ms,
)


def test_supported_timeframes_are_ascending_and_parseable():
    assert SUPPORTED_TIMEFRAMES == ["5m", "15m", "1h", "4h", "1d"]
    ms = [timeframe_to_ms(tf) for tf in SUPPORTED_TIMEFRAMES]
    assert all(m > 0 for m in ms)
    assert ms == sorted(ms)  # strictly ascending duration


def test_is_supported_timeframe():
    assert is_supported_timeframe("1h")
    assert is_supported_timeframe("1d")
    assert not is_supported_timeframe("3m")
    assert not is_supported_timeframe("all")
    assert not is_supported_timeframe("")


def test_resolve_all_expands_to_every_timeframe():
    assert resolve_timeframes("all") == SUPPORTED_TIMEFRAMES
    # A fresh list (not the shared object) so callers can't mutate the constant.
    assert resolve_timeframes("all") is not SUPPORTED_TIMEFRAMES


def test_resolve_single_timeframe():
    assert resolve_timeframes("1h") == ["1h"]
    assert resolve_timeframes("4h") == ["4h"]


def test_resolve_invalid_is_empty():
    assert resolve_timeframes("3m") == []
    assert resolve_timeframes("") == []
    assert resolve_timeframes("weekly") == []


def test_autonomous_loop_default_timeframe_unchanged():
    """The batched analyzer still defaults to 1h — multi-TF is on-demand only,
    the always-on loop must not fan out across timeframes."""
    import inspect

    from bot.core.engine import RuneClawEngine

    sig = inspect.signature(RuneClawEngine._analyze_signals_batched)
    assert sig.parameters["timeframe"].default == "1h"
    # And the tick calls it without overriding the timeframe.
    tick_src = inspect.getsource(RuneClawEngine._tick)
    assert "_analyze_signals_batched(signals)" in tick_src
