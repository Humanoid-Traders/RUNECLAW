"""
Regime-conditional gating (Fable-5 round 4).

Two pieces: (1) the shadow book tags every rejection with the market
regime and reports per-(gate, regime) — the evidence base for keying
gates by regime; (2) entry timing becomes regime-conditional via
ENTRY_TIMING_REGIMES (PR #359's A/B: timing helps chop, hurts trend),
instead of the all-or-nothing global flag.
"""

import inspect

from bot.core.entry_timing import timing_active
from bot.core.shadow_book import ShadowBook
from bot.utils.models import Direction, TradeIdea

T0 = 1_750_000_000.0


class _cfg:
    """Temporarily override frozen CONFIG.execution fields."""

    def __init__(self, **kw):
        self.kw = kw
        self.old: dict = {}

    def __enter__(self):
        from bot.config import CONFIG
        for k, v in self.kw.items():
            self.old[k] = getattr(CONFIG.execution, k)
            object.__setattr__(CONFIG.execution, k, v)
        return self

    def __exit__(self, *exc):
        from bot.config import CONFIG
        for k, v in self.old.items():
            object.__setattr__(CONFIG.execution, k, v)


def _idea(asset="BTC/USDT:USDT", idea_id="TI-rg"):
    return TradeIdea(
        id=idea_id, asset=asset, direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        confidence=0.8, risk_reward_ratio=2.0, reasoning="regime test",
    )


# ── timing_active: the regime condition ──────────────────────────────

class TestTimingActive:
    def test_global_flag_wins_regardless_of_regime(self):
        with _cfg(entry_timing_enabled=True, entry_timing_regimes=""):
            assert timing_active("") is True
            assert timing_active("STRONG_TREND_UP") is True

    def test_regime_set_matches_case_insensitive(self):
        with _cfg(entry_timing_enabled=False,
                  entry_timing_regimes="CHOP, ranging ,HIGH_VOLATILITY"):
            assert timing_active("chop") is True
            assert timing_active("RANGING") is True
            assert timing_active("HIGH_VOLATILITY") is True
            assert timing_active("TREND_UP") is False

    def test_empty_set_and_empty_regime_are_inactive(self):
        with _cfg(entry_timing_enabled=False, entry_timing_regimes=""):
            assert timing_active("CHOP") is False
        with _cfg(entry_timing_enabled=False, entry_timing_regimes="CHOP"):
            # unknown regime string only matches the global flag, never a set
            assert timing_active("") is False

    def test_default_config_is_all_off(self):
        import os
        import pytest
        if os.environ.get("ENTRY_TIMING_ENABLED") or \
                os.environ.get("ENTRY_TIMING_REGIMES"):
            pytest.skip("env override present")
        from bot.config import CONFIG
        assert CONFIG.execution.entry_timing_enabled is False
        assert CONFIG.execution.entry_timing_regimes == ""


# ── shadow book regime tagging ────────────────────────────────────────

class TestShadowRegime:
    def _book(self, tmp_path):
        return ShadowBook(state_file=str(tmp_path / "sb.json"))

    def test_rejection_carries_regime(self, tmp_path):
        sb = self._book(tmp_path)
        tr = sb.record_rejection(_idea(), ["G"], "x", now_ts=T0,
                                 regime="trend_up")
        assert tr["regime"] == "TREND_UP"

    def test_gate_regime_report_splits_by_regime(self, tmp_path):
        sb = self._book(tmp_path)
        # Gate G blocks a winner in CHOP and a loser in TREND_UP.
        sb.record_rejection(_idea(idea_id="a"), ["G"], "x", ref_price=99.5,
                            now_ts=T0, regime="CHOP")
        sb.record_rejection(_idea(idea_id="b", asset="ETH/USDT:USDT"),
                            ["G"], "x", ref_price=99.5,
                            now_ts=T0, regime="TREND_UP")
        sb.update({"BTC/USDT:USDT": {"last": 111.0},
                   "ETH/USDT:USDT": {"last": 94.0}}, now_ts=T0 + 60)
        rep = sb.gate_regime_report()
        assert rep["G"]["CHOP"]["net_r"] == 2.0     # blocked a winner here
        assert rep["G"]["TREND_UP"]["net_r"] == -1.0  # saved money here

    def test_untagged_trades_land_in_unknown(self, tmp_path):
        sb = self._book(tmp_path)
        sb.record_rejection(_idea(), ["G"], "x", ref_price=99.5, now_ts=T0)
        sb.update({"BTC/USDT:USDT": {"last": 111.0}}, now_ts=T0 + 60)
        assert sb.gate_regime_report()["G"]["UNKNOWN"]["n"] == 1

    def test_render_shows_split_only_when_verdicts_differ(self, tmp_path):
        sb = self._book(tmp_path)
        sb.record_rejection(_idea(idea_id="a"), ["G"], "x", ref_price=99.5,
                            now_ts=T0, regime="CHOP")
        sb.record_rejection(_idea(idea_id="b", asset="ETH/USDT:USDT"),
                            ["G"], "x", ref_price=99.5,
                            now_ts=T0, regime="TREND_UP")
        sb.update({"BTC/USDT:USDT": {"last": 111.0},
                   "ETH/USDT:USDT": {"last": 94.0}}, now_ts=T0 + 60)
        out = sb.render_report()
        assert "by regime:" in out and "CHOP" in out

    def test_render_no_split_when_uniform(self, tmp_path):
        sb = self._book(tmp_path)
        for i, (asset, reg) in enumerate([("BTC/USDT:USDT", "CHOP"),
                                          ("ETH/USDT:USDT", "TREND_UP")]):
            sb.record_rejection(_idea(idea_id=str(i), asset=asset),
                                ["G"], "x", ref_price=99.5,
                                now_ts=T0, regime=reg)
        # both hit TP → uniformly positive → no split line
        sb.update({"BTC/USDT:USDT": {"last": 111.0},
                   "ETH/USDT:USDT": {"last": 111.0}}, now_ts=T0 + 60)
        assert "by regime:" not in sb.render_report()


# ── wiring pins ───────────────────────────────────────────────────────

class TestWiring:
    def test_backtest_arming_is_regime_aware(self):
        from bot.backtest.engine import BacktestEngine
        src = inspect.getsource(BacktestEngine)
        assert "timing_active(_et_regime)" in src
        assert "_current_regimes" in src

    def test_engine_rejection_passes_regime(self):
        from bot.core import engine as eng_mod
        src = inspect.getsource(eng_mod)
        assert "regime=_sb_regime" in src

    def test_config_flag_exists(self):
        from bot.config import CONFIG
        assert isinstance(CONFIG.execution.entry_timing_regimes, str)
