"""
Regression tests for V6.1 deep-backtest edge-case crashes
(docs/AUDIT_REPORT_V6.1.md).

The 500-run deep backtest produced 6 hard errors, all on extreme inputs:
  - 5× PEPE/USDT "Crash Recovery": a long downtrend on a ~1.3e-5 priced asset
    underflows the synthetic price to 0.0, which then becomes the next segment's
    start_price → ZeroDivisionError in DataLoader.generate_synthetic.
  - 1× DOGE/USDT "High Volatility": the ATR-derived stop/target distance falls
    below tick precision, so rounding collapses SL/TP onto entry → the TradeIdea
    directional-sanity validator raises and aborts the whole run.

Both now degrade gracefully (skip the degenerate bar/idea) instead of crashing.
"""
import pytest

from bot.backtest.data_loader import DataLoader


# ── data_loader: zero / near-zero start price must not divide by zero ──

def test_generate_synthetic_zero_start_price_does_not_crash():
    bars = DataLoader.generate_synthetic(bars=50, start_price=0.0,
                                         volatility=0.04, trend=-0.0006, seed=42)
    assert len(bars) == 50  # no ZeroDivisionError


def test_generate_synthetic_tiny_price_long_downtrend_underflow():
    """A very-low-priced asset under a sustained downtrend can underflow to 0;
    the generator must still return a full series without raising."""
    bars = DataLoader.generate_synthetic(bars=1000, start_price=1.3e-5,
                                         volatility=0.072, trend=-0.0006, seed=137)
    assert len(bars) == 1000
    assert all(b.high >= b.low for b in bars)


# ── full pipeline: the two failing deep-backtest scenarios now complete ──

@pytest.mark.asyncio
async def test_pepe_crash_recovery_completes_without_error():
    import run_deep_backtest as rdb
    sym = {"symbol": "PEPE/USDT", "price": 0.000013, "vol": 0.040, "name": "Pepe"}
    regime = {"trend": None, "label": "Crash Recovery", "vol_mult": 1.0}
    r = await rdb.run_single_backtest(sym, regime, 42)
    assert "error" not in r
    assert r["symbol"] == "PEPE/USDT"


@pytest.mark.asyncio
async def test_doge_high_vol_completes_without_validation_crash():
    import run_deep_backtest as rdb
    sym = {"symbol": "DOGE/USDT", "price": 0.23, "vol": 0.030, "name": "Dogecoin"}
    regime = {"trend": 0.0, "label": "High Volatility", "vol_mult": 1.8}
    r = await rdb.run_single_backtest(sym, regime, 137)
    assert "error" not in r
    assert r["total_trades"] >= 0
