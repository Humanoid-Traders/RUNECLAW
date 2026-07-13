"""
Ladder↔trailing parity fix (2026-07-13) — found by the wave-trail A/B.

LIVE runs the multistage trail + structure/wave ratchets on EVERY open
position (check_positions), with the partial-TP ladder as an additive
overlay. The backtest's ladder branch `continue`d before
update_trailing_stop ever ran — instrumentation counted ZERO ratchet
calls across a full 33-trade portfolio replay — so live tightened to
breakeven at 1R while replay sat on the original stop until TP1 (1.5R),
and no trailing change was measurable in any ladder-on backtest.

_apply_ladder_trailing now overlays the tighten-only trailing/ratchet
updates on the ladder stop each bar (BACKTEST_LADDER_TRAILING, default
ON; OFF reproduces the old replay exactly).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

from bot.backtest.engine import BacktestEngine
from bot.utils.models import Direction


def _bar(o, h, l, c, ts=0):
    return SimpleNamespace(open=o, high=h, low=l, close=c,
                           timestamp=datetime.fromtimestamp(1_752_000_000 + ts * 3600,
                                                            tz=timezone.utc),
                           volume=1000.0)


def _ladder_meta(entry=100.0, sl=95.0, atr=2.0):
    """bt_meta shaped like _execute_fill builds it: trailing state spread in
    + a live partial-TP state object."""
    from bot.core.partial_tp import create_partial_tp_state
    from bot.utils.trailing import make_trailing_state
    meta = dict(make_trailing_state(entry, "LONG", entry - sl, atr))
    meta["entry_price"] = entry
    meta["ptp_state"] = create_partial_tp_state(
        trade_id="T1", direction="LONG", entry_price=entry,
        stop_loss=sl, take_profit=115.0, quantity=1.0, atr=atr)
    return meta


class _Pos:
    direction = Direction.LONG

    def __init__(self, sl):
        self.stop_loss = sl


def test_overlay_tightens_ladder_stop_before_tp1():
    """Price runs to 1.2R (below the 1.5R TP1): the ladder alone would keep
    the ORIGINAL stop, but live's multistage trail activates at 1R and
    floors the stop at breakeven. The overlay must reproduce that."""
    eng = BacktestEngine.__new__(BacktestEngine)
    meta = _ladder_meta(entry=100.0, sl=95.0, atr=2.0)   # 1R = 5.0
    pos = _Pos(95.0)
    # bar reaching 1.2R favorable (high=106), no adverse breach
    eng._apply_ladder_trailing(meta, pos, _bar(101.0, 106.0, 100.5, 105.0))
    state = meta["ptp_state"]
    assert state.current_sl >= 100.0          # >= breakeven (stage-1 floor)
    assert pos.stop_loss == state.current_sl
    assert not state.tp1_hit                  # the ladder itself untouched


def test_overlay_never_loosens_a_ladder_move():
    """After TP2 the ladder locks 1R of profit; a WIDE trailing candidate
    (far ATR distance) must not loosen that lock."""
    eng = BacktestEngine.__new__(BacktestEngine)
    meta = _ladder_meta(entry=100.0, sl=95.0, atr=50.0)  # huge ATR -> loose trail
    state = meta["ptp_state"]
    state.tp1_hit = True
    state.tp2_hit = True
    state.current_sl = 105.0                  # ladder locked +1R
    pos = _Pos(105.0)
    eng._apply_ladder_trailing(meta, pos, _bar(106.0, 108.0, 105.5, 107.0))
    assert state.current_sl >= 105.0          # lock preserved


def test_below_1r_overlay_is_a_noop():
    """No trailing activation below 1R: stop stays exactly the original."""
    eng = BacktestEngine.__new__(BacktestEngine)
    meta = _ladder_meta(entry=100.0, sl=95.0, atr=2.0)
    pos = _Pos(95.0)
    eng._apply_ladder_trailing(meta, pos, _bar(100.5, 102.0, 100.0, 101.5))
    assert meta["ptp_state"].current_sl == 95.0
    assert pos.stop_loss == 95.0


# ── wiring pins ──────────────────────────────────────────────────────
def test_ladder_branch_wires_the_overlay():
    src = inspect.getsource(BacktestEngine._check_stops_intrabar)
    assert "_apply_ladder_trailing" in src
    assert "_ladder_trailing_enabled" in src
    # struct_win is built BEFORE the ladder branch so the ratchets see it
    assert src.index("struct_win") < src.index("_check_ladder_intrabar")


def test_flag_defaults_on():
    src = inspect.getsource(BacktestEngine.__init__)
    assert 'BACKTEST_LADDER_TRAILING", True' in src
