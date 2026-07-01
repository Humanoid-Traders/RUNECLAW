"""
Closed-trade PnL must never report a gross figure as "net," nor double-count
a fee into "gross."

Bitget's position-history endpoint exposes achievedProfits (gross) and
netProfit (fee-adjusted) as SEPARATE fields. The bot's fetch_my_trades
fallback paths (used when position-history lookup fails/times out) read a
per-fill "profit" value that follows the same "achieved profit" (gross)
convention, and only ever see the CLOSING fill's fee (never the entry fee).

Before this fix, close_position() treated EVERY exchange-reported pnl value
as already net-of-fees: `gross_pnl = exchange_pnl + exchange_close_fees;
net_pnl = exchange_pnl`. For the fetch_my_trades paths this was wrong twice
over: it added the close fee to an already-gross figure (inflating
"gross_pnl"), and it reported that same still-gross figure as "net_pnl"
(overstating profitability by the fee amount, and additionally never
accounting for the entry-side fee at all since these queries can't see it).

_reconcile_exchange_close_pnl() makes the distinction explicit via a
pnl_is_net flag threaded from _fetch_bitget_close_data()'s return value.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition


class TestReconcileExchangeClosePnlPure:
    """Direct tests of the extracted pure combination function."""

    def test_net_path_reconstructs_gross_by_adding_full_round_trip_fees(self):
        # Bitget netProfit=48, full round-trip fee (open+close)=2 -> gross=50.
        gross, net, commission = LiveExecutor._reconcile_exchange_close_pnl(
            exchange_pnl=48.0, exchange_close_fees=2.0, pnl_is_net=True,
            entry_notional=1000.0, entry_fee_pct=0.02,
        )
        assert gross == pytest.approx(50.0)
        assert net == pytest.approx(48.0)
        assert commission == pytest.approx(2.0)

    def test_gross_path_does_not_double_count_the_close_fee(self):
        # Per-fill "profit"=50 (gross), close-side fee only=1, entry
        # notional=1000 @ 0.02% maker -> estimated entry fee=0.2.
        gross, net, commission = LiveExecutor._reconcile_exchange_close_pnl(
            exchange_pnl=50.0, exchange_close_fees=1.0, pnl_is_net=False,
            entry_notional=1000.0, entry_fee_pct=0.02,
        )
        # gross_pnl must stay exactly the reported (already-gross) figure --
        # NOT 50 + 1 = 51, which is what the old code computed.
        assert gross == pytest.approx(50.0)
        assert commission == pytest.approx(1.0 + 1000.0 * 0.02 / 100.0)  # 1.2
        assert net == pytest.approx(50.0 - 1.2)  # 48.8, NOT 50.0

    def test_gross_path_net_pnl_is_always_less_than_gross_pnl_for_positive_fees(self):
        gross, net, commission = LiveExecutor._reconcile_exchange_close_pnl(
            exchange_pnl=10.0, exchange_close_fees=0.5, pnl_is_net=False,
            entry_notional=500.0, entry_fee_pct=0.06,
        )
        assert net < gross
        assert commission > 0.5  # includes the estimated entry fee on top

    def test_net_path_with_zero_fees_is_a_no_op(self):
        gross, net, commission = LiveExecutor._reconcile_exchange_close_pnl(
            exchange_pnl=10.0, exchange_close_fees=0.0, pnl_is_net=True,
            entry_notional=500.0, entry_fee_pct=0.06,
        )
        assert gross == net == pytest.approx(10.0)
        assert commission == 0.0


class TestFetchBitgetCloseDataPnlIsNetFlag:
    """_fetch_bitget_close_data must correctly flag whether its 'pnl' value
    is already fee-adjusted, so the caller reconciles it correctly."""

    def _executor(self) -> LiveExecutor:
        executor = LiveExecutor()
        executor._exchange = AsyncMock()
        return executor

    def _pos(self) -> LivePosition:
        return LivePosition(
            trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
            entry_price=100_000.0, quantity=0.001, cost_usd=100.0,
            stop_loss=98_000.0, take_profit=105_000.0, status="open",
        )

    @pytest.mark.asyncio
    async def test_position_history_with_nonzero_net_profit_is_flagged_net(self):
        executor = self._executor()
        executor._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [{
                "openPrice": "100000", "closeAvgPrice": "105000",
                "achievedProfits": "50", "openFee": "1", "closeFee": "1",
                "netProfit": "48", "closeType": "normal",
            }]}})
        result = await executor._fetch_bitget_close_data(self._pos())
        assert result["pnl_is_net"] is True
        assert result["pnl"] == pytest.approx(48.0)
        assert result["fees"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_position_history_with_zero_net_profit_falls_back_to_gross(self):
        executor = self._executor()
        executor._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [{
                "openPrice": "100000", "closeAvgPrice": "105000",
                "achievedProfits": "50", "openFee": "1", "closeFee": "1",
                "netProfit": "0", "closeType": "normal",
            }]}})
        result = await executor._fetch_bitget_close_data(self._pos())
        assert result["pnl_is_net"] is False
        assert result["pnl"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_fetch_my_trades_sltp_match_is_flagged_gross(self):
        executor = self._executor()
        executor._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            side_effect=RuntimeError("position history unavailable"))
        pos = self._pos()
        pos.tp_order_id = "TP-1"
        executor._exchange.fetch_my_trades = AsyncMock(return_value=[{
            "order": "TP-1", "price": 105_000.0,
            "info": {"profit": "50", "feeDetail": {"totalFee": "-1"}},
        }])
        result = await executor._fetch_bitget_close_data(pos)
        assert result["pnl_is_net"] is False
        assert result["pnl"] == pytest.approx(50.0)
        assert result["fees"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_fetch_my_trades_recent_close_side_match_is_flagged_gross(self):
        executor = self._executor()
        executor._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            side_effect=RuntimeError("position history unavailable"))
        pos = self._pos()  # LONG, no sl/tp order ids -> falls to close-side match
        executor._exchange.fetch_my_trades = AsyncMock(return_value=[{
            "order": "CLOSE-1", "side": "sell", "price": 105_000.0,
            "info": {"profit": "50", "feeDetail": {"totalFee": "-1"}},
        }])
        result = await executor._fetch_bitget_close_data(pos)
        assert result["pnl_is_net"] is False
        assert result["pnl"] == pytest.approx(50.0)
        assert result["fees"] == pytest.approx(1.0)


class TestClosePositionEndToEndGrossNetSplit:
    """Drives the full close_position() path to confirm the persisted
    gross_pnl/pnl_usd/commission reflect the corrected reconciliation."""

    def _executor_with_mock(self):
        executor = LiveExecutor()
        mock_ex = AsyncMock()
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-1", "average": 105_000.0, "filled": 0.001,
            "cost": 105.0, "status": "filled",
        })
        mock_ex.fetch_ticker = AsyncMock(return_value={"last": 105_000.0})
        executor._exchange = mock_ex
        return executor, mock_ex

    def _seed(self, executor) -> str:
        tid = "T-GROSS-NET"
        executor._positions[tid] = LivePosition(
            trade_id=tid, symbol="BTC/USDT", direction="LONG",
            entry_price=100_000.0, quantity=0.001, cost_usd=100.0,
            stop_loss=98_000.0, take_profit=105_000.0, status="open",
        )
        return tid

    @pytest.mark.asyncio
    async def test_gross_only_exchange_data_yields_net_below_gross(self):
        executor, mock_ex = self._executor_with_mock()
        tid = self._seed(executor)
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 105_000.0, "fill_qty": 0.001,
            "fees": 0.0, "remaining_qty": 0.0, "failure_stage": "",
        })
        # Simulate position-history failing entirely and the fetch_my_trades
        # fallback (inside _fetch_bitget_close_data) returning a GROSS profit
        # with close-side-only fees.
        executor._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 105_000.0, "pnl": 5.0, "fees": 0.1,
            "reason": "CLOSED (unknown)", "source": "exchange_fill_recent",
            "pnl_is_net": False,
        })

        with patch("asyncio.sleep", new=AsyncMock()):
            await executor.close_position(tid, reason="manual")

        closed = next(t for t in executor._closed_trades if t.trade_id == tid)
        assert closed.gross_pnl == pytest.approx(5.0)
        # net must be strictly less than gross (fee-adjusted), not equal to it.
        assert closed.pnl_usd < closed.gross_pnl
        assert closed.commission > 0.1  # close fee + estimated entry fee

    @pytest.mark.asyncio
    async def test_net_profit_exchange_data_is_used_directly(self):
        executor, mock_ex = self._executor_with_mock()
        tid = self._seed(executor)
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 105_000.0, "fill_qty": 0.001,
            "fees": 0.0, "remaining_qty": 0.0, "failure_stage": "",
        })
        executor._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 105_000.0, "pnl": 4.8, "fees": 0.2,
            "reason": "TP HIT (exchange)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })

        with patch("asyncio.sleep", new=AsyncMock()):
            await executor.close_position(tid, reason="manual")

        closed = next(t for t in executor._closed_trades if t.trade_id == tid)
        assert closed.pnl_usd == pytest.approx(4.8)
        assert closed.gross_pnl == pytest.approx(5.0)
        assert closed.commission == pytest.approx(0.2)
