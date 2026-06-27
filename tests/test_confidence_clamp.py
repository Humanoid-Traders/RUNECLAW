"""
Regression: blended confidence must never exceed 1.0.

The analyzer clamps confidence at one point, but a later additive session-aware
boost (`+confidence_adjustment`, floored at min_floor) was applied AFTER that
clamp with no re-cap. A positive boost could push the value to e.g. 1.01, which
trips TradeIdea's `confidence <= 1` pydantic validator and aborts the whole
analysis/backtest run. A definitive final clamp now guards every downstream use.
"""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.core.analyzer import Analyzer


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_clamp_is_applied_after_the_session_adjustment():
    src = inspect.getsource(Analyzer.analyze)
    clamp = "blended_confidence = max(0.0, min(1.0, blended_confidence))"
    assert clamp in src
    # It must come AFTER the session boost, else the boost can un-cap it again.
    assert src.index("session.confidence_adjustment") < src.index(clamp)
    # ...and BEFORE the idea is built.
    assert src.index(clamp) < src.index("TradeIdea(")


def test_backtest_completes_with_a_large_positive_session_boost():
    # A +0.9 session boost would push almost every idea's confidence past 1.0.
    # Before the clamp this aborted the run with a pydantic ValidationError;
    # now it completes cleanly.
    cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", initial_balance=10000.0,
                         commission_pct=0.1, slippage_pct=0.05, use_llm=False,
                         lookback_size=100, scan_interval=4)
    bars = DataLoader.generate_synthetic(bars=300, start_price=108000.0,
                                         volatility=0.012, trend=0.0004, seed=42)
    with patch("bot.core.session_aware.get_current_session",
               return_value=SimpleNamespace(confidence_adjustment=0.9)):
        result = _run(BacktestEngine(cfg).run(bars))
    # The run finished (no ValidationError); trade count is a valid integer.
    assert isinstance(result.total_trades, int)
    assert result.total_trades >= 0
