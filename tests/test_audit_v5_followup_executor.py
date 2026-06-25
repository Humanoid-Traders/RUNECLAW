"""
Regression tests for the V5 audit follow-up fixes in bot.core.live_executor.

Covers:
  RC-AUD-021  — startup recovery of positions stranded in "closing" status.
  RC-AUD-022  — orphan-adoption SL failure now alerts loudly (gated on the SL id
                being None) and records the unprotected state WITHOUT auto-closing.
  RC-AUD-023b — residual-close reconciliation: when the exchange still shows a
                residual position after a close, the local record is kept OPEN
                (tracking the remainder) instead of being silently marked closed.

These are UNIT tests of the new logic. They deliberately avoid driving the full
execute() harness (which is incomplete in a bare environment — it needs
market/leverage stubs and CONFIG.is_live()), per the audit's guidance. All
exchange interactions are mocked; no real Bitget calls are made and no orders
are placed.

Mock patterns (_make_idea, _mock_exchange, _executor_with_mock, LivePosition)
mirror tests/test_live_executor.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor, LivePosition


# ── Isolation: never touch the repo's real data/ state files ─────────


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path):
    """Point the executor's persistence files at a fresh temp dir for every
    test. The module computes _POSITIONS_FILE/_CLOSED_TRADES_FILE at import
    time from the real ``data/`` dir; without this, LiveExecutor() would load
    (and these tests would clobber) the operator's live state on disk, and
    leak state between tests.
    """
    pos_file = tmp_path / "live_positions.json"
    closed_file = tmp_path / "closed_trades.json"
    with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE", str(closed_file)):
        yield


# ── Shared fixtures (mirrors tests/test_live_executor.py) ────────────


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
    ex.fetch_tickers = AsyncMock(return_value={"BTC/USDT": {"last": 100_000.0}})
    ex.cancel_order = AsyncMock(return_value=None)
    ex.fetch_open_orders = AsyncMock(return_value=[])
    ex.fetch_my_trades = AsyncMock(return_value=[])
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.close = AsyncMock()
    return ex


def _executor_with_mock() -> tuple[LiveExecutor, AsyncMock]:
    """Return a LiveExecutor with its exchange pre-injected as a mock."""
    executor = LiveExecutor()
    mock_ex = _mock_exchange()
    executor._exchange = mock_ex
    return executor, mock_ex


# ── RC-AUD-021: startup recovery of stranded "closing" positions ─────


class TestStuckClosingRecovery:
    """A position left in "closing" on disk is reverted to "open" on load."""

    def _write_positions_file(self, path: Path, records: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2))

    def _record(self, status: str, tid: str = "TI-STUCK-1") -> dict:
        return {
            "trade_id": tid,
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "entry_price": 100_000.0,
            "quantity": 0.0001,
            "cost_usd": 10.0,
            "stop_loss": 98_000.0,
            "take_profit": 105_000.0,
            "leverage": 1,
            "is_spot": False,
            "sl_order_id": None,
            "tp_order_id": None,
            "opened_at": None,
            "status": status,
            "order_type": "market",
        }

    def test_closing_status_reverted_to_open_on_load(self, tmp_path):
        """A persisted "closing" record loads back as "open" (re-monitored)."""
        pos_file = tmp_path / "live_positions.json"
        # NOTE: _save_positions() filters out non-(open|pending_fill) statuses,
        # so the bot never writes "closing" itself. The finding's stranded state
        # is reproduced here by writing the JSON directly (the same shape a
        # mid-close crash / .bak / manual edit could leave), then loading it.
        self._write_positions_file(pos_file, {"TI-STUCK-1": self._record("closing")})

        with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)):
            executor = LiveExecutor()  # __init__ -> _load_positions()

        assert "TI-STUCK-1" in executor._positions
        assert executor._positions["TI-STUCK-1"].status == "open"

    def test_open_status_unchanged_on_load(self, tmp_path):
        """A normal "open" record is loaded untouched (no false recovery)."""
        pos_file = tmp_path / "live_positions.json"
        self._write_positions_file(
            pos_file, {"TI-OK-1": self._record("open", tid="TI-OK-1")})

        with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)):
            executor = LiveExecutor()

        assert executor._positions["TI-OK-1"].status == "open"

    def test_mixed_records_only_closing_is_recovered(self, tmp_path):
        """Among mixed records, only the "closing" one flips to "open"."""
        pos_file = tmp_path / "live_positions.json"
        self._write_positions_file(pos_file, {
            "TI-STUCK-1": self._record("closing", tid="TI-STUCK-1"),
            "TI-OK-1": self._record("open", tid="TI-OK-1"),
            "TI-PEND-1": self._record("pending_fill", tid="TI-PEND-1"),
        })

        with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)):
            executor = LiveExecutor()

        assert executor._positions["TI-STUCK-1"].status == "open"
        assert executor._positions["TI-OK-1"].status == "open"
        assert executor._positions["TI-PEND-1"].status == "pending_fill"


# ── RC-AUD-022: orphan-adoption unprotected-on-SL-failure alert ──────


def _synthetic_ex_position(symbol: str = "BTC/USDT:USDT",
                           side: str = "long",
                           contracts: float = 0.0001) -> dict:
    """A ccxt-shaped exchange position dict with NO SL/TP set, so the
    adoption safety-SL path runs."""
    return {
        "symbol": symbol,
        "side": side,
        "contracts": contracts,
        "entryPrice": 100_000.0,
        "leverage": 1,
        "initialMargin": 10.0,
        "timestamp": None,
        "info": {
            "openPriceAvg": "100000",
            "totalQty": str(contracts),
            "margin": "10",
            "leverage": "1",
            # No stopLoss / takeProfit / stopLossId keys → need_sl/need_tp true.
        },
    }


class TestAdoptionUnprotectedAlert:
    """When the adopted-position safety SL cannot be placed, alert loudly,
    mark unprotected, and do NOT auto-close."""

    @pytest.mark.asyncio
    async def test_sl_failure_marks_unprotected_and_alerts(self):
        """_place_sl_tp returning sl_id=None (TP ok) → unprotected marker +
        UNPROTECTED audit; position is STILL adopted (not closed)."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_positions = AsyncMock(return_value=[_synthetic_ex_position()])
        # SL fails (None) but TP succeeds — gate must trigger on the SL id alone.
        executor._place_sl_tp = AsyncMock(return_value=(None, "TP-123"))

        audited: list[tuple] = []
        real_audit = live_executor_mod.audit

        def _capture_audit(logger, msg, **kwargs):
            audited.append((msg, kwargs))
            return real_audit(logger, msg, **kwargs)

        # CONFIG is a frozen dataclass instance — patch the method on its class.
        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True), \
                patch.object(live_executor_mod, "audit", side_effect=_capture_audit):
            adopted = await executor.adopt_exchange_positions()

        # Position was adopted (NOT auto-closed).
        # adopt_exchange_positions() returns normalize_symbol(raw) → "BTC".
        assert adopted == ["BTC"]
        lp = next(p for p in executor._positions.values()
                  if p.symbol == "BTC/USDT:USDT")
        # SL id stays None; unprotected runtime marker is set.
        assert lp.sl_order_id is None
        assert getattr(lp, "unprotected", False) is True
        # SL was retried once (initial + 1 retry).
        assert executor._place_sl_tp.await_count == 2
        # A loud UNPROTECTED audit was emitted.
        assert any(kw.get("result") == "UNPROTECTED" for _, kw in audited), \
            "expected an UNPROTECTED audit event on SL-None adoption"

    @pytest.mark.asyncio
    async def test_sl_success_no_unprotected_marker(self):
        """When SL places successfully, the position is protected and no
        UNPROTECTED marker/alert is recorded."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_positions = AsyncMock(return_value=[_synthetic_ex_position()])
        executor._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))

        audited: list[tuple] = []

        def _capture_audit(logger, msg, **kwargs):
            audited.append((msg, kwargs))

        # CONFIG is a frozen dataclass instance — patch the method on its class.
        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True), \
                patch.object(live_executor_mod, "audit", side_effect=_capture_audit):
            adopted = await executor.adopt_exchange_positions()

        # adopt_exchange_positions() returns normalize_symbol(raw) → "BTC".
        assert adopted == ["BTC"]
        lp = next(p for p in executor._positions.values()
                  if p.symbol == "BTC/USDT:USDT")
        assert lp.sl_order_id == "SL-1"
        assert getattr(lp, "unprotected", False) is False
        # No retry needed — placed on the first attempt.
        assert executor._place_sl_tp.await_count == 1
        assert not any(kw.get("result") == "UNPROTECTED" for _, kw in audited)

    @pytest.mark.asyncio
    async def test_sl_placement_raises_still_alerts(self):
        """If _place_sl_tp raises on both the initial call and the retry, the
        position is still adopted and flagged UNPROTECTED (no auto-close)."""
        executor, mock_ex = _executor_with_mock()
        mock_ex.fetch_positions = AsyncMock(return_value=[_synthetic_ex_position()])
        executor._place_sl_tp = AsyncMock(side_effect=RuntimeError("venue 5xx"))

        audited: list[tuple] = []

        def _capture_audit(logger, msg, **kwargs):
            audited.append((msg, kwargs))

        # CONFIG is a frozen dataclass instance — patch the method on its class.
        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True), \
                patch.object(live_executor_mod, "audit", side_effect=_capture_audit):
            adopted = await executor.adopt_exchange_positions()

        # adopt_exchange_positions() returns normalize_symbol(raw) → "BTC".
        assert adopted == ["BTC"]
        lp = next(p for p in executor._positions.values()
                  if p.symbol == "BTC/USDT:USDT")
        assert getattr(lp, "unprotected", False) is True
        assert lp.sl_order_id is None
        assert any(kw.get("result") == "UNPROTECTED" for _, kw in audited)


# ── RC-AUD-023b: residual-close reconciliation ───────────────────────


class TestResidualCloseReconciliation:
    """A partial close that leaves residual exchange exposure must NOT mark the
    local record fully closed — it keeps tracking the remainder and warns."""

    def _seed_open(self, executor: LiveExecutor, qty: float = 0.0002) -> str:
        tid = "T-RES-1"
        executor._positions[tid] = LivePosition(
            trade_id=tid,
            symbol="BTC/USDT",
            direction="LONG",
            entry_price=100_000.0,
            quantity=qty,
            cost_usd=20.0,
            stop_loss=98_000.0,
            take_profit=105_000.0,
            status="open",
            sl_order_id=None,
            tp_order_id=None,
        )
        return tid

    @pytest.mark.asyncio
    async def test_residual_keeps_position_open(self):
        """Exchange still shows residual after close → position stays OPEN,
        quantity becomes the remainder, NOT appended to closed trades."""
        executor, mock_ex = _executor_with_mock()
        tid = self._seed_open(executor, qty=0.0002)
        # The close order is "placed" (market reduceOnly) but only partially fills.
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-RES", "average": 97_000.0, "filled": 0.0001,
            "cost": 9.7, "status": "filled",
        })
        # Verification reports a residual still open on the exchange.
        residual = 0.0001
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": False,
            "fill_price": 97_000.0,
            "fill_qty": 0.0001,
            "fees": 0.0,
            "remaining_qty": residual,
            "failure_stage": "position_still_open",
        })

        result = await executor.close_position(tid, reason="manual")

        # Position is still tracked and OPEN, sized to the remainder.
        assert tid in executor._positions
        pos = executor._positions[tid]
        assert pos.status == "open"
        assert pos.quantity == residual
        # It must NOT have been recorded as a closed trade.
        assert all(t.trade_id != tid for t in executor._closed_trades)
        # The operator-facing message flags the residual.
        assert "RESIDUAL" in result

    @pytest.mark.asyncio
    async def test_confirmed_close_marks_closed(self):
        """Control: a fully-confirmed close (no residual) still finalizes the
        position as closed (existing behavior preserved)."""
        executor, mock_ex = _executor_with_mock()
        tid = self._seed_open(executor, qty=0.0001)
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-OK", "average": 105_000.0, "filled": 0.0001,
            "cost": 10.5, "status": "filled",
        })
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": True,
            "fill_price": 105_000.0,
            "fill_qty": 0.0001,
            "fees": 0.0,
            "remaining_qty": 0.0,
            "failure_stage": "",
        })

        result = await executor.close_position(tid, reason="TP HIT")

        # _save_positions() prunes closed entries from the in-memory dict.
        assert tid not in executor._positions
        assert any(t.trade_id == tid for t in executor._closed_trades)
        assert "CLOSED" in result
        assert "RESIDUAL" not in result

    @pytest.mark.asyncio
    async def test_unconfirmed_without_residual_does_not_reopen(self):
        """Guard conservatism: unconfirmed close but remaining_qty == 0 (e.g. a
        verification hiccup) must NOT trigger the residual re-open path."""
        executor, mock_ex = _executor_with_mock()
        tid = self._seed_open(executor, qty=0.0001)
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-HICCUP", "average": 105_000.0, "filled": 0.0001,
            "cost": 10.5, "status": "filled",
        })
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": False,
            "fill_price": 105_000.0,
            "fill_qty": 0.0001,
            "fees": 0.0,
            "remaining_qty": 0.0,
            "failure_stage": "close_order_unconfirmed",
        })

        result = await executor.close_position(tid, reason="manual")

        # No residual → the normal close finalization runs (position closed).
        assert tid not in executor._positions
        assert "RESIDUAL" not in result
