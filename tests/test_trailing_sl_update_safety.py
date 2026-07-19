"""_update_exchange_sl must not flat-close a position, and must round its
trigger to the market tick grid.

Audit bug 19 (severe): when _is_uta was still unknown (None), the method
defaulted to the classic ccxt triggerPrice path. On a UTA account that path
executes as an IMMEDIATE market order — a trailing-stop UPDATE would instantly
close the position. Fix: probe the account type first (mirrors _place_sl_tp) and
take the v3 path for UTA.

Audit bug 18: the classic path sent an UNROUNDED triggerPrice, which Bitget
rejects (45115) — the SL update then silently fails and the looser old stop
stays in place. Fix: snap the trigger via _round_price_to_market first.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition


def _pos() -> LivePosition:
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=98.0, take_profit=110.0, status="open",
        sl_order_id="OLD-SL",
    )


@pytest.mark.asyncio
async def test_uta_account_uses_v3_not_immediate_market_close():
    ex = LiveExecutor()
    ex._is_uta = None  # not yet detected — the dangerous state
    ex._save_positions = MagicMock()
    ex._round_price_to_market = MagicMock(return_value="99.0")
    ex._place_sl_tp_v3 = AsyncMock(return_value=("SL-NEW", None))
    exchange = AsyncMock()
    # UTA account: the v2 account probe raises 40085.
    exchange.privateMixGetV2MixAccountAccount = AsyncMock(
        side_effect=RuntimeError("bitget {code: 40085} UTA account"))

    pos = _pos()
    ok = await ex._update_exchange_sl(exchange, pos, new_sl=99.0)

    ex._place_sl_tp_v3.assert_awaited()          # took the SAFE v3 path
    exchange.create_order.assert_not_called()    # never the immediate-close path
    assert ex._is_uta is True
    assert pos.sl_order_id == "SL-NEW"
    assert ok is True                            # confirmed on exchange


@pytest.mark.asyncio
async def test_classic_account_probe_then_rounds_trigger():
    ex = LiveExecutor()
    ex._is_uta = None
    ex._save_positions = MagicMock()
    # Round 99.53 -> tick grid 99.5.
    ex._round_price_to_market = MagicMock(return_value="99.5")
    exchange = AsyncMock()
    # Classic account: the v2 account probe SUCCEEDS.
    exchange.privateMixGetV2MixAccountAccount = AsyncMock(return_value={"data": {}})
    exchange.create_order = AsyncMock(return_value={"id": "SL-CLASSIC"})

    pos = _pos()
    ok = await ex._update_exchange_sl(exchange, pos, new_sl=99.53)

    assert ex._is_uta is False
    exchange.create_order.assert_awaited_once()
    # Bug 18: the trigger sent must be the ROUNDED value, not the raw 99.53.
    params = exchange.create_order.await_args.kwargs["params"]
    assert params["triggerPrice"] == pytest.approx(99.5)
    assert pos.sl_order_id == "SL-CLASSIC"
    assert ok is True                            # confirmed on exchange


@pytest.mark.asyncio
async def test_failed_new_placement_preserves_old_sl():
    ex = LiveExecutor()
    ex._is_uta = False  # classic, already known
    ex._save_positions = MagicMock()
    ex._round_price_to_market = MagicMock(return_value="99.5")
    exchange = AsyncMock()
    exchange.create_order = AsyncMock(side_effect=RuntimeError("45115 rejected"))

    pos = _pos()
    ok = await ex._update_exchange_sl(exchange, pos, new_sl=99.5)

    # New placement failed -> old SL id untouched, old order NOT cancelled.
    assert pos.sl_order_id == "OLD-SL"
    exchange.cancel_order.assert_not_called()
    # M-02: the caller MUST learn the exchange update failed, so it can keep the
    # local stop equal to what the exchange holds (no over-reported protection).
    assert ok is False


def test_trailing_advances_local_stop_only_after_exchange_confirms():
    """M-02 regression (source invariant): in the trailing-stop update block the
    local pos.stop_loss must be written only AFTER a truthy _update_exchange_sl
    result — never before. Persisting the tighter stop first (the old bug) makes
    the position claim protection the exchange isn't holding during any downtime.
    """
    import inspect
    src = inspect.getsource(LiveExecutor.check_positions)
    # Both substrings are unique to the trailing-stop update block.
    i_call = src.index("sl_applied = await self._update_exchange_sl(")
    i_local = src.index("pos.stop_loss = new_sl")
    assert i_call < i_local, (
        "local trailing stop advanced before the exchange confirmed the tighten")
    # And it must be inside the success branch.
    assert "if sl_applied:" in src
    assert src.index("if sl_applied:") < i_local
