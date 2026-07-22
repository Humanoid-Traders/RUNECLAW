"""
Tests for live-order idempotency (clientOid) and orphan detection.

These cover the execution-layer reliability upgrade:
  - clientOid is deterministic and Bitget-safe
  - a normal submit places exactly one order
  - a timed-out-but-landed order is RECOVERED via clientOid (never resubmitted)
  - a confirmed-absent order re-raises (failure is never silently swallowed)
  - orphan detection flags exchange positions with no local record
"""

import pytest

from bot.core.live_executor import LiveExecutor


def test_client_oid_is_deterministic_and_safe():
    a = LiveExecutor._client_oid("idea-123:BTC/USDT")
    b = LiveExecutor._client_oid("idea-123:BTC/USDT")
    assert a == b                       # deterministic -> dedup works across retries
    assert a.isalnum()                  # Bitget clientOid must be alphanumeric
    assert len(a) <= 32                 # well within Bitget's 64-char limit
    # empty / symbol-only ids still produce a valid key
    assert LiveExecutor._client_oid("").isalnum()


class _FakeExchange:
    def __init__(self, mode):
        self.mode = mode
        self.create_calls = 0

    async def create_order(self, **kw):
        self.create_calls += 1
        if self.mode == "ok":
            return {"id": "O1", "clientOrderId": kw["params"]["clientOid"], "status": "closed"}
        raise TimeoutError("network timeout")

    async def fetch_open_orders(self, symbol):
        if self.mode == "timeout_landed":
            return [{"id": "O9", "clientOrderId": "rcCOID", "info": {"clientOid": "rcCOID"}}]
        return []

    async def fetch_closed_orders(self, symbol):
        return []


@pytest.mark.asyncio
async def test_happy_path_places_once_with_client_oid():
    ex = LiveExecutor()
    fx = _FakeExchange("ok")
    order = await ex._create_order_idempotent(
        fx, symbol="BTC/USDT", type="market", side="buy", amount=1.0, coid="rcCOID"
    )
    assert order["id"] == "O1"
    assert fx.create_calls == 1
    assert order["clientOrderId"] == "rcCOID"   # idempotency key was injected


@pytest.mark.asyncio
async def test_timeout_but_landed_is_recovered_not_resubmitted():
    ex = LiveExecutor()
    fx = _FakeExchange("timeout_landed")
    order = await ex._create_order_idempotent(
        fx, symbol="BTC/USDT", type="market", side="buy", amount=1.0, coid="rcCOID"
    )
    assert order["id"] == "O9"      # recovered the order that actually landed
    assert fx.create_calls == 1     # CRITICAL: never double-submitted


@pytest.mark.asyncio
async def test_confirmed_absent_order_reraises():
    ex = LiveExecutor()
    fx = _FakeExchange("timeout_gone")  # create raises, lookups return nothing
    with pytest.raises(TimeoutError):
        await ex._create_order_idempotent(
            fx, symbol="BTC/USDT", type="market", side="buy", amount=1.0, coid="rcCOID"
        )


@pytest.mark.asyncio
async def test_orphan_detection_flags_untracked_positions(monkeypatch):
    from bot.config import CONFIG
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)

    class _Fx:
        async def fetch_positions(self, params=None):  # current code passes params=
            return [
                {"symbol": "ETH/USDT:USDT", "contracts": 2.0},  # untracked -> orphan
                {"symbol": "BTC/USDT:USDT", "contracts": 0},    # flat -> ignored
            ]

    ex = LiveExecutor()
    ex._exchange = _Fx()

    async def _get():
        return ex._exchange
    monkeypatch.setattr(ex, "_get_exchange", _get)

    report = await ex.detect_untracked_positions()
    # normalize_symbol() returns the base ("ETH") for "ETH/USDT:USDT".
    assert report["untracked"] == ["ETH"]


def test_exchange_min_amount_and_notional_validation():
    market = {"base": "BTC", "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}}}
    # below min amount -> blocked
    assert "below exchange minimum" in (LiveExecutor._validate_order_limits(market, 0.0001, 100.0) or "")
    # below min notional -> blocked
    assert "below exchange minimum" in (LiveExecutor._validate_order_limits(market, 1.0, 2.0) or "")
    # valid order -> allowed
    assert LiveExecutor._validate_order_limits(market, 0.01, 50.0) is None
    # missing market data -> never block on absent filters
    assert LiveExecutor._validate_order_limits(None, 0.0, 0.0) is None


def test_tick_grid_price_rounding_and_fallback():
    class _Ex:
        def price_to_precision(self, symbol, price):
            return f"{price:.2f}"          # pretend tick size = 0.01

    assert LiveExecutor._round_price_to_market(_Ex(), "BTC/USDT", 12345.6789) == "12345.68"

    class _ExBad:
        def price_to_precision(self, *a):
            raise RuntimeError("market data unavailable")

    # graceful fallback -> None lets the caller use its heuristic
    assert LiveExecutor._round_price_to_market(_ExBad(), "BTC/USDT", 1.0) is None
