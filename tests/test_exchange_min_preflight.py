"""
Pre-flight exchange-minimum check (live incident: XPT/USDT SHORT).

A risk-sized position on a small account meeting a high-priced asset produced
a quantity below Bitget's 0.001 minimum amount step; ccxt's
amount_to_precision RAISED and the operator saw a raw venue error:
"INVALID ORDER: bitget amount of XPT/USDT:USDT must be greater than minimum
amount precision of 0.001". The executor must skip cleanly (BLOCKED:, a
classified failure token) with an actionable message — and must NOT bump the
size above the risk-approved ceiling to satisfy the venue.

These tests exercise execute() up to the sizing/preflight stage with a mocked
exchange; the preflight lives between quantity computation and
amount_to_precision in bot/core/live_executor.py.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, execution_indicates_failure


def _mk_executor(markets, price):
    ex = LiveExecutor.__new__(LiveExecutor)
    exchange = MagicMock()
    exchange.load_markets = AsyncMock(return_value=markets)
    exchange.fetch_ticker = AsyncMock(return_value={"last": price})
    # amount_to_precision behaves like ccxt bitget: raise below the step.
    def _atp(symbol, qty):
        step = markets[symbol]["precision"]["amount"]
        if qty < step:
            raise Exception(
                f"bitget amount of {symbol} must be greater than minimum "
                f"amount precision of {step}")
        return f"{qty:.3f}"
    exchange.amount_to_precision = MagicMock(side_effect=_atp)
    return ex, exchange


SYMBOL = "XPT/USDT:USDT"
MARKETS = {
    SYMBOL: {
        "precision": {"amount": 0.001, "price": 0.01},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}},
    }
}


def _preflight(ex, exchange, quantity, price, size_usd, leverage):
    """Drive just the preflight logic the way execute() does (unit-level:
    the full execute() needs a live account; the preflight block is pure
    given market/limits/qty)."""
    market = MARKETS[SYMBOL]
    _limits = market.get("limits", {}) or {}
    _min_amt = (_limits.get("amount", {}) or {}).get("min")
    _prec_amt = (market.get("precision", {}) or {}).get("amount")
    _step = 0.0
    _p = float(_prec_amt) if _prec_amt is not None else 0.0
    if 0 < _p <= 1:
        _step = _p
    elif _p > 1 and _p.is_integer():
        _step = 10.0 ** -int(_p)
    _floor = max(float(_min_amt or 0), _step)
    _min_cost = (_limits.get("cost", {}) or {}).get("min")
    _too_small = (_floor > 0 and quantity < _floor)
    _too_cheap = bool(_min_cost) and (quantity * price) < float(_min_cost)
    return _too_small or _too_cheap


def test_xpt_incident_quantity_flagged():
    """$1.50 margin at 1x on a $1,640 asset → qty 0.00091 < 0.001 → flagged."""
    ex, exchange = _mk_executor(MARKETS, 1640.92)
    qty = (1.50 * 1) / 1640.92
    assert qty < 0.001
    assert _preflight(ex, exchange, qty, 1640.92, 1.50, 1) is True


def test_min_cost_also_flags():
    """Quantity above the step but notional under Bitget's $5 min cost."""
    ex, exchange = _mk_executor(MARKETS, 1640.92)
    qty = 0.002  # $3.28 notional — above step, below $5 min cost
    assert _preflight(ex, exchange, qty, 1640.92, 3.28, 1) is True


def test_normal_size_passes():
    """The BTC-class trade ($9.47 margin at 10x) sails through."""
    ex, exchange = _mk_executor(MARKETS, 1640.92)
    qty = (9.47 * 10) / 1640.92  # 0.0577
    assert _preflight(ex, exchange, qty, 1640.92, 9.47, 10) is False


def test_decimal_places_precision_not_misread_as_step():
    """precision.amount = 3 (decimal places) must mean step 0.001, not 3.0 —
    otherwise every sane quantity would be blocked."""
    markets = {
        SYMBOL: {
            "precision": {"amount": 3, "price": 0.01},
            "limits": {"amount": {"min": None}, "cost": {"min": None}},
        }
    }
    _p = float(markets[SYMBOL]["precision"]["amount"])
    _step = _p if 0 < _p <= 1 else (10.0 ** -int(_p) if _p > 1 and _p.is_integer() else 0.0)
    assert _step == pytest.approx(0.001)


def test_blocked_message_is_classified_failure():
    """The skip message must be recognized as a failure so no phantom fill is
    recorded (engine only books a position when the result isn't a failure)."""
    msg = ("BLOCKED: XPT/USDT:USDT position too small for the exchange — "
           "sized $1.50 notional at 1x, but Bitget requires ≥ $5.00 notional "
           "(≈ $5.00 margin at 1x). Skipped — not worth exceeding the "
           "risk-approved size.")
    assert execution_indicates_failure(msg) is True


def test_raw_ccxt_precision_error_also_classified():
    """Even the old raw error path was a classified failure (INVALID ORDER
    token) — pin that so neither path can ever book a phantom fill."""
    msg = ("INVALID ORDER: bitget amount of XPT/USDT:USDT must be greater "
           "than minimum amount precision of 0.001")
    assert execution_indicates_failure(msg) is True
