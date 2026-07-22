"""
Per-symbol loss-streak cooldown (default ON).

The account-wide consecutive-loss streak (RiskEngine) decays on ANY win, so a
symbol that keeps losing stays fully eligible for new entries as long as OTHER
symbols occasionally win. This tracks consecutive losses PER SYMBOL and, once
a symbol hits CONFIG.risk.symbol_loss_streak_threshold, arms a long cooldown
by reusing the same engine._symbol_cooldowns dict the existing post-SL
cooldown uses (already checked first thing in _analyze_signal).
"""

import types


import bot.core.engine as eng_mod
from bot.config import CONFIG


def _pos(symbol, pnl, close_reason="manual"):
    return types.SimpleNamespace(symbol=symbol, pnl_usd=pnl, close_reason=close_reason,
                                  direction="LONG", trade_id="t1")


def _stub_engine():
    stub = types.SimpleNamespace()
    stub._symbol_cooldowns = {}
    stub._symbol_loss_streaks = {}
    stub._symbol_cooldown_seconds = 1800.0
    stub.learning = types.SimpleNamespace(record_closed_outcome=lambda **kw: None)
    stub._auto_refit = types.SimpleNamespace(note_closed_trade=lambda *a, **k: None)
    stub._outcome_regime = lambda symbol: ""
    stub._invalidate_live_balance_cache = lambda: None
    return stub


class TestSymbolLossStreak:
    def test_three_losses_same_symbol_arms_cooldown(self, monkeypatch):
        stub = _stub_engine()
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        for _ in range(CONFIG.risk.symbol_loss_streak_threshold):
            eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("BIO/USDT", -5.0))
        assert "BIO" in stub._symbol_cooldowns
        # Streak resets after arming so it needs a fresh run to re-trip.
        assert stub._symbol_loss_streaks.get("BIO", 0) == 0

    def test_win_decrements_streak_and_prevents_cooldown(self, monkeypatch):
        stub = _stub_engine()
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("DFEN/USDT:USDT", -5.0))
        eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("DFEN/USDT:USDT", -5.0))
        eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("DFEN/USDT:USDT", 3.0))  # win
        eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("DFEN/USDT:USDT", -5.0))
        # Streak: +1, +1, -1, +1 = 2, below threshold 3 -> no cooldown armed.
        assert "DFEN" not in stub._symbol_cooldowns
        assert stub._symbol_loss_streaks.get("DFEN") == 2

    def test_breakeven_does_not_change_streak(self, monkeypatch):
        stub = _stub_engine()
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("SEI/USDT", -5.0))
        eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("SEI/USDT", 0.0))  # breakeven
        assert stub._symbol_loss_streaks.get("SEI") == 1

    def test_other_symbols_unaffected(self, monkeypatch):
        stub = _stub_engine()
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        for _ in range(CONFIG.risk.symbol_loss_streak_threshold):
            eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("BIO/USDT", -5.0))
        assert "BIO" in stub._symbol_cooldowns
        assert "ANIME" not in stub._symbol_cooldowns
        assert stub._symbol_loss_streaks.get("ANIME", 0) == 0

    def test_disabled_flag_skips_tracking_entirely(self, monkeypatch):
        stub = _stub_engine()
        fake_config = types.SimpleNamespace(
            risk=types.SimpleNamespace(
                symbol_loss_streak_enabled=False,
                symbol_loss_streak_threshold=CONFIG.risk.symbol_loss_streak_threshold,
                symbol_loss_streak_cooldown_seconds=CONFIG.risk.symbol_loss_streak_cooldown_seconds,
            ),
            analyzer=CONFIG.analyzer,
        )
        monkeypatch.setattr(eng_mod, "CONFIG", fake_config)
        for _ in range(10):
            eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("BIO/USDT", -5.0))
        assert "BIO" not in stub._symbol_cooldowns
        assert stub._symbol_loss_streaks == {}

    def test_cooldown_duration_matches_config(self, monkeypatch):
        stub = _stub_engine()
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import time as _time
        before = _time.monotonic()
        for _ in range(CONFIG.risk.symbol_loss_streak_threshold):
            eng_mod.RuneClawEngine._on_live_position_closed(stub, _pos("BIO/USDT", -5.0))
        expiry = stub._symbol_cooldowns["BIO"]
        expected_min = before + CONFIG.risk.symbol_loss_streak_cooldown_seconds - 5
        assert expiry >= expected_min
