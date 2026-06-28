"""
/openorders reconciliation.

/openorders queried Bitget account-wide (fetch_open_orders with no symbol) and
reported "No pending orders" while /livepositions showed a bot-tracked pending
limit (status == "pending_fill") — the two commands disagreed. The reconcile
helpers make /openorders fall back to a per-symbol re-fetch and, failing that,
surface the bot-tracked orders flagged as a possible desync. These tests pin the
pure decision logic (no exchange/async needed).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from bot.skills.telegram_handler import TelegramHandler as H


def _pending(direction="SHORT", sym="ZEC/USDT:USDT", price=393.09, qty=1.284):
    return SimpleNamespace(
        direction=direction, trade_id="Tl-1ba08847", symbol=sym,
        entry_price=price, quantity=qty, status="pending_fill",
        opened_at=datetime(2026, 6, 28, 14, 51, tzinfo=timezone.utc),
    )


class TestSynthOrder:
    def test_maps_short_to_sell_limit(self):
        o = H._synth_order_from_tracked(_pending("SHORT"))
        assert o["type"] == "limit"
        assert o["side"] == "sell"
        assert o["price"] == 393.09
        assert o["amount"] == 1.284
        assert o["filled"] == 0
        assert o["datetime"].startswith("2026-06-28")

    def test_maps_long_to_buy(self):
        assert H._synth_order_from_tracked(_pending("LONG"))["side"] == "buy"

    def test_tolerates_missing_fields(self):
        o = H._synth_order_from_tracked(SimpleNamespace())
        assert o["type"] == "limit" and o["price"] == 0 and o["datetime"] == ""


class TestReconcile:
    def test_exchange_orders_win(self):
        orders, desync = H._reconcile_open_orders([{"id": "x"}], [_pending()], [])
        assert orders == [{"id": "x"}] and desync is False

    def test_genuinely_empty(self):
        assert H._reconcile_open_orders([], [], []) == ([], False)

    def test_per_symbol_refetch_recovers(self):
        # Account-wide empty but a per-symbol re-fetch found the order → use it,
        # no desync warning (the exchange does have it).
        orders, desync = H._reconcile_open_orders([], [_pending()], [{"id": "y"}])
        assert orders == [{"id": "y"}] and desync is False

    def test_desync_surfaces_tracked_with_flag(self):
        # Exchange shows nothing even per-symbol, but the bot tracks a pending
        # limit → surface it, flagged as a possible desync.
        orders, desync = H._reconcile_open_orders([], [_pending()], [])
        assert desync is True
        assert len(orders) == 1
        assert orders[0]["type"] == "limit"
        assert orders[0]["symbol"] == "ZEC/USDT:USDT"

    def test_no_desync_when_nothing_tracked(self):
        # The old behaviour: nothing anywhere → empty, no false alarm.
        assert H._reconcile_open_orders([], [], None) == ([], False)
