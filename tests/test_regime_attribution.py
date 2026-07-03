"""Edge attribution: per-regime / per-setup P&L breakdown + risk-adjusted
metrics. Shows WHERE the edge lives so entries can be gated to profitable
regimes — the go/no-go evidence the aggregate return can't provide."""

from datetime import datetime, timedelta

from bot.backtest.models import BacktestTrade
from bot.backtest.runner import _attribution_report, _group_stats, _risk_adjusted
from bot.compat import UTC


def _trade(net, regime="TREND_UP", setup="swing"):
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    return BacktestTrade(
        trade_id="x", symbol="BTC/USDT", direction="LONG",
        entry_price=100.0, exit_price=101.0, entry_time=t0,
        exit_time=t0 + timedelta(hours=1), quantity=1.0, size_usd=100.0,
        pnl_usd=net, pnl_pct=net, commission_usd=0.0, slippage_usd=0.0,
        net_pnl_usd=net, exit_reason="TP" if net > 0 else "SL",
        confidence=0.7, risk_verdict="APPROVED",
        entry_regime=regime, setup=setup)


class TestGroupStats:
    def test_per_regime_split(self):
        trades = [_trade(10, "TREND_UP"), _trade(20, "TREND_UP"),
                  _trade(-15, "CHOP"), _trade(-5, "CHOP")]
        g = _group_stats(trades, lambda t: t.entry_regime)
        assert g["TREND_UP"]["net_pnl"] == 30.0 and g["TREND_UP"]["win_rate"] == 1.0
        assert g["CHOP"]["net_pnl"] == -20.0 and g["CHOP"]["win_rate"] == 0.0
        # Sorted by net_pnl desc: the profitable regime leads.
        assert list(g.keys())[0] == "TREND_UP"

    def test_profit_factor(self):
        g = _group_stats([_trade(30), _trade(-10)], lambda t: t.entry_regime)
        assert g["TREND_UP"]["profit_factor"] == 3.0


class TestRiskAdjusted:
    def test_sortino_positive_when_upside_dominates(self):
        class _R:
            trades = [_trade(10), _trade(20), _trade(-5), _trade(15)]
            total_return_pct = 4.0
            max_drawdown_pct = 2.0
            sharpe_ratio = 1.5
        ra = _risk_adjusted(_R())
        assert ra["sortino"] > 0
        assert ra["calmar"] == 2.0   # 4.0 / 2.0


class TestAttributionReport:
    def test_report_shows_regime_and_setup_when_they_vary(self):
        class _R:
            trades = [_trade(10, "TREND_UP", "swing"),
                      _trade(-8, "CHOP", "scalp"),
                      _trade(12, "TREND_UP", "swing")]
            total_return_pct = 1.4
            max_drawdown_pct = 1.0
            sharpe_ratio = 1.0
        out = _attribution_report(_R())
        assert "EDGE ATTRIBUTION" in out
        assert "By regime" in out and "TREND_UP" in out and "CHOP" in out
        assert "By setup" in out and "swing" in out and "scalp" in out
        assert "Sortino" in out and "Calmar" in out

    def test_empty_trades_no_report(self):
        class _R:
            trades = []
        assert _attribution_report(_R()) == ""
