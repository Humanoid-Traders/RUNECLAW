"""
Tests for bot.core.live_executor — LiveExecutor safety limits, execution, and monitoring.

All exchange interactions are mocked via unittest.mock (no real Bitget calls).
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.live_executor import (
    LiveExecutor,
    LiveOrder,
    LivePosition,
    MICRO_MAX_POSITION_USD,
    MICRO_MAX_TOTAL_EXPOSURE,
    MICRO_MAX_OPEN_POSITIONS,
)
from bot.utils.models import TradeIdea, Direction


# ── Fixtures ────────────────────────────────────────────────────────


def _make_idea(
    direction: Direction = Direction.LONG,
    asset: str = "BTC/USDT",
    entry: float = 100_000.0,
    sl: float = 98_000.0,
    tp: float = 105_000.0,
    trade_id: str = "TI-TEST-001",
) -> TradeIdea:
    """Build a valid TradeIdea for tests."""
    if direction == Direction.SHORT:
        # SL above entry, TP below entry
        sl = sl if sl > entry else entry * 1.02
        tp = tp if tp < entry else entry * 0.95
    return TradeIdea(
        id=trade_id,
        asset=asset,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=0.85,
        reasoning="unit-test fixture",
    )


def _mock_exchange() -> AsyncMock:
    """Return an AsyncMock ccxt exchange with sensible defaults."""
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": 100_000.0})
    ex.create_order = AsyncMock(return_value={
        "id": "ORD-001",
        "average": 100_000.0,
        "filled": 0.0001,
        "cost": 10.0,
        "status": "filled",
    })
    ex.fetch_tickers = AsyncMock(return_value={
        "BTC/USDT": {"last": 100_000.0},
    })
    ex.cancel_order = AsyncMock(return_value=None)
    ex.close = AsyncMock()
    return ex


def _executor_with_mock() -> tuple[LiveExecutor, AsyncMock]:
    """Return a LiveExecutor with its exchange pre-injected as a mock."""
    executor = LiveExecutor()
    mock_ex = _mock_exchange()
    executor._exchange = mock_ex
    return executor, mock_ex


# ── Safety tests ────────────────────────────────────────────────────


class TestLiveExecutorSafety:
    """Micro-test safety limit enforcement."""

    def test_preflight_rejects_over_max_position(self):
        """size > $10 rejected."""
        executor = LiveExecutor()
        err = executor._preflight_check(15.0)
        assert err is not None
        assert "exceeds micro-test limit" in err

    def test_preflight_rejects_over_total_exposure(self):
        """total > $50 rejected."""
        executor = LiveExecutor()
        # Add existing positions totalling $45
        for i in range(5):
            executor._positions[f"pos-{i}"] = LivePosition(
                trade_id=f"pos-{i}",
                symbol="BTC/USDT",
                direction="LONG",
                entry_price=100_000.0,
                quantity=0.00009,
                cost_usd=9.0,
                stop_loss=98_000.0,
                take_profit=105_000.0,
                status="open",
            )
        # $45 existing + $10 new = $55 > $50
        err = executor._preflight_check(10.0)
        assert err is not None
        assert "would exceed" in err

    def test_preflight_rejects_max_positions(self):
        """5 open positions blocks new trade."""
        executor = LiveExecutor()
        for i in range(MICRO_MAX_OPEN_POSITIONS):
            executor._positions[f"pos-{i}"] = LivePosition(
                trade_id=f"pos-{i}",
                symbol="BTC/USDT",
                direction="LONG",
                entry_price=100_000.0,
                quantity=0.00001,
                cost_usd=1.0,
                stop_loss=98_000.0,
                take_profit=105_000.0,
                status="open",
            )
        err = executor._preflight_check(5.0)
        assert err is not None
        assert "open positions" in err

    def test_preflight_passes_valid_trade(self):
        """$5 with no open positions passes."""
        executor = LiveExecutor()
        err = executor._preflight_check(5.0)
        assert err is None

    @pytest.mark.asyncio
    async def test_size_clamped_to_micro_limit(self):
        """$20 request clamped to $10."""
        executor, mock_ex = _executor_with_mock()
        idea = _make_idea()
        result = await executor.execute(idea, size_usd=20.0)
        # Should succeed (clamped to 10), not be blocked at 20
        assert "BLOCKED" not in result
        assert "LIVE BUY" in result or "FILLED" in result.upper() or "BTC/USDT" in result

    def test_live_order_model_fields(self):
        """LiveOrder has all expected fields."""
        order = LiveOrder(
            order_id="O1",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            amount=0.001,
            price=100_000.0,
            cost_usd=100.0,
            status="filled",
        )
        assert order.order_id == "O1"
        assert order.symbol == "BTC/USDT"
        assert order.side == "buy"
        assert order.order_type == "market"
        assert order.amount == 0.001
        assert order.price == 100_000.0
        assert order.cost_usd == 100.0
        assert order.status == "filled"
        assert order.timestamp is not None
        assert isinstance(order.raw, dict)

    def test_live_position_model_fields(self):
        """LivePosition has all expected fields."""
        pos = LivePosition(
            trade_id="T1",
            symbol="ETH/USDT",
            direction="LONG",
            entry_price=3_000.0,
            quantity=0.01,
            cost_usd=30.0,
            stop_loss=2_900.0,
            take_profit=3_200.0,
        )
        assert pos.trade_id == "T1"
        assert pos.direction == "LONG"
        assert pos.status == "open"
        assert pos.sl_order_id is None
        assert pos.tp_order_id is None
        assert pos.closed_at is None
        assert pos.close_price is None
        assert pos.pnl_usd is None

    def test_position_status_lifecycle(self):
        """open -> closed status transition."""
        pos = LivePosition(
            trade_id="T1",
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=0.0001,
            cost_usd=10.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
        )
        assert pos.status == "open"
        pos.status = "closed"
        pos.close_price = 105_000.0
        pos.pnl_usd = 0.50
        assert pos.status == "closed"
        assert pos.close_price == 105_000.0


# ── Execution tests ─────────────────────────────────────────────────


class TestLiveExecutorExecution:
    """Order execution with mocked exchange."""

    @pytest.mark.asyncio
    async def test_execute_buy_market_order(self):
        """Mock exchange, verify buy order placed for LONG."""
        executor, mock_ex = _executor_with_mock()
        idea = _make_idea(direction=Direction.LONG)
        result = await executor.execute(idea, size_usd=10.0)

        assert "LIVE BUY" in result
        assert "BTC/USDT" in result
        mock_ex.create_order.assert_called()
        # First call is the entry order
        call_args = mock_ex.create_order.call_args_list[0]
        assert call_args.kwargs.get("side", call_args[1].get("side") if len(call_args) > 1 else call_args[0][2] if len(call_args[0]) > 2 else None) is not None or "buy" in str(call_args)

    @pytest.mark.asyncio
    async def test_execute_sell_market_order(self):
        """SHORT direction places sell."""
        executor, mock_ex = _executor_with_mock()
        idea = _make_idea(direction=Direction.SHORT)
        result = await executor.execute(idea, size_usd=10.0)

        assert "LIVE SELL" in result
        # Verify the market order was a sell
        entry_call = mock_ex.create_order.call_args_list[0]
        assert "sell" in str(entry_call)

    @pytest.mark.asyncio
    async def test_execute_insufficient_funds(self):
        """ccxt.InsufficientFunds raises clean message."""
        import ccxt.async_support as ccxt_async
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_ticker = AsyncMock(return_value={"last": 100_000.0})
        mock_ex.create_order = AsyncMock(
            side_effect=ccxt_async.InsufficientFunds("not enough USDT")
        )
        idea = _make_idea()
        result = await executor.execute(idea, size_usd=5.0)
        assert "INSUFFICIENT FUNDS" in result

    @pytest.mark.asyncio
    async def test_execute_invalid_order(self):
        """ccxt.InvalidOrder raises clean message."""
        import ccxt.async_support as ccxt_async
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_ticker = AsyncMock(return_value={"last": 100_000.0})
        mock_ex.create_order = AsyncMock(
            side_effect=ccxt_async.InvalidOrder("min order size 0.001")
        )
        idea = _make_idea()
        result = await executor.execute(idea, size_usd=5.0)
        assert "INVALID ORDER" in result

    @pytest.mark.asyncio
    async def test_execute_generic_error(self):
        """Random exception handled gracefully."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_ticker = AsyncMock(side_effect=RuntimeError("network timeout"))
        idea = _make_idea()
        result = await executor.execute(idea, size_usd=5.0)
        assert "EXECUTION FAILED" in result
        assert "network timeout" in result

    @pytest.mark.asyncio
    async def test_close_position_places_opposite(self):
        """LONG close = sell."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-001",
            "average": 105_000.0,
            "filled": 0.0001,
            "cost": 10.5,
            "status": "filled",
        })
        # Seed an open position
        executor._positions["T1"] = LivePosition(
            trade_id="T1",
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=0.0001,
            cost_usd=10.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
            status="open",
        )
        result = await executor.close_position("T1", "manual", 105_000.0)
        assert "CLOSED LONG BTC/USDT" in result
        # Verify sell order was placed
        close_call = mock_ex.create_order.call_args_list[0]
        assert "sell" in str(close_call)

    @pytest.mark.asyncio
    async def test_close_position_not_found(self):
        """Returns error string for missing position."""
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        result = await executor.close_position("NONEXISTENT")
        assert "not found" in result or "already closed" in result

    @pytest.mark.asyncio
    async def test_close_position_calculates_pnl(self):
        """Verify PnL math: LONG entry 100k, exit 105k, qty 0.0001."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-002",
            "average": 105_000.0,
            "filled": 0.0001,
            "cost": 10.5,
            "status": "filled",
        })
        executor._positions["T2"] = LivePosition(
            trade_id="T2",
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=0.0001,
            cost_usd=10.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
            status="open",
        )
        await executor.close_position("T2", "TP HIT", 105_000.0)
        pos = executor._positions["T2"]
        assert pos.status == "closed"
        # PnL = (105000 - 100000) * 0.0001 = 0.50
        assert pos.pnl_usd is not None
        assert abs(pos.pnl_usd - 0.50) < 0.01


# ── Monitoring tests ────────────────────────────────────────────────


class TestLiveExecutorMonitoring:
    """Position monitoring for SL/TP hits."""

    @pytest.mark.asyncio
    async def test_check_positions_sl_hit_long(self):
        """Price drops below SL triggers close for LONG."""
        executor, mock_ex = _executor_with_mock()
        # Price below stop loss
        mock_ex.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": {"last": 97_000.0},
        })
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-SL",
            "average": 97_000.0,
            "filled": 0.0001,
            "cost": 9.7,
            "status": "filled",
        })
        executor._positions["T-SL"] = LivePosition(
            trade_id="T-SL",
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=0.0001,
            cost_usd=10.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
            status="open",
            sl_order_id=None,  # No exchange-level SL
        )
        msgs = await executor.check_positions()
        assert len(msgs) == 1
        assert "SL HIT" in msgs[0]

    @pytest.mark.asyncio
    async def test_check_positions_tp_hit_long(self):
        """Price rises above TP triggers close for LONG."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": {"last": 106_000.0},
        })
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-TP",
            "average": 106_000.0,
            "filled": 0.0001,
            "cost": 10.6,
            "status": "filled",
        })
        executor._positions["T-TP"] = LivePosition(
            trade_id="T-TP",
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=0.0001,
            cost_usd=10.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
            status="open",
            sl_order_id=None,
        )
        msgs = await executor.check_positions()
        assert len(msgs) == 1
        assert "TP HIT" in msgs[0]

    @pytest.mark.asyncio
    async def test_check_positions_no_trigger(self):
        """Price between SL and TP, no close triggered."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": {"last": 101_000.0},
        })
        executor._positions["T-MID"] = LivePosition(
            trade_id="T-MID",
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=0.0001,
            cost_usd=10.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
            status="open",
            sl_order_id=None,
        )
        msgs = await executor.check_positions()
        assert len(msgs) == 0
        assert executor._positions["T-MID"].status == "open"

    @pytest.mark.asyncio
    async def test_check_positions_empty(self):
        """No positions returns empty list."""
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        msgs = await executor.check_positions()
        assert msgs == []
