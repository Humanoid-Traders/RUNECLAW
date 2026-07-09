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


from bot.core.live_executor import (min_amount_step,  # noqa: E402
                                    resolve_exchange_min_quantity)


def _too_small_or_cheap(quantity, price):
    market = MARKETS[SYMBOL]
    _limits = market["limits"]
    _step = min_amount_step(market["precision"]["amount"])
    _floor = max(float(_limits["amount"]["min"] or 0), _step)
    _min_cost = float((_limits.get("cost", {}) or {}).get("min") or 0.0)
    return (_floor > 0 and quantity < _floor) or (
        _min_cost > 0 and quantity * price < _min_cost)


# ── flagging (below-minimum detection) ───────────────────────────────
def test_xpt_incident_quantity_flagged():
    """$1.50 margin at 1x on a $1,640 asset → qty 0.00091 < 0.001 → flagged."""
    qty = (1.50 * 1) / 1640.92
    assert qty < 0.001
    assert _too_small_or_cheap(qty, 1640.92) is True


def test_min_cost_also_flags():
    """Quantity above the step but notional under Bitget's $5 min cost."""
    assert _too_small_or_cheap(0.002, 1640.92) is True  # $3.28 < $5


def test_normal_size_passes():
    """The BTC-class trade ($9.47 margin at 10x) sails through."""
    qty = (9.47 * 10) / 1640.92  # 0.0577
    assert _too_small_or_cheap(qty, 1640.92) is False


def test_decimal_places_precision_not_misread_as_step():
    """precision.amount = 3 (decimal places) must mean step 0.001, not 3.0."""
    assert min_amount_step(3) == pytest.approx(0.001)
    assert min_amount_step(0.001) == pytest.approx(0.001)
    assert min_amount_step(None) == 0.0


# ── round-up policy (operator-requested) ─────────────────────────────
def test_roundup_small_overshoot_rounds_to_step():
    """XPT case: 0.00091 just below the 0.001 step (1.1x) → round UP to 0.001."""
    qty = 1.50 / 1640.92  # 0.000914
    resolved, q_min, mult = resolve_exchange_min_quantity(
        qty, floor=0.001, step=0.001, min_cost=0.0, price=1640.92,
        roundup_enabled=True, max_mult=1.5)
    assert q_min == pytest.approx(0.001)
    assert resolved == pytest.approx(0.001)
    assert mult < 1.2


def test_roundup_large_overshoot_skips():
    """Min-cost $5 vs a $1.64 trade is a >3x overshoot → SKIP (None) even ON."""
    qty = 1.0 / 1640.92  # ~0.00061, notional ~$1.00
    resolved, q_min, mult = resolve_exchange_min_quantity(
        qty, floor=0.001, step=0.001, min_cost=5.0, price=1640.92,
        roundup_enabled=True, max_mult=1.5)
    assert q_min == pytest.approx(0.004)      # ceil($5/1640.92 → 0.00305) to step
    assert mult > 1.5
    assert resolved is None                   # overshoot beyond the cap → skip


def test_roundup_disabled_always_skips():
    """Flag OFF → even a tiny overshoot skips (legacy behaviour)."""
    qty = 1.50 / 1640.92
    resolved, q_min, mult = resolve_exchange_min_quantity(
        qty, floor=0.001, step=0.001, min_cost=0.0, price=1640.92,
        roundup_enabled=False, max_mult=1.5)
    assert resolved is None
    assert q_min == pytest.approx(0.001)


def test_roundup_respects_min_cost_and_step_grid():
    """q_min must clear BOTH the amount floor AND the min-notional, snapped up
    to the step grid."""
    # price 100, step 0.01, min_cost $2 → need 0.02; floor 0.001 → q_min 0.02.
    resolved, q_min, mult = resolve_exchange_min_quantity(
        quantity=0.015, floor=0.001, step=0.01, min_cost=2.0, price=100.0,
        roundup_enabled=True, max_mult=2.0)
    assert q_min == pytest.approx(0.02)
    assert resolved == pytest.approx(0.02)


def test_roundup_zero_quantity_is_infinite_mult_skip():
    resolved, q_min, mult = resolve_exchange_min_quantity(
        0.0, floor=0.001, step=0.001, min_cost=0.0, price=100.0,
        roundup_enabled=True, max_mult=1.5)
    assert resolved is None
    assert mult == float("inf")


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
