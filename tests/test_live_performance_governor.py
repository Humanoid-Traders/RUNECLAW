"""
Live-performance governor.

A closed-loop backstop ON TOP of the pre-trade checks: it scores REALIZED
closed-trade outcomes over a rolling window and de-risks when the strategy is
actually losing — a graduated SIZE REDUCTION when the recent window
underperforms (low win rate OR net-negative), and a PAUSE (size 0 → trade
rejected) only when it is BOTH losing often AND net-negative. Tighten-only,
fail-open, no-op below the min-sample count. Gated by
LIVE_PERFORMANCE_GOVERNOR_ENABLED (default OFF).
"""

import dataclasses
import os
import tempfile
from collections import deque
from unittest.mock import patch

from bot.config import CONFIG as REAL_CONFIG
from bot.utils.models import Direction, TradeIdea
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine, RiskVerdict


# ── Property in isolation ───────────────────────────────────────────

def _prop_engine(pnls):
    eng = RiskEngine.__new__(RiskEngine)
    eng._realized_pnl_window = pnls if pnls is None else deque(pnls)
    return eng


def _cfg(window=20, min_samples=10, reduce_wr=0.40, pause_wr=0.25, reduce_mult=0.5):
    p = patch("bot.risk.risk_engine.CONFIG")
    m = p.start()
    m.risk.live_perf_window = window
    m.risk.live_perf_min_samples = min_samples
    m.risk.live_perf_reduce_winrate = reduce_wr
    m.risk.live_perf_pause_winrate = pause_wr
    m.risk.live_perf_reduce_mult = reduce_mult
    return p


class TestGovernorMultiplier:
    def test_cold_start_below_min_samples_is_full_size(self):
        p = _cfg(min_samples=10)
        try:
            # 9 brutal losses, but below the sample floor → fail OPEN (no penalty).
            assert _prop_engine([-100.0] * 9).live_performance_size_multiplier == 1.0
        finally:
            p.stop()

    def test_healthy_window_is_full_size(self):
        p = _cfg()
        try:
            # 6 wins / 4 losses, net +40 → healthy.
            w = [10.0] * 6 + [-5.0] * 4
            assert _prop_engine(w).live_performance_size_multiplier == 1.0
        finally:
            p.stop()

    def test_reduce_on_low_win_rate_even_if_net_positive(self):
        p = _cfg(reduce_mult=0.5)
        try:
            # win rate 0.3 (<=0.40) but net positive (big winners) → reduce.
            w = [100.0] * 3 + [-1.0] * 7
            assert _prop_engine(w).live_performance_size_multiplier == 0.5
        finally:
            p.stop()

    def test_reduce_on_net_negative_even_if_win_rate_ok(self):
        p = _cfg(reduce_mult=0.5)
        try:
            # win rate 0.6 (>0.40) but net negative (losers bigger) → reduce.
            w = [1.0] * 6 + [-100.0] * 4
            assert _prop_engine(w).live_performance_size_multiplier == 0.5
        finally:
            p.stop()

    def test_pause_on_low_win_rate_and_net_negative(self):
        p = _cfg(pause_wr=0.25)
        try:
            # win rate 0.2 (<=0.25) AND net negative → pause.
            w = [1.0] * 2 + [-10.0] * 8
            assert _prop_engine(w).live_performance_size_multiplier == 0.0
        finally:
            p.stop()

    def test_no_pause_when_net_positive_despite_low_win_rate(self):
        p = _cfg(pause_wr=0.25, reduce_mult=0.5)
        try:
            # win rate 0.2 (<=pause) BUT net positive → reduce, NOT pause.
            w = [100.0] * 2 + [-1.0] * 8
            assert _prop_engine(w).live_performance_size_multiplier == 0.5
        finally:
            p.stop()

    def test_reduce_threshold_is_inclusive(self):
        p = _cfg(reduce_wr=0.40, reduce_mult=0.5)
        try:
            # win rate exactly 0.40, net positive → reduce (<=).
            w = [10.0] * 4 + [-1.0] * 6
            assert _prop_engine(w).live_performance_size_multiplier == 0.5
        finally:
            p.stop()

    def test_only_most_recent_window_counts(self):
        p = _cfg(window=10, min_samples=10)
        try:
            # Oldest 10 are losses; newest 10 are healthy → governor reads healthy.
            w = [-100.0] * 10 + ([10.0] * 6 + [-5.0] * 4)
            assert _prop_engine(w).live_performance_size_multiplier == 1.0
        finally:
            p.stop()

    def test_fail_open_on_error(self):
        p = _cfg()
        try:
            # _realized_pnl_window=None → list(None) raises → fail open to 1.0.
            assert _prop_engine(None).live_performance_size_multiplier == 1.0
        finally:
            p.stop()


# ── record_trade_result populates the window ────────────────────────

def _make_engine(balance: float = 10_000.0) -> RiskEngine:
    state_file = os.path.join(tempfile.mkdtemp(), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state_file)


class TestWindowRecording:
    def test_record_trade_result_appends_every_close(self):
        eng = _make_engine()
        for pnl in (5.0, -3.0, 0.0, 7.0):
            eng.record_trade_result(pnl)
        assert list(eng._realized_pnl_window) == [5.0, -3.0, 0.0, 7.0]


# ── Integration through evaluate() ──────────────────────────────────

class _CfgProxy:
    """Real CONFIG with an overridden .risk (frozen-safe via dataclasses.replace)."""
    def __init__(self, **risk_overrides):
        self.risk = dataclasses.replace(REAL_CONFIG.risk, **risk_overrides)

    def __getattr__(self, name):
        return getattr(REAL_CONFIG, name)


def _make_idea(**kwargs) -> TradeIdea:
    defaults = dict(
        asset="BTC/USDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        confidence=0.75,
        reasoning="test idea",
        source="test",
    )
    defaults.update(kwargs)
    return TradeIdea(**defaults)


_GOV_ON = dict(
    live_performance_governor_enabled=True,
    live_perf_window=20,
    live_perf_min_samples=10,
    live_perf_reduce_winrate=0.40,
    live_perf_pause_winrate=0.25,
    live_perf_reduce_mult=0.5,
)


def _pause_window():
    # win rate 0.2, net negative → governor pause.
    return [1.0] * 2 + [-10.0] * 8


class TestGovernorIntegration:
    def test_pause_rejects_trade_with_governor_reason(self):
        eng = _make_engine()
        eng._realized_pnl_window.extend(_pause_window())
        cfg = _CfgProxy(**_GOV_ON)
        with patch("bot.risk.risk_engine.CONFIG", cfg):
            rc = eng.evaluate(_make_idea(), atr=1.0)
        assert rc.verdict == RiskVerdict.REJECTED
        assert any("LIVE_PERF_GOVERNOR" in f for f in rc.checks_failed)

    def test_disabled_does_not_add_governor_failure(self):
        eng = _make_engine()
        eng._realized_pnl_window.extend(_pause_window())
        cfg = _CfgProxy(**{**_GOV_ON, "live_performance_governor_enabled": False})
        with patch("bot.risk.risk_engine.CONFIG", cfg):
            rc = eng.evaluate(_make_idea(), atr=1.0)
        assert not any("LIVE_PERF_GOVERNOR" in f for f in rc.checks_failed)

    def test_healthy_window_does_not_reject_on_governor(self):
        eng = _make_engine()
        eng._realized_pnl_window.extend([10.0] * 6 + [-5.0] * 4)  # healthy
        cfg = _CfgProxy(**_GOV_ON)
        with patch("bot.risk.risk_engine.CONFIG", cfg):
            rc = eng.evaluate(_make_idea(), atr=1.0)
        assert not any("LIVE_PERF_GOVERNOR" in f for f in rc.checks_failed)

    def test_cold_start_does_not_reject(self):
        eng = _make_engine()
        eng._realized_pnl_window.extend([-100.0] * 5)  # below min_samples=10
        cfg = _CfgProxy(**_GOV_ON)
        with patch("bot.risk.risk_engine.CONFIG", cfg):
            rc = eng.evaluate(_make_idea(), atr=1.0)
        assert not any("LIVE_PERF_GOVERNOR" in f for f in rc.checks_failed)
