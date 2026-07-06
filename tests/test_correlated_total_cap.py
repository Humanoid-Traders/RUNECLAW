"""
Round 7 revised Phase 2 — global same-direction correlated-exposure cap.

Per-group caps don't bound TOTAL correlated exposure (each group has its own
budget), so a market-wide move can stack many same-direction correlated bets.
max_correlated_same_dir_positions caps concurrent same-direction positions
across ALL correlated groups. Gated: only active when the perp mapping is
enabled AND the cap is > 0.
"""

import threading
from types import SimpleNamespace
from unittest.mock import patch

from bot.risk.risk_engine import RiskEngine


def _engine(open_positions=()):
    eng = RiskEngine.__new__(RiskEngine)
    eng._portfolio = SimpleNamespace(
        open_positions=list(open_positions),
        _positions={f"t{i}": p for i, p in enumerate(open_positions)},
    )
    eng._price_history = {}
    eng._pending_intents = {}
    eng._lock = threading.RLock()
    eng._sim_now = None
    return eng


def _pos(asset, direction="LONG"):
    return SimpleNamespace(asset=asset, direction=SimpleNamespace(value=direction))


def _idea(asset, direction="LONG", tid="TI-new"):
    return SimpleNamespace(id=tid, asset=asset,
                           direction=SimpleNamespace(value=direction))


def _cfg(mapping=True, total_cap=2, per_group=2):
    cfg = patch("bot.risk.risk_engine.CONFIG")
    m = cfg.start()
    m.risk.correlation_perp_group_mapping_enabled = mapping
    m.risk.max_correlated_same_dir_positions = total_cap
    m.risk.max_correlation_per_group = per_group
    m.risk.max_unmapped_correlated = 3
    m.risk.max_correlation = 0.85
    m.risk.correlation_forward_intents_enabled = False
    return cfg


# Two open LONGs in DIFFERENT groups (so the per-group cap doesn't fire first).
_TWO_LONGS = [_pos("SOL/USDT:USDT", "LONG"), _pos("AAVE/USDT:USDT", "LONG")]


def test_global_cap_blocks_third_same_direction():
    eng = _engine(_TWO_LONGS)  # ALT_L1 long + DEFI long
    cfg = _cfg(mapping=True, total_cap=2)
    try:
        # DOGE is MEME (per-group count 0), but it's the 3rd concurrent LONG.
        reason = eng._check_correlation(_idea("DOGE/USDT:USDT", "LONG"))
        assert reason is not None and "CORRELATION_TOTAL" in reason
    finally:
        cfg.stop()


def test_opposite_direction_not_counted():
    eng = _engine(_TWO_LONGS)
    cfg = _cfg(mapping=True, total_cap=2)
    try:
        # A SHORT is a different directional bet — not blocked by the LONG count.
        assert eng._check_correlation(_idea("DOGE/USDT:USDT", "SHORT")) is None
    finally:
        cfg.stop()


def test_cap_zero_disables():
    eng = _engine(_TWO_LONGS)
    cfg = _cfg(mapping=True, total_cap=0)  # disabled
    try:
        assert eng._check_correlation(_idea("DOGE/USDT:USDT", "LONG")) is None
    finally:
        cfg.stop()


def test_inert_when_mapping_disabled():
    # The global cap only applies alongside the corrected per-group mapping.
    eng = _engine(_TWO_LONGS)
    cfg = _cfg(mapping=False, total_cap=2)
    try:
        assert eng._check_correlation(_idea("DOGE/USDT:USDT", "LONG")) is None
    finally:
        cfg.stop()


def test_under_cap_allowed():
    eng = _engine([_pos("SOL/USDT:USDT", "LONG")])  # only 1 concurrent LONG
    cfg = _cfg(mapping=True, total_cap=2)
    try:
        assert eng._check_correlation(_idea("DOGE/USDT:USDT", "LONG")) is None
    finally:
        cfg.stop()
