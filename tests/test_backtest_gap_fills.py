"""
Roadmap PR3 — backtest integrity: gap-aware stop fills.

A stop-loss is a stop/market order: when a bar GAPS through the stop at the open,
the realistic fill is the open (worse than the stop level), not the stop price.
Filling exactly at the stop understates loss tails and overstates win rate.
A take-profit is a limit order and fills at its level even on a favorable gap.
"""

from datetime import datetime

import pytest

from bot.compat import UTC
from bot.utils.models import TradeIdea, Direction


def _engine(slippage_pct=0.0):
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=10_000.0,
        slippage_pct=slippage_pct, commission_pct=0.0))


def _bar(o, h, l, c):
    from bot.backtest.models import BacktestBar
    return BacktestBar(timestamp=datetime(2025, 1, 1, 5, tzinfo=UTC),
                       open=o, high=h, low=l, close=c, volume=1000.0, symbol="BTC/USDT")


def _open(eng, direction, entry, sl, tp):
    idea = TradeIdea(
        id="TI-GAP", asset="BTC/USDT", direction=direction,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        confidence=0.7, reasoning="x", source="t",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )
    trade = eng.portfolio.open_position(idea, size_usd=100.0)
    eng._open_bt_positions[trade.trade_id] = {
        "idea": idea,
        "adjusted_entry": entry,
        "slippage_entry": 0.0,
        "entry_time": datetime(2025, 1, 1, tzinfo=UTC),
        "risk_verdict": "APPROVED",
        "trailing_active": False,
    }
    return trade


class TestGapAwareStopFills:
    def test_long_gap_down_fills_at_open_not_stop(self):
        eng = _engine()
        try:
            _open(eng, Direction.LONG, entry=100.0, sl=95.0, tp=110.0)
            # Bar gaps DOWN through the 95 stop: opens at 90 (below SL).
            eng._check_stops_intrabar(_bar(o=90.0, h=91.0, l=88.0, c=89.0))
            assert len(eng._trades) == 1
            t = eng._trades[0]
            assert t.exit_reason in ("SL", "TRAILING_SL")
            # Filled at the gap-open (90), NOT magically at the stop (95).
            assert t.exit_price == pytest.approx(90.0)
        finally:
            eng.cleanup()

    def test_long_intrabar_stop_still_fills_at_stop(self):
        eng = _engine()
        try:
            _open(eng, Direction.LONG, entry=100.0, sl=95.0, tp=110.0)
            # No gap: opens above SL (98), dips to 94 intrabar -> fills at the stop.
            eng._check_stops_intrabar(_bar(o=98.0, h=99.0, l=94.0, c=96.0))
            t = eng._trades[0]
            assert t.exit_price == pytest.approx(95.0)
        finally:
            eng.cleanup()

    def test_short_gap_up_fills_at_open_not_stop(self):
        eng = _engine()
        try:
            _open(eng, Direction.SHORT, entry=100.0, sl=105.0, tp=90.0)
            # Bar gaps UP through the 105 stop: opens at 110 (above SL).
            eng._check_stops_intrabar(_bar(o=110.0, h=112.0, l=109.0, c=111.0))
            t = eng._trades[0]
            assert t.exit_reason in ("SL", "TRAILING_SL")
            assert t.exit_price == pytest.approx(110.0)
        finally:
            eng.cleanup()

    def test_take_profit_still_fills_at_limit_on_gap(self):
        eng = _engine()
        try:
            _open(eng, Direction.LONG, entry=100.0, sl=95.0, tp=110.0)
            # Favorable gap up through TP: opens at 115. TP is a limit -> fills at 110.
            eng._check_stops_intrabar(_bar(o=115.0, h=116.0, l=114.0, c=115.5))
            t = eng._trades[0]
            assert t.exit_reason == "TP"
            assert t.exit_price == pytest.approx(110.0)
        finally:
            eng.cleanup()


class TestSyntheticTailsAllowed:
    def test_synthetic_data_can_exceed_10pct_bars(self):
        """The old ±10%/bar clamp is gone: with enough bars, the tail-event
        branch must produce at least one intra-bar move beyond 10%."""
        from bot.backtest.data_loader import DataLoader
        bars = DataLoader.generate_synthetic(
            bars=4000, start_price=100.0, volatility=0.015, seed=7)
        max_move = max(abs(b.high - b.low) / b.open for b in bars if b.open > 0)
        assert max_move > 0.10, f"no bar exceeded 10% range (max {max_move:.3f})"
        # Prices stay positive (the underflow floor holds).
        assert all(b.low > 0 for b in bars)
