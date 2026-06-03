#!/usr/bin/env python3
"""
RUNECLAW Real-Data Backtest — validates strategy on actual Bitget/Binance OHLCV.

This script addresses the primary audit finding (F-1): synthetic backtests cannot
validate the system's claimed edge because they contain none of the market
microstructure signals the strategy exploits.

Key differences from backtest_audit.py:
  - Uses REAL historical OHLCV from Binance public API (no key required)
  - Runs with use_llm=True when LLM_API_KEY is configured
  - Walk-forward out-of-sample validation (70/30 train/test split)
  - Benchmarks against buy-and-hold for each asset
  - Reports results separately for rule-based vs LLM-enabled runs

Usage:
  # Rule-based only (no API key needed):
  python run_realdata_backtest.py

  # With LLM enabled (set LLM_API_KEY in .env):
  python run_realdata_backtest.py --llm

  # Specific assets:
  python run_realdata_backtest.py --symbols BTC ETH SOL

  # Save results:
  python run_realdata_backtest.py --output realdata_results.json
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from datetime import datetime

# Suppress noisy logs
logging.getLogger("runeclaw.trade").setLevel(logging.CRITICAL)
logging.getLogger("runeclaw.risk").setLevel(logging.CRITICAL)
logging.getLogger("runeclaw.system").setLevel(logging.CRITICAL)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig


# --- Configuration -----------------------------------------------------------

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
TIMEFRAME = "1h"
BARS_TO_FETCH = 1000  # ~42 days of 1h candles
WALK_FORWARD_SPLIT = 0.7  # 70% train, 30% test
INITIAL_BALANCE = 10_000.0
COMMISSION_PCT = 0.1
SLIPPAGE_PCT = 0.05


async def fetch_real_data(symbol: str) -> list:
    """Fetch real OHLCV data from Binance public API."""
    print(f"  Fetching {symbol}/USDT ({TIMEFRAME}, {BARS_TO_FETCH} bars)...", end=" ")
    bars = await DataLoader.from_public_api(
        symbol=f"{symbol}/USDT",
        timeframe=TIMEFRAME,
        limit=BARS_TO_FETCH,
    )
    if bars:
        start = bars[0].timestamp.strftime("%Y-%m-%d")
        end = bars[-1].timestamp.strftime("%Y-%m-%d")
        print(f"OK ({len(bars)} bars, {start} to {end})")
    else:
        print("FAILED (no data returned)")
    return bars


def compute_buy_and_hold(bars: list) -> dict:
    """Compute buy-and-hold benchmark for comparison."""
    if not bars or len(bars) < 2:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0}

    start_price = bars[0].open
    end_price = bars[-1].close
    return_pct = ((end_price - start_price) / start_price) * 100

    # Max drawdown
    peak = start_price
    max_dd = 0.0
    for bar in bars:
        peak = max(peak, bar.high)
        dd = (peak - bar.low) / peak * 100
        max_dd = max(max_dd, dd)

    return {"return_pct": round(return_pct, 2), "max_drawdown_pct": round(max_dd, 2)}


async def run_backtest_on_bars(
    symbol: str, bars: list, use_llm: bool, label: str
) -> dict:
    """Run backtest engine on real bar data and return metrics."""
    config = BacktestConfig(
        symbol=f"{symbol}/USDT",
        timeframe=TIMEFRAME,
        initial_balance=INITIAL_BALANCE,
        commission_pct=COMMISSION_PCT,
        slippage_pct=SLIPPAGE_PCT,
        use_llm=use_llm,
    )

    engine = BacktestEngine(config)
    result = await engine.run(bars)

    return {
        "label": label,
        "symbol": symbol,
        "bars": len(bars),
        "use_llm": use_llm,
        "return_pct": round(result.total_return_pct, 2),
        "net_pnl": round(result.net_pnl, 2),
        "trades": result.total_trades,
        "win_rate": round(result.win_rate * 100, 1),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "sharpe": round(result.sharpe_ratio, 3) if result.sharpe_ratio else 0,
        "sortino": round(result.sortino_ratio, 3) if result.sortino_ratio else 0,
        "profit_factor": round(result.profit_factor, 3) if result.profit_factor else 0,
        "signals_generated": getattr(result, "signals_generated", 0),
        "risk_rejections": getattr(result, "risk_rejections", 0),
        "confidence_rejections": getattr(result, "confidence_rejections", 0),
    }


def print_results_table(results: list, benchmarks: dict):
    """Print formatted comparison table."""
    print("\n" + "=" * 100)
    print("RUNECLAW REAL-DATA BACKTEST RESULTS")
    print("=" * 100)
    print(f"{'Label':<30} {'Ret%':>7} {'B&H%':>7} {'Alpha':>7} "
          f"{'Trades':>6} {'WR%':>5} {'MDD%':>6} {'Sharpe':>7} {'PF':>6}")
    print("-" * 100)

    for r in results:
        sym = r["symbol"]
        bh = benchmarks.get(sym, {}).get("return_pct", 0)
        alpha = round(r["return_pct"] - bh, 2)
        print(f"{r['label']:<30} {r['return_pct']:>7.2f} {bh:>7.2f} {alpha:>+7.2f} "
              f"{r['trades']:>6} {r['win_rate']:>5.1f} {r['max_drawdown_pct']:>6.2f} "
              f"{r['sharpe']:>7.3f} {r['profit_factor']:>6.3f}")

    print("-" * 100)

    # Summary stats
    if results:
        avg_ret = sum(r["return_pct"] for r in results) / len(results)
        avg_alpha = sum(
            r["return_pct"] - benchmarks.get(r["symbol"], {}).get("return_pct", 0)
            for r in results
        ) / len(results)
        avg_wr = sum(r["win_rate"] for r in results) / len(results)
        avg_mdd = sum(r["max_drawdown_pct"] for r in results) / len(results)
        print(f"{'AVERAGE':<30} {avg_ret:>7.2f} {'':>7} {avg_alpha:>+7.2f} "
              f"{'':>6} {avg_wr:>5.1f} {avg_mdd:>6.2f}")

    print()
    print("Legend: Ret% = strategy return, B&H% = buy-and-hold benchmark,")
    print("        Alpha = excess return vs B&H, MDD = max drawdown, PF = profit factor")
    print()
    print("NOTE: These results use REAL historical market data from Binance public API.")
    print("      This is NOT synthetic/GBM data. Results reflect actual market conditions.")


async def main():
    parser = argparse.ArgumentParser(
        description="RUNECLAW Real-Data Backtest — validates on actual market data"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS,
        help="Symbols to backtest (default: BTC ETH SOL BNB XRP)"
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="Enable LLM analysis (requires LLM_API_KEY in .env)"
    )
    parser.add_argument(
        "--walk-forward", action="store_true", default=True,
        help="Use walk-forward validation (default: True)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save results to JSON file"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("RUNECLAW Real-Data Backtest")
    print(f"  Symbols:       {', '.join(args.symbols)}")
    print(f"  Timeframe:     {TIMEFRAME}")
    print(f"  Bars:          {BARS_TO_FETCH}")
    print(f"  LLM enabled:   {args.llm}")
    print(f"  Walk-forward:  {args.walk_forward}")
    print(f"  Balance:       ${INITIAL_BALANCE:,.0f}")
    print(f"  Commission:    {COMMISSION_PCT}% + {SLIPPAGE_PCT}% slippage")
    print("=" * 70)

    if args.llm:
        llm_key = os.getenv("LLM_API_KEY", "")
        if not llm_key:
            print("\nWARNING: --llm flag set but LLM_API_KEY not found in environment.")
            print("         Falling back to rule-based analysis.\n")
            args.llm = False

    # --- Fetch real data ---
    print("\nFetching real market data...")
    all_data = {}
    for sym in args.symbols:
        bars = await fetch_real_data(sym)
        if bars and len(bars) >= 100:
            all_data[sym] = bars
        else:
            print(f"  Skipping {sym}: insufficient data ({len(bars) if bars else 0} bars)")

    if not all_data:
        print("\nERROR: No data fetched for any symbol. Check network connectivity.")
        sys.exit(1)

    # --- Compute benchmarks ---
    print("\nComputing buy-and-hold benchmarks...")
    benchmarks = {}
    for sym, bars in all_data.items():
        benchmarks[sym] = compute_buy_and_hold(bars)
        print(f"  {sym}: {benchmarks[sym]['return_pct']:+.2f}% "
              f"(MDD: {benchmarks[sym]['max_drawdown_pct']:.2f}%)")

    # --- Run backtests ---
    results = []

    for sym, bars in all_data.items():
        if args.walk_forward:
            # Walk-forward: train on first 70%, test on last 30%
            split_idx = int(len(bars) * WALK_FORWARD_SPLIT)
            test_bars = bars[split_idx:]
            label = f"{sym} (OOS {len(test_bars)} bars)"
            print(f"\nRunning walk-forward on {sym}: "
                  f"train={split_idx} bars, test={len(test_bars)} bars...")

            # Recompute benchmark for test period only
            benchmarks[sym] = compute_buy_and_hold(test_bars)

            r = await run_backtest_on_bars(sym, test_bars, args.llm, label)
        else:
            label = f"{sym} (full {len(bars)} bars)"
            print(f"\nRunning backtest on {sym}: {len(bars)} bars...")
            r = await run_backtest_on_bars(sym, bars, args.llm, label)

        results.append(r)
        print(f"  -> Return: {r['return_pct']:+.2f}%, "
              f"Trades: {r['trades']}, Win Rate: {r['win_rate']}%, "
              f"Sharpe: {r['sharpe']:.3f}")

    # --- Print results ---
    print_results_table(results, benchmarks)

    # --- Save if requested ---
    if args.output:
        output = {
            "timestamp": datetime.utcnow().isoformat(),
            "config": {
                "timeframe": TIMEFRAME,
                "bars": BARS_TO_FETCH,
                "llm_enabled": args.llm,
                "walk_forward": args.walk_forward,
                "initial_balance": INITIAL_BALANCE,
                "commission_pct": COMMISSION_PCT,
                "slippage_pct": SLIPPAGE_PCT,
            },
            "benchmarks": benchmarks,
            "results": results,
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
