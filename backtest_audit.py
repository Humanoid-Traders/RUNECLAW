#!/usr/bin/env python3
"""
RUNECLAW Backtest Audit -- runs multiple backtests with different configurations
and prints a comparison table of all results.
"""

import asyncio
import sys
import os
import logging

# Suppress noisy log output to stderr so our table is readable.
# Only suppress bot loggers, not the global root — importing this module
# as a library won't silence the entire application's audit trail.
logging.getLogger("bot").setLevel(logging.CRITICAL)
logging.getLogger("runeclaw").setLevel(logging.CRITICAL)

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig


async def run_single_backtest(
    label: str,
    bars: int = 720,
    seed: int = 42,
    timeframe: str = "1h",
    volatility: float = 0.015,
    trend: float = 0.0001,
    start_price: float = 65000.0,
    initial_balance: float = 10000.0,
):
    """Run a single backtest and return a dict of key metrics."""
    config = BacktestConfig(
        symbol="BTC/USDT",
        timeframe=timeframe,
        initial_balance=initial_balance,
        commission_pct=0.1,
        slippage_pct=0.05,
        use_llm=False,
    )

    bar_data = DataLoader.generate_synthetic(
        bars=bars,
        start_price=start_price,
        volatility=volatility,
        trend=trend,
        seed=seed,
    )

    engine = BacktestEngine(config)
    result = await engine.run(bar_data)

    return {
        "label": label,
        "bars": result.bars_processed,
        "seed": seed,
        "timeframe": timeframe,
        "total_return_pct": result.total_return_pct,
        "final_equity": result.final_equity,
        "net_pnl": result.net_pnl,
        "win_rate": result.win_rate,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "max_drawdown_pct": result.max_drawdown_pct,
        "max_drawdown_usd": result.max_drawdown_usd,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "calmar_ratio": result.calmar_ratio,
        "profit_factor": result.profit_factor,
        "avg_win_usd": result.avg_win_usd,
        "avg_loss_usd": result.avg_loss_usd,
        "largest_win_usd": result.largest_win_usd,
        "largest_loss_usd": result.largest_loss_usd,
        "avg_trade_duration_hours": result.avg_trade_duration_hours,
        "max_consecutive_losses": result.max_consecutive_losses,
        "total_commission": result.total_commission,
        "total_slippage": result.total_slippage,
        "signals_generated": result.total_signals_generated,
        "ideas_generated": result.total_ideas_generated,
        "ideas_rejected_risk": result.total_ideas_rejected_risk,
        "ideas_rejected_confidence": result.total_ideas_rejected_confidence,
        "duration_seconds": result.duration_seconds,
        "start_date": result.start_date,
        "end_date": result.end_date,
    }


def print_divider(char="=", width=120):
    print(char * width)


def print_section(title):
    print()
    print_divider()
    print(f"  {title}")
    print_divider()


def print_result_detail(r):
    """Print a detailed single-run report."""
    print(f"\n  [{r['label']}]")
    print(f"    Period:           {r['start_date']} -> {r['end_date']}  ({r['bars']} bars, seed={r['seed']}, tf={r['timeframe']})")
    print(f"    Final Equity:     ${r['final_equity']:>12,.2f}    Total Return: {r['total_return_pct']:>+8.2f}%")
    print(f"    Net PnL:          ${r['net_pnl']:>12,.2f}    Commission: ${r['total_commission']:>8,.2f}  Slippage: ${r['total_slippage']:>8,.2f}")
    print(f"    Trades:           {r['total_trades']:>4}  (W:{r['winning_trades']} / L:{r['losing_trades']})   Win Rate: {r['win_rate']:.0%}")
    print(f"    Avg Win:          ${r['avg_win_usd']:>10,.2f}    Avg Loss: ${r['avg_loss_usd']:>10,.2f}")
    print(f"    Largest Win:      ${r['largest_win_usd']:>10,.2f}    Largest Loss: ${r['largest_loss_usd']:>10,.2f}")
    print(f"    Max Drawdown:     {r['max_drawdown_pct']:>8.2f}%  (${r['max_drawdown_usd']:>10,.2f})")
    print(f"    Max Consec Loss:  {r['max_consecutive_losses']}")
    print(f"    Sharpe:           {r['sharpe_ratio']:>8.2f}    Sortino: {r['sortino_ratio']:>8.2f}    Calmar: {r['calmar_ratio']:>8.2f}")
    print(f"    Profit Factor:    {r['profit_factor']:>8.2f}    Avg Duration: {r['avg_trade_duration_hours']:>6.1f}h")
    print(f"    Pipeline:         Signals={r['signals_generated']}  Ideas={r['ideas_generated']}  RiskRej={r['ideas_rejected_risk']}  ConfRej={r['ideas_rejected_confidence']}")
    exec_rate = (r['total_trades'] / r['ideas_generated'] * 100) if r['ideas_generated'] > 0 else 0
    print(f"    Execution Rate:   {exec_rate:.1f}% of ideas executed")
    print(f"    Runtime:          {r['duration_seconds']:.2f}s")


def print_comparison_table(results):
    """Print a compact comparison table."""
    print_section("COMPARISON TABLE")

    # Header
    headers = ["Run", "Bars", "Seed", "Return%", "NetPnL", "Trades", "WinRate", "MaxDD%", "Sharpe", "Sortino", "PF", "Signals", "Ideas", "RiskRej", "ConfRej"]
    fmt =     "{:<25} {:>5} {:>5} {:>8} {:>10} {:>6} {:>7} {:>7} {:>7} {:>7} {:>7} {:>7} {:>6} {:>7} {:>7}"
    print()
    print(fmt.format(*headers))
    print("-" * 135)

    for r in results:
        print(fmt.format(
            r["label"][:25],
            r["bars"],
            r["seed"],
            f"{r['total_return_pct']:+.2f}",
            f"${r['net_pnl']:,.0f}",
            r["total_trades"],
            f"{r['win_rate']:.0%}",
            f"{r['max_drawdown_pct']:.2f}",
            f"{r['sharpe_ratio']:.2f}",
            f"{r['sortino_ratio']:.2f}",
            f"{r['profit_factor']:.2f}",
            r["signals_generated"],
            r["ideas_generated"],
            r["ideas_rejected_risk"],
            r["ideas_rejected_confidence"],
        ))
    print()


def print_robustness_summary(seed_results):
    """Print a statistical summary across seeds."""
    if not seed_results:
        return

    print_section("ROBUSTNESS ANALYSIS (across seeds)")

    import numpy as np

    returns = [r["total_return_pct"] for r in seed_results]
    win_rates = [r["win_rate"] for r in seed_results]
    sharpes = [r["sharpe_ratio"] for r in seed_results]
    sortinos = [r["sortino_ratio"] for r in seed_results]
    drawdowns = [r["max_drawdown_pct"] for r in seed_results]
    pfs = [r["profit_factor"] for r in seed_results]
    trades = [r["total_trades"] for r in seed_results]

    def stat_line(name, values, fmt_str=".2f", suffix=""):
        arr = [v for v in values if v is not None]
        if not arr:
            return
        mn, mx, avg, med, std = min(arr), max(arr), np.mean(arr), np.median(arr), np.std(arr)
        print(f"    {name:<22}  Min={mn:{fmt_str}}{suffix}  Max={mx:{fmt_str}}{suffix}  "
              f"Mean={avg:{fmt_str}}{suffix}  Median={med:{fmt_str}}{suffix}  Std={std:{fmt_str}}{suffix}")

    print()
    stat_line("Total Return", returns, "+.2f", "%")
    stat_line("Win Rate", [w * 100 for w in win_rates], ".1f", "%")
    stat_line("Sharpe Ratio", sharpes)
    stat_line("Sortino Ratio", sortinos)
    stat_line("Max Drawdown", drawdowns, ".2f", "%")
    stat_line("Profit Factor", pfs)
    stat_line("Total Trades", trades, ".0f")

    profitable = sum(1 for r in returns if r > 0)
    print(f"\n    Profitable runs:  {profitable}/{len(returns)} ({profitable/len(returns)*100:.0f}%)")
    print()


async def main():
    print_divider("=", 80)
    print("  RUNECLAW BACKTEST AUDIT")
    print("  Running multiple configurations to assess strategy robustness")
    print_divider("=", 80)

    all_results = []
    seed_results = []

    # ── 1. Default run (720 bars, seed=42) ──
    print_section("RUN 1: Default (720 bars, seed=42)")
    r = await run_single_backtest("Default (720/s42)", bars=720, seed=42)
    all_results.append(r)
    print_result_detail(r)

    # ── 2. Longer period (2000 bars, seed=42) ──
    print_section("RUN 2: Longer Period (2000 bars, seed=42)")
    r = await run_single_backtest("Long (2000/s42)", bars=2000, seed=42)
    all_results.append(r)
    print_result_detail(r)

    # ── 3. Multi-seed robustness (720 bars, different seeds) ──
    seeds = [42, 123, 777, 2024, 9999]
    print_section(f"RUNS 3-7: Multi-Seed Robustness (720 bars, seeds: {seeds})")
    for i, seed in enumerate(seeds):
        label = f"Seed {seed} (720)"
        r = await run_single_backtest(label, bars=720, seed=seed)
        seed_results.append(r)
        if seed != 42:  # 42 already in all_results from Run 1
            all_results.append(r)
        print_result_detail(r)

    # ── 4. Different volatility regimes ──
    print_section("RUNS 8-9: Volatility Regimes (720 bars, seed=42)")

    r = await run_single_backtest("Low Vol (0.008)", bars=720, seed=42, volatility=0.008)
    all_results.append(r)
    print_result_detail(r)

    r = await run_single_backtest("High Vol (0.030)", bars=720, seed=42, volatility=0.030)
    all_results.append(r)
    print_result_detail(r)

    # ── 5. Different trend biases ──
    print_section("RUNS 10-11: Trend Biases (720 bars, seed=42)")

    r = await run_single_backtest("Bull Trend (0.0005)", bars=720, seed=42, trend=0.0005)
    all_results.append(r)
    print_result_detail(r)

    r = await run_single_backtest("Bear Trend (-0.0003)", bars=720, seed=42, trend=-0.0003)
    all_results.append(r)
    print_result_detail(r)

    # ── Comparison Table ──
    print_comparison_table(all_results)

    # ── Robustness Summary ──
    print_robustness_summary(seed_results)

    print_divider("=", 80)
    print("  AUDIT COMPLETE")
    print_divider("=", 80)


if __name__ == "__main__":
    asyncio.run(main())
