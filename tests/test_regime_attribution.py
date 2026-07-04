"""Edge attribution: per-regime / per-setup P&L breakdown + risk-adjusted
metrics. Shows WHERE the edge lives so entries can be gated to profitable
regimes — the go/no-go evidence the aggregate return can't provide."""

from datetime import datetime, timedelta

from bot.backtest.models import BacktestTrade
from bot.backtest.runner import (
    _attribution_report,
    _group_stats,
    _pooled_attribution_report,
    _risk_adjusted,
    _trend_alignment,
)
from bot.compat import UTC


def _trade(net, regime="TREND_UP", setup="swing", direction="LONG",
           signal_type="momentum_confluence"):
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    return BacktestTrade(
        trade_id="x", symbol="BTC/USDT", direction=direction,
        entry_price=100.0, exit_price=101.0, entry_time=t0,
        exit_time=t0 + timedelta(hours=1), quantity=1.0, size_usd=100.0,
        pnl_usd=net, pnl_pct=net, commission_usd=0.0, slippage_usd=0.0,
        net_pnl_usd=net, exit_reason="TP" if net > 0 else "SL",
        confidence=0.7, risk_verdict="APPROVED",
        entry_regime=regime, setup=setup, signal_type=signal_type)


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

    def test_report_shows_signal_type_and_trend_alignment(self):
        class _R:
            trades = [_trade(10, "TREND_UP", "swing", "LONG", "momentum_confluence"),
                      _trade(-8, "TREND_UP", "scalp", "SHORT", "vwap_reversion"),
                      _trade(12, "TREND_DOWN", "swing", "SHORT", "momentum_confluence")]
            total_return_pct = 1.4
            max_drawdown_pct = 1.0
            sharpe_ratio = 1.0
        out = _attribution_report(_R())
        assert "By signal type" in out
        assert "momentum_confluence" in out and "vwap_reversion" in out
        assert "By trend alignment" in out
        assert "with-trend" in out and "counter-trend" in out


class TestTrendAlignment:
    def test_long_in_uptrend_is_with_trend(self):
        assert _trend_alignment(_trade(1, "TREND_UP", direction="LONG")) == "with-trend"

    def test_short_in_uptrend_is_counter_trend(self):
        assert _trend_alignment(_trade(1, "TREND_UP", direction="SHORT")) == "counter-trend"

    def test_short_in_downtrend_is_with_trend(self):
        assert _trend_alignment(_trade(1, "TREND_DOWN", direction="SHORT")) == "with-trend"

    def test_long_in_downtrend_is_counter_trend(self):
        assert _trend_alignment(_trade(1, "TREND_DOWN", direction="LONG")) == "counter-trend"

    def test_non_trending_regime_is_neutral(self):
        for regime in ("RANGE", "CHOP", "BREAKOUT", "EXPANSION", "", "UNKNOWN"):
            assert "neutral" in _trend_alignment(_trade(1, regime, direction="LONG"))

    def test_case_insensitive(self):
        assert _trend_alignment(_trade(1, "trend_up", direction="long")) == "with-trend"

    def test_counter_trend_bleed_isolated_in_group_stats(self):
        # The lens that matters: with-trend wins, counter-trend bleeds.
        trades = [_trade(10, "TREND_UP", direction="LONG"),
                  _trade(8, "TREND_DOWN", direction="SHORT"),
                  _trade(-12, "TREND_UP", direction="SHORT"),
                  _trade(-9, "TREND_DOWN", direction="LONG")]
        g = _group_stats(trades, _trend_alignment)
        assert g["with-trend"]["net_pnl"] == 18.0
        assert g["counter-trend"]["net_pnl"] == -21.0


class TestPooledAttribution:
    def test_pools_trades_and_shows_all_buckets(self):
        trades = [_trade(10, "TREND_UP", "swing", "LONG", "momentum_confluence"),
                  _trade(-6, "TREND_UP", "scalp", "SHORT", "vwap_reversion"),
                  _trade(4, "TREND_DOWN", "swing", "SHORT", "regime_trend")]
        out = _pooled_attribution_report(trades, label="6-fold OOS pooled")
        assert "6-fold OOS pooled" in out
        assert "Pooled: 3 trades" in out
        # All four dimensions present.
        for section in ("By regime", "By setup", "By signal type", "By trend alignment"):
            assert section in out

    def test_empty_pool_is_blank(self):
        assert _pooled_attribution_report([], label="x") == ""

    def test_pooled_pf_matches_gross_ratio(self):
        # gross win 12, gross loss 6 -> PF 2.00
        trades = [_trade(8), _trade(4), _trade(-6)]
        out = _pooled_attribution_report(trades, label="x")
        assert "PF 2.00" in out
