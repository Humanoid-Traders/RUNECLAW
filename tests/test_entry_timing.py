"""
Entry-timing engine (degree-nested confirmation), stage 1 (2026-07-13).

Live diagnosis: the one measured bleed is stop-outs (SL bucket -$264 vs
TP +$167) — entries fire the moment a signal qualifies, i.e. EARLY in a
pullback-driven system. A qualified idea now ARMS instead of executing
(ENTRY_TIMING_ENABLED, default OFF pending the frozen-benchmark A/B):
it fires only when the sub-degree confirms the turn (confirmed ZigZag
pullback pivot + trigger candle), disarms silently when its own stop is
touched first, or when the window expires.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from bot.core.entry_timing import (DISARM_EXPIRED, DISARM_INVALIDATED, FIRE,
                                   WAIT, evaluate_armed, invalidated,
                                   subdegree_turn_confirmed)

UTC = timezone.utc


def _pullback_then_turn(trigger=True):
    """20 bars down (the pullback), 5 bars up (the turn). With trigger=True
    the last closed bar is a green bar closing above the prior bar's high;
    otherwise a small inside bar (structure turned, no trigger yet)."""
    opens, highs, lows, closes = [], [], [], []
    px = 120.0
    for _ in range(20):                    # pullback leg
        px -= 1.0
        opens.append(px + 0.5)
        closes.append(px)
        highs.append(px + 1.0)
        lows.append(px - 0.5)
    for j in range(5):                     # reversal leg
        px += 1.5
        opens.append(px - 1.2)
        closes.append(px)
        highs.append(px + 0.3)
        lows.append(px - 1.5)
    if not trigger:                        # replace last bar with inside bar
        closes[-1] = closes[-2] + 0.05
        opens[-1] = closes[-2] + 0.1       # red-ish, below prior high
        highs[-1] = highs[-2] - 0.1
        lows[-1] = closes[-2] - 0.2
    return opens, highs, lows, closes


def _mirror(series):
    o, h, lo, c = series
    m = max(h) + min(lo)
    return ([m - x for x in o], [m - x for x in lo],
            [m - x for x in h], [m - x for x in c])


# ── confirmation doctrine ────────────────────────────────────────────
def test_long_confirms_after_pullback_low_plus_trigger():
    o, h, lo, c = _pullback_then_turn(trigger=True)
    ok, reason = subdegree_turn_confirmed("LONG", h, lo, c, opens=o)
    assert ok, reason


def test_long_waits_without_trigger_bar():
    o, h, lo, c = _pullback_then_turn(trigger=False)
    ok, reason = subdegree_turn_confirmed("LONG", h, lo, c, opens=o)
    assert not ok
    assert "trigger" in reason or "pullback" in reason


def test_short_mirror_confirms():
    o, h, lo, c = _mirror(_pullback_then_turn(trigger=True))
    ok, reason = subdegree_turn_confirmed("SHORT", h, lo, c, opens=o)
    assert ok, reason


def test_wrong_direction_does_not_confirm():
    """A completed pullback-low turn confirms a LONG, never a SHORT."""
    o, h, lo, c = _pullback_then_turn(trigger=True)
    ok, _ = subdegree_turn_confirmed("SHORT", h, lo, c, opens=o)
    assert not ok


def test_thin_or_flat_series_never_confirms():
    flat = [100.0] * 20
    assert subdegree_turn_confirmed("LONG", flat, flat, flat)[0] is False
    assert subdegree_turn_confirmed("LONG", [1, 2], [1, 2], [1, 2])[0] is False


# ── evaluate_armed precedence ────────────────────────────────────────
def test_invalidation_beats_confirmation():
    """Even on a confirming bar, if the bar also touched the would-be stop
    the setup disarms — the trade was wrong before it existed."""
    o, h, lo, c = _pullback_then_turn(trigger=True)
    verdict, _ = evaluate_armed(
        "LONG", stop_loss=lo[-1] + 0.01, armed_ts=0, now_ts=60,
        max_wait_sec=14400, bar_high=h[-1], bar_low=lo[-1],
        highs=h, lows=lo, closes=c, opens=o)
    assert verdict == DISARM_INVALIDATED


def test_expiry_beats_confirmation():
    o, h, lo, c = _pullback_then_turn(trigger=True)
    verdict, _ = evaluate_armed(
        "LONG", stop_loss=50.0, armed_ts=0, now_ts=20000,
        max_wait_sec=14400, bar_high=h[-1], bar_low=lo[-1],
        highs=h, lows=lo, closes=c, opens=o)
    assert verdict == DISARM_EXPIRED


def test_fire_and_wait_paths():
    o, h, lo, c = _pullback_then_turn(trigger=True)
    verdict, _ = evaluate_armed(
        "LONG", stop_loss=50.0, armed_ts=0, now_ts=60,
        max_wait_sec=14400, bar_high=h[-1], bar_low=lo[-1],
        highs=h, lows=lo, closes=c, opens=o)
    assert verdict == FIRE
    o, h, lo, c = _pullback_then_turn(trigger=False)
    verdict, _ = evaluate_armed(
        "LONG", stop_loss=50.0, armed_ts=0, now_ts=60,
        max_wait_sec=14400, bar_high=h[-1], bar_low=lo[-1],
        highs=h, lows=lo, closes=c, opens=o)
    assert verdict == WAIT


def test_invalidated_geometry():
    assert invalidated("LONG", 95.0, bar_high=101.0, bar_low=94.9) is True
    assert invalidated("LONG", 95.0, bar_high=101.0, bar_low=95.1) is False
    assert invalidated("SHORT", 105.0, bar_high=105.2, bar_low=99.0) is True
    assert invalidated("SHORT", 0.0, bar_high=999.0, bar_low=0.1) is False


# ── backtest wiring ──────────────────────────────────────────────────
def _bt_engine():
    from bot.backtest.engine import BacktestEngine
    eng = BacktestEngine.__new__(BacktestEngine)
    eng._armed_setups = []
    eng._et_bar_win = []
    eng._et_armed = eng._et_fired = 0
    eng._et_disarmed_invalidated = eng._et_disarmed_expired = 0
    eng.risk = MagicMock()
    eng._execute_fill = MagicMock()
    return eng


def _bar(o, h, lo, c, ts_h=0):
    return SimpleNamespace(open=o, high=h, low=lo, close=c, volume=1000.0,
                           timestamp=datetime.fromtimestamp(
                               1_752_000_000 + ts_h * 3600, tz=UTC))


def _idea(direction="LONG", sl=50.0):
    return SimpleNamespace(id="TI-et1", asset="BTC/USDT",
                           direction=SimpleNamespace(value=direction),
                           stop_loss=sl)


def test_backtest_fire_executes_at_confirming_close():
    eng = _bt_engine()
    o, h, lo, c = _pullback_then_turn(trigger=True)
    eng._et_bar_win = list(zip(o, h, lo, c))
    eng._armed_setups = [{"idea": _idea(), "risk_check": MagicMock(),
                          "armed_ts": 1_752_000_000}]
    eng._evaluate_armed_setups(_bar(o[-1], h[-1], lo[-1], c[-1], ts_h=1))
    eng._execute_fill.assert_called_once()
    assert eng._et_fired == 1 and eng._armed_setups == []


def test_backtest_invalidation_clears_intent_without_fill():
    eng = _bt_engine()
    o, h, lo, c = _pullback_then_turn(trigger=False)
    eng._et_bar_win = list(zip(o, h, lo, c))
    eng._armed_setups = [{"idea": _idea(sl=lo[-1] + 0.01),
                          "risk_check": MagicMock(),
                          "armed_ts": 1_752_000_000}]
    eng._evaluate_armed_setups(_bar(o[-1], h[-1], lo[-1], c[-1], ts_h=1))
    eng._execute_fill.assert_not_called()
    eng.risk.clear_pending_intent.assert_called_once_with("TI-et1")
    assert eng._et_disarmed_invalidated == 1 and eng._armed_setups == []


def test_backtest_wait_keeps_setup_armed():
    eng = _bt_engine()
    o, h, lo, c = _pullback_then_turn(trigger=False)
    eng._et_bar_win = list(zip(o, h, lo, c))
    eng._armed_setups = [{"idea": _idea(), "risk_check": MagicMock(),
                          "armed_ts": 1_752_000_000}]
    eng._evaluate_armed_setups(_bar(o[-1], h[-1], lo[-1], c[-1], ts_h=1))
    eng._execute_fill.assert_not_called()
    assert len(eng._armed_setups) == 1


def test_engine_wiring_pins():
    from bot.backtest.engine import BacktestEngine
    src = inspect.getsource(BacktestEngine._process_bar)
    # Round 4: the arming condition moved from the raw flag read to
    # timing_active(), which honors both the global flag AND the
    # regime-conditional set (ENTRY_TIMING_REGIMES).
    assert "timing_active" in src and "_armed_setups.append" in src
    src2 = inspect.getsource(BacktestEngine._check_stops_intrabar)
    assert "_evaluate_armed_setups" in src2
    assert "_et_bar_win" in src2


def test_flag_defaults_off():
    from bot.config import CONFIG
    assert CONFIG.execution.entry_timing_enabled is False
    assert CONFIG.execution.entry_timing_max_wait_sec > 0
