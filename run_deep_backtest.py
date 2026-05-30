"""
RUNECLAW Deep Backtest -- Top 10 Crypto Symbols
Runs comprehensive backtests across multiple market conditions.
"""
import asyncio
import json
import sys
import os
import time
from datetime import timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig, BacktestBar
from bot.backtest.data_loader import DataLoader


# Top 10 symbols with realistic starting prices and volatility profiles
SYMBOLS = [
    {"symbol": "BTC/USDT",  "price": 108000.0, "vol": 0.012, "name": "Bitcoin"},
    {"symbol": "ETH/USDT",  "price": 2550.0,   "vol": 0.018, "name": "Ethereum"},
    {"symbol": "SOL/USDT",  "price": 180.0,     "vol": 0.025, "name": "Solana"},
    {"symbol": "BNB/USDT",  "price": 640.0,     "vol": 0.015, "name": "BNB"},
    {"symbol": "XRP/USDT",  "price": 2.35,      "vol": 0.022, "name": "XRP"},
    {"symbol": "ADA/USDT",  "price": 0.78,      "vol": 0.025, "name": "Cardano"},
    {"symbol": "DOGE/USDT", "price": 0.23,      "vol": 0.030, "name": "Dogecoin"},
    {"symbol": "AVAX/USDT", "price": 24.0,      "vol": 0.028, "name": "Avalanche"},
    {"symbol": "LINK/USDT", "price": 16.5,      "vol": 0.022, "name": "Chainlink"},
    {"symbol": "SUI/USDT",  "price": 3.80,      "vol": 0.030, "name": "Sui"},
]

# Market regimes to test
REGIMES = [
    {"trend": 0.0003,  "label": "Bull Trend",      "vol_mult": 1.0},
    {"trend": -0.0003, "label": "Bear Trend",       "vol_mult": 1.0},
    {"trend": 0.0,     "label": "Range/Chop",       "vol_mult": 1.0},
    {"trend": 0.0,     "label": "High Volatility",  "vol_mult": 1.8},
    {"trend": None,    "label": "Crash Recovery",    "vol_mult": 1.0},  # special handling
]

BARS = 1500        # ~62 days of 1H data per run
SEEDS = [42, 137, 256, 512, 1024]  # 5 seeds per regime for statistical robustness

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_deep_results.json")


def generate_bars_for_regime(sym_info, regime, seed):
    """Generate synthetic bars, with special handling for Crash Recovery."""
    vol = sym_info["vol"] * regime["vol_mult"]

    if regime["label"] == "Crash Recovery":
        # First half: crash (trend=-0.0006), second half: recovery (trend=+0.0004)
        half = BARS // 2
        second_half_bars = BARS - half

        # Generate first half (crash)
        bars_first = DataLoader.generate_synthetic(
            bars=half,
            start_price=sym_info["price"],
            volatility=vol,
            trend=-0.0006,
            seed=seed,
        )

        # Second half starts where first half ended
        last_close = bars_first[-1].close
        last_ts = bars_first[-1].timestamp

        bars_second_raw = DataLoader.generate_synthetic(
            bars=second_half_bars,
            start_price=last_close,
            volatility=vol,
            trend=0.0004,
            seed=seed + 10000,  # different seed for second half
        )

        # Adjust timestamps on second half to continue from first half
        bars_second = []
        for j, bar in enumerate(bars_second_raw):
            bars_second.append(BacktestBar(
                timestamp=last_ts + timedelta(hours=j + 1),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            ))

        return bars_first + bars_second
    else:
        return DataLoader.generate_synthetic(
            bars=BARS,
            start_price=sym_info["price"],
            volatility=vol,
            trend=regime["trend"],
            seed=seed,
        )


async def run_single_backtest(sym_info, regime, seed):
    """Run a single backtest and return summary dict."""
    config = BacktestConfig(
        symbol=sym_info["symbol"],
        timeframe="1h",
        initial_balance=10000.0,
        commission_pct=0.1,
        slippage_pct=0.05,
        use_llm=False,
        lookback_size=100,
        scan_interval=4,
    )
    engine = BacktestEngine(config)
    bars = generate_bars_for_regime(sym_info, regime, seed)

    try:
        result = await engine.run(bars)
    except Exception as e:
        # Catch validation errors (e.g. DOGE/low-price rounding issues) and surface them
        raise RuntimeError(f"engine.run failed: {e}") from e

    avg_pnl = result.net_pnl / result.total_trades if result.total_trades > 0 else 0.0

    return {
        "symbol": sym_info["symbol"],
        "name": sym_info["name"],
        "regime": regime["label"],
        "seed": seed,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "profit_factor": result.profit_factor,
        "calmar_ratio": result.calmar_ratio,
        "risk_reward_avg": result.risk_reward_avg,
        "avg_trade_pnl": round(avg_pnl, 2),
        "largest_win_usd": result.largest_win_usd,
        "largest_loss_usd": result.largest_loss_usd,
        "avg_win_usd": result.avg_win_usd,
        "avg_loss_usd": result.avg_loss_usd,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "max_consecutive_losses": result.max_consecutive_losses,
        "total_commission": result.total_commission,
        "total_slippage": result.total_slippage,
        "net_pnl": result.net_pnl,
        "final_equity": round(result.final_equity, 2),
        "avg_trade_duration_hours": result.avg_trade_duration_hours,
        "total_signals_generated": result.total_signals_generated,
        "total_ideas_generated": result.total_ideas_generated,
        "total_ideas_rejected_risk": result.total_ideas_rejected_risk,
        "total_ideas_rejected_confidence": result.total_ideas_rejected_confidence,
        "bars_processed": BARS,
    }


async def main():
    total_runs = len(SYMBOLS) * len(REGIMES) * len(SEEDS)
    print(f"RUNECLAW Deep Backtest: {len(SYMBOLS)} symbols x {len(REGIMES)} regimes x {len(SEEDS)} seeds = {total_runs} runs")
    print(f"Bars per run: {BARS} (1H candles, ~{BARS // 24} days)")
    print("=" * 100)

    all_results = []
    start = time.time()
    completed = 0
    error_count = 0

    for sym in SYMBOLS:
        for regime in REGIMES:
            for seed in SEEDS:
                try:
                    r = await run_single_backtest(sym, regime, seed)
                    all_results.append(r)
                    completed += 1
                    pct = completed / total_runs * 100
                    print(f"  [{completed:3d}/{total_runs}] {pct:5.1f}% | {sym['symbol']:12s} | {regime['label']:16s} | seed={seed:4d} | "
                          f"trades={r['total_trades']:3d} | ret={r['total_return_pct']:+7.2f}% | DD={r['max_drawdown_pct']:5.2f}% | "
                          f"WR={r['win_rate']:5.1f}% | sharpe={r['sharpe_ratio']:+6.2f}")
                except Exception as e:
                    completed += 1
                    error_count += 1
                    print(f"  [{completed:3d}/{total_runs}] ERROR: {sym['symbol']} {regime['label']} seed={seed}: {e}")
                    all_results.append({
                        "symbol": sym["symbol"], "name": sym["name"],
                        "regime": regime["label"], "seed": seed,
                        "error": str(e),
                    })

    elapsed = time.time() - start
    print("=" * 100)
    print(f"Completed {completed} runs in {elapsed:.1f}s ({elapsed/completed:.2f}s/run)")

    # ── Aggregate Statistics ──
    valid = [r for r in all_results if "error" not in r]
    errors = [r for r in all_results if "error" in r]

    if not valid:
        print("No valid results!")
        # Still save the error results
        with open(OUTPUT_PATH, "w") as f:
            json.dump({"meta": {"total_runs": total_runs, "errors": len(errors)}, "results": all_results}, f, indent=2, default=str)
        print(f"\nResults saved to {OUTPUT_PATH}")
        return

    # Per-symbol aggregates
    print("\n" + "=" * 100)
    print("PER-SYMBOL AGGREGATE (across all regimes and seeds)")
    print("=" * 100)
    print(f"{'Symbol':12s} {'Runs':>5s} {'Trades':>7s} {'Avg Ret%':>9s} {'Avg DD%':>8s} {'Avg WR%':>8s} {'Avg Sharpe':>11s} {'Avg PF':>8s} {'Worst DD%':>10s} {'Avg Sortino':>12s}")
    print("-" * 100)

    sym_agg = {}
    for r in valid:
        s = r["symbol"]
        if s not in sym_agg:
            sym_agg[s] = []
        sym_agg[s].append(r)

    for s in [x["symbol"] for x in SYMBOLS]:
        if s not in sym_agg:
            continue
        runs = sym_agg[s]
        n = len(runs)
        avg_trades = sum(r["total_trades"] for r in runs) / n
        avg_ret = sum(r["total_return_pct"] for r in runs) / n
        avg_dd = sum(r["max_drawdown_pct"] for r in runs) / n
        avg_wr = sum(r["win_rate"] for r in runs) / n
        avg_sharpe = sum(r["sharpe_ratio"] for r in runs) / n
        avg_pf = sum(r["profit_factor"] for r in runs) / n
        avg_sortino = sum(r["sortino_ratio"] for r in runs) / n
        worst_dd = max(r["max_drawdown_pct"] for r in runs)
        print(f"{s:12s} {n:5d} {avg_trades:7.0f} {avg_ret:+9.2f} {avg_dd:8.2f} {avg_wr:8.1f} {avg_sharpe:+11.2f} {avg_pf:8.2f} {worst_dd:10.2f} {avg_sortino:+12.2f}")

    # Per-regime aggregates
    print("\n" + "=" * 100)
    print("PER-REGIME AGGREGATE (across all symbols and seeds)")
    print("=" * 100)
    print(f"{'Regime':16s} {'Runs':>5s} {'Trades':>7s} {'Avg Ret%':>9s} {'Avg DD%':>8s} {'Avg WR%':>8s} {'Avg Sharpe':>11s} {'Avg PF':>8s} {'Avg Sortino':>12s}")
    print("-" * 100)

    for regime in REGIMES:
        runs = [r for r in valid if r["regime"] == regime["label"]]
        if not runs:
            continue
        n = len(runs)
        avg_trades = sum(r["total_trades"] for r in runs) / n
        avg_ret = sum(r["total_return_pct"] for r in runs) / n
        avg_dd = sum(r["max_drawdown_pct"] for r in runs) / n
        avg_wr = sum(r["win_rate"] for r in runs) / n
        avg_sharpe = sum(r["sharpe_ratio"] for r in runs) / n
        avg_pf = sum(r["profit_factor"] for r in runs) / n
        avg_sortino = sum(r["sortino_ratio"] for r in runs) / n
        print(f"{regime['label']:16s} {n:5d} {avg_trades:7.0f} {avg_ret:+9.2f} {avg_dd:8.2f} {avg_wr:8.1f} {avg_sharpe:+11.2f} {avg_pf:8.2f} {avg_sortino:+12.2f}")

    # Global summary
    total_trades = sum(r["total_trades"] for r in valid)
    avg_return = sum(r["total_return_pct"] for r in valid) / len(valid)
    avg_dd = sum(r["max_drawdown_pct"] for r in valid) / len(valid)
    avg_wr = sum(r["win_rate"] for r in valid) / len(valid)
    avg_sharpe = sum(r["sharpe_ratio"] for r in valid) / len(valid)
    avg_sortino = sum(r["sortino_ratio"] for r in valid) / len(valid)
    avg_pf = sum(r["profit_factor"] for r in valid) / len(valid)
    worst_dd = max(r["max_drawdown_pct"] for r in valid)
    best_ret = max(r["total_return_pct"] for r in valid)
    worst_ret = min(r["total_return_pct"] for r in valid)
    crashed = sum(1 for r in valid if r["max_drawdown_pct"] > 20)
    total_commission = sum(r["total_commission"] for r in valid)
    total_slippage = sum(r["total_slippage"] for r in valid)

    print("\n" + "=" * 100)
    print("GLOBAL SUMMARY")
    print("=" * 100)
    print(f"  Total runs:            {len(valid)} valid, {len(errors)} errors (of {total_runs} planned)")
    print(f"  Total trades:          {total_trades}")
    print(f"  Avg return:            {avg_return:+.2f}%")
    print(f"  Best return:           {best_ret:+.2f}%")
    print(f"  Worst return:          {worst_ret:+.2f}%")
    print(f"  Avg max drawdown:      {avg_dd:.2f}%")
    print(f"  Worst drawdown:        {worst_dd:.2f}%")
    print(f"  Avg win rate:          {avg_wr:.1f}%")
    print(f"  Avg Sharpe:            {avg_sharpe:+.2f}")
    print(f"  Avg Sortino:           {avg_sortino:+.2f}")
    print(f"  Avg profit factor:     {avg_pf:.2f}")
    print(f"  Crashed runs (DD>20%): {crashed}")
    print(f"  Total commission:      ${total_commission:,.2f}")
    print(f"  Total slippage:        ${total_slippage:,.2f}")
    print(f"  Runtime:               {elapsed:.1f}s")

    # Save full results
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "meta": {
                "symbols": len(SYMBOLS),
                "regimes": len(REGIMES),
                "seeds": len(SEEDS),
                "total_runs": total_runs,
                "valid_runs": len(valid),
                "error_runs": len(errors),
                "bars_per_run": BARS,
                "runtime_seconds": round(elapsed, 1),
            },
            "summary": {
                "total_trades": total_trades,
                "avg_return_pct": round(avg_return, 2),
                "best_return_pct": round(best_ret, 2),
                "worst_return_pct": round(worst_ret, 2),
                "avg_max_drawdown_pct": round(avg_dd, 2),
                "worst_drawdown_pct": round(worst_dd, 2),
                "avg_win_rate": round(avg_wr, 1),
                "avg_sharpe": round(avg_sharpe, 2),
                "avg_sortino": round(avg_sortino, 2),
                "avg_profit_factor": round(avg_pf, 2),
                "crashed_runs": crashed,
                "total_commission": round(total_commission, 2),
                "total_slippage": round(total_slippage, 2),
            },
            "results": all_results,
        }, f, indent=2, default=str)
    print(f"\nFull results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
