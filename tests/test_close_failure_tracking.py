"""A FAILED close must never untrack the position (adversarial review, critical).

close_position sets status="closing" then saves; _save_positions' M-06 prune
rebuilt the in-memory dict keeping only open/pending_fill — evicting the
in-flight record. On close FAILURE the H-01 revert then operated on an
untracked object: a live, possibly stop-less position invisible to
check_positions and un-closeable via the bot, while the operator saw
"CLOSE FAILED". The prune now keeps "closing" (matching the tracked-set
definition adopt_exchange_positions uses); successful closes still prune on
their final save (status "closed" by then).
"""
import pytest
from unittest.mock import AsyncMock

from bot.core.live_executor import LiveExecutor, LivePosition


def _pos(status="open"):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=95.0, take_profit=110.0,
        status=status,
    )


def test_save_positions_prune_keeps_closing(tmp_path):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._positions = {"T1": _pos("closing"), "T2": _pos("open"), "T3": _pos("closed")}
    e._positions["T2"].trade_id = "T2"
    e._positions["T3"].trade_id = "T3"
    e._save_positions()
    assert "T1" in e._positions, "in-flight close must stay tracked in memory"
    assert "T2" in e._positions
    assert "T3" not in e._positions, "finished closes still prune"


@pytest.mark.asyncio
async def test_failed_close_keeps_position_tracked_and_open(tmp_path):
    # End-to-end reproduction of the reviewed failure: venue error during the
    # close order. The position must remain tracked, status reverted to open,
    # and the failure surfaced as CLOSE FAILED.
    e = LiveExecutor(state_dir=str(tmp_path))
    p = _pos()
    e._positions = {"T1": p}
    ex = AsyncMock()
    ex.create_order = AsyncMock(side_effect=RuntimeError("venue 5xx"))
    ex.fetch_ticker = AsyncMock(return_value={"last": 100.0})
    e._get_exchange = AsyncMock(return_value=ex)

    msg = await e.close_position("T1", reason="sl_placement_failed")

    assert "CLOSE FAILED" in msg
    assert "T1" in e._positions, "failed close must NOT untrack the position"
    assert p.status == "open", "H-01 revert must leave it retryable"
