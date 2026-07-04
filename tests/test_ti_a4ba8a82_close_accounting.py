"""Regression suite for the TI-a4ba8a82 close-accounting incident.

A LONG opened at 0.5638 with a trailing stop that had ratcheted to 0.5679
(above entry) closed for a PROFIT (real fill 0.5703, +$4.58, 20x). The bot
recorded exit=0.5672, +$2.06, 10x, "SL HIT (inferred)". Root causes:

  1. `_fetch_bitget_close_data` read Bitget v1 field names (openPrice/
     achievedProfits) off the v2 position-history endpoint, so it matched
     nothing and returned None -> the code fabricated the close from a ticker
     "last" price + locally-computed PnL + stale pos.leverage.
  2. The close-reason inference labelled a profitable trailing-stop exit
     "SL HIT", which reads as a loss.
  3. The exchange's applied leverage (20x) was never reconciled onto the
     tracked position while it was open, so the record showed a stale 10x.

These tests lock in the fixes.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.close_reason import is_profit_locking_stop, stop_exit_label


# ── 1. Pure close-reason labelling ──────────────────────────────────────

class TestStopExitLabel:
    def test_incident_long_trailing_stop_above_entry_is_trailing(self):
        # The exact incident geometry: LONG, stop ratcheted above entry.
        assert stop_exit_label(True, 0.5638, 0.5679, 0.5672) == "TRAILING SL HIT"

    def test_long_genuine_loss_stop_below_entry_is_plain_sl(self):
        assert stop_exit_label(True, 100.0, 98.0, 98.0) == "SL HIT"

    def test_short_profit_stop_below_entry_is_trailing(self):
        # SHORT: a stop BELOW entry locks profit.
        assert stop_exit_label(False, 100.0, 96.0, 96.0) == "TRAILING SL HIT"

    def test_short_genuine_loss_stop_above_entry_is_plain_sl(self):
        assert stop_exit_label(False, 100.0, 103.0, 103.0) == "SL HIT"

    def test_trailing_active_flag_forces_trailing(self):
        # Even with loss-side geometry, an explicit live trailing stop wins.
        assert stop_exit_label(True, 100.0, 98.0, 98.0, trailing_active=True) == \
            "TRAILING SL HIT"

    def test_positive_realized_pnl_is_never_labelled_a_bare_loss(self):
        assert stop_exit_label(True, 100.0, 98.0, 101.0, realized_pnl=5.0) == \
            "TRAILING SL HIT"

    def test_zero_stop_never_flags_short_as_profit(self):
        # A SHORT with stop_loss=0 must not read as "<= entry" profit-side.
        assert is_profit_locking_stop(False, 100.0, 0.0) is False


# ── 2. Exchange field-name parse + leverage capture ─────────────────────

class TestFetchBitgetCloseDataV2Fields:
    def _executor(self) -> LiveExecutor:
        ex = LiveExecutor()
        ex._exchange = AsyncMock()
        return ex

    def _pos(self) -> LivePosition:
        return LivePosition(
            trade_id="TI-a4ba8a82", symbol="GRASS/USDT:USDT", direction="LONG",
            entry_price=0.5638, quantity=700.0, cost_usd=39.5,
            stop_loss=0.5679, take_profit=0.58, leverage=10, status="open",
        )

    @pytest.mark.asyncio
    async def test_v2_keyed_history_row_is_matched_and_parsed(self):
        ex = self._executor()
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [{
                "openAvgPrice": "0.5638", "closeAvgPrice": "0.5703",
                "pnl": "4.55", "netProfit": "4.58",
                "openFee": "0.02", "closeFee": "0.02",
                "leverage": "20", "closeType": "sl",
            }]}})
        result = await ex._fetch_bitget_close_data(self._pos())
        assert result is not None
        assert result["close_price"] == pytest.approx(0.5703)
        assert result["pnl"] == pytest.approx(4.58)         # netProfit wins
        assert result["leverage"] == 20                     # exchange truth
        # A profit-side stop labelled by the exchange as "sl" is a trailing lock.
        assert result["reason"] == "TRAILING SL HIT (exchange)"

    @pytest.mark.asyncio
    async def test_v1_only_keys_still_match_via_fallback(self):
        # A legacy payload keyed the old way still parses (defensive fallback).
        ex = self._executor()
        ex._exchange.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value={"data": {"list": [{
                "openPrice": "0.5638", "closeAvgPrice": "0.5703",
                "achievedProfits": "4.55", "netProfit": "0",
                "openFee": "0.02", "closeFee": "0.02", "closeType": "sl",
            }]}})
        result = await ex._fetch_bitget_close_data(self._pos())
        assert result is not None
        assert result["close_price"] == pytest.approx(0.5703)
        # netProfit==0 with fees present -> net derived = 4.55 gross - 0.04 fees.
        assert result["pnl"] == pytest.approx(4.51)
        assert result["pnl_is_net"] is True


# ── 3. Close-reason inference on the ticker-fallback path ────────────────

class TestInferCloseReason:
    def _pos(self, **kw) -> LivePosition:
        base = dict(
            trade_id="T", symbol="X/USDT:USDT", direction="LONG",
            entry_price=0.5638, quantity=700.0, cost_usd=39.5,
            stop_loss=0.5679, take_profit=0.58, leverage=10, status="open",
        )
        base.update(kw)
        return LivePosition(**base)

    def test_incident_profit_trailing_exit_is_trailing_sl_inferred(self):
        ex = LiveExecutor()
        reason = ex._infer_close_reason(self._pos(), 0.5672)
        assert reason == "TRAILING SL HIT (inferred)"

    def test_genuine_loss_stop_is_plain_sl_inferred(self):
        ex = LiveExecutor()
        pos = self._pos(entry_price=100.0, stop_loss=98.0, take_profit=105.0)
        assert ex._infer_close_reason(pos, 98.0) == "SL HIT (inferred)"


# ── 4. Close handler reconciles leverage + records provenance ───────────

class TestHandleAlreadyClosedPosition:
    def _executor(self) -> LiveExecutor:
        ex = LiveExecutor()
        ex._get_exchange = AsyncMock(return_value=AsyncMock())
        ex._save_positions = MagicMock()
        ex._save_closed_trades = MagicMock()
        ex._fire_position_closed = MagicMock()
        return ex

    def _pos(self) -> LivePosition:
        return LivePosition(
            trade_id="TI-a4ba8a82", symbol="GRASS/USDT:USDT", direction="LONG",
            entry_price=0.5638, quantity=700.0, cost_usd=39.5,
            stop_loss=0.5679, take_profit=0.58, leverage=10, status="open",
        )

    @pytest.mark.asyncio
    async def test_exchange_close_reconciles_leverage_and_price(self):
        ex = self._executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 0.5703, "pnl": 4.58, "fees": 0.04,
            "reason": "TRAILING SL HIT (exchange)",
            "source": "bitget_position_history", "leverage": 20,
            "pnl_is_net": True,
        })
        pos = self._pos()
        await ex._handle_already_closed_position(pos)
        assert pos.close_price == pytest.approx(0.5703)   # real fill, not ticker
        assert pos.pnl_usd == pytest.approx(4.58)          # exchange PnL
        assert pos.leverage == 20                          # reconciled from 10
        assert pos.fill_source == "bitget_position_history"
        assert pos.close_reason == "TRAILING SL HIT (exchange)"

    @pytest.mark.asyncio
    async def test_ticker_fallback_flags_fill_source_as_inferred(self):
        ex = self._executor()
        ex._fetch_bitget_close_data = AsyncMock(return_value=None)  # exchange miss
        exchange = AsyncMock()
        exchange.fetch_ticker = AsyncMock(return_value={"last": 0.5672})
        ex._get_exchange = AsyncMock(return_value=exchange)
        pos = self._pos()
        await ex._handle_already_closed_position(pos)
        # Provenance makes the fabricated record identifiable.
        assert pos.fill_source == "ticker_fallback"
        # And the label still distinguishes the trailing profit-lock.
        assert pos.close_reason == "TRAILING SL HIT (inferred)"


# ── 5. Provenance survives the closed-trades round-trip ─────────────────

class TestProvenanceRoundTrip:
    def test_origin_and_fill_source_persist_through_save_and_load(self, tmp_path):
        path = str(tmp_path / "closed_trades.json")
        writer = LiveExecutor()
        writer._closed_trades_file = path
        writer._closed_trades = []
        pos = LivePosition(
            trade_id="TI-a4ba8a82", symbol="GRASS/USDT:USDT", direction="LONG",
            entry_price=0.5638, quantity=700.0, cost_usd=39.5,
            stop_loss=0.5679, take_profit=0.58, leverage=20, status="closed",
            close_price=0.5703, pnl_usd=4.58, gross_pnl=4.58, commission=0.0,
            close_reason="TRAILING SL HIT (exchange)",
            origin="adopted", fill_source="bitget_position_history",
        )
        writer._append_closed_trade(pos)

        reader = LiveExecutor()
        reader._closed_trades_file = path
        reader._closed_trades = []
        reader._load_closed_trades()
        loaded = next(t for t in reader._closed_trades if t.trade_id == "TI-a4ba8a82")
        assert loaded.origin == "adopted"
        assert loaded.fill_source == "bitget_position_history"
        assert loaded.leverage == 20

    def test_legacy_record_without_provenance_loads_with_defaults(self, tmp_path):
        import json
        path = tmp_path / "closed_trades.json"
        path.write_text(json.dumps([{
            "trade_id": "OLD-1", "symbol": "BTC/USDT:USDT", "direction": "LONG",
            "entry_price": 100.0, "quantity": 1.0, "cost_usd": 100.0,
            "stop_loss": 98.0, "take_profit": 105.0, "leverage": 5,
            "close_price": 105.0, "pnl_usd": 5.0, "status": "closed",
            "close_reason": "TP HIT (exchange)",
        }]))
        reader = LiveExecutor()
        reader._closed_trades_file = str(path)
        reader._closed_trades = []
        reader._load_closed_trades()
        loaded = next(t for t in reader._closed_trades if t.trade_id == "OLD-1")
        assert loaded.origin == "executed"       # default
        assert loaded.fill_source is None         # default
