"""
Covariance-based portfolio VaR (roadmap H-05).

These tests pin three things:
  1. Default OFF is a byte-for-byte no-op — the per-trade proxy still runs.
  2. When enabled but the price history is insufficient, it FALLS BACK to the
     per-trade proxy (it never silently downgrades the check to a skip).
  3. When enabled with real history, the matrix math is correct AND an opposing
     hedge nets portfolio variance down — the whole point of the upgrade.

The covariance path is validated against an independent reference computation
(Python's ``statistics``) rather than a hardcoded magic number.
"""

import dataclasses
import os
import statistics
import tempfile

from unittest.mock import MagicMock

import bot.config as cfg_mod
from bot.config import CONFIG


def _make_idea(asset="BTC/USDT", direction_value="LONG"):
    idea = MagicMock()
    idea.direction = MagicMock()
    idea.direction.value = direction_value
    idea.asset = asset
    return idea


def _make_position(asset, entry_price, quantity, direction_value="LONG"):
    pos = MagicMock()
    pos.asset = asset
    pos.entry_price = entry_price
    pos.quantity = quantity
    pos.direction = MagicMock()
    pos.direction.value = direction_value
    return pos


def _make_closed_trade(entry_price, exit_price, quantity, pnl, direction_value="LONG"):
    t = MagicMock()
    t.entry_price = entry_price
    t.exit_price = exit_price
    t.quantity = quantity
    t.pnl = pnl
    t.direction = MagicMock()
    t.direction.value = direction_value
    t.asset = "BTC/USDT"
    return t


def _engine(open_positions=None, trade_history=None, equity=10_000.0):
    portfolio = MagicMock()
    portfolio.open_positions = open_positions or []
    portfolio.trade_history = trade_history or []
    snap = MagicMock()
    snap.equity_usd = equity
    snap.open_positions = len(open_positions or [])
    portfolio.snapshot.return_value = snap
    portfolio.get_position_value.return_value = 0.0

    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
        state_file = f.name
    try:
        from bot.risk.risk_engine import RiskEngine
        engine = RiskEngine(portfolio, state_file=state_file)
    finally:
        if os.path.exists(state_file):
            os.unlink(state_file)
    return engine


def _enable_covariance(monkeypatch, min_points=5):
    new_risk = dataclasses.replace(
        CONFIG.risk, var_covariance_enabled=True, var_covariance_min_points=min_points)
    new_cfg = dataclasses.replace(CONFIG, risk=new_risk)
    monkeypatch.setattr(cfg_mod, "CONFIG", new_cfg)
    monkeypatch.setattr("bot.risk.risk_engine.CONFIG", new_cfg, raising=False)
    return new_cfg


# A price series with genuine (non-zero) variance, reused across tests.
_PRICES = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.0, 104.0, 103.5, 105.0]


def _returns(prices):
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


class TestDefaultOff:
    def test_disabled_by_default(self):
        assert CONFIG.risk.var_covariance_enabled is False

    def test_default_off_is_a_noop(self):
        # With the flag OFF, passing an idea must change nothing: the result is
        # identical to the legacy per-trade call that takes no idea.
        trades = [
            _make_closed_trade(100, 105, 1.0, 5.0),
            _make_closed_trade(100, 98, 1.0, -2.0),
            _make_closed_trade(100, 103, 1.0, 3.0),
            _make_closed_trade(100, 97, 1.0, -3.0),
            _make_closed_trade(100, 110, 1.0, 10.0),
        ]
        engine = _engine(trade_history=trades)
        # Even with price history present, the OFF flag means it's never consulted.
        engine._price_history = {"BTC/USDT": list(_PRICES)}
        legacy = engine._compute_portfolio_var(1000.0)
        with_idea = engine._compute_portfolio_var(1000.0, idea=_make_idea())
        assert legacy.status == with_idea.status
        assert legacy.current_var_pct == with_idea.current_var_pct
        assert legacy.proposed_var_pct == with_idea.proposed_var_pct


class TestFallback:
    def test_enabled_but_no_history_falls_back_to_per_trade(self, monkeypatch):
        _enable_covariance(monkeypatch)
        trades = [
            _make_closed_trade(100, 105, 1.0, 5.0),
            _make_closed_trade(100, 98, 1.0, -2.0),
            _make_closed_trade(100, 103, 1.0, 3.0),
            _make_closed_trade(100, 97, 1.0, -3.0),
            _make_closed_trade(100, 110, 1.0, 10.0),
        ]
        engine = _engine(trade_history=trades)
        # No _price_history → covariance returns None → per-trade proxy runs.
        result = engine._compute_portfolio_var(1000.0, idea=_make_idea())
        assert result.status == "OK"
        # Matches what the per-trade path alone would produce.
        ref = engine._compute_portfolio_var(1000.0)
        assert result.proposed_var_pct == ref.proposed_var_pct

    def test_enabled_short_history_falls_back_not_skip(self, monkeypatch):
        _enable_covariance(monkeypatch, min_points=20)
        engine = _engine()
        engine._price_history = {"BTC/USDT": list(_PRICES)}  # only ~9 returns < 20
        # Too few points for covariance AND zero closed trades → per-trade SKIPs.
        result = engine._compute_portfolio_var(1000.0, idea=_make_idea())
        assert result.status == "SKIP"


class TestCovarianceMath:
    def test_single_asset_matches_reference(self, monkeypatch):
        _enable_covariance(monkeypatch)
        engine = _engine(equity=10_000.0)
        engine._price_history = {"BTC/USDT": list(_PRICES)}
        position_usd = 500.0
        result = engine._compute_portfolio_var(position_usd, idea=_make_idea())
        assert result.status == "OK"

        # Independent reference: VaR% = z * |w| * std(returns) * 100.
        rets = _returns(_PRICES)
        std = statistics.stdev(rets)  # sample std (ddof=1)
        lev = getattr(CONFIG.exchange, "default_leverage", 1) or 1
        w = (position_usd * lev) / 10_000.0
        expected = 1.645 * abs(w) * std * 100.0
        assert abs(result.proposed_var_pct - expected) < 1e-3
        # No open positions → current VaR is zero.
        assert result.current_var_pct == 0.0

    def test_covariance_runs_with_zero_closed_trades(self, monkeypatch):
        # The whole point of H-05: it does NOT need closed-trade history, only
        # price series. So it computes even when the per-trade proxy would SKIP.
        _enable_covariance(monkeypatch)
        engine = _engine(trade_history=[])  # zero closed trades
        engine._price_history = {"BTC/USDT": list(_PRICES)}
        result = engine._compute_portfolio_var(500.0, idea=_make_idea())
        assert result.status == "OK"
        assert result.proposed_var_pct > 0.0

    def test_hedge_nets_variance_below_double_long(self, monkeypatch):
        # Two PERFECTLY correlated assets (identical return series). A long+short
        # pair of equal notional should net portfolio variance ~0; two longs of
        # equal notional should add. Hedge VaR must be far below double-long VaR.
        _enable_covariance(monkeypatch)
        eth = _make_position("ETH/USDT", entry_price=100.0, quantity=5.0,
                             direction_value="LONG")  # notional 500
        engine = _engine(open_positions=[eth], equity=10_000.0)
        engine._price_history = {
            "ETH/USDT": list(_PRICES),
            "BTC/USDT": list(_PRICES),  # identical → correlation = 1.0
        }
        # Proposed BTC long, same notional as the ETH leg (500 = 500*lev? use lev).
        lev = getattr(CONFIG.exchange, "default_leverage", 1) or 1
        notional_match = 500.0 / lev  # so proposed notional == 500 == ETH leg

        both_long = engine._compute_portfolio_var(
            notional_match, idea=_make_idea("BTC/USDT", "LONG"))
        hedge = engine._compute_portfolio_var(
            notional_match, idea=_make_idea("BTC/USDT", "SHORT"))

        assert both_long.status == "OK" and hedge.status == "OK"
        assert hedge.proposed_var_pct < both_long.proposed_var_pct
        # Perfect-hedge equal-notional → essentially flat.
        assert hedge.proposed_var_pct < 1e-6

    def test_two_longs_add_more_than_one(self, monkeypatch):
        _enable_covariance(monkeypatch)
        eth = _make_position("ETH/USDT", entry_price=100.0, quantity=5.0,
                             direction_value="LONG")
        engine = _engine(open_positions=[eth], equity=10_000.0)
        engine._price_history = {
            "ETH/USDT": list(_PRICES),
            "BTC/USDT": list(_PRICES),
        }
        lev = getattr(CONFIG.exchange, "default_leverage", 1) or 1
        notional_match = 500.0 / lev
        result = engine._compute_portfolio_var(
            notional_match, idea=_make_idea("BTC/USDT", "LONG"))
        # current = ETH leg alone; proposed = ETH + BTC both long, correlated.
        assert result.proposed_var_pct > result.current_var_pct > 0.0
