"""Backtest parity for live's per-symbol loss-streak cooldown (round 4 item 2).

Live (bot/core/engine.py close hook, SYMBOL_LOSS_STREAK_ENABLED default True)
benches a symbol for SYMBOL_LOSS_STREAK_COOLDOWN_SEC after SYMBOL_LOSS_STREAK_
THRESHOLD consecutive losing positions on it. The backtest never modeled that;
now it does, bar-time based, gated behind BACKTEST_SYMBOL_LOSS_STREAK (default
OFF for byte-identical default backtests; --honest enables it).

NOTE on measurability: on the current frozen benchmarks this layer is a
verified no-op (~1 trade per symbol per walk-forward fold — no symbol ever
strings 3 consecutive losses inside one fold), so it exists for parity, not
because it moved the honest number. See docs/FROZEN_BENCHMARK.md.
"""

import inspect
from datetime import datetime, timedelta, timezone

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.config import CONFIG


def _engine(monkeypatch, enabled=True):
    if enabled:
        monkeypatch.setenv("BACKTEST_SYMBOL_LOSS_STREAK", "1")
    else:
        monkeypatch.delenv("BACKTEST_SYMBOL_LOSS_STREAK", raising=False)
    cfg = BacktestConfig(symbol="TAG/USDT:USDT", timeframe="1h",
                         initial_balance=10_000.0)
    eng = BacktestEngine(cfg)
    eng._saved_learning_flags = (None, None, None)  # skip cleanup restore
    return eng


_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestStreakStateMachine:
    def test_threshold_losses_arm_cooldown_and_reset(self, monkeypatch):
        eng = _engine(monkeypatch)
        thr = CONFIG.risk.symbol_loss_streak_threshold
        for i in range(thr):
            eng._update_symbol_streak("TAG/USDT:USDT", -10.0, _T0 + timedelta(hours=i))
        until = eng._symbol_cooldown_until.get("TAG/USDT:USDT")
        assert until is not None
        expected = (_T0 + timedelta(hours=thr - 1)
                    + timedelta(seconds=CONFIG.risk.symbol_loss_streak_cooldown_seconds))
        assert until == expected
        # Streak reset: takes a fresh run of losses to re-trip.
        assert eng._symbol_loss_streaks["TAG/USDT:USDT"] == 0

    def test_win_decays_streak_by_one(self, monkeypatch):
        eng = _engine(monkeypatch)
        eng._update_symbol_streak("TAG/USDT:USDT", -10.0, _T0)
        eng._update_symbol_streak("TAG/USDT:USDT", -10.0, _T0)
        assert eng._symbol_loss_streaks["TAG/USDT:USDT"] == 2
        eng._update_symbol_streak("TAG/USDT:USDT", +5.0, _T0)
        assert eng._symbol_loss_streaks["TAG/USDT:USDT"] == 1

    def test_breakeven_leaves_streak_unchanged(self, monkeypatch):
        eng = _engine(monkeypatch)
        eng._update_symbol_streak("TAG/USDT:USDT", -10.0, _T0)
        eng._update_symbol_streak("TAG/USDT:USDT", 0.0, _T0)
        assert eng._symbol_loss_streaks["TAG/USDT:USDT"] == 1

    def test_cooldown_max_merges_with_existing(self, monkeypatch):
        eng = _engine(monkeypatch)
        later = _T0 + timedelta(days=30)
        eng._symbol_cooldown_until["TAG/USDT:USDT"] = later
        thr = CONFIG.risk.symbol_loss_streak_threshold
        for i in range(thr):
            eng._update_symbol_streak("TAG/USDT:USDT", -10.0, _T0 + timedelta(hours=i))
        # An earlier-expiring new cooldown must not SHORTEN the existing one.
        assert eng._symbol_cooldown_until["TAG/USDT:USDT"] == later


class TestGating:
    def test_flag_off_by_default_and_no_tracking(self, monkeypatch):
        eng = _engine(monkeypatch, enabled=False)
        assert eng._symbol_streak_enabled is False

    def test_flag_respects_live_master_switch(self, monkeypatch):
        # BACKTEST_SYMBOL_LOSS_STREAK=1 but the live feature itself disabled
        # -> stays off (parity: model what live actually runs).
        monkeypatch.setenv("BACKTEST_SYMBOL_LOSS_STREAK", "1")
        orig = CONFIG.risk.symbol_loss_streak_enabled
        object.__setattr__(CONFIG.risk, "symbol_loss_streak_enabled", False)
        try:
            cfg = BacktestConfig(symbol="TAG/USDT:USDT", timeframe="1h",
                                 initial_balance=10_000.0)
            eng = BacktestEngine(cfg)
            eng._saved_learning_flags = (None, None, None)
            assert eng._symbol_streak_enabled is False
        finally:
            object.__setattr__(CONFIG.risk, "symbol_loss_streak_enabled", orig)

    def test_entry_guard_blocks_during_cooldown(self, monkeypatch):
        # The _process_bar early-out is keyed on bar time vs cooldown expiry.
        eng = _engine(monkeypatch)
        eng._symbol_cooldown_until["TAG/USDT:USDT"] = _T0 + timedelta(hours=12)
        src = inspect.getsource(BacktestEngine._process_bar)
        assert "_symbol_cooldown_until" in src
        assert "bar.timestamp < _cd_until" in src

    def test_honest_enables_via_env(self):
        from bot.backtest import runner
        src = inspect.getsource(runner._apply_honest_fidelity)
        assert 'os.environ.setdefault("BACKTEST_SYMBOL_LOSS_STREAK", "1")' in src


class TestPositionTotalSemantics:
    def test_partial_close_accumulates_banked_pnl(self):
        # The final-close hook must see runner residual + banked TP legs
        # (live counts one outcome per POSITION, not per scale-out leg).
        src = inspect.getsource(BacktestEngine._partial_close)
        assert 'bt_meta["banked_net_pnl"]' in src
        close_src = inspect.getsource(BacktestEngine._close_position)
        assert 'bt_meta.get("banked_net_pnl", 0.0)' in close_src
