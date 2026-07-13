"""
Wave-anchored trailing (2026-07-13) — the structure-trail retune.

The fractal structure ratchet was MEASURED OFF: 3-bar swing wiggles
tightened stops onto noise and cut winners short (~1.3pp / ~17 trades on
the honest benchmark; see TrailingConfig). wave_ratchet keeps the same
tighten-only contract but takes its pivots from the ATR-normalized
ZigZag (the Elliott pivot engine) — a pivot registers only after a
>= atr_mult*ATR reversal, confirmed by construction, so the stop trails
genuine wave lows/highs. Live trails the SUB-DEGREE of the entry
(swing/4h entry -> 1h sub-wave pivots); the backtest applies the same
pivot engine on the run timeframe.
"""

from __future__ import annotations

import inspect

from bot.core.elliott import subdegree_timeframe
from bot.utils.trailing import structure_ratchet, wave_ratchet


def _wavy_series():
    """Uptrend with two structural pullbacks (~5% each) + small noise
    wiggles (~0.5%) that a 3-bar fractal would treat as swings."""
    closes = []
    p = 100.0
    for leg in ((30, +0.7), (6, -0.9), (24, +0.7), (6, -0.9), (12, +0.7)):
        n, step = leg
        for i in range(n):
            p += step
            # noise wiggle every 3rd bar
            closes.append(p + (0.35 if i % 3 == 0 else -0.35))
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return highs, lows, closes


# ── pure ratchet semantics ───────────────────────────────────────────
def test_long_ratchets_to_newest_zigzag_low():
    highs, lows, closes = _wavy_series()
    sl = wave_ratchet(highs, lows, closes, "LONG", current_sl=90.0, buffer=0.5)
    assert sl > 90.0                        # tightened
    # The anchor is a genuine structural low (the last ~5% pullback low),
    # not a noise wiggle near the top of the series.
    assert sl < max(closes)


def test_wave_anchor_is_looser_than_fractal_on_noise():
    """The core hypothesis: on a noisy uptrend the fractal ratchet parks
    the stop at the newest 3-bar wiggle (tight, near price), while the
    ZigZag anchor stays at the last STRUCTURAL pullback low (looser) —
    giving winners room. Verify the ordering."""
    highs, lows, closes = _wavy_series()
    frac = structure_ratchet(highs, lows, "LONG", current_sl=0.0, buffer=0.5)
    wave = wave_ratchet(highs, lows, closes, "LONG", current_sl=0.0, buffer=0.5)
    assert 0.0 < wave <= frac               # wave anchor never tighter


def test_short_mirrors_with_swing_highs():
    highs, lows, closes = _wavy_series()
    # Mirror the series downward for a SHORT
    m = max(closes) + min(closes)
    inv_h = [m - low for low in lows]
    inv_l = [m - high for high in highs]
    inv_c = [m - c for c in closes]
    sl = wave_ratchet(inv_h, inv_l, inv_c, "SHORT",
                      current_sl=m - 90.0 + 10.0 + 100.0, buffer=0.5)
    assert sl < m - 90.0 + 10.0 + 100.0     # tightened downward


def test_tighten_only_never_loosens():
    highs, lows, closes = _wavy_series()
    very_tight = max(closes)                # SL already above every pivot
    assert wave_ratchet(highs, lows, closes, "LONG", very_tight, 0.5) == very_tight


def test_no_pivots_or_bad_input_returns_unchanged():
    flat = [100.0] * 20                     # zero-vol: zigzag finds nothing
    assert wave_ratchet(flat, flat, flat, "LONG", 95.0, 0.5) == 95.0
    assert wave_ratchet([1.0], [1.0], [1.0], "LONG", 95.0, 0.5) == 95.0
    assert wave_ratchet([], [], [], "SHORT", 95.0, 0.5) == 95.0
    # mismatched lengths fail open
    assert wave_ratchet([1.0] * 10, [1.0] * 9, [1.0] * 10, "LONG", 95.0, 0.5) == 95.0


# ── sub-degree map ───────────────────────────────────────────────────
def test_subdegree_is_one_step_below_entry_degree():
    assert subdegree_timeframe("swing") == "1h"      # entered on 4h waves
    assert subdegree_timeframe("position") == "4h"   # entered on 1d waves
    assert subdegree_timeframe("intraday") == "15m"
    assert subdegree_timeframe("scalp") == "5m"
    assert subdegree_timeframe("unknown") == "1h"


# ── wiring pins ──────────────────────────────────────────────────────
def test_live_trailing_block_wired():
    from bot.core.live_executor import LiveExecutor
    src = inspect.getsource(LiveExecutor.check_positions)
    assert "wave_trail_enabled" in src
    assert "subdegree_timeframe" in src              # sub-degree candles
    assert "wave_ratchet" in src
    # struct candles now carry closes for the zigzag
    sc = inspect.getsource(LiveExecutor._struct_candles)
    assert "timeframe" in sc and "c[4]" in sc


def test_backtest_ratchet_wired():
    from bot.backtest.engine import BacktestEngine
    src = inspect.getsource(BacktestEngine._maybe_structure_ratchet)
    assert "wave_trail_enabled" in src
    assert "wave_ratchet" in src
    assert "structure_ratchet" in src                # fractal path preserved


def test_config_flags_exist():
    from bot.config import CONFIG
    assert isinstance(CONFIG.trailing.wave_trail_enabled, bool)
    assert CONFIG.trailing.wave_trail_zigzag_atr_mult > 0
