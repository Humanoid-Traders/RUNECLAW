"""
Funding carry-cost awareness — the dimension the existing funding signals miss.

RUNECLAW already reads the *directional* funding signal three ways: a contrarian
``of_funding`` confluence voter (order_flow), a ``funding_arb`` confidence nudge
(analyzer), and smart-money cascade/squeeze risk. All three look at the
**instantaneous** funding rate and its direction. None of them account for the
**carry cost over the holding period** — and that is what actually erodes net
edge: a scalp pays ~no funding, but a multi-day swing pays it every interval.

This module estimates the funding a trade would PAY over its expected hold (only
on the side that pays — the crowded side) and turns an adverse, material cost into
a small **bounded confidence haircut**. It only ever REDUCES confidence (carry is
a drag); favourable funding is already rewarded by ``funding_arb`` so we don't
double-count it. Gated (default OFF); pure math, no I/O, no orders.
"""

from __future__ import annotations

# Bitget USDT-M perpetual funding settles every 8 hours.
_FUNDING_INTERVAL_HOURS = 8.0

# Expected holding time (hours) per strategy type → funding intervals paid.
_HOLD_HOURS = {
    "scalp": 2.0,       # < 1 interval — funding ~ irrelevant
    "intraday": 8.0,    # ~1 interval
    "swing": 48.0,      # ~6 intervals
    "position": 120.0,  # ~15 intervals
}
_DEFAULT_HOLD_HOURS = 24.0

_MAX_HAIRCUT = 0.05     # cap on the confidence reduction
_GAIN = 4.0             # cost%(decimal) → haircut scaling before the cap
_MIN_RATE = 1e-9


def expected_intervals(strategy_type: str) -> float:
    """Funding intervals paid over the expected hold for this strategy type."""
    hours = _HOLD_HOURS.get(str(strategy_type or "").lower(), _DEFAULT_HOLD_HOURS)
    return max(0.0, hours / _FUNDING_INTERVAL_HOURS)


def adverse_funding_cost(funding_rate, direction: str, strategy_type: str) -> float:
    """Fraction of notional the trade would PAY in funding over its hold (>= 0).

    You pay funding only on the crowded side: positive funding → longs pay; negative
    funding → shorts pay. Earning funding (the other side) returns 0.0 here — this
    function measures cost only.
    """
    if funding_rate is None:
        return 0.0
    fr = float(funding_rate)
    d = str(direction or "").strip().upper()
    if abs(fr) < _MIN_RATE or d not in ("LONG", "SHORT"):
        return 0.0
    pays = (d == "LONG" and fr > 0) or (d == "SHORT" and fr < 0)
    if not pays:
        return 0.0
    return abs(fr) * expected_intervals(strategy_type)


def funding_cost_haircut(funding_rate, direction: str, strategy_type: str,
                         max_haircut: float = _MAX_HAIRCUT, gain: float = _GAIN) -> float:
    """Bounded, non-positive confidence adjustment for adverse carry cost.

    Returns a value in ``[-max_haircut, 0]``: 0 when funding is favourable / mild /
    the hold is short, scaling toward ``-max_haircut`` as the expected paid funding
    over the hold grows. Never positive (carry is only ever a drag here).
    """
    cost = adverse_funding_cost(funding_rate, direction, strategy_type)
    if cost <= 0.0:
        return 0.0
    return -min(max(0.0, max_haircut), cost * gain)


def describe(funding_rate, direction: str, strategy_type: str) -> str:
    """Short human-readable note for reasoning/audit (never raises)."""
    cost = adverse_funding_cost(funding_rate, direction, strategy_type)
    if cost <= 0.0:
        return ""
    return (f"Adverse funding carry ~{cost:.4%} over ~{expected_intervals(strategy_type):.0f} "
            f"intervals ({strategy_type}) — confidence shaded")
