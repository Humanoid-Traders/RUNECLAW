"""
Orphan adoption must never re-"adopt" a position/order the bot itself just
placed.

Real incident: a limit BUY on AMD filled, and — in the SAME position-check
cycle — the periodic exchange sync ran adopt_exchange_positions() and
flagged that very same AMD position as an untracked "orphan," sending a
confusing duplicate "Adopted Exchange Positions" notification for a position
the user had just watched the bot open. The same thing happened to a
still-pending XPT limit order via adopt_exchange_limit_orders(). Both
adoption paths already de-duplicate via an exact (symbol, direction[, price])
match against locally tracked positions, but any transient disagreement
between what the bot recorded and how the exchange echoes an order/position
back falls through that exact match straight into "this is a new orphan."

The fix: LiveExecutor now stamps _recent_local_opens[symbol] = time.time()
at every genuine local-open site (fresh order placement, emergency
post-crash recovery). Both adoption methods skip a symbol for
_RECENT_LOCAL_OPEN_GRACE seconds after such a stamp, regardless of whether
the exact-match check also happens to hit. A position/order that is a
GENUINE orphan (no recent local open) is adopted exactly as before.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.live_executor import LiveExecutor, LivePosition, _RECENT_LOCAL_OPEN_GRACE
from bot.utils.models import TradeIdea, Direction


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path):
    pos_file = tmp_path / "live_positions.json"
    closed_file = tmp_path / "closed_trades.json"
    with patch.object(live_executor_mod, "_POSITIONS_FILE", str(pos_file)), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE", str(closed_file)):
        yield


def _mock_exchange() -> AsyncMock:
    ex = AsyncMock()
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.fetch_open_orders = AsyncMock(return_value=[])
    return ex


def _synthetic_ex_position(symbol="AMD/USDT:USDT", side="long", contracts=0.74) -> dict:
    return {
        "symbol": symbol, "side": side, "contracts": contracts,
        "entryPrice": 575.22, "leverage": 10, "initialMargin": 42.57,
        "timestamp": None,
        "info": {"openPriceAvg": "575.22", "totalQty": str(contracts),
                  "margin": "42.57", "leverage": "10"},
    }


def _synthetic_ex_limit_order(symbol="XPT/USDT:USDT", side="sell",
                               price=1560.51, amount=0.274, oid="9999") -> dict:
    return {
        "id": oid, "symbol": symbol, "side": side, "type": "limit",
        "price": price, "amount": amount, "remaining": amount,
        "datetime": "2026-07-01T12:45:00.000Z",
        "info": {"clientOid": "some-other-order"},
    }


class TestPositionAdoptionGraceWindow:
    @pytest.mark.asyncio
    async def test_recently_opened_symbol_is_not_reflagged_as_orphan(self):
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_positions = AsyncMock(
            return_value=[_synthetic_ex_position()])
        # Simulate a genuine local open of AMD moments ago, WITHOUT it ending
        # up matched by the exact (symbol, direction) tuple check (e.g. any
        # transient disagreement) -- the grace window must still catch it.
        executor._recent_local_opens["AMD"] = __import__("time").time()

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_positions()

        assert adopted == []
        assert executor._positions == {}

    @pytest.mark.asyncio
    async def test_stale_local_open_past_grace_window_still_adopts(self):
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_positions = AsyncMock(
            return_value=[_synthetic_ex_position()])
        executor._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
        executor._recent_local_opens["AMD"] = (
            __import__("time").time() - _RECENT_LOCAL_OPEN_GRACE - 1
        )

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_positions()

        assert adopted == ["AMD"]

    @pytest.mark.asyncio
    async def test_genuine_orphan_with_no_local_history_is_still_adopted(self):
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_positions = AsyncMock(
            return_value=[_synthetic_ex_position()])
        executor._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_positions()

        assert adopted == ["AMD"]


class TestLimitOrderAdoptionGraceWindow:
    @pytest.mark.asyncio
    async def test_recently_placed_symbol_is_not_reflagged_as_orphan(self):
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_open_orders = AsyncMock(
            return_value=[_synthetic_ex_limit_order()])
        executor._recent_local_opens["XPT"] = __import__("time").time()

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_limit_orders()

        assert adopted == []
        assert executor._positions == {}

    @pytest.mark.asyncio
    async def test_stale_local_open_past_grace_window_still_adopts_order(self):
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_open_orders = AsyncMock(
            return_value=[_synthetic_ex_limit_order()])
        executor._recent_local_opens["XPT"] = (
            __import__("time").time() - _RECENT_LOCAL_OPEN_GRACE - 1
        )

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_limit_orders()

        assert adopted == ["XPT/USDT:USDT"]

    @pytest.mark.asyncio
    async def test_genuine_orphan_order_with_no_local_history_is_still_adopted(self):
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_open_orders = AsyncMock(
            return_value=[_synthetic_ex_limit_order()])

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_limit_orders()

        assert adopted == ["XPT/USDT:USDT"]

    @pytest.mark.asyncio
    async def test_fresh_exchange_order_not_adopted_even_with_empty_local_maps(self):
        """The reported ALGO incident: a limit order placed THIS session was
        re-adopted ~1 min later with a false 'SL/TP may not be set' alarm. The
        local grace map / exact-match guards can all miss (order-id format drift
        between place and fetch, a second executor instance whose _recent_local_
        opens is empty, stale in-memory _positions). The exchange's OWN creation
        timestamp is immune: an order it says is seconds old cannot be a
        prior-session orphan. Here local tracking is entirely empty — only the
        freshness guard can save it."""
        import time as _t
        order = _synthetic_ex_limit_order(oid="ALGO-FRESH")
        order["symbol"] = "ALGO/USDT:USDT"
        order["timestamp"] = int((_t.time() - 60) * 1000)  # created 60s ago
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_open_orders = AsyncMock(return_value=[order])
        assert executor._recent_local_opens == {}  # nothing tracked locally

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_limit_orders()

        assert adopted == []
        assert executor._positions == {}

    @pytest.mark.asyncio
    async def test_bot_own_order_reclaimed_quietly_not_flagged_external(self):
        """An order carrying the bot's own 'rc' clientOid prefix must NEVER be
        surfaced as an EXTERNAL 'previous-session' orphan, even when local
        tracking lost it and it is past the freshness window (the reported TRX
        recurrence). It is reclaimed under its real trade_id and kept out of the
        adopted-notification list."""
        import time as _t
        order = _synthetic_ex_limit_order(oid="TRX-OWN")
        order["symbol"] = "TRX/USDT:USDT"
        order["timestamp"] = int((_t.time() - 3600) * 1000)  # 1h old, past grace
        order["info"] = {"clientOid": "rcTIabcd1234"}         # the bot placed it
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_open_orders = AsyncMock(return_value=[order])
        assert executor._recent_local_opens == {}  # nothing tracked locally

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_limit_orders()

        # No external-orphan alarm...
        assert adopted == []
        # ...but the order IS tracked, under its reconstructed real trade_id.
        assert "TI-abcd1234" in executor._positions
        assert executor._positions["TI-abcd1234"].limit_order_id == "TRX-OWN"

    @pytest.mark.asyncio
    async def test_old_exchange_order_still_adopted_past_freshness_window(self):
        """A genuine orphan — the exchange reports it created well past the grace
        window — is adopted exactly as before, even with empty local maps."""
        import time as _t
        order = _synthetic_ex_limit_order(oid="XPT-OLD")
        order["timestamp"] = int((_t.time() - _RECENT_LOCAL_OPEN_GRACE - 3600) * 1000)
        executor = LiveExecutor()
        executor._exchange = _mock_exchange()
        executor._exchange.fetch_open_orders = AsyncMock(return_value=[order])

        with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
            adopted = await executor.adopt_exchange_limit_orders()

        assert adopted == ["XPT/USDT:USDT"]


def _full_mock_exchange() -> AsyncMock:
    """Mirrors tests/test_live_executor.py's _mock_exchange — enough surface
    for execute() to run its full order-placement path (not just the
    adoption-scoped stubs above)."""
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": 100_000.0})
    ex.create_order = AsyncMock(return_value={
        "id": "ORD-001", "average": 100_000.0, "filled": 0.0001,
        "cost": 10.0, "status": "filled",
    })
    ex.fetch_tickers = AsyncMock(return_value={"BTC/USDT": {"last": 100_000.0}})
    ex.cancel_order = AsyncMock(return_value=None)
    ex.close = AsyncMock()
    ex.fetch_funding_rate = AsyncMock(return_value={"fundingRate": 0.0})
    _markets = {
        "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "swap": True, "linear": True,
                          "contract": True, "active": True},
    }
    ex.load_markets = AsyncMock(return_value=_markets)
    _lev = {"value": 1}

    async def _set_lev(*args, **kwargs):
        if args:
            _lev["value"] = args[0]
        return None

    async def _fetch_lev(*args, **kwargs):
        v = _lev["value"]
        return {"longLeverage": v, "shortLeverage": v, "leverage": v}

    ex.set_leverage = AsyncMock(side_effect=_set_lev)
    ex.set_margin_mode = AsyncMock(return_value=None)
    ex.fetch_leverage = AsyncMock(side_effect=_fetch_lev)
    ex.fetch_balance = AsyncMock(return_value={
        "USDT": {"free": 10_000.0, "total": 10_000.0},
        "free": {"USDT": 10_000.0}, "total": {"USDT": 10_000.0},
    })
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.fetch_open_orders = AsyncMock(return_value=[])
    ex.fetch_closed_orders = AsyncMock(return_value=[])
    ex.fetch_my_trades = AsyncMock(return_value=[])
    ex.fetch_order = AsyncMock(return_value={
        "id": "ORD-001", "status": "closed", "filled": 0.0001,
        "average": 100_000.0, "cost": 10.0,
    })
    ex.fetch_ohlcv = AsyncMock(return_value=[])
    ex.price_to_precision = MagicMock(side_effect=lambda symbol, price: float(price))
    ex.amount_to_precision = MagicMock(side_effect=lambda symbol, amount: float(amount))
    ex.markets = _markets
    return ex


def _make_idea(direction=Direction.LONG, asset="BTC/USDT", entry=100_000.0,
               sl=98_000.0, tp=105_000.0, trade_id="TI-TEST-001") -> TradeIdea:
    if direction == Direction.SHORT:
        sl = sl if sl > entry else entry * 1.02
        tp = tp if tp < entry else entry * 0.95
    return TradeIdea(
        id=trade_id, asset=asset, direction=direction, entry_price=entry,
        stop_loss=sl, take_profit=tp, confidence=0.85,
        reasoning="unit-test fixture",
    )


class TestRecentLocalOpensStamping:
    @pytest.mark.asyncio
    async def test_execute_stamps_recent_local_opens_for_the_symbol(self):
        """The real execute() path must stamp _recent_local_opens itself --
        this is what actually protects a freshly-placed trade from the
        periodic sync's next orphan-adoption pass."""
        from bot.config import CONFIG
        executor = LiveExecutor()
        executor._exchange = _full_mock_exchange()
        assert executor._recent_local_opens == {}

        with patch.object(type(CONFIG), "is_live", return_value=True):
            result = await executor.execute(_make_idea(), size_usd=10.0)

        assert "BLOCKED" not in result
        assert "BTC" in executor._recent_local_opens
        assert __import__("time").time() - executor._recent_local_opens["BTC"] < 5
