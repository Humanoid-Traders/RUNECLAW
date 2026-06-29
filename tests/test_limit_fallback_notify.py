"""
Live limit→market fallback notification fixes (reported from a live ANIME trade).

Two bugs in the "LIMIT → MARKET FALLBACK" path:
  1. The fallback message (a position OPEN) was misrouted to the close-notify
     path and shown as "❌ Trade Closed" instead of "📥 TRADE OPENED".
  2. The message printed the SL/TP exchange ORDER IDs (huge integers, and
     identical) instead of the SL/TP prices.
"""

import inspect

from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor


class TestFillClassification:
    def test_limit_filled_is_fill(self):
        assert RuneClawEngine._is_fill_message("LIMIT FILLED: LONG BTC/USDT\nQty: 1") is True

    def test_market_fallback_is_fill(self):
        # The exact shape the executor emits (unicode arrow included).
        msg = ("LIMIT → MARKET FALLBACK: LONG ANIME/USDT\n"
               "Original limit: $0.0028 → Market fill: $0.0029\n"
               "Qty: 78342.610000 | SL: $0.0028 | TP: $0.0030\n"
               "Reason: momentum breakout past limit price")
        assert RuneClawEngine._is_fill_message(msg) is True

    def test_actual_close_is_not_fill(self):
        assert RuneClawEngine._is_fill_message("BTC/USDT LONG closed +$5.00 (TP)") is False

    def test_empty_is_not_fill(self):
        assert RuneClawEngine._is_fill_message("") is False


class TestFallbackMessageShowsPrices:
    def test_source_uses_sltp_prices_not_order_ids(self):
        src = inspect.getsource(LiveExecutor._execute_drift_market_fallback)
        # SL/TP must come from the position PRICES…
        assert "pos.stop_loss" in src
        assert "pos.take_profit" in src
        # …not the raw order IDs in the SL/TP display lines.
        assert "SL: {sl_id}" not in src
        assert "TP: {tp_id}" not in src
