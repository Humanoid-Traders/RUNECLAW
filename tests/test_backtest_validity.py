"""
Regression tests for the V6.1 HIGH backtest-validity fixes (BT-H1, BT-H2).

BT-H1 — PortfolioTracker now honors a per-portfolio commission_pct override, so
        the backtest charges the fee it reports (the config knob was ignored).
BT-H2 — analyze()/evaluate() accept an `as_of` time so session-aware
        confidence/sizing uses the simulated bar time, not the wall clock.
        This makes backtests deterministic and causal.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from bot.compat import UTC
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeIdea


def _idea():
    return TradeIdea(
        id="TI-test", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        confidence=0.8, risk_reward_ratio=2.0, reasoning="test",
    )


# ── BT-H1: commission override is honored and proportional ──────────

def test_commission_override_changes_charged_fee():
    def _commission_for(pct):
        p = PortfolioTracker(initial_balance=10_000.0, commission_pct=pct)
        p.open_position(_idea(), size_usd=1000.0, leverage=1)
        tid = p.open_positions[0].trade_id
        closed = p.close_position(tid, exit_price=100.0)  # flat: isolate fees
        return closed.commission

    c_low = _commission_for(0.06)
    c_high = _commission_for(0.10)
    # 0.10% must charge more than 0.06%, and in the right ratio.
    assert c_high > c_low > 0
    assert c_high == pytest.approx(c_low * (0.10 / 0.06), rel=1e-6)
    # 0.10% on a 1000 entry + 1000 exit notional = (2000)*0.001 = 2.0
    assert c_high == pytest.approx(2.0, rel=1e-6)


def test_commission_override_none_uses_config_default():
    from bot.config import CONFIG
    p = PortfolioTracker(initial_balance=10_000.0)  # no override
    p.open_position(_idea(), size_usd=1000.0, leverage=1)
    tid = p.open_positions[0].trade_id
    closed = p.close_position(tid, exit_price=100.0)
    expected = 2000.0 * (CONFIG.risk.commission_pct / 100.0)
    assert closed.commission == pytest.approx(expected, rel=1e-6)


# ── BT-H2: a full backtest run is wall-clock independent + reproducible ──

def _run_once(seed, fake_now=None, monkeypatch=None):
    import logging
    logging.disable(logging.WARNING)
    import run_deep_backtest as rdb
    if fake_now is not None:
        import bot.core.session_aware as sa

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now
        monkeypatch.setattr(sa, "datetime", _FrozenDT)
    sym = {"symbol": "ETH/USDT", "price": 2550.0, "vol": 0.018, "name": "Ethereum"}
    regime = {"trend": 0.0003, "label": "Bull Trend", "vol_mult": 1.0}
    return asyncio.run(rdb.run_single_backtest(sym, regime, seed))


def test_backtest_is_reproducible_same_seed():
    r1 = _run_once(42)
    r2 = _run_once(42)
    for k in ("total_trades", "total_return_pct", "win_rate", "net_pnl",
              "total_commission", "sharpe_ratio", "max_drawdown_pct"):
        assert r1[k] == r2[k], f"{k} differs: {r1[k]} != {r2[k]}"


def test_backtest_independent_of_wall_clock(monkeypatch):
    """Same seed must yield identical results no matter the wall-clock hour —
    proving session adjustments use the bar time (as_of), not datetime.now()."""
    asian = datetime(2025, 6, 2, 3, 0, tzinfo=UTC)    # Asian session
    overlap = datetime(2025, 6, 2, 14, 0, tzinfo=UTC)  # London/NY overlap
    r_asian = _run_once(42, fake_now=asian, monkeypatch=monkeypatch)
    monkeypatch.undo()
    r_overlap = _run_once(42, fake_now=overlap, monkeypatch=monkeypatch)
    for k in ("total_trades", "total_return_pct", "net_pnl", "total_commission"):
        assert r_asian[k] == r_overlap[k], (
            f"{k} depends on wall clock: asian={r_asian[k]} overlap={r_overlap[k]}")


# ── BT-L: metric-convention fixes (Sharpe ddof, Calmar annualized, breakeven) ──

def _make_trade(net_pnl):
    from bot.backtest.models import BacktestTrade
    return BacktestTrade(
        trade_id="t", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        exit_price=101.0, entry_time=datetime(2025, 1, 1, tzinfo=UTC),
        exit_time=datetime(2025, 1, 1, 4, tzinfo=UTC), quantity=1.0, size_usd=100.0,
        pnl_usd=net_pnl, pnl_pct=0.0, commission_usd=0.0, slippage_usd=0.0,
        net_pnl_usd=net_pnl, exit_reason="TP", confidence=0.7, risk_verdict="APPROVED",
    )


def _engine():
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    return BacktestEngine(BacktestConfig(symbol="BTC/USDT", timeframe="1h",
                                         initial_balance=10_000.0))


def test_breakeven_is_neutral_not_a_loss():
    """A breakeven trade between two losses must not extend the loss streak nor
    be counted among losers (matches the risk engine's pnl==0 handling)."""
    eng = _engine()
    try:
        eng._trades = [_make_trade(-50.0), _make_trade(0.0), _make_trade(-50.0)]
        res = eng._compile_result(bars=[], duration=0.0)
        # Old (<=0) behavior would give 3 consecutive losses; neutral gives 2.
        assert res.max_consecutive_losses == 2
        assert res.losing_trades == 2  # breakeven excluded
    finally:
        eng.cleanup()


def test_sharpe_uses_sample_stddev_ddof1():
    """_compute_sharpe must use ddof=1; verify against a manual computation."""
    import numpy as np
    from bot.backtest.models import EquityPoint
    eng = _engine()
    try:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        eqs = [10000, 10100, 10050, 10200, 10150, 10300]
        eng._equity_curve = [
            EquityPoint(timestamp=base + timedelta(hours=i), equity=float(e),
                        drawdown_pct=0.0, open_positions=0)
            for i, e in enumerate(eqs)
        ]
        got = eng._compute_sharpe()
        # Manual ddof=1 reproduction
        returns = np.diff(eqs) / np.array(eqs[:-1])
        std1 = np.std(returns, ddof=1)
        ppy = (365.25 * 24 * 3600) / ((len(eqs) - 1) and
              ((eng._equity_curve[-1].timestamp - eng._equity_curve[0].timestamp).total_seconds() / (len(eqs) - 1)))
        excess = np.mean(returns) - 0.04 / ppy
        expected = float(excess / std1 * np.sqrt(ppy))
        assert got == pytest.approx(expected, rel=1e-6)
        # And it must differ from the ddof=0 value.
        std0 = np.std(returns, ddof=0)
        ddof0_val = float((np.mean(returns) - 0.04 / ppy) / std0 * np.sqrt(ppy))
        assert got != pytest.approx(ddof0_val, rel=1e-9)
    finally:
        eng.cleanup()


def test_calmar_is_annualized():
    """Calmar must annualize the return: for a ~30-day span it should be ~12x the
    raw period-return/maxDD ratio."""
    from bot.backtest.models import EquityPoint
    eng = _engine()
    try:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        # 30-day span, final equity +3%, max drawdown 2%.
        eng._equity_curve = [
            EquityPoint(timestamp=base, equity=10_000.0, drawdown_pct=0.0, open_positions=0),
            EquityPoint(timestamp=base + timedelta(days=15), equity=9_800.0, drawdown_pct=2.0, open_positions=0),
            EquityPoint(timestamp=base + timedelta(days=30), equity=10_300.0, drawdown_pct=0.0, open_positions=0),
        ]
        # Force portfolio equity to match final point for total_return.
        eng.portfolio.balance = 10_300.0
        res = eng._compile_result(bars=[], duration=0.0)
        raw = 3.0 / 2.0  # period return% / maxDD%
        # ~30-day span annualizes by ~365.25/30 ≈ 12.2x
        assert res.calmar_ratio > raw * 8
    finally:
        eng.cleanup()
