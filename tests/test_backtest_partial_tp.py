"""
Backtest partial-TP / pyramiding parity (deep-audit medium #18).

The backtest historically modelled a single full entry and a single full exit,
while live trading scales out through the partial-TP ladder (TP1 50% @1.5R with
SL→breakeven, TP2 30% @2.5R locking 1R, runner 20% on an ATR trail). That made
backtest win-rate / R:R systematically diverge from live.

`BacktestEngine` now ports the SAME ladder (bot.core.partial_tp) into
`_check_stops_intrabar`, gated behind the env flag BACKTEST_PARTIAL_TP and
default OFF — so default backtests stay byte-identical to the single-exit model.

These tests exercise the ladder directly against crafted bars.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.config import CONFIG
from bot.core.partial_tp import create_partial_tp_state
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _bar(o, h, lo, c, n=0):
    return BacktestBar(timestamp=T0 + timedelta(hours=n), open=o, high=h,
                       low=lo, close=c, volume=1000.0, symbol="BTC/USDT")


def _engine(partial_on: bool) -> BacktestEngine:
    cfg = BacktestConfig(symbol="BTC/USDT", initial_balance=10_000.0,
                         commission_pct=0.1, slippage_pct=0.0)
    eng = BacktestEngine(cfg)
    eng._partial_tp_enabled = partial_on
    return eng


def _open(eng: BacktestEngine, direction: Direction, entry: float, sl: float,
          tp: float, size_usd: float = 1000.0, atr: float = 100.0):
    """Open a position in the engine's portfolio + register ladder bt_meta,
    mirroring what _execute_idea does (without the analyzer pipeline)."""
    idea = TradeIdea(asset="BTC/USDT", direction=direction, entry_price=entry,
                     stop_loss=sl, take_profit=tp, confidence=0.7,
                     reasoning="test")
    trade = eng.portfolio.open_position(idea, size_usd)
    eng._open_bt_positions[idea.id] = {
        "entry_time": T0,
        "adjusted_entry": entry,
        "commission_entry": size_usd * (eng.config.commission_pct / 100),
        "slippage_entry": 0.0,
        "idea": idea,
        "risk_verdict": "APPROVED",
    }
    if eng._partial_tp_enabled:
        eng._open_bt_positions[idea.id]["ptp_state"] = create_partial_tp_state(
            trade_id=idea.id, direction=direction.value, entry_price=entry,
            stop_loss=sl, take_profit=tp, quantity=trade.quantity, atr=atr,
        )
    return idea.id


@pytest.fixture(autouse=True)
def _ensure_ladder_enabled():
    # The ladder uses CONFIG.partial_tp thresholds; make sure the live default
    # (enabled) holds for these tests regardless of env.
    assert CONFIG.partial_tp.enabled
    yield


class TestFlagGating:
    def test_default_off_no_ladder_state(self, monkeypatch):
        monkeypatch.delenv("BACKTEST_PARTIAL_TP", raising=False)
        eng = BacktestEngine(BacktestConfig())
        assert eng._partial_tp_enabled is False

    def test_env_on(self, monkeypatch):
        monkeypatch.setenv("BACKTEST_PARTIAL_TP", "1")
        eng = BacktestEngine(BacktestConfig())
        assert eng._partial_tp_enabled is True

    def test_off_path_records_single_exit(self):
        # With the flag off, a winning long that touches TP exits ONCE.
        eng = _engine(partial_on=False)
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=120.0)
        # bt_meta has no ptp_state when off
        assert "ptp_state" not in eng._open_bt_positions[tid]
        eng._check_stops_intrabar(_bar(100, 121, 99, 120))  # hits TP
        assert len(eng._trades) == 1
        assert eng._trades[0].exit_reason == "TP"


class TestLongLadder:
    def test_tp1_scales_out_50pct_and_moves_breakeven(self):
        eng = _engine(partial_on=True)
        # entry 100, sl 90 → 1R = 10. TP1 @1.5R = 115.
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=200.0)
        state = eng._open_bt_positions[tid]["ptp_state"]
        full_qty = state.original_qty
        # Bar reaches 116 (past TP1 115) but not TP2 (125).
        eng._check_ladder_intrabar(tid, eng.portfolio._positions[tid],
                                   eng._open_bt_positions[tid], _bar(100, 116, 99, 114))
        assert state.tp1_hit and not state.tp2_hit
        # 50% closed
        assert eng._trades[-1].exit_reason == "TP1"
        assert abs(state.remaining_qty - full_qty * 0.5) < 1e-9
        # SL moved to breakeven (entry + 0.1% buffer)
        assert state.current_sl == pytest.approx(100.0 + 100.0 * 0.001)
        assert eng.portfolio._positions[tid].stop_loss == state.current_sl

    def test_full_ladder_tp1_tp2_then_runner_trails(self):
        eng = _engine(partial_on=True)
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=300.0, atr=10.0)
        state = eng._open_bt_positions[tid]["ptp_state"]
        full_qty = state.original_qty
        pos = eng.portfolio._positions[tid]
        meta = eng._open_bt_positions[tid]
        # One bar blasts through TP1 (115) and TP2 (125) → both scale-outs.
        eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 130, 99, 128))
        assert state.tp1_hit and state.tp2_hit
        # remaining = runner 20%
        assert abs(state.remaining_qty - full_qty * 0.2) < 1e-9
        # Two partial trades recorded (TP1, TP2)
        reasons = [t.exit_reason for t in eng._trades]
        assert reasons == ["TP1", "TP2"]
        # Runner trail engaged: SL moved above the locked-1R level (110) toward
        # best(130) - atr*mult.
        assert state.current_sl > 110.0

    def test_runner_stop_closes_remaining_next_bar(self):
        eng = _engine(partial_on=True)
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=300.0, atr=10.0)
        pos = eng.portfolio._positions[tid]
        meta = eng._open_bt_positions[tid]
        eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 130, 99, 128))
        sl_after_runner = meta["ptp_state"].current_sl
        # Next bar dips to the trailed stop → runner closes, position gone.
        eng._check_ladder_intrabar(tid, pos, meta, _bar(128, 129, sl_after_runner - 1, sl_after_runner - 0.5))
        assert tid not in eng._open_bt_positions
        assert eng._trades[-1].exit_reason == "TRAILING_SL"

    def test_quantity_conserved_across_scaleouts(self):
        eng = _engine(partial_on=True)
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=300.0, atr=10.0)
        full_qty = eng._open_bt_positions[tid]["ptp_state"].original_qty
        pos = eng.portfolio._positions[tid]
        meta = eng._open_bt_positions[tid]
        eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 130, 99, 128))
        # TP1 + TP2 closed qty + remaining runner == original.
        closed = sum(t.quantity for t in eng._trades)
        assert closed + pos.quantity == pytest.approx(full_qty, abs=1e-6)


class TestShortLadder:
    def test_short_tp1_tp2_scaleout(self):
        eng = _engine(partial_on=True)
        # short entry 100, sl 110 → 1R=10. TP1 @1.5R = 85, TP2 @2.5R = 75.
        tid = _open(eng, Direction.SHORT, entry=100.0, sl=110.0, tp=50.0, atr=10.0)
        state = eng._open_bt_positions[tid]["ptp_state"]
        pos = eng.portfolio._positions[tid]
        meta = eng._open_bt_positions[tid]
        # Bar drops through TP1 (85) and TP2 (75).
        eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 101, 70, 72))
        assert state.tp1_hit and state.tp2_hit
        assert [t.exit_reason for t in eng._trades] == ["TP1", "TP2"]
        # Short locks 1R: SL pulled down to entry - 1R = 90.
        assert state.current_sl <= 90.0 + 1e-9


class TestStopFirstPessimism:
    def test_gap_through_stop_closes_full_at_open(self):
        eng = _engine(partial_on=True)
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=200.0)
        pos = eng.portfolio._positions[tid]
        meta = eng._open_bt_positions[tid]
        # Bar gaps down: open 88 (below sl 90), even though it later prints 116.
        # Pessimistic SL-first: full stop at the gapped open, no TP1.
        eng._check_ladder_intrabar(tid, pos, meta, _bar(88, 116, 85, 100))
        assert tid not in eng._open_bt_positions
        assert len(eng._trades) == 1
        assert eng._trades[0].exit_reason == "SL"
        # Gap fill is at the open (worse than the stop), not the stop level.
        assert eng._trades[0].exit_price == pytest.approx(88.0)


class TestBalanceAccounting:
    def test_balance_conserved_vs_manual_pnl(self):
        eng = _engine(partial_on=True)
        start_balance = eng.portfolio.balance
        tid = _open(eng, Direction.LONG, entry=100.0, sl=90.0, tp=300.0, atr=10.0)
        # margin was deducted on open
        after_open = eng.portfolio.balance
        pos = eng.portfolio._positions[tid]
        meta = eng._open_bt_positions[tid]
        eng._check_ladder_intrabar(tid, pos, meta, _bar(100, 130, 99, 128))
        # Close the runner explicitly at 120.
        eng._close_position(tid, 120.0, _bar(128, 129, 119, 120, n=1), "MANUAL")
        # All margin returned + realized net PnL across every scale-out: final
        # balance == start + sum(net_pnl of all recorded trades).
        total_net = sum(t.net_pnl_usd for t in eng._trades)
        assert eng.portfolio.balance == pytest.approx(start_balance + total_net, abs=0.05)
        assert after_open < start_balance  # margin really was locked
