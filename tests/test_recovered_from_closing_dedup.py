"""
A position recovered from a stuck "closing" status must never generate a
duplicate close notification, and check_positions() must never submit a
second, redundant close order for it.

Real incident: HYPE's position (trade_id TI-764dc897) closed via the normal
local SL/TP check at 13:49 UTC (a "live_auto_close" notification went out,
PnL -$0.57, priced off a ticker fallback since fill verification couldn't
confirm the exact exchange fill). The bot process then restarted before
that close's "closing" -> "closed" transition durably persisted, so on
reload the position came back as "open" (via _load_positions()'s stuck-
"closing" recovery). check_positions() then re-evaluated it against the
SAME local SL/TP levels, found the condition still satisfied, and started
a SECOND close attempt -- but the position was already gone from Bitget (the
first close had gone through for real), so this redundant attempt's own
"close" produced no genuine new exchange event.  reconcile_positions()
(which runs right after check_positions() every tick) then discovered the
position was gone, fetched Bitget's own authoritative position-history
record, and closed it AGAIN locally -- with different, more accurate
numbers (+$0.09) -- sending a SECOND, conflicting "closed" notification for
what the user experienced as one trade.

The fix: _load_positions() flags trade_ids recovered from "closing" in
_recovered_from_closing. check_positions() skips ALL close-triggering logic
(TIME_STOP, static SL/TP) for flagged trade_ids, deferring entirely to
reconcile_positions() -- which resolves the position's true state from the
exchange directly and, if it finds the position already gone, finalizes it
using authoritative data WITHOUT sending a second notification (the user
almost certainly already saw one before the restart). The flag is cleared
either way once reconcile resolves it.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


def _pos(trade_id="TI-764dc897", symbol="HYPE/USDT:USDT", direction="SHORT",
          entry=63.701, sl=66.0, tp=60.0, qty=0.416) -> LivePosition:
    return LivePosition(
        trade_id=trade_id, symbol=symbol, direction=direction,
        entry_price=entry, quantity=qty, cost_usd=26.50,
        stop_loss=sl, take_profit=tp, leverage=10, status="open",
        opened_at=datetime.now(UTC) - timedelta(hours=2),
    )


class TestCheckPositionsDefersRecoveredTrades:
    @pytest.mark.asyncio
    async def test_recovered_trade_is_skipped_by_local_sltp_check(self):
        """Even though price has clearly breached the local SL level, a
        flagged trade_id must not get a second close order submitted."""
        executor = LiveExecutor()
        pos = _pos(sl=64.0)  # SHORT: price >= 64.0 triggers local SL close
        executor._positions[pos.trade_id] = pos
        executor._recovered_from_closing.add(pos.trade_id)
        executor.close_position = AsyncMock(return_value="CLOSED (should not be called)")

        mock_ex = AsyncMock()
        mock_ex.fetch_ticker = AsyncMock(return_value={"last": 65.0})  # past the SL
        executor._exchange = mock_ex
        executor.reconcile_positions = AsyncMock(return_value=[])
        executor.adopt_exchange_positions = AsyncMock(return_value=[])
        executor.adopt_exchange_limit_orders = AsyncMock(return_value=[])
        executor._last_exchange_sync = __import__("time").time()

        await executor.check_positions()

        executor.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_recovered_trade_still_closes_normally(self):
        """Control: a normal (non-flagged) position still closes on a real
        SL breach -- the defer is scoped to recovered trade_ids only."""
        executor = LiveExecutor()
        pos = _pos(trade_id="TI-normal001", sl=64.0)
        executor._positions[pos.trade_id] = pos
        # NOT in _recovered_from_closing.

        mock_ex = AsyncMock()
        mock_ex.fetch_ticker = AsyncMock(return_value={"last": 65.0})
        executor._exchange = mock_ex
        executor.close_position = AsyncMock(return_value="CLOSED normally")
        executor.adopt_exchange_positions = AsyncMock(return_value=[])
        executor.adopt_exchange_limit_orders = AsyncMock(return_value=[])
        executor._last_exchange_sync = __import__("time").time()

        result = await executor.check_positions()

        executor.close_position.assert_called_once()
        assert "CLOSED normally" in result


class TestReconcileSuppressesDuplicateNotification:
    def _mock_exchange_no_position(self):
        ex = AsyncMock()
        ex.fetch_positions = AsyncMock(return_value=[])  # gone from exchange
        return ex

    @pytest.mark.asyncio
    async def test_recovered_trade_close_is_not_notified(self):
        executor = LiveExecutor()
        pos = _pos()
        executor._positions[pos.trade_id] = pos
        executor._recovered_from_closing.add(pos.trade_id)
        executor._exchange = self._mock_exchange_no_position()
        executor._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 63.628, "pnl": 0.09, "fees": 0.21,
            "reason": "CLOSED (unknown)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })

        with patch.object(live_executor_mod, "CONFIG") as mock_cfg:
            mock_cfg.risk.maker_fee_pct = 0.02
            mock_cfg.risk.taker_fee_pct = 0.06
            messages = await executor.reconcile_positions()

        assert messages == []
        # Still finalized correctly -- just quietly.
        assert pos.trade_id not in executor._positions
        assert any(t.trade_id == pos.trade_id for t in executor._closed_trades)
        assert pos.trade_id not in executor._recovered_from_closing

    @pytest.mark.asyncio
    async def test_non_recovered_trade_close_is_notified_normally(self):
        """Control: a genuine reconcile-discovered close (not a restart
        recovery) still notifies the user as before."""
        executor = LiveExecutor()
        pos = _pos(trade_id="TI-genuine01")
        executor._positions[pos.trade_id] = pos
        # NOT in _recovered_from_closing.
        executor._exchange = self._mock_exchange_no_position()
        executor._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 63.628, "pnl": 0.09, "fees": 0.21,
            "reason": "CLOSED (unknown)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })

        with patch.object(live_executor_mod, "CONFIG") as mock_cfg:
            mock_cfg.risk.maker_fee_pct = 0.02
            mock_cfg.risk.taker_fee_pct = 0.06
            messages = await executor.reconcile_positions()

        assert len(messages) == 1
        assert "RECONCILED" in messages[0]

    @pytest.mark.asyncio
    async def test_recovered_flag_cleared_when_position_confirmed_still_open(self):
        """If reconcile finds the position genuinely still open (the local
        close attempt never actually reached the exchange), the flag clears
        so normal monitoring resumes -- it does not stay stuck forever."""
        executor = LiveExecutor()
        pos = _pos()
        executor._positions[pos.trade_id] = pos
        executor._recovered_from_closing.add(pos.trade_id)

        ex = AsyncMock()
        ex.fetch_positions = AsyncMock(return_value=[
            {"contracts": 0.416, "side": "short",
             "info": {"stopLoss": "0", "takeProfit": "0"}}
        ])
        executor._exchange = ex

        messages = await executor.reconcile_positions()

        assert messages == []
        assert pos.trade_id in executor._positions  # still tracked, still open
        assert pos.trade_id not in executor._recovered_from_closing
