"""
RUNECLAW Backtest Runner -- standalone CLI for running backtests.

Usage:
    python -m bot.backtest.runner                          # synthetic data, defaults
    python -m bot.backtest.runner --symbol BTC/USDT --bars 1440
    python -m bot.backtest.runner --csv data/btc_1h.csv
    python -m bot.backtest.runner --fetch --symbol SOL/USDT --limit 500
    python -m bot.backtest.runner --seed 123 --volatility 0.02
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig


def _format_result_summary(result) -> str:
    """Format a BacktestResult as a human-readable report."""
    trades = result.trades

    # Trade log table
    trade_lines = []
    for i, t in enumerate(trades[:20], 1):  # cap at 20 for display
        marker = "+" if t.net_pnl_usd > 0 else "-"
        trade_lines.append(
            f"  {i:>3}. {t.direction:>5} {t.symbol:<12} "
            f"entry=${t.entry_price:>10,.2f}  exit=${t.exit_price:>10,.2f}  "
            f"PnL={marker}${abs(t.net_pnl_usd):>8,.2f}  "
            f"reason={t.exit_reason:<12} conf={t.confidence:.0%}  "
            f"dur={t.exit_time - t.entry_time}"
        )
    if len(trades) > 20:
        trade_lines.append(f"  ... and {len(trades) - 20} more trades")

    # Equity curve sparkline (terminal-safe)
    sparkline = _ascii_equity_curve(result.equity_curve)

    return f"""
╔══════════════════════════════════════════════════════════════════════════╗
║                    RUNECLAW BACKTEST REPORT                             ║
╚══════════════════════════════════════════════════════════════════════════╝

  Symbol:           {result.symbol}
  Timeframe:        {result.timeframe}
  Period:           {result.start_date} → {result.end_date}
  Bars processed:   {result.bars_processed:,}
  Duration:         {result.duration_seconds:.1f}s

── PERFORMANCE ────────────────────────────────────────────────────────────

  Initial Balance:  ${result.initial_balance:>12,.2f}
  Final Equity:     ${result.final_equity:>12,.2f}
  Total Return:     {result.total_return_pct:>+11.2f}%
  Net PnL:          ${result.net_pnl:>12,.2f}
  Total Commission: ${result.total_commission:>12,.2f}
  Total Slippage:   ${result.total_slippage:>12,.2f}

── TRADE STATISTICS ───────────────────────────────────────────────────────

  Total Trades:     {result.total_trades}
  Winners:          {result.winning_trades}  ({result.win_rate:.0%})
  Losers:           {result.losing_trades}
  Avg Win:          ${result.avg_win_usd:>12,.2f}
  Avg Loss:         ${result.avg_loss_usd:>12,.2f}
  Largest Win:      ${result.largest_win_usd:>12,.2f}
  Largest Loss:     ${result.largest_loss_usd:>12,.2f}
  Avg Duration:     {result.avg_trade_duration_hours:.1f}h

── RISK METRICS ───────────────────────────────────────────────────────────

  Max Drawdown:     {result.max_drawdown_pct:.2f}%  (${result.max_drawdown_usd:,.2f})
  Max Consec Loss:  {result.max_consecutive_losses}
  Profit Factor:    {result.profit_factor:.2f}
  Sharpe Ratio:     {result.sharpe_ratio:.2f}
  Sortino Ratio:    {result.sortino_ratio:.2f}
  Calmar Ratio:     {result.calmar_ratio:.2f}

── PIPELINE STATS ─────────────────────────────────────────────────────────

  Signals Scanned:  {result.total_signals_generated}
  Ideas Generated:  {result.total_ideas_generated}
  Rejected (Risk):  {result.total_ideas_rejected_risk}
  Rejected (Conf):  {result.total_ideas_rejected_confidence}
  Execution Rate:   {(result.total_trades / result.total_ideas_generated * 100) if result.total_ideas_generated > 0 else 0:.1f}% of ideas executed

── EQUITY CURVE ───────────────────────────────────────────────────────────

{sparkline}

── TRADE LOG ──────────────────────────────────────────────────────────────

{chr(10).join(trade_lines) if trade_lines else "  No trades executed."}

════════════════════════════════════════════════════════════════════════════
"""


def _ascii_equity_curve(equity_points, width: int = 70, height: int = 12) -> str:
    """Render an ASCII equity curve chart."""
    if not equity_points:
        return "  (no data)"

    values = [p.equity for p in equity_points]
    n = len(values)

    # Downsample to fit width
    if n > width:
        step = n / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    min_val = min(sampled)
    max_val = max(sampled)
    val_range = max_val - min_val if max_val != min_val else 1

    # Build the chart
    lines = []
    for row in range(height - 1, -1, -1):
        threshold = min_val + (row / (height - 1)) * val_range
        line = "  "
        if row == height - 1:
            line += f"${max_val:>10,.0f} │"
        elif row == 0:
            line += f"${min_val:>10,.0f} │"
        elif row == height // 2:
            mid = (max_val + min_val) / 2
            line += f"${mid:>10,.0f} │"
        else:
            line += "            │"

        for v in sampled:
            normalized = (v - min_val) / val_range * (height - 1)
            if abs(normalized - row) < 0.5:
                line += "█"
            elif normalized > row:
                line += "│"
            else:
                line += " "
        lines.append(line)

    # X axis
    lines.append("  " + "            └" + "─" * len(sampled))
    start_label = equity_points[0].timestamp.strftime("%m/%d")
    end_label = equity_points[-1].timestamp.strftime("%m/%d")
    padding = len(sampled) - len(start_label) - len(end_label)
    lines.append("  " + "             " + start_label + " " * max(padding, 1) + end_label)

    return "\n".join(lines)


async def _load_bars(args: argparse.Namespace, config) -> tuple[list, bool, str]:
    """Load OHLCV bars for a backtest. Returns (bars, used_synthetic, data_source).

    data_source is one of "csv" | "bitget_real" | "synthetic" |
    "synthetic_fallback" so the caller can stamp it into the saved result and a
    synthetic run is never mistaken for a real backtest.

    REAL market data is the DEFAULT — synthetic GBM is the single biggest
    backtest-vs-live divergence risk (no microstructure, no fat tails, no gaps),
    so it is demoted to an explicit --synthetic smoke test. Precedence:
    --csv > --synthetic > real Bitget fetch (default). If the real fetch fails
    (offline / no exchange) the runner falls back to a clearly-labelled synthetic
    smoke test rather than aborting — UNLESS --strict-data is set, in which case
    the failure is raised so automated/CI runs never silently use fake data.
    """
    def _synthetic():
        return DataLoader.generate_synthetic(
            bars=args.bars, start_price=args.start_price,
            volatility=args.volatility, trend=args.trend, seed=args.seed)

    if args.csv:
        bars = DataLoader.from_csv(args.csv)
        print(f"  Loaded {len(bars)} bars from {args.csv}")
        return bars, False, "csv"

    if args.synthetic:
        bars = _synthetic()
        print(f"  ⚠️  SMOKE TEST: {len(bars)} SYNTHETIC bars "
              f"(seed={args.seed}) — NOT a real backtest")
        return bars, True, "synthetic"

    # Default: real Bitget klines (--fetch is accepted but redundant now).
    try:
        bars = await DataLoader.from_bitget(
            symbol=config.symbol, timeframe=config.timeframe, limit=args.limit)
        if not bars:
            raise RuntimeError("no bars returned")
        print(f"  Fetched {len(bars)} REAL bars from Bitget")
        return bars, False, "bitget_real"
    except Exception as exc:
        # --strict-data: never silently substitute synthetic for a failed real
        # fetch (protects automated/CI runs that expect real data).
        if getattr(args, "strict_data", False):
            print(f"  ❌  Real-data fetch failed ({exc}); --strict-data set, aborting "
                  f"instead of using synthetic data.")
            raise
        print(f"  ⚠️  Real-data fetch failed ({exc}); "
              f"falling back to a SYNTHETIC smoke test")
        bars = _synthetic()
        print(f"  ⚠️  SMOKE TEST: {len(bars)} synthetic bars — NOT a real backtest")
        return bars, True, "synthetic_fallback"


async def _run_backtest(args: argparse.Namespace) -> None:
    """Execute a backtest with the given CLI arguments."""
    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        initial_balance=args.balance,
        commission_pct=args.commission,
        slippage_pct=args.slippage,
        use_llm=args.use_llm,
        use_recorded_llm=args.use_recorded_llm,
    )

    # Load data (real-data-first; see _load_bars).
    print(f"\n  Loading data for {config.symbol}...")
    bars, used_synthetic, data_source = await _load_bars(args, config)

    if len(bars) < 110:
        print(f"  ERROR: Need at least 110 bars, got {len(bars)}. Aborting.")
        sys.exit(1)

    # Save synthetic data for reproducibility
    if used_synthetic and args.save_data:
        data_path = f"data/{config.symbol.replace('/', '_')}_{config.timeframe}_{args.seed}.csv"
        DataLoader.save_csv(bars, data_path)
        print(f"  Saved data to {data_path}")

    # Run backtest
    print("  Running backtest...")
    engine = BacktestEngine(config)
    result = await engine.run(bars)
    engine.cleanup()  # remove temp state dir

    # Stamp data provenance so the saved result is self-describing.
    result.used_synthetic = used_synthetic
    result.data_source = data_source

    # Display results
    print(_format_result_summary(result))
    if used_synthetic:
        print("  ⚠️  These numbers come from SYNTHETIC data "
              f"(data_source={data_source}) — NOT a real backtest.")

    # Save JSON result
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(
                result.model_dump(mode="json", exclude={"equity_curve"}),
                f, indent=2, default=str,
            )
        print(f"  Results saved to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RUNECLAW Backtest Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bot.backtest.runner                              # REAL Bitget data (default)
  python -m bot.backtest.runner --limit 720                  # 720 real 1h bars
  python -m bot.backtest.runner --csv data/btc_1h.csv        # from CSV file
  python -m bot.backtest.runner --synthetic --bars 2160      # synthetic SMOKE TEST
  python -m bot.backtest.runner --output results/bt.json     # save results

  Real market data is the default. Synthetic GBM (--synthetic) is a smoke test
  only — no microstructure / fat tails / gaps — never trust its numbers as a
  real backtest. If the real-data fetch fails, the runner falls back to a
  clearly-labelled synthetic smoke test.
        """,
    )

    # Data source
    data_group = parser.add_argument_group("data source")
    data_group.add_argument("--csv", type=str, help="Path to OHLCV CSV file")
    data_group.add_argument("--fetch", action="store_true",
                            help="(deprecated: real data is the default) Fetch from Bitget API")
    data_group.add_argument("--synthetic", action="store_true",
                            help="Use a SYNTHETIC smoke test instead of real data (not a real backtest)")
    data_group.add_argument("--limit", type=int, default=720, help="Real bars to fetch (default: 720)")
    data_group.add_argument("--bars", type=int, default=720, help="Synthetic bars to generate (default: 720)")
    data_group.add_argument("--save-data", action="store_true", help="Save synthetic data to CSV")
    data_group.add_argument("--strict-data", action="store_true",
                            help="Abort (don't fall back to synthetic) if the real-data fetch fails")

    # Synthetic data params
    synth_group = parser.add_argument_group("synthetic data")
    synth_group.add_argument("--start-price", type=float, default=65000.0, help="Start price (default: 65000)")
    synth_group.add_argument("--volatility", type=float, default=0.015, help="Hourly volatility (default: 0.015)")
    synth_group.add_argument("--trend", type=float, default=0.0001, help="Hourly drift (default: 0.0001)")
    synth_group.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")

    # Trading params
    trade_group = parser.add_argument_group("trading")
    trade_group.add_argument("--symbol", type=str, default="BTC/USDT", help="Trading pair (default: BTC/USDT)")
    trade_group.add_argument("--timeframe", type=str, default="1h", help="Candle timeframe (default: 1h)")
    trade_group.add_argument("--balance", type=float, default=10000.0, help="Starting balance (default: 10000)")
    trade_group.add_argument("--commission", type=float, default=0.1, help="Commission %% (default: 0.1)")
    trade_group.add_argument("--slippage", type=float, default=0.05, help="Slippage %% (default: 0.05)")
    trade_group.add_argument("--use-llm", action="store_true", help="Use LLM for analysis (default: rule-based)")
    trade_group.add_argument("--use-recorded-llm", action="store_true",
                             help="Replay recorded LLM theses (data/learning/llm_calibration.jsonl) "
                                  "for deterministic parity with the live blended path")

    # Walk-forward analysis
    wf_group = parser.add_argument_group("walk-forward")
    wf_group.add_argument("--walk-forward", type=int, metavar="N", default=0,
                          help="Run N-fold walk-forward analysis instead of a single backtest")
    wf_group.add_argument("--wf-optimize", action="store_true",
                          help="Anchored optimisation: sweep confidence_threshold on in-sample, "
                               "validate out-of-sample (reports the overfitting gap)")

    # Output
    parser.add_argument("--output", "-o", type=str, help="Save JSON results to file")

    args = parser.parse_args()
    if args.walk_forward and args.walk_forward > 0:
        asyncio.run(_run_walk_forward(args))
    else:
        asyncio.run(_run_backtest(args))


async def _run_walk_forward(args: argparse.Namespace) -> None:
    """Execute walk-forward analysis with the given CLI arguments."""
    from bot.backtest.walk_forward import run_walk_forward
    config = BacktestConfig(
        symbol=args.symbol, timeframe=args.timeframe, initial_balance=args.balance,
        commission_pct=args.commission, slippage_pct=args.slippage, use_llm=args.use_llm,
        use_recorded_llm=args.use_recorded_llm,
    )
    print(f"\n  Loading data for {config.symbol}...")
    bars, used_synthetic, data_source = await _load_bars(args, config)
    if len(bars) < 220:
        print(f"  ERROR: walk-forward needs more bars (got {len(bars)}). Try --limit 1000+.")
        sys.exit(1)

    base = {"symbol": args.symbol, "timeframe": args.timeframe,
            "initial_balance": args.balance, "commission_pct": args.commission,
            "slippage_pct": args.slippage, "use_llm": args.use_llm}
    grid = ([{"confidence_threshold": t} for t in (0.45, 0.5, 0.55, 0.6)]
            if args.wf_optimize else None)
    print(f"  Running {args.walk_forward}-fold walk-forward"
          f"{' with IS optimisation' if grid else ''}...")
    report = await run_walk_forward(bars, base, n_folds=args.walk_forward, param_grid=grid)

    print("\n" + "=" * 60)
    print("  WALK-FORWARD ANALYSIS")
    print("=" * 60)
    print(f"  {report.summary()}\n")
    if used_synthetic:
        print("  ⚠️  SYNTHETIC data "
              f"(data_source={data_source}) — walk-forward numbers are NOT real.\n")
    for f in report.folds:
        chosen = (f" thr={f.chosen.get('confidence_threshold')}"
                  if grid and "confidence_threshold" in f.chosen else "")
        print(f"  fold {f.index}: OOS return {f.oos_return_pct:+6.2f}%  "
              f"win {f.oos_win_rate:.0%}  trades {f.oos_trades:>3}  "
              f"Sharpe {f.oos_sharpe:5.2f}  maxDD {f.oos_max_dd:5.2f}%{chosen}")
    print("=" * 60)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump({"summary": report.summary(),
                       "used_synthetic": used_synthetic,
                       "data_source": data_source,
                       "folds": [vars(fl) for fl in report.folds]}, f, indent=2, default=str)
        print(f"  Results saved to {args.output}")


if __name__ == "__main__":
    main()
