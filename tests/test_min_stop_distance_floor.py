"""A pathologically-tight ATR stop gets rejected by the venue as a conditional
order (position left UNPROTECTED — the live TAG case) or is tripped instantly by
noise. `_floor_stop_distance` widens |entry - SL| to a minimum placeable fraction
of entry, before sizing, and never tightens.
"""
from bot.config import CONFIG
from bot.core.analyzer import _floor_stop_distance
from bot.utils.models import Direction


def test_default_config_is_a_small_nonzero_floor():
    assert CONFIG.analyzer.min_stop_distance_pct == 0.004


def test_widens_a_too_tight_long_stop():
    # SL only 0.05% below entry -> widen to the 0.4% floor (further below).
    sl = _floor_stop_distance(100.0, 99.95, Direction.LONG, 0.004)
    assert abs(sl - 99.6) < 1e-9  # 100 * (1 - 0.004)


def test_widens_a_too_tight_short_stop():
    # SHORT stop is ABOVE entry; widen upward to the floor.
    sl = _floor_stop_distance(100.0, 100.05, Direction.SHORT, 0.004)
    assert abs(sl - 100.4) < 1e-9  # 100 * (1 + 0.004)


def test_leaves_a_wide_enough_stop_untouched():
    # A normal 2% ATR stop is well beyond the 0.4% floor — unchanged.
    assert _floor_stop_distance(100.0, 98.0, Direction.LONG, 0.004) == 98.0
    assert _floor_stop_distance(100.0, 102.0, Direction.SHORT, 0.004) == 102.0


def test_never_tightens():
    # Even a stop exactly at the floor is not moved inward.
    assert _floor_stop_distance(100.0, 99.6, Direction.LONG, 0.004) == 99.6


def test_disabled_when_zero_or_bad_entry():
    assert _floor_stop_distance(100.0, 99.99, Direction.LONG, 0.0) == 99.99
    assert _floor_stop_distance(0.0, 99.99, Direction.LONG, 0.004) == 99.99


def test_low_priced_asset_floor_engages():
    # The TAG-class case: ~0.0009 asset, an SL only a couple ticks from entry.
    # 0.4% of 0.0009 = 0.0000036, so the stop is pushed to a placeable distance.
    sl = _floor_stop_distance(0.0009, 0.0008999, Direction.LONG, 0.004)
    assert sl < 0.0008999
    assert abs((0.0009 - sl) / 0.0009 - 0.004) < 1e-6
