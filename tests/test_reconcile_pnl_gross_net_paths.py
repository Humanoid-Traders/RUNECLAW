"""The two reconcile close paths must honor whether the exchange PnL is gross
or net, instead of assuming net — otherwise fees are silently dropped and
realized PnL is overstated.

Audit bugs: _handle_already_closed_position (the 25227 already-closed path) and
reconcile_positions (SL/TP-triggered closes) both did
`net_pnl = exchange_reported_pnl; commission = gross_pnl - net_pnl`, treating a
GROSS exchange figure as net. Both now route through
_reconcile_exchange_close_pnl with the pnl_is_net flag, mirroring
_close_position_inner. Also: when position-history netProfit==0 but the full
round-trip fees are present, _fetch_bitget_close_data derives net locally and
flags pnl_is_net=True so the caller doesn't add a SECOND entry-fee estimate.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition


def _pos() -> LivePosition:
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100_000.0, quantity=0.001, cost_usd=100.0,
        stop_loss=98_000.0, take_profit=105_000.0, status="open",
    )


class TestReconcileHelperContract:
    def test_gross_flag_deducts_fees(self):
        gross, net, comm = LiveExecutor._reconcile_exchange_close_pnl(
            exchange_pnl=5.0, exchange_close_fees=0.1, pnl_is_net=False,
            entry_notional=100.0, entry_fee_pct=0.02)
        assert gross == pytest.approx(5.0)
        assert net < gross                 # fees actually deducted
        assert comm > 0.1                  # close fee + estimated entry fee

    def test_net_flag_is_used_directly(self):
        gross, net, comm = LiveExecutor._reconcile_exchange_close_pnl(
            exchange_pnl=4.8, exchange_close_fees=0.2, pnl_is_net=True,
            entry_notional=100.0, entry_fee_pct=0.02)
        assert net == pytest.approx(4.8)
        assert gross == pytest.approx(5.0)
        assert comm == pytest.approx(0.2)


class TestAlreadyClosedPathHonorsGrossNet:
    def _executor(self):
        ex = LiveExecutor()
        ex._get_exchange = AsyncMock(return_value=AsyncMock())
        ex._save_positions = MagicMock()
        ex._save_closed_trades = MagicMock()
        ex._fire_position_closed = MagicMock()
        return ex

    @pytest.mark.asyncio
    async def test_gross_exchange_pnl_records_net_below_gross(self):
        ex = self._executor()
        # Gross profit reported, close-side fee only -> net must be below gross.
        ex._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 105_000.0, "pnl": 5.0, "fees": 0.1,
            "reason": "TP HIT (exchange)", "source": "exchange_fill_sltp",
            "pnl_is_net": False,
        })
        pos = _pos()
        await ex._handle_already_closed_position(pos)
        assert pos.gross_pnl == pytest.approx(5.0)
        assert pos.pnl_usd < pos.gross_pnl          # fees no longer dropped
        assert pos.commission > 0.1                  # close + estimated entry fee

    @pytest.mark.asyncio
    async def test_net_exchange_pnl_used_directly(self):
        ex = self._executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 105_000.0, "pnl": 4.8, "fees": 0.2,
            "reason": "TP HIT (exchange)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })
        pos = _pos()
        await ex._handle_already_closed_position(pos)
        assert pos.pnl_usd == pytest.approx(4.8)
        assert pos.gross_pnl == pytest.approx(5.0)


class TestNetProfitZeroDerivesNetFromFees:
    @pytest.mark.asyncio
    async def test_zero_netprofit_with_fees_is_flagged_net(self):
        ex = LiveExecutor()
        ex._exchange = AsyncMock()
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [{
                "openAvgPrice": "100000", "closeAvgPrice": "105000",
                "pnl": "5.0", "netProfit": "0",
                "openFee": "0.3", "closeFee": "0.3", "closeType": "tp",
            }]}})
        result = await ex._fetch_bitget_close_data(_pos())
        # net derived locally = gross 5.0 - full fees 0.6 = 4.4, flagged net so
        # the caller does NOT add another entry-fee estimate.
        assert result["pnl_is_net"] is True
        assert result["pnl"] == pytest.approx(4.4)
        assert result["fees"] == pytest.approx(0.6)
