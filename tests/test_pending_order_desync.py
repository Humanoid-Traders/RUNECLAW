"""
Pending-order desync round (2026-07-13) — from a live /orders paste:

A SHORT limit (FIL @ $0.7668, market $0.7697) showed as "⚠️ Possible
desync — verify on Bitget". A sell limit below market is marketable — it
cannot rest on the book — so "exchange shows no resting orders" simply
meant "it filled". Two hardenings:

  1. /orders now RESOLVES the desync (fetch_order by id sees the
     filled/cancelled orders that fetch_open_orders excludes by design)
     and tells the operator what actually happened, instead of sending
     them to the exchange UI to find out.
  2. Executor bug found while tracing: a pending_fill record whose
     limit_order_id was lost returned early BEFORE the stale hard-timeout
     check — stuck in _positions forever, invisibly. The hard timeout now
     runs first.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

UTC = timezone.utc


# ── 1. desync resolution in /orders ──────────────────────────────────
def _handler():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    return TelegramHandler(RuneClawEngine())


def _pending(oid="1234567890", symbol="FIL/USDT:USDT", direction="SHORT"):
    return SimpleNamespace(
        limit_order_id=oid, symbol=symbol, direction=direction,
        entry_price=0.7668, quantity=204.2, trade_id="TI-8a86e1a5",
        opened_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_resolves_filled_order():
    h = _handler()
    exchange = MagicMock()
    exchange.fetch_order = AsyncMock(return_value={
        "status": "closed", "average": 0.7669, "price": 0.7668})
    notes, synths = await h._resolve_desync_orders(exchange, [_pending()])
    assert synths == []                      # a filled order is not open
    assert len(notes) == 1
    assert "FILLED" in notes[0] and "$0.7669" in notes[0]
    assert "next check tick" in notes[0]
    exchange.fetch_order.assert_awaited_once_with("1234567890", "FIL/USDT:USDT")


@pytest.mark.asyncio
async def test_resolves_cancelled_order():
    h = _handler()
    exchange = MagicMock()
    exchange.fetch_order = AsyncMock(return_value={"status": "canceled"})
    notes, synths = await h._resolve_desync_orders(exchange, [_pending()])
    assert synths == []
    assert "CANCELED" in notes[0]


@pytest.mark.asyncio
async def test_genuinely_resting_order_renders_without_scare():
    """fetch_order says open -> the account-wide query just missed it.
    Render it as an open order, no warning note."""
    h = _handler()
    exchange = MagicMock()
    exchange.fetch_order = AsyncMock(return_value={"status": "open"})
    notes, synths = await h._resolve_desync_orders(exchange, [_pending()])
    assert notes == []
    assert len(synths) == 1 and synths[0]["symbol"] == "FIL/USDT:USDT"


@pytest.mark.asyncio
async def test_unverifiable_order_keeps_desync_warning():
    h = _handler()
    exchange = MagicMock()
    exchange.fetch_order = AsyncMock(side_effect=RuntimeError("api down"))
    notes, synths = await h._resolve_desync_orders(exchange, [_pending()])
    assert len(synths) == 1                  # still shown, conservatively
    assert "could not be verified" in notes[0]


@pytest.mark.asyncio
async def test_missing_order_id_treated_as_unverifiable():
    h = _handler()
    exchange = MagicMock()
    exchange.fetch_order = AsyncMock()
    notes, synths = await h._resolve_desync_orders(
        exchange, [_pending(oid=None)])
    exchange.fetch_order.assert_not_awaited()
    assert len(synths) == 1
    assert "could not be verified" in notes[0]


def test_cmd_orders_uses_resolver_not_guesswork():
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._cmd_orders)
    assert "_resolve_desync_orders" in src
    assert "They may have" not in src        # the old guessing text is gone


# ── 2. hard timeout reachable without an order id ────────────────────
def _stuck_pos(oid, hours_old):
    from bot.core.live_executor import LivePosition
    return LivePosition(
        trade_id="T1", symbol="FIL/USDT:USDT", direction="SHORT",
        entry_price=0.7668, quantity=204.2, cost_usd=31.3,
        stop_loss=0.79, take_profit=0.72, leverage=5,
        status="pending_fill", limit_order_id=oid,
        opened_at=datetime.now(UTC) - timedelta(hours=hours_old))


@pytest.mark.asyncio
async def test_stale_pending_without_order_id_force_closes(tmp_path):
    """The id-less pending record previously returned early BEFORE the
    hard-timeout check and sat in _positions forever."""
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _stuck_pos(oid=None, hours_old=100)
    exchange = MagicMock()
    exchange.cancel_order = AsyncMock()
    msg = await ex._check_pending_limit(exchange, "T1", pos)
    assert pos.status == "closed"
    assert pos.close_reason == "stale_pending"
    assert msg and "STALE PENDING CLOSED" in msg
    exchange.cancel_order.assert_not_awaited()   # nothing to cancel


@pytest.mark.asyncio
async def test_stale_pending_with_order_id_still_cancels(tmp_path):
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _stuck_pos(oid="999", hours_old=100)
    exchange = MagicMock()
    exchange.cancel_order = AsyncMock()
    msg = await ex._check_pending_limit(exchange, "T1", pos)
    assert pos.status == "closed" and pos.close_reason == "stale_pending"
    exchange.cancel_order.assert_awaited_once_with("999", "FIL/USDT:USDT")


@pytest.mark.asyncio
async def test_fresh_pending_without_order_id_left_alone(tmp_path):
    """Within the timeout window an id-less record must NOT be closed —
    the fill/cancel may still be reported by a later reconcile."""
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = _stuck_pos(oid=None, hours_old=0)
    exchange = MagicMock()
    msg = await ex._check_pending_limit(exchange, "T1", pos)
    assert msg is None and pos.status == "pending_fill"
