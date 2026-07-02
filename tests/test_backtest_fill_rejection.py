"""Balance-exhaustion race at fill time must reject the FILL, not kill the RUN.

The risk engine approves a position size against the balance available at scan
time. By fill time (the next bar in next_open mode, or after other streams'
fills in portfolio mode) that margin may already be committed.
``PortfolioTracker.open_position`` rejects-not-clamps by contract (C2-15) and
the live path catches the ``ValueError`` in ``confirm_trade`` — the backtest
fill path must degrade the same way. Surfaced by the MIN_CONFIDENCE=0.50
robustness perturbation, which crashed mid-run on exactly this race.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from bot.compat import UTC
from bot.utils.models import Direction, TradeIdea


def _engine():
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=1_000.0,
        slippage_pct=0.0, commission_pct=0.0))


def _bar():
    from bot.backtest.models import BacktestBar
    return BacktestBar(timestamp=datetime(2025, 1, 1, 5, tzinfo=UTC),
                       open=100.0, high=101.0, low=99.0, close=100.5,
                       volume=1000.0, symbol="BTC/USDT")


def _risk_check(size_usd):
    return SimpleNamespace(position_size_usd=size_usd,
                           verdict=SimpleNamespace(value="APPROVED"))


def _idea():
    return TradeIdea(
        id="TI-FILLREJ", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=110.0,
        confidence=0.7, reasoning="x", source="t",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )


class TestFillRejectedNotFatal:
    def test_oversized_fill_is_skipped_not_raised(self):
        eng = _engine()
        try:
            # Approved size exceeds the whole account: the race in miniature.
            risk_check = _risk_check(5_000.0)
            before = eng._ideas_rejected_risk
            eng._execute_fill(_idea(), risk_check, fill_price=100.0, bar=_bar())
            assert eng._ideas_rejected_risk == before + 1
            assert not eng.portfolio.open_positions
            assert eng.portfolio.balance == pytest.approx(1_000.0)
        finally:
            eng.cleanup()

    def test_normal_fill_still_opens(self):
        eng = _engine()
        try:
            eng._execute_fill(_idea(), _risk_check(100.0), fill_price=100.0,
                              bar=_bar())
            assert len(eng.portfolio.open_positions) == 1
        finally:
            eng.cleanup()
