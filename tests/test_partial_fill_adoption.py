"""QC-1 execution-safety round (2026-07 quant audit, all three verified live):

1. ccxt maps Bitget's partial-fill statuses to plain "open", so a partially
   filled resting limit never hits the sweep's "partially_filled" branch.
   When drift/expiry (or the exchange) cancelled it, the record was booked
   "closed, pnl 0" while the FILLED portion stayed live on the exchange —
   no tracking, no stop-loss. Now the filled portion is ADOPTED as an open,
   protected position.
2. The drift→market fallback placed a market order for the FULL original
   quantity after the cancel — on top of any partial fill, up to 2x the
   risk-approved exposure. Now it markets only the remainder and blends
   the entry so SL/TP cover the real total.
3. The POST_ONLY rejection retry reprices the limit up to 1 ATR away but
   kept SL/TP computed for the ORIGINAL entry (source-pinned here; the
   shift uses recalc_sl_tp_for_shifted_entry like the wrong-side path).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


def _pos(qty=1.0, entry=100.0, hours_old=0.1):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=entry, quantity=qty, cost_usd=entry * qty / 5,
        stop_loss=95.0, take_profit=110.0, leverage=5,
        status="pending_fill", limit_order_id="OID1",
        atr_at_entry=0.0,
        opened_at=datetime.now(UTC) - timedelta(hours=hours_old))


def _executor(pos):
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._positions = {"T1": pos}
    ex._venue = SimpleNamespace(order_symbol=lambda s: s,
                                futures_params=lambda: {})
    ex._save_positions = lambda: None
    ex._append_closed_trade = lambda p: None
    ex._is_duplicate_fill = lambda p, price: False
    ex._fmt_fill_protection = lambda *a, **k: ""
    ex._place_sl_tp = AsyncMock(return_value=("SL1", "TP1"))
    ex._reattempt_post_fill_sl = AsyncMock(return_value=("SL1", "TP1", None))
    return ex


class TestCancelAdoptsPartialFill:
    @pytest.mark.asyncio
    async def test_expiry_cancel_adopts_partial_as_protected_open(self):
        # Resting limit, 40% filled (ccxt reports status "open"), then the
        # sweep expires it: the cancel removes only the remainder — the
        # filled 0.4 must become an OPEN protected position, not an orphan.
        pos = _pos(hours_old=6)   # past expire (4h), before stale hard-stop (8h)
        ex = _executor(pos)
        exchange = MagicMock()
        exchange.fetch_order = AsyncMock(side_effect=[
            {"status": "open", "filled": 0.4, "average": 101.0},
            {"status": "canceled", "filled": 0.4, "average": 101.0},
        ])
        exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        exchange.cancel_order = AsyncMock()
        msg = await ex._check_pending_limit(exchange, "T1", pos)
        assert msg and "PARTIAL FILL ADOPTED" in msg
        assert pos.status == "open"
        assert pos.quantity == 0.4
        assert pos.entry_price == 101.0
        assert pos.limit_order_id is None
        ex._place_sl_tp.assert_awaited_once()
        assert ex._place_sl_tp.await_args.args[3] == 0.4  # protect FILLED qty

    @pytest.mark.asyncio
    async def test_zero_fill_expiry_still_books_clean_close(self):
        pos = _pos(hours_old=6)
        ex = _executor(pos)
        exchange = MagicMock()
        exchange.fetch_order = AsyncMock(side_effect=[
            {"status": "open", "filled": 0.0},
            {"status": "canceled", "filled": 0.0},
        ])
        exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        exchange.cancel_order = AsyncMock()
        msg = await ex._check_pending_limit(exchange, "T1", pos)
        assert msg and "EXPIRED" in msg
        assert pos.status == "closed" and pos.close_reason == "expired"
        ex._place_sl_tp.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exchange_side_cancel_with_fill_adopts(self):
        # The order arrives already cancelled (exchange/operator/prior sweep)
        # but with a partial fill on it — same adoption, no close booking.
        pos = _pos()
        ex = _executor(pos)
        exchange = MagicMock()
        exchange.fetch_order = AsyncMock(return_value={
            "status": "canceled", "filled": 0.3, "average": 99.0})
        msg = await ex._check_pending_limit(exchange, "T1", pos)
        assert msg and "PARTIAL FILL ADOPTED" in msg
        assert pos.status == "open" and pos.quantity == 0.3
        assert pos.entry_price == 99.0

    @pytest.mark.asyncio
    async def test_exchange_side_cancel_without_fill_closes(self):
        pos = _pos()
        ex = _executor(pos)
        exchange = MagicMock()
        exchange.fetch_order = AsyncMock(return_value={
            "status": "canceled", "filled": 0})
        msg = await ex._check_pending_limit(exchange, "T1", pos)
        assert msg and "CANCELED" in msg.upper()
        assert pos.status == "closed"


class TestMarketFallbackRemainderOnly:
    @pytest.mark.asyncio
    async def test_fallback_markets_only_unfilled_remainder_and_blends_entry(self):
        pos = _pos(qty=1.0, entry=100.0)
        ex = _executor(pos)
        exchange = MagicMock()
        exchange.cancel_order = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={
            "status": "canceled", "filled": 0.4, "average": 99.0})
        exchange.create_order = AsyncMock(return_value={
            "average": 101.0, "filled": 0.6})
        msg = await ex._execute_drift_market_fallback(exchange, "T1", pos, 101.0)
        assert msg is not None
        # Market order sized to the REMAINDER, never the full original qty.
        assert exchange.create_order.await_args.args[3] == pytest.approx(0.6)
        # Tracked position covers the REAL total exposure at the blended entry.
        assert pos.quantity == pytest.approx(1.0)
        assert pos.entry_price == pytest.approx(99.0 * 0.4 + 101.0 * 0.6)
        assert ex._place_sl_tp.await_args.args[3] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_fallback_fully_filled_during_cancel_places_no_market_order(self):
        pos = _pos(qty=1.0)
        ex = _executor(pos)
        exchange = MagicMock()
        exchange.cancel_order = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={
            "status": "canceled", "filled": 1.0, "average": 99.5})
        exchange.create_order = AsyncMock()
        msg = await ex._execute_drift_market_fallback(exchange, "T1", pos, 101.0)
        assert msg is None
        exchange.create_order.assert_not_awaited()


class TestPostOnlyRepriceShiftsSlTp:
    def test_retry_path_recalcs_sl_tp_for_the_new_entry(self):
        src = inspect.getsource(LiveExecutor.execute)
        retry = src.find("POST_ONLY reprice SL/TP shift")
        assert retry > 0, "reprice retry must shift SL/TP with the moved entry"
        # The shift must use the shared shifted-entry helper, not ad-hoc math.
        assert "recalc_sl_tp_for_shifted_entry" in src[retry - 2000:retry + 2000]
