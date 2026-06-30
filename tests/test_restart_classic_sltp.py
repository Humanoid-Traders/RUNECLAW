"""
Restart re-placement of classic (two-order) SL/TP legs (deep-audit medium).

verify_and_fix_sltp re-places protection when the stored SL/TP IDs are both
empty or identical (v3 combined order), but when they are DISTINCT and present
(two separate classic orders) it trusted them blindly. A leg lost while the bot
was offline (filled / cancelled on-venue) left the position half-protected and
was never re-placed.

When CONFIG.execution.verify_classic_sltp_on_restart is ON, each distinct
classic leg is verified against the exchange's live orders and the SL/TP pair is
re-placed if either is gone (placement cancels survivors first). Default OFF
keeps restart behaviour byte-identical.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path):
    pos_file = tmp_path / "live_positions.json"
    closed_file = tmp_path / "closed_trades.json"
    with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE", str(closed_file)):
        yield


_legs = LiveExecutor._missing_classic_legs


class TestMissingClassicLegs:
    def test_both_present(self):
        assert _legs("SL-1", "TP-2", {"SL-1", "TP-2"}) == (False, False)

    def test_sl_gone(self):
        assert _legs("SL-1", "TP-2", {"TP-2"}) == (True, False)

    def test_tp_gone(self):
        assert _legs("SL-1", "TP-2", {"SL-1"}) == (False, True)

    def test_both_gone(self):
        assert _legs("SL-1", "TP-2", set()) == (True, True)

    def test_falsy_id_is_missing(self):
        assert _legs("", "TP-2", {"TP-2"}) == (True, False)
        assert _legs("SL-1", None, {"SL-1"}) == (False, True)

    def test_int_id_coerced_to_str(self):
        assert _legs(123, 456, {"123", "456"}) == (False, False)


def _exchange(plans=None, positions=None, plan_raises=False, pos_raises=False):
    ex = AsyncMock()
    ex.fetch_open_orders = (AsyncMock(side_effect=RuntimeError("boom"))
                            if plan_raises else AsyncMock(return_value=plans or []))
    ex.fetch_positions = (AsyncMock(side_effect=RuntimeError("boom"))
                          if pos_raises else AsyncMock(return_value=positions or []))
    return ex


class TestLiveProtectiveOrderIds:
    def _exec(self, ex):
        e = LiveExecutor()
        e._get_exchange = AsyncMock(return_value=ex)
        return e

    def test_union_of_plans_and_position_ids(self):
        ex = _exchange(
            plans=[{"id": "P1"}, {"info": {"orderId": "P2"}}],
            positions=[{"info": {"stopLossId": "S1", "takeProfitId": "T1"}}])
        ids = asyncio.run(self._exec(ex)._live_protective_order_ids(
            SimpleNamespace(symbol="BTC/USDT")))
        assert ids == {"P1", "P2", "S1", "T1"}

    def test_both_sources_fail_returns_none(self):
        ex = _exchange(plan_raises=True, pos_raises=True)
        ids = asyncio.run(self._exec(ex)._live_protective_order_ids(
            SimpleNamespace(symbol="BTC/USDT")))
        assert ids is None

    def test_partial_success_returns_set(self):
        # Plan fetch fails but position fetch works → authoritative (not None).
        ex = _exchange(plan_raises=True,
                       positions=[{"info": {"stopLossId": "S1"}}])
        ids = asyncio.run(self._exec(ex)._live_protective_order_ids(
            SimpleNamespace(symbol="BTC/USDT")))
        assert ids == {"S1"}

    def test_empty_but_queried_is_empty_set_not_none(self):
        ids = asyncio.run(self._exec(_exchange())._live_protective_order_ids(
            SimpleNamespace(symbol="BTC/USDT")))
        assert ids == set()


def _classic_pos():
    return SimpleNamespace(
        status="open", sl_order_id="SL-1", tp_order_id="TP-2",
        stop_loss=98_000.0, take_profit=105_000.0, direction="LONG",
        quantity=0.001, symbol="BTC/USDT", trade_id="T1")


def _run_verify(monkeypatch, *, enabled, live_ids):
    executor = LiveExecutor()
    pos = _classic_pos()
    executor._positions = {"T1": pos}
    executor._get_exchange = AsyncMock(return_value=AsyncMock())
    executor._place_sl_tp = AsyncMock(return_value=("NEW-SL", "NEW-TP"))
    executor._live_protective_order_ids = AsyncMock(return_value=live_ids)
    executor._save_positions = lambda: None
    monkeypatch.setattr(live_executor_mod, "CONFIG", SimpleNamespace(
        execution=SimpleNamespace(verify_classic_sltp_on_restart=enabled)))
    asyncio.run(executor.verify_and_fix_sltp())
    return executor, pos


class TestVerifyAndFixClassic:
    def test_disabled_does_not_touch_distinct_classic(self, monkeypatch):
        # Flag OFF → byte-identical: distinct present IDs are trusted, no re-place.
        executor, pos = _run_verify(monkeypatch, enabled=False, live_ids=set())
        executor._place_sl_tp.assert_not_called()
        executor._live_protective_order_ids.assert_not_called()
        assert pos.sl_order_id == "SL-1" and pos.tp_order_id == "TP-2"

    def test_enabled_replaces_when_leg_missing(self, monkeypatch):
        # Only TP-2 is still live → SL leg gone → re-place the pair.
        executor, pos = _run_verify(monkeypatch, enabled=True, live_ids={"TP-2"})
        executor._place_sl_tp.assert_awaited_once()
        assert pos.sl_order_id == "NEW-SL" and pos.tp_order_id == "NEW-TP"

    def test_enabled_no_replace_when_both_present(self, monkeypatch):
        executor, pos = _run_verify(monkeypatch, enabled=True, live_ids={"SL-1", "TP-2"})
        executor._place_sl_tp.assert_not_called()
        assert pos.sl_order_id == "SL-1" and pos.tp_order_id == "TP-2"

    def test_enabled_failopen_when_query_unavailable(self, monkeypatch):
        # _live_protective_order_ids returns None (couldn't verify) → trust stored
        # IDs, do not re-place (a transient failure must not cause churn).
        executor, pos = _run_verify(monkeypatch, enabled=True, live_ids=None)
        executor._place_sl_tp.assert_not_called()
        assert pos.sl_order_id == "SL-1" and pos.tp_order_id == "TP-2"


class TestDefaultOn:
    def test_flag_defaults_on(self, monkeypatch):
        # Enabled by default (operator-requested activation); explicit env still wins.
        monkeypatch.delenv("VERIFY_CLASSIC_SLTP_ON_RESTART", raising=False)
        from bot.config import ExecutionConfig
        assert ExecutionConfig().verify_classic_sltp_on_restart is True
