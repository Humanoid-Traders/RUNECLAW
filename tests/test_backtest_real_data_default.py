"""
Backtest integrity: real market data is the DEFAULT data source; synthetic GBM is
an explicit --synthetic smoke test. The single biggest backtest-vs-live divergence
risk is feeding synthetic data, so the runner must reach for real Bitget klines
unless told otherwise — and fall back to a clearly-flagged smoke test only when
the real fetch fails.
"""

import asyncio
import types
from unittest.mock import AsyncMock, patch

from bot.backtest import runner
from bot.backtest.models import BacktestConfig


def _args(**over):
    base = dict(csv=None, synthetic=False, fetch=False, strict_data=False,
                limit=300, bars=300,
                start_price=100.0, volatility=0.015, trend=0.0001, seed=1)
    base.update(over)
    return types.SimpleNamespace(**base)


def _cfg():
    return BacktestConfig(symbol="BTC/USDT", timeframe="1h", initial_balance=10_000.0)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_default_uses_real_bitget_data():
    real = [object()] * 300
    with patch.object(runner.DataLoader, "from_bitget",
                      new=AsyncMock(return_value=real)) as fb, \
         patch.object(runner.DataLoader, "generate_synthetic") as gs:
        bars, used_synth, src = _run(runner._load_bars(_args(), _cfg()))
    assert fb.called
    assert not gs.called
    assert used_synth is False
    assert src == "bitget_real"
    assert bars is real


def test_synthetic_flag_forces_smoke_test():
    synth = [object()] * 300
    with patch.object(runner.DataLoader, "from_bitget",
                      new=AsyncMock()) as fb, \
         patch.object(runner.DataLoader, "generate_synthetic", return_value=synth) as gs:
        bars, used_synth, src = _run(runner._load_bars(_args(synthetic=True), _cfg()))
    assert not fb.called          # never reaches for real data
    assert gs.called
    assert used_synth is True
    assert src == "synthetic"
    assert bars is synth


def test_real_fetch_failure_falls_back_to_synthetic():
    synth = [object()] * 300
    with patch.object(runner.DataLoader, "from_bitget",
                      new=AsyncMock(side_effect=RuntimeError("offline"))), \
         patch.object(runner.DataLoader, "generate_synthetic", return_value=synth) as gs:
        bars, used_synth, src = _run(runner._load_bars(_args(), _cfg()))
    assert gs.called               # graceful fallback
    assert used_synth is True
    assert src == "synthetic_fallback"
    assert bars is synth


def test_empty_real_fetch_also_falls_back():
    synth = [object()] * 300
    with patch.object(runner.DataLoader, "from_bitget",
                      new=AsyncMock(return_value=[])), \
         patch.object(runner.DataLoader, "generate_synthetic", return_value=synth) as gs:
        _, used_synth, src = _run(runner._load_bars(_args(), _cfg()))
    assert gs.called
    assert used_synth is True
    assert src == "synthetic_fallback"


def test_csv_takes_precedence():
    csv_bars = [object()] * 300
    with patch.object(runner.DataLoader, "from_csv", return_value=csv_bars) as fc, \
         patch.object(runner.DataLoader, "from_bitget", new=AsyncMock()) as fb:
        bars, used_synth, src = _run(runner._load_bars(_args(csv="x.csv"), _cfg()))
    assert fc.called and not fb.called
    assert used_synth is False
    assert src == "csv"
    assert bars is csv_bars
