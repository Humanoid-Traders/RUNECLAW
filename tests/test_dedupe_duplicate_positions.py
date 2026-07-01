"""
Two local records for the SAME real exchange position/order must never be
left standing side by side.

Real incident: /openorders and /livepositions disagreed about the tracked
order's id for an XPT short limit — one showed "ORPHAN-14561", the other
"TI-f3efabde" — for a position with the identical price and quantity. This
happens when the (already-partially-fixed) false-orphan-adoption bug creates
a SECOND LivePosition record for a position/order the bot was already
tracking. Leaving both standing is actively dangerous, not just confusing:
if either record's own stale/expiry logic tried to cancel/close "its" order,
it could cancel or close the single real order/position shared by both,
while the OTHER record keeps believing it is still live.

dedupe_duplicate_positions() merges same (symbol, direction) groups down to
one record — preferring the bot's own original (non-adoption-artifact)
record, or the earliest-opened one — and marks the rest closed locally.
It never touches the exchange.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path):
    pos_file = tmp_path / "live_positions.json"
    closed_file = tmp_path / "closed_trades.json"
    with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE", str(closed_file)):
        yield


def _pos(trade_id, symbol="XPT/USDT:USDT", direction="SHORT", entry=1560.51,
          qty=0.274, status="pending_fill", opened_at=None, sl=0, tp=0):
    return LivePosition(
        trade_id=trade_id, symbol=symbol, direction=direction,
        entry_price=entry, quantity=qty, cost_usd=42.76,
        stop_loss=sl, take_profit=tp, leverage=10, status=status,
        opened_at=opened_at or datetime.now(UTC),
    )


class TestDedupeDuplicatePositions:
    def test_merges_orphan_duplicate_keeping_original(self):
        executor = LiveExecutor()
        original = _pos("TI-f3efabde", sl=1579.97, tp=1533.25,
                         opened_at=datetime.now(UTC) - timedelta(hours=1))
        orphan = _pos("ORPHAN-14561", sl=0, tp=0,
                       opened_at=datetime.now(UTC) - timedelta(minutes=5))
        executor._positions[original.trade_id] = original
        executor._positions[orphan.trade_id] = orphan

        messages = executor.dedupe_duplicate_positions()

        assert len(messages) == 1
        assert "TI-f3efabde" in messages[0]
        assert "ORPHAN-14561" in messages[0]
        assert executor._positions["TI-f3efabde"].status == "pending_fill"
        # _save_positions() prunes non-(open|pending_fill) records from the
        # in-memory dict, so the merged duplicate is gone entirely, not left
        # sitting around with status="closed".
        assert "ORPHAN-14561" not in executor._positions

    def test_never_touches_the_exchange(self):
        """The merge is local-only bookkeeping -- no cancel/close order call."""
        executor = LiveExecutor()
        executor._positions["TI-f3efabde"] = _pos("TI-f3efabde")
        executor._positions["ORPHAN-14561"] = _pos("ORPHAN-14561")
        # No exchange stub is wired up at all -- if the merge tried to touch
        # the exchange it would raise (self._exchange is None).
        messages = executor.dedupe_duplicate_positions()
        assert len(messages) == 1

    def test_no_duplicates_is_a_no_op(self):
        executor = LiveExecutor()
        executor._positions["TI-f3efabde"] = _pos("TI-f3efabde")
        executor._positions["TI-other0001"] = _pos(
            "TI-other0001", symbol="AMD/USDT:USDT", direction="LONG", entry=575.22)
        messages = executor.dedupe_duplicate_positions()
        assert messages == []
        assert executor._positions["TI-f3efabde"].status == "pending_fill"
        assert executor._positions["TI-other0001"].status == "pending_fill"

    def test_prefers_earliest_when_neither_is_an_adoption_artifact(self):
        executor = LiveExecutor()
        older = _pos("TI-older0001", opened_at=datetime.now(UTC) - timedelta(hours=2))
        newer = _pos("TI-newer0002", opened_at=datetime.now(UTC) - timedelta(minutes=1))
        executor._positions[older.trade_id] = older
        executor._positions[newer.trade_id] = newer

        executor.dedupe_duplicate_positions()

        assert executor._positions["TI-older0001"].status == "pending_fill"
        assert "TI-newer0002" not in executor._positions

    def test_different_direction_same_symbol_is_not_merged(self):
        """A genuine hedge-mode long+short on the same symbol must survive."""
        executor = LiveExecutor()
        long_pos = _pos("TI-long00001", direction="LONG")
        short_pos = _pos("TI-short0001", direction="SHORT")
        executor._positions[long_pos.trade_id] = long_pos
        executor._positions[short_pos.trade_id] = short_pos

        messages = executor.dedupe_duplicate_positions()

        assert messages == []
        assert executor._positions["TI-long00001"].status == "pending_fill"
        assert executor._positions["TI-short0001"].status == "pending_fill"

    @pytest.mark.asyncio
    async def test_adopt_exchange_positions_runs_dedupe_first(self):
        """The dedupe pass runs automatically on every adoption cycle, so
        leftover duplicates from before this fix get cleaned up without any
        manual intervention."""
        from unittest.mock import AsyncMock

        executor = LiveExecutor()
        executor._exchange = AsyncMock()
        executor._exchange.fetch_positions = AsyncMock(return_value=[])
        executor._positions["TI-f3efabde"] = _pos(
            "TI-f3efabde", symbol="AMD/USDT:USDT", direction="LONG",
            entry=575.22, status="open")
        executor._positions["ORPHAN-14561"] = _pos(
            "ORPHAN-14561", symbol="AMD/USDT:USDT", direction="LONG",
            entry=575.22, status="open")

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            await executor.adopt_exchange_positions()

        assert executor._positions["TI-f3efabde"].status == "open"
        assert "ORPHAN-14561" not in executor._positions
