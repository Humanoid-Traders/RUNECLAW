"""
Backtest gate-rejection visibility (A/B comparability).

Every A/B on the frozen benchmark was confounded by the STATEFUL risk gates
(circuit-breaker / live-perf governor / loss-streak / …) firing at different
points because they depend on prior-trade cumulative PnL. The backtest now
tallies rejections per gate and separates the path-dependent (stateful) ones,
so a diff there flags an A/B comparison as untrustworthy (different trade set)
rather than a real parameter effect.
"""
from bot.backtest.engine import STATEFUL_RISK_GATES, _gate_token
from bot.backtest.models import BacktestResult


def test_gate_token_extracts_name():
    assert _gate_token("CIRCUIT_BREAKER: system halted due to prior losses") == "CIRCUIT_BREAKER"
    assert _gate_token("MTF_ALIGNMENT: higher-timeframe trend BEARISH") == "MTF_ALIGNMENT"
    assert _gate_token("CONFIDENCE: 0.59 < 0.6 minimum") == "CONFIDENCE"
    assert _gate_token("bare") == "bare"
    assert _gate_token("") == ""


def test_stateful_set_covers_path_dependent_gates():
    # The gates whose firing depends on prior-trade OUTCOMES.
    for g in ("CIRCUIT_BREAKER", "LIVE_PERF_GOVERNOR", "LOSS_STREAK",
              "COOLDOWN", "DAILY_LOSS", "DRAWDOWN", "WARNING_RATE_BREAKER"):
        assert g in STATEFUL_RISK_GATES
    # Stateless (function of the current bar/idea) must NOT be flagged.
    for g in ("MTF_ALIGNMENT", "RSI_BLOCK", "CONFIDENCE", "RISK_REWARD",
              "VOLATILITY", "MACRO_EVENT"):
        assert g not in STATEFUL_RISK_GATES


def test_result_carries_gate_tally_fields():
    r = BacktestResult(
        symbol="X", timeframe="1h", start_date="a", end_date="b",
        initial_balance=100.0, commission_pct=0.1, slippage_pct=0.0,
        final_equity=100.0, total_return_pct=0.0, total_pnl=0.0,
        net_pnl=0.0, total_commission=0.0, total_slippage=0.0,
        total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
        avg_win_usd=0.0, avg_loss_usd=0.0, largest_win_usd=0.0,
        largest_loss_usd=0.0, avg_trade_duration_hours=0.0,
        max_drawdown_pct=0.0, max_drawdown_usd=0.0, max_consecutive_losses=0,
        profit_factor=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
        calmar_ratio=0.0, risk_reward_avg=0.0, total_signals_generated=0,
        total_ideas_generated=0, total_ideas_rejected_risk=0,
        total_ideas_rejected_confidence=0,
        rejections_by_gate={"CIRCUIT_BREAKER": 5, "MTF_ALIGNMENT": 3},
        stateful_rejections=5)
    assert r.rejections_by_gate["CIRCUIT_BREAKER"] == 5
    assert r.stateful_rejections == 5
    # Default when omitted
    assert BacktestResult.model_fields["rejections_by_gate"].default_factory() == {}


def test_stateful_rejection_sum_logic():
    """The engine's stateful_rejections = sum of counts for stateful gates only."""
    tally = {"CIRCUIT_BREAKER": 8, "LIVE_PERF_GOVERNOR": 5,
             "MTF_ALIGNMENT": 12, "CONFIDENCE": 4}
    stateful = sum(c for g, c in tally.items() if g in STATEFUL_RISK_GATES)
    assert stateful == 13   # 8 + 5, NOT the stateless 12 + 4
