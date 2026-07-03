"""The LIMIT FILLED notification must show SL/TP PRICES, not order ids.

Reported twice: the fill card printed the 19-digit exchange order id where the
operator expects the stop/target price ("SL: 1456972736727867394 | TP:
1456972736727867394"). This locks the formatter to prices.
"""

from bot.core.live_executor import LiveExecutor

_BIG_ORDER_ID = "1456972736727867394"


def test_shows_prices_not_order_ids():
    s = LiveExecutor._fmt_fill_protection(
        0.32808, 0.33937, sl_id=_BIG_ORDER_ID, tp_id=_BIG_ORDER_ID, trailing=True)
    assert "SL: 0.32808" in s
    assert "TP: 0.33937" in s
    assert "Trailing: armed" in s
    # The order id must never leak into the operator-facing line.
    assert _BIG_ORDER_ID not in s


def test_missing_stop_order_omits_sl():
    s = LiveExecutor._fmt_fill_protection(
        0.32808, 0.33937, sl_id=None, tp_id=_BIG_ORDER_ID, trailing=False)
    assert "SL:" not in s
    assert "TP: 0.33937" in s
    assert "Trailing" not in s


def test_no_orders_is_empty():
    assert LiveExecutor._fmt_fill_protection(0.3, 0.4, None, None, False) == ""


def test_large_and_small_prices_format_readably():
    big = LiveExecutor._fmt_fill_protection(61750.0, 63000.0, "1", "2", False)
    assert "SL: 61750" in big and "TP: 63000" in big
    small = LiveExecutor._fmt_fill_protection(0.0087, 0.0091, "1", "2", False)
    assert "SL: 0.0087" in small and "TP: 0.0091" in small
