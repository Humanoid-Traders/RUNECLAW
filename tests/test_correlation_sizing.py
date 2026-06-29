"""
Portfolio-aware correlation sizing (Tier 3b).

The existing _check_correlation is a count-cap CONCENTRATION GATE: it rejects
once a correlation group is full but admits every trade below the cap at FULL
size. _correlation_size_factor adds a graduated size REDUCTION for a new trade
that stacks on already-open positions in the SAME correlation group AND the SAME
direction — the marginal portfolio risk of each additional correlated, same-side
bet is larger, so it is sized down. It can only SHRINK (multiplier in
[floor, 1.0]) and fails open (1.0) — it can never block a trade.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.risk.risk_engine import RiskEngine


def _pos(asset, direction="LONG"):
    return SimpleNamespace(asset=asset, direction=SimpleNamespace(value=direction))


def _idea(asset, direction="LONG"):
    return SimpleNamespace(asset=asset, direction=SimpleNamespace(value=direction))


def _engine(positions):
    eng = RiskEngine.__new__(RiskEngine)
    eng._portfolio = SimpleNamespace(
        open_positions=list(positions),
        _positions={f"t{i}": p for i, p in enumerate(positions)},
    )
    eng._price_history = {}
    return eng


def _cfg(step=0.20, floor=0.5):
    """A CONFIG patch context with the sizing knobs set to known values."""
    cfg = patch("bot.risk.risk_engine.CONFIG")
    mock = cfg.start()
    mock.risk.correlation_sizing_step = step
    mock.risk.correlation_sizing_floor = floor
    return cfg, mock


class TestNoReduction:
    def test_no_open_positions_is_full_size(self):
        eng = _engine([])
        cfg, _ = _cfg()
        try:
            assert eng._correlation_size_factor(_idea("SOL/USDT")) == 1.0
        finally:
            cfg.stop()

    def test_different_group_is_full_size(self):
        # BTC open, ALT_L1 idea — different groups, no reduction.
        eng = _engine([_pos("BTC/USDT")])
        cfg, _ = _cfg()
        try:
            assert eng._correlation_size_factor(_idea("SOL/USDT")) == 1.0
        finally:
            cfg.stop()

    def test_same_group_opposite_direction_is_full_size(self):
        # SOL long open, NEAR short idea — same group, opposite side: a hedge,
        # not a concentrated directional bet, so no reduction.
        eng = _engine([_pos("SOL/USDT", "LONG")])
        cfg, _ = _cfg()
        try:
            assert eng._correlation_size_factor(_idea("NEAR/USDT", "SHORT")) == 1.0
        finally:
            cfg.stop()

    def test_unmapped_bucket_is_excluded(self):
        # Unmapped alts share one bucket but aren't all mutually correlated, so
        # co-membership must NOT trigger a same-direction size cut.
        eng = _engine([_pos("AAA/USDT", "LONG"), _pos("BBB/USDT", "LONG")])
        cfg, _ = _cfg()
        try:
            assert eng._correlation_size_factor(_idea("CCC/USDT", "LONG")) == 1.0
        finally:
            cfg.stop()


class TestGraduatedReduction:
    def test_one_correlated_same_side_position(self):
        # One ALT_L1 long open → second ALT_L1 long sized at 1 - 0.20 = 0.80.
        eng = _engine([_pos("SOL/USDT", "LONG")])
        cfg, _ = _cfg(step=0.20, floor=0.5)
        try:
            assert eng._correlation_size_factor(_idea("AVAX/USDT", "LONG")) == 0.80
        finally:
            cfg.stop()

    def test_two_correlated_same_side_positions(self):
        eng = _engine([_pos("SOL/USDT", "LONG"), _pos("AVAX/USDT", "LONG")])
        cfg, _ = _cfg(step=0.20, floor=0.5)
        try:
            # 1 - 0.40 = 0.60
            assert eng._correlation_size_factor(_idea("NEAR/USDT", "LONG")) == 0.60
        finally:
            cfg.stop()

    def test_reduction_is_floored(self):
        # Three same-side correlated → 1 - 0.60 = 0.40, but floor is 0.5.
        eng = _engine([
            _pos("SOL/USDT", "LONG"),
            _pos("AVAX/USDT", "LONG"),
            _pos("NEAR/USDT", "LONG"),
        ])
        cfg, _ = _cfg(step=0.20, floor=0.5)
        try:
            assert eng._correlation_size_factor(_idea("APT/USDT", "LONG")) == 0.5
        finally:
            cfg.stop()

    def test_only_same_direction_positions_count(self):
        # One ALT_L1 long + one ALT_L1 short open; a new ALT_L1 long counts ONLY
        # the long (1 same-side) → 0.80, not 0.60.
        eng = _engine([_pos("SOL/USDT", "LONG"), _pos("AVAX/USDT", "SHORT")])
        cfg, _ = _cfg(step=0.20, floor=0.5)
        try:
            assert eng._correlation_size_factor(_idea("NEAR/USDT", "LONG")) == 0.80
        finally:
            cfg.stop()


class TestFailOpen:
    def test_exception_returns_full_size(self):
        # A portfolio that raises on iteration must not block sizing — fail-open.
        class _Boom:
            @property
            def open_positions(self):
                raise RuntimeError("boom")

        eng = RiskEngine.__new__(RiskEngine)
        eng._portfolio = _Boom()
        cfg, _ = _cfg()
        try:
            assert eng._correlation_size_factor(_idea("SOL/USDT")) == 1.0
        finally:
            cfg.stop()
