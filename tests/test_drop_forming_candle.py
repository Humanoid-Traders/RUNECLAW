"""
Repaint fix — drop the in-progress (unclosed) candle before TA.

Live OHLCV includes the current forming bar as its last element; computing
indicators/patterns on it makes every voter flicker pre-close. _drop_forming_candle
removes the still-forming last candle so all TA uses CLOSED bars only (aligning
live with the bar-closed backtest). Gated by DROP_UNCLOSED_CANDLE_ENABLED
(default OFF → byte-identical). Entry pricing is unaffected (analyzer prices off
the live ticker, not the last candle).
"""

import time

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine

_HOUR = 3_600_000


def _eng():
    return RuneClawEngine.__new__(RuneClawEngine)


class _cfg:
    """Flip the real CONFIG flag (the logic now lives in bot.utils.candles,
    which reads bot.config.CONFIG directly — patching the engine module's
    CONFIG reference no longer reaches it)."""

    def __init__(self, enabled):
        self._enabled = enabled

    def start(self):
        self._old = CONFIG.analyzer.drop_unclosed_candle_enabled
        object.__setattr__(CONFIG.analyzer, "drop_unclosed_candle_enabled", self._enabled)

    def stop(self):
        object.__setattr__(CONFIG.analyzer, "drop_unclosed_candle_enabled", self._old)


def _candles(n, tf_ms, last_open):
    """n candles spaced tf_ms apart, the last opening at last_open (ms)."""
    return [[last_open - (n - 1 - i) * tf_ms, 1.0, 1.0, 1.0, 1.0, 1.0]
            for i in range(n)]


class TestTimeframeToMs:
    def test_known_units(self):
        f = RuneClawEngine._timeframe_to_ms
        assert f("5m") == 300_000
        assert f("1h") == _HOUR
        assert f("4h") == 14_400_000
        assert f("1d") == 86_400_000
        assert f("1w") == 604_800_000

    def test_unparseable_is_zero(self):
        f = RuneClawEngine._timeframe_to_ms
        assert f("garbage") == 0
        assert f("") == 0
        assert f("1x") == 0


class TestDropForming:
    def test_flag_off_is_noop_even_when_forming(self):
        p = _cfg(False)
        p.start()
        try:
            now = time.time() * 1000.0
            c = _candles(50, _HOUR, last_open=now - _HOUR // 2)  # last is forming
            assert _eng()._drop_forming_candle(c, "1h") is c
        finally:
            p.stop()

    def test_drops_forming_last_candle(self):
        p = _cfg(True)
        p.start()
        try:
            now = time.time() * 1000.0
            c = _candles(50, _HOUR, last_open=now - _HOUR // 2)  # period not elapsed
            out = _eng()._drop_forming_candle(c, "1h")
            assert len(out) == 49
            assert out[-1] == c[-2]  # the prior (closed) candle is now last
        finally:
            p.stop()

    def test_keeps_already_closed_last_candle(self):
        p = _cfg(True)
        p.start()
        try:
            now = time.time() * 1000.0
            c = _candles(50, _HOUR, last_open=now - 3 * _HOUR)  # fully elapsed
            out = _eng()._drop_forming_candle(c, "1h")
            assert len(out) == 50
        finally:
            p.stop()

    def test_too_few_candles_unchanged(self):
        p = _cfg(True)
        p.start()
        try:
            now = time.time() * 1000.0
            c = _candles(2, _HOUR, last_open=now - _HOUR // 2)
            assert _eng()._drop_forming_candle(c, "1h") is c
        finally:
            p.stop()

    def test_none_is_safe(self):
        p = _cfg(True)
        p.start()
        try:
            assert _eng()._drop_forming_candle(None, "1h") is None
        finally:
            p.stop()

    def test_bad_timeframe_unchanged(self):
        p = _cfg(True)
        p.start()
        try:
            now = time.time() * 1000.0
            c = _candles(50, _HOUR, last_open=now - _HOUR // 2)
            assert _eng()._drop_forming_candle(c, "zzz") is c
        finally:
            p.stop()
