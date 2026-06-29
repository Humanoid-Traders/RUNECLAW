"""
Funding carry-cost awareness.

The directional funding signal already exists elsewhere; this covers the *new*
dimension — carry cost over the holding period — and its bounded, never-positive
confidence haircut.
"""

from bot.core.funding import (
    expected_intervals, adverse_funding_cost, funding_cost_haircut, describe,
)


def test_expected_intervals_scale_with_strategy():
    # 8h funding interval: scalp << intraday << swing << position.
    assert expected_intervals("scalp") < expected_intervals("intraday")
    assert expected_intervals("intraday") < expected_intervals("swing")
    assert expected_intervals("swing") < expected_intervals("position")
    assert expected_intervals("intraday") == 1.0       # 8h / 8h
    assert expected_intervals("unknown") == 24.0 / 8.0  # default hold


def test_you_pay_on_the_crowded_side():
    # Positive funding -> longs pay, shorts earn.
    assert adverse_funding_cost(0.001, "LONG", "swing") > 0
    assert adverse_funding_cost(0.001, "SHORT", "swing") == 0.0
    # Negative funding -> shorts pay, longs earn.
    assert adverse_funding_cost(-0.001, "SHORT", "swing") > 0
    assert adverse_funding_cost(-0.001, "LONG", "swing") == 0.0


def test_cost_scales_with_hold_length():
    scalp = adverse_funding_cost(0.001, "LONG", "scalp")
    swing = adverse_funding_cost(0.001, "LONG", "swing")
    assert swing > scalp >= 0
    # cost = |rate| * intervals
    assert swing == 0.001 * expected_intervals("swing")


def test_haircut_is_nonpositive_and_bounded():
    # Favourable / mild funding -> no haircut.
    assert funding_cost_haircut(0.001, "SHORT", "swing") == 0.0     # short earns
    assert funding_cost_haircut(None, "LONG", "swing") == 0.0
    assert funding_cost_haircut(0.0, "LONG", "swing") == 0.0
    # Adverse, large funding over a long hold -> bounded haircut.
    big = funding_cost_haircut(0.05, "LONG", "position")            # extreme + long hold
    assert -0.05 <= big < 0.0
    assert big == -0.05                                             # hits the cap
    # A small adverse cost produces a small (capped) haircut.
    small = funding_cost_haircut(0.0002, "LONG", "intraday")
    assert -0.05 <= small <= 0.0


def test_haircut_monotonic_in_cost():
    a = funding_cost_haircut(0.0005, "LONG", "intraday")
    b = funding_cost_haircut(0.0015, "LONG", "intraday")
    # More adverse funding -> haircut at least as negative (more reduction).
    assert b <= a <= 0.0


def test_describe():
    assert describe(0.001, "SHORT", "swing") == ""        # earning -> no note
    note = describe(0.001, "LONG", "swing")
    assert "Adverse funding" in note and "%" in note


def test_invalid_direction_is_safe():
    assert adverse_funding_cost(0.001, "", "swing") == 0.0
    assert funding_cost_haircut(0.001, "auto", "swing") == 0.0
