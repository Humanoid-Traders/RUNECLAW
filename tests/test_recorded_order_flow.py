"""
Recorded order-flow replay for backtest ↔ live parity (deep-audit medium #17).

The backtest called analyze() with order_flow=None, so the smart-money voter /
order-flow confluence / veto / funding haircut never ran — backtest signals
diverged from live. Live order flow is now shadow-recordable (record_snapshot,
gated OF_RECORD_SNAPSHOTS) and replayed causally into the backtest via
RecordedOrderFlow, wired by BacktestEngine.use_recorded_order_flow (default OFF →
order_flow=None, identical to before).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.recorded_order_flow import RecordedOrderFlow, record_snapshot
from bot.core.order_flow import OrderFlowSignal

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _sig(symbol="BTC/USDT", ts=None, **over):
    fields = dict(symbol=symbol, book_imbalance=0.3, cvd_trend="rising",
                  whale_bias="accumulation", smart_money_score=0.45,
                  confidence=0.7, funding_rate=0.0001,
                  components_ok=["book", "cvd"],
                  timestamp=ts or T0)
    fields.update(over)
    return OrderFlowSignal(**fields)


class TestRecordRoundTrip:
    def test_record_then_load_reconstructs_signal(self, tmp_path):
        path = tmp_path / "of.jsonl"
        sig = _sig(ts=T0, book_imbalance=0.42, smart_money_score=0.6)
        assert record_snapshot(path, sig) is True

        rec = RecordedOrderFlow.from_jsonl(path)
        assert len(rec) == 1
        got = rec.signal_at("BTC/USDT", as_of=T0)
        assert got is not None
        assert got.symbol == "BTC/USDT"
        assert got.book_imbalance == pytest.approx(0.42)
        assert got.smart_money_score == pytest.approx(0.6)
        assert got.confidence == pytest.approx(0.7)

    def test_record_is_fail_open_on_bad_path(self):
        # A path under a file (not a dir) can't be created → returns False, no raise.
        assert record_snapshot("/dev/null/cannot/exist.jsonl", _sig()) is False


class TestCausalLookup:
    def _rec(self, tmp_path):
        path = tmp_path / "of.jsonl"
        for n in (0, 4, 8):
            record_snapshot(path, _sig(ts=T0 + timedelta(hours=n),
                                       smart_money_score=float(n)))
        return RecordedOrderFlow.from_jsonl(path)

    def test_returns_most_recent_at_or_before(self, tmp_path):
        rec = self._rec(tmp_path)
        # at hour 6 → most recent is the hour-4 snapshot
        got = rec.signal_at("BTC/USDT", as_of=T0 + timedelta(hours=6))
        assert got.smart_money_score == pytest.approx(4.0)
        # exactly at hour 8 → the hour-8 snapshot (bisect_right inclusive)
        got = rec.signal_at("BTC/USDT", as_of=T0 + timedelta(hours=8))
        assert got.smart_money_score == pytest.approx(8.0)

    def test_none_before_first_record(self, tmp_path):
        rec = self._rec(tmp_path)
        assert rec.signal_at("BTC/USDT", as_of=T0 - timedelta(hours=1)) is None

    def test_as_of_none_returns_latest(self, tmp_path):
        rec = self._rec(tmp_path)
        assert rec.signal_at("BTC/USDT", as_of=None).smart_money_score == pytest.approx(8.0)

    def test_unknown_symbol_returns_none(self, tmp_path):
        rec = self._rec(tmp_path)
        assert rec.signal_at("ETH/USDT", as_of=T0 + timedelta(hours=8)) is None


class TestLoaderResilience:
    def test_missing_file_is_empty(self, tmp_path):
        rec = RecordedOrderFlow.from_jsonl(tmp_path / "nope.jsonl")
        assert len(rec) == 0
        assert rec.signal_at("BTC/USDT", as_of=T0) is None

    def test_malformed_lines_skipped(self, tmp_path):
        path = tmp_path / "of.jsonl"
        record_snapshot(path, _sig(ts=T0))
        with open(path, "a") as f:
            f.write("not json\n")
            f.write('{"symbol": "BTC/USDT"}\n')          # no signal/ts → skipped
            f.write('{"ts": "2025-01-01T00:00:00+00:00", "signal": {"bad": 1}}\n')  # no symbol
        rec = RecordedOrderFlow.from_jsonl(path)
        assert len(rec) == 1  # only the good record survived


class TestBacktestWiring:
    def _engine(self, **cfg_over):
        cfg = BacktestConfig(symbol="BTC/USDT", **cfg_over)
        return BacktestEngine(cfg)

    def test_default_off_no_recorder(self):
        eng = self._engine()
        assert eng._recorded_order_flow is None

    def test_on_loads_recorder(self, tmp_path):
        path = tmp_path / "of.jsonl"
        record_snapshot(path, _sig(ts=T0))
        eng = self._engine(use_recorded_order_flow=True,
                           recorded_order_flow_path=str(path))
        assert eng._recorded_order_flow is not None
        assert len(eng._recorded_order_flow) == 1

    async def _run_process_bar(self, eng):
        eng.analyzer.analyze = AsyncMock(return_value=None)
        bars = [BacktestBar(timestamp=T0 + timedelta(hours=i),
                            open=100.0, high=101.0, low=99.0, close=100.0,
                            volume=1000.0, symbol="BTC/USDT")
                for i in range(35)]
        await eng._process_bar(bars[-1], bars, 34)
        return eng.analyzer.analyze

    async def test_off_passes_none_order_flow(self):
        eng = self._engine()
        mock = await self._run_process_bar(eng)
        assert mock.await_count == 1
        assert mock.await_args.kwargs.get("order_flow") is None

    async def test_on_injects_recorded_order_flow(self, tmp_path):
        path = tmp_path / "of.jsonl"
        record_snapshot(path, _sig(ts=T0, smart_money_score=0.55))
        eng = self._engine(use_recorded_order_flow=True,
                           recorded_order_flow_path=str(path))
        mock = await self._run_process_bar(eng)
        assert mock.await_count == 1
        injected = mock.await_args.kwargs.get("order_flow")
        assert injected is not None
        assert injected.smart_money_score == pytest.approx(0.55)
