"""
Candle-pattern entry veto (opt-in): skip a pullback-limit entry when the last
closed bar prints a strong reversal OPPOSING the trade. The core is the pure,
harness-sweepable `candle_entry_veto(patterns, direction)` — tested here against
crafted pattern dicts (the shape `_detect_candlestick_patterns` returns).
"""
from bot.core.analyzer import candle_entry_veto
from bot.utils.models import Direction


def _has(reason):
    return reason is not None and "CANDLE_VETO" in reason


# ── LONG is vetoed by bearish reversals ──────────────────────────────
def test_long_vetoed_by_bearish_engulfing():
    assert _has(candle_entry_veto({"bearish_engulfing": "bearish"}, Direction.LONG))


def test_long_vetoed_by_shooting_star():
    assert _has(candle_entry_veto({"shooting_star": "bearish"}, Direction.LONG))


def test_long_vetoed_by_gravestone_doji():
    assert _has(candle_entry_veto({"gravestone_doji": "bearish"}, Direction.LONG))


def test_long_vetoed_by_bearish_marubozu():
    assert _has(candle_entry_veto({"marubozu": "bearish"}, Direction.LONG))


# ── SHORT is vetoed by the bullish mirror ────────────────────────────
def test_short_vetoed_by_bullish_engulfing():
    assert _has(candle_entry_veto({"bullish_engulfing": "bullish"}, Direction.SHORT))


def test_short_vetoed_by_hammer():
    assert _has(candle_entry_veto({"hammer": "bullish"}, Direction.SHORT))


def test_short_vetoed_by_bullish_marubozu():
    assert _has(candle_entry_veto({"marubozu": "bullish"}, Direction.SHORT))


# ── With-trend / neutral patterns do NOT veto ────────────────────────
def test_long_not_vetoed_by_bullish_pattern():
    assert candle_entry_veto({"bullish_engulfing": "bullish"}, Direction.LONG) is None


def test_short_not_vetoed_by_bearish_pattern():
    assert candle_entry_veto({"bearish_engulfing": "bearish"}, Direction.SHORT) is None


def test_long_not_vetoed_by_bullish_marubozu():
    assert candle_entry_veto({"marubozu": "bullish"}, Direction.LONG) is None


def test_neutral_doji_does_not_veto():
    assert candle_entry_veto({"doji": "neutral"}, Direction.LONG) is None
    assert candle_entry_veto({"spinning_top": "neutral"}, Direction.SHORT) is None


def test_empty_patterns_never_veto():
    assert candle_entry_veto({}, Direction.LONG) is None
    assert candle_entry_veto(None, Direction.SHORT) is None


def test_reason_lists_the_pattern():
    r = candle_entry_veto({"bearish_engulfing": "bearish"}, Direction.LONG)
    assert "bearish_engulfing" in r
