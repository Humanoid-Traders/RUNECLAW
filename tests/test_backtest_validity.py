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
