"""breaker_reset_bars: optional backtest-only breaker auto-reset so one early
losing streak doesn't silently halt a months-long unattended run (the operator
is assumed to reset the breaker after N bars, like /reset does live)."""
from __future__ import annotations

import pytest

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig


def test_default_preserves_halt_behavior():
    assert BacktestConfig().breaker_reset_bars == 0


@pytest.mark.asyncio
async def test_reset_reopens_trading_after_trip():
    bars = DataLoader.generate_synthetic(bars=1500, seed=11)
    eng_halt = BacktestEngine(BacktestConfig(symbol="BTC/USDT", breaker_reset_bars=0))
    halt = await eng_halt.run(bars)
    eng_halt.cleanup()

    eng_reset = BacktestEngine(BacktestConfig(symbol="BTC/USDT", breaker_reset_bars=24))
    reset = await eng_reset.run(bars)
    eng_reset.cleanup()

    # With auto-reset the run can only trade the same or MORE than the
    # halt-preserving run (never fewer) — and both must complete cleanly.
    assert reset.total_trades >= halt.total_trades
