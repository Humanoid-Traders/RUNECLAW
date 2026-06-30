"""
Backtest data provenance (deep-audit medium).

A real-data fetch failure silently fell back to synthetic GBM data, and the
saved result JSON never recorded which source it used — so a synthetic-fallback
run was indistinguishable from a real backtest. _load_bars now returns a
data_source label ("csv" | "bitget_real" | "synthetic" | "synthetic_fallback"),
the runner stamps it (+ used_synthetic) into BacktestResult, and --strict-data
aborts instead of silently substituting synthetic data.
"""

import argparse
import asyncio

import pytest

import bot.backtest.runner as runner
from bot.backtest.data_loader import DataLoader
from bot.backtest.models import BacktestConfig, BacktestResult

_CONFIG = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
_FAKE_BARS = list(range(120))  # opaque; _load_bars never inspects bar contents


def _args(**over):
    base = dict(csv=None, synthetic=False, strict_data=False, limit=720,
                bars=720, start_price=50000.0, volatility=0.02, trend=0.0, seed=42)
    base.update(over)
    return argparse.Namespace(**base)


def _load(monkeypatch, args, *, fetch=None, csv=None, synth=None):
    if fetch is not None:
        monkeypatch.setattr(DataLoader, "from_bitget", fetch)
    if csv is not None:
        monkeypatch.setattr(DataLoader, "from_csv", csv)
    if synth is not None:
        monkeypatch.setattr(DataLoader, "generate_synthetic", synth)
    return asyncio.run(runner._load_bars(args, _CONFIG))


async def _ok_fetch(**kw):
    return _FAKE_BARS


async def _bad_fetch(**kw):
    raise RuntimeError("offline")


class TestDataSourceLabel:
    def test_real_fetch_is_bitget_real(self, monkeypatch):
        bars, used, src = _load(monkeypatch, _args(), fetch=_ok_fetch)
        assert used is False and src == "bitget_real" and bars == _FAKE_BARS

    def test_csv_is_csv(self, monkeypatch):
        bars, used, src = _load(monkeypatch, _args(csv="x.csv"),
                                csv=lambda p: _FAKE_BARS)
        assert used is False and src == "csv"

    def test_explicit_synthetic(self, monkeypatch):
        bars, used, src = _load(monkeypatch, _args(synthetic=True),
                                synth=lambda **kw: _FAKE_BARS)
        assert used is True and src == "synthetic"

    def test_fetch_failure_falls_back_labelled(self, monkeypatch):
        bars, used, src = _load(monkeypatch, _args(),
                                fetch=_bad_fetch, synth=lambda **kw: _FAKE_BARS)
        assert used is True and src == "synthetic_fallback"

    def test_empty_fetch_falls_back(self, monkeypatch):
        async def _empty(**kw):
            return []
        bars, used, src = _load(monkeypatch, _args(),
                                fetch=_empty, synth=lambda **kw: _FAKE_BARS)
        assert used is True and src == "synthetic_fallback"


class TestStrictData:
    def test_strict_aborts_on_fetch_failure(self, monkeypatch):
        # --strict-data: a failed real fetch raises instead of using synthetic.
        called = {"synth": False}

        def _synth(**kw):
            called["synth"] = True
            return _FAKE_BARS

        with pytest.raises(RuntimeError, match="offline"):
            _load(monkeypatch, _args(strict_data=True),
                  fetch=_bad_fetch, synth=_synth)
        assert called["synth"] is False  # never substituted synthetic

    def test_strict_does_not_block_successful_real_fetch(self, monkeypatch):
        bars, used, src = _load(monkeypatch, _args(strict_data=True), fetch=_ok_fetch)
        assert used is False and src == "bitget_real"


class TestResultStamping:
    def test_default_fields(self):
        # Defaults keep older results readable: not-synthetic, unknown source.
        r = BacktestResult(
            symbol="BTC/USDT", timeframe="1h", start_date="", end_date="",
            initial_balance=10000.0, commission_pct=0.1, slippage_pct=0.05,
            final_equity=10000.0, total_return_pct=0.0, total_pnl=0.0,
            total_commission=0.0, total_slippage=0.0, net_pnl=0.0,
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
            avg_win_usd=0.0, avg_loss_usd=0.0, largest_win_usd=0.0,
            largest_loss_usd=0.0, avg_trade_duration_hours=0.0,
            max_drawdown_pct=0.0, max_drawdown_usd=0.0, max_consecutive_losses=0,
            profit_factor=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            calmar_ratio=0.0, risk_reward_avg=0.0, total_signals_generated=0,
            total_ideas_generated=0, total_ideas_rejected_risk=0,
            total_ideas_rejected_confidence=0)
        assert r.used_synthetic is False
        assert r.data_source == "unknown"

    def test_stamp_round_trips_through_dump(self):
        r = BacktestResult(
            symbol="BTC/USDT", timeframe="1h", start_date="", end_date="",
            initial_balance=10000.0, commission_pct=0.1, slippage_pct=0.05,
            final_equity=10000.0, total_return_pct=0.0, total_pnl=0.0,
            total_commission=0.0, total_slippage=0.0, net_pnl=0.0,
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
            avg_win_usd=0.0, avg_loss_usd=0.0, largest_win_usd=0.0,
            largest_loss_usd=0.0, avg_trade_duration_hours=0.0,
            max_drawdown_pct=0.0, max_drawdown_usd=0.0, max_consecutive_losses=0,
            profit_factor=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            calmar_ratio=0.0, risk_reward_avg=0.0, total_signals_generated=0,
            total_ideas_generated=0, total_ideas_rejected_risk=0,
            total_ideas_rejected_confidence=0)
        r.used_synthetic = True
        r.data_source = "synthetic_fallback"
        dumped = r.model_dump(mode="json", exclude={"equity_curve"})
        assert dumped["used_synthetic"] is True
        assert dumped["data_source"] == "synthetic_fallback"
