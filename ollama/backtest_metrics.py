"""
RUNECLAW Backtest — Metrics
Turns a list of TradeResults into the numbers that actually tell you whether
there's an edge. Everything here is computed AFTER costs.

The headline number is EXPECTANCY in R-multiples: average R per trade. This is
the single most honest summary — it's denominated in units of risk, so it's
comparable across symbols and position sizes, and it already accounts for win
rate AND win/loss size together. Positive after costs = there may be an edge.
Zero or negative = there is no edge, no matter how good the eval score was.
"""

import numpy as np
from dataclasses import dataclass, asdict
from backtest_engine import TradeResult, Outcome


@dataclass
class BacktestMetrics:
    n_trades: int
    n_filled: int
    n_no_fill: int
    win_rate: float
    loss_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float          # ← the headline number
    profit_factor: float
    total_r: float
    total_pnl_quote: float
    total_costs_quote: float
    cost_drag_r: float           # how much costs ate, in R
    max_drawdown_pct: float
    max_drawdown_r: float
    sharpe: float
    sortino: float
    avg_bars_held: float
    target_rate: float
    stop_rate: float
    timeout_rate: float
    longest_loss_streak: int
    final_equity: float
    starting_equity: float
    return_pct: float

    def verdict(self) -> str:
        """A blunt, honest read — not advice, just what the numbers say."""
        if self.n_filled < 30:
            return ("INSUFFICIENT DATA — under 30 filled trades. Any conclusion "
                    "here is noise. Need a larger out-of-sample period.")
        if self.expectancy_r <= 0:
            return ("NO EDGE — expectancy is zero or negative after costs. The "
                    "model produces well-formed trades that do not make money on "
                    "unseen data. A higher eval score will not fix this.")
        if self.expectancy_r < 0.05:
            return ("MARGINAL — positive but tiny expectancy. Likely within noise "
                    "and fragile to cost/slippage assumptions. Not deployable.")
        if self.profit_factor < 1.2:
            return ("WEAK — positive expectancy but profit factor under 1.2. "
                    "Thin margin; stress-test costs before trusting it.")
        return ("PROMISING (in simulation) — positive expectancy and profit "
                "factor after costs on out-of-sample data. Next step is "
                "forward/paper testing, NOT live capital.")


def compute_metrics(results: list[TradeResult], starting_equity: float = 10_000.0,
                    bars_per_year: float = 8760) -> BacktestMetrics:
    """
    bars_per_year: for annualizing Sharpe. 8760 = hourly. Use 2190 for 4h,
    365 for daily. Only affects the Sharpe scale, not the edge sign.
    """
    filled = [r for r in results if r.outcome not in (Outcome.NO_FILL, Outcome.NO_DATA)]
    no_fill = [r for r in results if r.outcome == Outcome.NO_FILL]
    n = len(filled)

    if n == 0:
        return BacktestMetrics(
            n_trades=len(results), n_filled=0, n_no_fill=len(no_fill),
            win_rate=0, loss_rate=0, avg_win_r=0, avg_loss_r=0, expectancy_r=0,
            profit_factor=0, total_r=0, total_pnl_quote=0, total_costs_quote=0,
            cost_drag_r=0, max_drawdown_pct=0, max_drawdown_r=0, sharpe=0, sortino=0,
            avg_bars_held=0, target_rate=0, stop_rate=0, timeout_rate=0,
            longest_loss_streak=0, final_equity=starting_equity,
            starting_equity=starting_equity, return_pct=0,
        )

    r_mults = np.array([r.r_multiple for r in filled])
    pnls    = np.array([r.pnl_quote for r in filled])
    costs   = np.array([r.cost_quote for r in filled])

    wins   = r_mults[r_mults > 0]
    losses = r_mults[r_mults < 0]

    win_rate  = len(wins) / n
    loss_rate = len(losses) / n
    avg_win_r  = wins.mean() if len(wins) else 0.0
    avg_loss_r = losses.mean() if len(losses) else 0.0
    expectancy_r = r_mults.mean()

    gross_profit = wins.sum() if len(wins) else 0.0
    gross_loss   = abs(losses.sum()) if len(losses) else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Equity curve in quote currency (sequential, decision-ordered)
    ordered = sorted(filled, key=lambda x: x.entry_ts or x.idea.decision_ts)
    equity_curve = [starting_equity]
    for r in ordered:
        equity_curve.append(equity_curve[-1] + r.pnl_quote)
    equity_curve = np.array(equity_curve)

    # Max drawdown (% and R)
    running_max = np.maximum.accumulate(equity_curve)
    dd = (equity_curve - running_max) / running_max
    max_dd_pct = abs(dd.min()) * 100

    r_curve = np.concatenate([[0], np.cumsum([r.r_multiple for r in ordered])])
    r_running_max = np.maximum.accumulate(r_curve)
    max_dd_r = abs((r_curve - r_running_max).min())

    # Sharpe / Sortino on per-trade returns (net %)
    net_pcts = np.array([r.net_return_pct for r in filled]) / 100
    if net_pcts.std() > 0:
        sharpe = net_pcts.mean() / net_pcts.std() * np.sqrt(len(net_pcts))
    else:
        sharpe = 0.0
    downside = net_pcts[net_pcts < 0]
    if len(downside) >= 5 and downside.std() > 0:
        sortino = net_pcts.mean() / downside.std() * np.sqrt(len(net_pcts))
        sortino = float(np.clip(sortino, -50, 50))  # guard tiny-sample blowups
    else:
        sortino = 0.0

    # Outcome rates
    target_rate  = sum(1 for r in filled if r.outcome == Outcome.TARGET) / n
    stop_rate    = sum(1 for r in filled if r.outcome == Outcome.STOP) / n
    timeout_rate = sum(1 for r in filled if r.outcome == Outcome.TIMEOUT) / n

    # Longest losing streak
    streak = max_streak = 0
    for r in ordered:
        if r.r_multiple < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    final_equity = equity_curve[-1]
    return BacktestMetrics(
        n_trades=len(results), n_filled=n, n_no_fill=len(no_fill),
        win_rate=round(win_rate, 4), loss_rate=round(loss_rate, 4),
        avg_win_r=round(avg_win_r, 3), avg_loss_r=round(avg_loss_r, 3),
        expectancy_r=round(expectancy_r, 4),
        profit_factor=round(profit_factor, 3) if profit_factor != float("inf") else 999.0,
        total_r=round(r_mults.sum(), 2),
        total_pnl_quote=round(pnls.sum(), 2),
        total_costs_quote=round(costs.sum(), 2),
        cost_drag_r=round(costs.sum() / (starting_equity * 0.02), 3),  # in ~R units
        max_drawdown_pct=round(max_dd_pct, 2),
        max_drawdown_r=round(max_dd_r, 2),
        sharpe=round(sharpe, 3), sortino=round(sortino, 3),
        avg_bars_held=round(np.mean([r.bars_held for r in filled]), 1),
        target_rate=round(target_rate, 3), stop_rate=round(stop_rate, 3),
        timeout_rate=round(timeout_rate, 3),
        longest_loss_streak=max_streak,
        final_equity=round(final_equity, 2), starting_equity=starting_equity,
        return_pct=round((final_equity / starting_equity - 1) * 100, 2),
    )


def print_metrics(m: BacktestMetrics) -> None:
    print(f"\n{'='*60}")
    print(f"  BACKTEST METRICS (after costs, out-of-sample)")
    print(f"{'='*60}")
    print(f"  Trades         : {m.n_filled} filled ({m.n_no_fill} no-fill of {m.n_trades})")
    print(f"  Win rate       : {m.win_rate*100:.1f}%  (target {m.target_rate*100:.0f}% / stop {m.stop_rate*100:.0f}% / timeout {m.timeout_rate*100:.0f}%)")
    print(f"  Avg win / loss : +{m.avg_win_r:.2f}R / {m.avg_loss_r:.2f}R")
    print(f"  ┌─ EXPECTANCY  : {m.expectancy_r:+.4f} R per trade  ←── the number that matters")
    print(f"  └─ Profit factor: {m.profit_factor:.2f}")
    print(f"  Total          : {m.total_r:+.1f}R  (${m.total_pnl_quote:+,.0f})")
    print(f"  Costs paid     : ${m.total_costs_quote:,.0f}  (drag ~{m.cost_drag_r:.2f}R)")
    print(f"  Max drawdown   : {m.max_drawdown_pct:.1f}%  ({m.max_drawdown_r:.1f}R)")
    print(f"  Sharpe / Sortino: {m.sharpe:.2f} / {m.sortino:.2f}")
    print(f"  Longest loss streak: {m.longest_loss_streak}")
    print(f"  Equity         : ${m.starting_equity:,.0f} → ${m.final_equity:,.0f}  ({m.return_pct:+.1f}%)")
    print(f"\n  VERDICT: {m.verdict()}")
    print()


if __name__ == "__main__":
    # Self-test against a synthetic strategy
    from backtest_data import synth_ohlcv
    from backtest_engine import TradeIdea, run_portfolio, CostModel
    import numpy as np

    data = synth_ohlcv(n_bars=3000, regime="mixed", seed=3)
    rng = np.random.default_rng(0)
    ideas = []
    # Random entries — should produce ~zero edge after costs (the honest baseline)
    for i in range(50, 2500, 40):
        ts = data.df.index[i]
        px = data.df["close"].iloc[i]
        direction = "LONG" if rng.random() > 0.5 else "SHORT"
        if direction == "LONG":
            idea = TradeIdea("TEST/USDT","LONG", px, px*0.97, px*1.06, ts, risk_pct=2.0)
        else:
            idea = TradeIdea("TEST/USDT","SHORT", px, px*1.03, px*0.94, ts, risk_pct=2.0)
        ideas.append(idea)

    results = run_portfolio(ideas, {"TEST/USDT": data}, 10_000, CostModel(5, 3))
    m = compute_metrics(results, 10_000)
    print_metrics(m)
