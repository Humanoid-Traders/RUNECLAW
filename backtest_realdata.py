#!/usr/bin/env python3
"""
RUNECLAW Real-Data Backtest -- uses historical Bitget OHLCV data.

This script addresses the key audit finding (F-1) that synthetic GBM/GARCH
backtests cannot validate the system's alpha-generating modules. It fetches
real historical data from Bitget and runs the backtest engine against it.

NOTE: This uses the rule-based fallback (use_llm=False) by default.
Set USE_LLM=true in .env to run with LLM analysis enabled (costs tokens).

Usage:
    python backtest_realdata.py                    # BTC/USDT 1h, 500 bars
    python backtest_realdata.py --symbol ETH/USDT  # ETH/USDT
    python backtest_realdata.py --llm              # Enable LLM analysis
    python backtest_realdata.py --symbols all      # Run multi-asset suite
"""

import asyncio
import sys
import os
import argparse
import logging
from datetime import datetime

logging.getLogger("runeclaw.trade").setLevel(logging.CRITICAL)
logging.getLogger("runeclaw.risk").setLevel(logging.CRITICAL)
logging.getLogger("runeclaw.system").setLevel(logging.CRITICAL)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig


DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
EXTENDED_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "SUI/USDT",
]


async def run_single(symbol: str, timeframe: str, limit: int, use_llm: bool):
    """Run a single real-data backtest and return metrics."""
    config = BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_balance=10000.0,
        commission_pct=0.1,
        slippage_pct=0.05,
        use_llm=use_llm,
    )

    try:
        bars = await DataLoader.from_bitget(symbol=symbol, timeframe=timeframe, limit=limit)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    if len(bars) < 50:
        return {"symbol": symbol, "error": f"Insufficient data: {len(bars)} bars"}

    engine = BacktestEngine(config)
    result = await engine.run(bars)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": result.bars_processed,
        "data_source": "Bitget historical OHLCV",
        "llm_enabled": use_llm,
        "total_return_pct": result.total_return_pct,
        "net_pnl": result.net_pnl,
        "total_trades": result.total_trades,
        "win_rate_pct": result.win_rate_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "calmar_ratio": result.calmar_ratio,
        "profit_factor": result.profit_factor,
        "signals_generated": result.signals_generated,
        "ideas_generated": result.ideas_generated,
        "risk_rejected": result.risk_rejected,
        "confidence_rejected": result.confidence_rejected,
    }


async def run_benchmark_bnh(symbol: str, timeframe: str, limit: int):
    """Buy-and-hold benchmark for comparison."""
    try:
        bars = await DataLoader.from_bitget(symbol=symbol, timeframe=timeframe, limit=limit)
    except Exception:
        return None

    if len(bars) < 2:
        return None

    start_price = bars[0].close
    end_price = bars[-1].close
    return_pct = ((end_price - start_price) / start_price) * 100.0
    return {"symbol": symbol, "bnh_return_pct": round(return_pct, 2)}


def print_results(results: list[dict], benchmarks: list[dict | None]):
    """Print formatted results table."""
    print("\n" + "=" * 100)
    print("RUNECLAW REAL-DATA BACKTEST RESULTS")
    print(f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Data source: Bitget historical OHLCV (live exchange data)")
    print("=" * 100)

    bnh_map = {}
    for b in benchmarks:
        if b:
            bnh_map[b["symbol"]] = b["bnh_return_pct"]

    header = f"{'Symbol':<12} {'Bars':>5} {'Return%':>9} {'B&H%':>7} {'Trades':>7} {'WinR%':>7} {'MaxDD%':>7} {'Sharpe':>7} {'PF':>6} {'LLM':>4}"
    print(header)
    print("-" * 100)

    for r in results:
        if "error" in r:
            print(f"{r['symbol']:<12} ERROR: {r['error']}")
            continue

        bnh = bnh_map.get(r["symbol"], "N/A")
        llm_flag = "ON" if r.get("llm_enabled") else "OFF"
        print(
            f"{r['symbol']:<12} "
            f"{r['bars']:>5} "
            f"{r['total_return_pct']:>8.2f}% "
            f"{bnh:>6}% " if isinstance(bnh, float) else f"{r['symbol']:<12} {r['bars']:>5} {r['total_return_pct']:>8.2f}% {'N/A':>6}  "
            f"{r['total_trades']:>7} "
            f"{r['win_rate_pct']:>6.1f}% "
            f"{r['max_drawdown_pct']:>6.2f}% "
            f"{r['sharpe_ratio']:>7.2f} "
            f"{r['profit_factor']:>5.2f} "
            f"{llm_flag:>4}"
        )

    print("-" * 100)
    print("\nMethodology notes:")
    print("  - Data: Real historical Bitget OHLCV (not synthetic)")
    print("  - Commission: 0.10% per side (Bitget VIP0 maker rate)")
    print("  - Slippage: 0.05% per trade")
    print("  - B&H% = Buy-and-hold benchmark return over same period")
    if any(r.get("llm_enabled") for r in results if "error" not in r):
        print("  - LLM: AI analysis enabled for trade thesis generation")
    else:
        print("  - LLM: OFF -- results reflect rule-based fallback only")
        print("  - To test with AI analysis: python backtest_realdata.py --llm")
    print()


async def main():
    parser = argparse.ArgumentParser(description="RUNECLAW real-data backtest")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--symbols", choices=["default", "all"], default=None,
                        help="Run multi-asset: 'default' (3) or 'all' (10)")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe")
    parser.add_argument("--bars", type=int, default=500, help="Number of bars")
    parser.add_argument("--llm", action="store_true", help="Enable LLM analysis")
    args = parser.parse_args()

    if args.symbols == "all":
        symbols = EXTENDED_SYMBOLS
    elif args.symbols == "default":
        symbols = DEFAULT_SYMBOLS
    else:
        symbols = [args.symbol]

    print(f"Running real-data backtest on {len(symbols)} symbol(s)...")
    print(f"Timeframe: {args.timeframe}, Bars: {args.bars}, LLM: {'ON' if args.llm else 'OFF'}")

    results = []
    benchmarks = []
    for sym in symbols:
        print(f"  Backtesting {sym}...", end=" ", flush=True)
        r = await run_single(sym, args.timeframe, args.bars, args.llm)
        b = await run_benchmark_bnh(sym, args.timeframe, args.bars)
        results.append(r)
        benchmarks.append(b)
        if "error" in r:
            print(f"ERROR: {r['error']}")
        else:
            print(f"done ({r['bars']} bars, {r['total_trades']} trades)")

    print_results(results, benchmarks)


if __name__ == "__main__":
    asyncio.run(main())
