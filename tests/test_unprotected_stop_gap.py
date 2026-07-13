"""
Unprotected-stop gap (2026-07-13) — live XPD incident:

A SHORT's exchange stop was rejected with Bitget 25588 ("For short
position TP/SL (close short), the stop-loss trigger price must be
greater than the latest price") — which MEANS the market was already
past the stop. The bot then sat 40+ minutes with price beyond the stop
because two safeguards composed badly for an UNPROTECTED position:

  1. The stale-ticker guard skipped local SL/TP monitoring "because the
     exchange stop remains the protection" — but there WAS no exchange
     stop, and thin TradFi perps (XPD trades sparsely) have stale
     tickers most cycles, so the local backstop never ran.
  2. The per-tick SL/TP retry kept re-submitting a stop the venue can
     never accept (trigger already crossed) instead of reading the
     rejection as what it is: a stop hit discovered via the venue.
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

UTC = timezone.utc

_LIVE_REJECTION = ("25588: For short position TP/SL (close short), the "
                   "stop-loss trigger price must be greater than the latest price")


# ── 1. rejection-signature truth table ───────────────────────────────
def test_sl_reject_means_breached_truth_table():
    from bot.core.live_executor import LiveExecutor
    f = LiveExecutor._sl_reject_means_breached
    assert f(_LIVE_REJECTION) is True                       # the live message
    # LONG mirror: trigger must be LESS than latest price
    assert f("For long position TP/SL (close long), the stop-loss "
             "trigger price must be less than the latest price") is True
    # TP-side price rejection must NOT force-close
    assert f("the take-profit trigger price must be less than the "
             "latest price") is False
    # Unrelated failures must not close
    assert f("insufficient balance") is False
    assert f("40019 holdSide cannot be empty") is False
    assert f("") is False
    assert f(None) is False


# ── 2. behavior: venue rejection closes the position ─────────────────
def _stuck_short(tmp_path):
    from bot.core.live_executor import LiveExecutor, LivePosition
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = LivePosition(
        trade_id="T-XPD", symbol="XPD/USDT:USDT", direction="SHORT",
        entry_price=1258.85, quantity=0.107, cost_usd=13.47,
        stop_loss=1260.94, take_profit=1253.57, leverage=10,
        status="open", sl_order_id=None, tp_order_id=None,
        opened_at=datetime.now(UTC) - timedelta(minutes=44),
    )
    ex._positions["T-XPD"] = pos
    return ex, pos


@pytest.mark.asyncio
async def test_venue_rejection_closes_breached_short(tmp_path):
    """Ticker says 1260.00 (below the 1260.94 stop -> static check would
    NOT fire), but the venue rejected the stop as already-breached. The
    position must close as a stop hit, not keep retrying placement."""
    ex, pos = _stuck_short(tmp_path)
    exchange = MagicMock()
    exchange.fetch_ticker = AsyncMock(return_value={
        "last": 1260.00, "timestamp": time.time() * 1000})
    ex._get_exchange = AsyncMock(return_value=exchange)
    ex._place_sl_tp = AsyncMock(return_value=(None, None))
    ex._note_sltp_error(pos.symbol, _LIVE_REJECTION)
    ex.close_position = AsyncMock(return_value="CLOSED SHORT XPD (SL HIT)")

    msgs = await ex.check_positions()

    ex.close_position.assert_awaited_once()
    args = ex.close_position.await_args.args
    assert args[0] == "T-XPD"
    assert "SL HIT" in args[1]          # labeled as a stop, not a manual close
    assert any("CLOSED SHORT XPD" in m for m in msgs)


@pytest.mark.asyncio
async def test_no_close_without_breach_signature(tmp_path):
    """A mundane placement failure (not the 25588 family) must NOT close —
    the escalation alert path handles it."""
    ex, pos = _stuck_short(tmp_path)
    exchange = MagicMock()
    exchange.fetch_ticker = AsyncMock(return_value={
        "last": 1260.00, "timestamp": time.time() * 1000})
    ex._get_exchange = AsyncMock(return_value=exchange)
    ex._place_sl_tp = AsyncMock(return_value=(None, None))
    ex._note_sltp_error(pos.symbol, "insufficient balance")
    ex.close_position = AsyncMock()

    await ex.check_positions()
    ex.close_position.assert_not_awaited()
    assert pos.status == "open"


@pytest.mark.asyncio
async def test_stale_ticker_still_monitors_unprotected_position(tmp_path):
    """Stale ticker + NO exchange stop: local monitoring must run anyway —
    price past the stop closes the position (the XPD shape: thin market,
    old ticker timestamp, breached stop, no venue protection)."""
    ex, pos = _stuck_short(tmp_path)
    stale_ts = (time.time() - 3600) * 1000       # 1h-old ticker
    exchange = MagicMock()
    exchange.fetch_ticker = AsyncMock(return_value={
        "last": 1262.57, "timestamp": stale_ts})  # past the 1260.94 stop
    ex._get_exchange = AsyncMock(return_value=exchange)
    ex._place_sl_tp = AsyncMock(return_value=(None, None))
    ex.close_position = AsyncMock(return_value="CLOSED SHORT XPD (SL HIT)")

    await ex.check_positions()
    ex.close_position.assert_awaited_once()
    assert "SL HIT" in ex.close_position.await_args.args[1]


@pytest.mark.asyncio
async def test_stale_ticker_with_exchange_stop_still_skips(tmp_path):
    """Regression: WITH an exchange stop in place, the stale-ticker skip
    is correct (a frozen price must not drive a local stop-out when the
    venue-side stop is the protection)."""
    ex, pos = _stuck_short(tmp_path)
    pos.sl_order_id = "sl-123"
    pos.tp_order_id = "tp-456"
    stale_ts = (time.time() - 3600) * 1000
    exchange = MagicMock()
    exchange.fetch_ticker = AsyncMock(return_value={
        "last": 1262.57, "timestamp": stale_ts})
    ex._get_exchange = AsyncMock(return_value=exchange)
    ex.close_position = AsyncMock()

    await ex.check_positions()
    ex.close_position.assert_not_awaited()


# ── 3. wiring pin ────────────────────────────────────────────────────
def test_check_positions_wires_breach_close():
    from bot.core.live_executor import LiveExecutor
    src = inspect.getsource(LiveExecutor.check_positions)
    assert "_sl_reject_means_breached" in src
    assert "MONITORING_UNPROTECTED" in src
