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

    # Highest precedence: a frozen benchmark snapshot. Reproducible by
    # construction — the whole point is that repeated runs read identical bars.
    if getattr(args, "dataset", None):
        from bot.backtest import snapshot as _snap
        man = _snap.load_manifest(args.dataset)
        bars = _snap.load_symbol(args.dataset, config.symbol, man)
        print(f"  Loaded {len(bars)} FROZEN bars for {config.symbol} from "
              f"{args.dataset} (dataset_hash={man['dataset_hash'][:12]}…)")
        return bars, False, f"frozen_snapshot:{man['dataset_hash']}"

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
        fill_mode=args.fill_mode,
        breaker_reset_bars=args.breaker_reset_bars,
        use_llm=args.use_llm,
        use_recorded_llm=args.use_recorded_llm,
        use_recorded_order_flow=args.use_recorded_order_flow,
        recorded_order_flow_path=args.of_snapshot_path,
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
    if config.use_recorded_order_flow:
        n_of = len(engine._recorded_order_flow) if engine._recorded_order_flow is not None else 0
        print(f"  Order-flow replay: {n_of} recorded snapshot(s) from "
              f"{config.recorded_order_flow_path}"
              + ("" if n_of else " — none found, running WITHOUT order flow (legacy path)"))
    result = await engine.run(bars)
    engine.cleanup()  # remove temp state dir

    # Stamp data provenance so the saved result is self-describing.
    result.used_synthetic = used_synthetic
    result.data_source = data_source

    # Display results
    print(_format_result_summary(result))
    print(_attribution_report(result))
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


def build_parser() -> argparse.ArgumentParser:
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
    data_group.add_argument("--dataset", type=str, metavar="DIR",
                            help="Read bars from a FROZEN benchmark snapshot dir "
                                 "(see bot.backtest.snapshot) instead of fetching live. "
                                 "Every run reads byte-identical candles, so an A/B delta "
                                 "is attributable to the code change, not to data drift. "
                                 "With --dataset and no --symbols, the snapshot's full "
                                 "universe is run as a portfolio.")
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
    trade_group.add_argument("--symbols", type=str, default="",
                             help="Comma-separated symbols for a PORTFOLIO backtest (shared "
                                  "equity/risk/breaker across all of them — measures the SYSTEM, "
                                  "not one pair). E.g. \"BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT\". "
                                  "Overrides --symbol; real data only.")
    trade_group.add_argument("--timeframe", type=str, default="1h", help="Candle timeframe (default: 1h)")
    trade_group.add_argument("--balance", type=float, default=10000.0, help="Starting balance (default: 10000)")
    trade_group.add_argument("--commission", type=float, default=0.1, help="Commission %% (default: 0.1)")
    trade_group.add_argument("--slippage", type=float, default=0.05, help="Slippage %% (default: 0.05)")
    trade_group.add_argument("--fill-mode", choices=("close", "next_open"), default="close",
                             help="Entry fill convention: same-bar close (legacy, optimistic) "
                                  "or next-bar open (conservative; audit fix #15). Run both "
                                  "and compare to see how much edge lives in the fill assumption.")
    trade_group.add_argument("--honest", action="store_true",
                             help="Preset for trustworthy numbers: --strict-data (never silently "
                                  "substitute synthetic) + --fill-mode next_open (conservative "
                                  "fills). Use this as your default invocation; close-fill "
                                  "numbers flatter by ~0.9pp/run.")
    trade_group.add_argument("--breaker-reset-bars", type=int, default=0,
                             help="Auto-reset a tripped circuit breaker after N bars (0=never, "
                                  "the default). A drawdown/streak trip needs MANUAL reset, so in "
                                  "a months-long run one early losing streak otherwise halts the "
                                  "rest — set e.g. 24 to emulate a daily operator reset.")
    trade_group.add_argument("--use-llm", action="store_true", help="Use LLM for analysis (default: rule-based)")
    trade_group.add_argument("--use-recorded-llm", action="store_true",
                             help="Replay recorded LLM theses (data/learning/llm_calibration.jsonl) "
                                  "for deterministic parity with the live blended path")
    trade_group.add_argument("--use-recorded-order-flow", action="store_true",
                             help="Replay shadow-recorded order-flow snapshots so the smart-money "
                                  "voter / OF confluence / veto / funding haircut fire in backtest "
                                  "(needs live OF_RECORD_SNAPSHOTS data; else runs without order flow)")
    trade_group.add_argument("--of-snapshot-path", type=str,
                             default="data/learning/order_flow_snapshots.jsonl",
                             help="Path to the recorded order-flow JSONL "
                                  "(default: data/learning/order_flow_snapshots.jsonl)")

    # Walk-forward analysis
    wf_group = parser.add_argument_group("walk-forward")
    wf_group.add_argument("--walk-forward", type=int, metavar="N", default=0,
                          help="Run N-fold walk-forward analysis instead of a single backtest")
    wf_group.add_argument("--wf-optimize", action="store_true",
                          help="Anchored optimisation: sweep confidence_threshold on in-sample, "
                               "validate out-of-sample (reports the overfitting gap)")

    # Output
    parser.add_argument("--output", "-o", type=str, help="Save JSON results to file")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.honest:
        args.strict_data = True
        args.fill_mode = "next_open"
    # `--dataset DIR` with no --symbols runs the snapshot's FULL universe as a
    # portfolio — the canonical one-liner benchmark.
    if getattr(args, "dataset", None) and not args.symbols.strip():
        from bot.backtest import snapshot as _snap
        args.symbols = ",".join(_snap.load_manifest(args.dataset).get("symbols", {}))
    if args.symbols.strip():
        asyncio.run(_run_portfolio(args))
        return
    if args.walk_forward and args.walk_forward > 0:
        asyncio.run(_run_walk_forward(args))
    else:
        asyncio.run(_run_backtest(args))



def _group_stats(trades, key_fn):
    """Aggregate net PnL / win-rate / profit-factor per group. Returns a dict
    {group: {trades, net_pnl, win_rate, profit_factor}} sorted by net_pnl."""
    groups: dict = {}
    for t in trades:
        k = key_fn(t) or "(unknown)"
        g = groups.setdefault(k, {"trades": 0, "net_pnl": 0.0, "wins": 0,
                                  "gross_win": 0.0, "gross_loss": 0.0})
        g["trades"] += 1
        g["net_pnl"] += t.net_pnl_usd
        if t.net_pnl_usd > 0:
            g["wins"] += 1
            g["gross_win"] += t.net_pnl_usd
        else:
            g["gross_loss"] += abs(t.net_pnl_usd)
    out = {}
    for k, g in groups.items():
        pf = (g["gross_win"] / g["gross_loss"]) if g["gross_loss"] > 0 else float("inf")
        out[k] = {"trades": g["trades"], "net_pnl": round(g["net_pnl"], 2),
                  "win_rate": g["wins"] / g["trades"] if g["trades"] else 0.0,
                  "profit_factor": pf}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True))


def _trend_alignment(trade) -> str:
    """Classify a trade as trading WITH or AGAINST the entry regime's trend.

    Only TREND_UP / TREND_DOWN carry a directional bias; RANGE/CHOP/BREAKOUT/
    EXPANSION have no trend to be counter to, so they bucket as neutral. This is
    the lens that isolates the counter-trend fade bleed the audit flagged: a
    SHORT into TREND_UP (or LONG into TREND_DOWN) fights the dominant move.
    """
    regime = (getattr(trade, "entry_regime", "") or "").upper()
    direction = (getattr(trade, "direction", "") or "").upper()
    if regime == "TREND_UP":
        return "with-trend" if direction == "LONG" else "counter-trend"
    if regime == "TREND_DOWN":
        return "with-trend" if direction == "SHORT" else "counter-trend"
    return "neutral (non-trending)"


def _risk_adjusted(result) -> dict:
    """Sortino + Calmar from the trade series and equity curve. Sharpe already
    lives on the result. Sortino uses downside deviation of per-trade net PnL;
    Calmar = total return % / max drawdown %."""
    pnls = [t.net_pnl_usd for t in result.trades]
    sortino = 0.0
    if len(pnls) >= 2:
        import statistics
        mean = statistics.mean(pnls)
        downside = [p for p in pnls if p < 0]
        dd = (statistics.pstdev(downside) if len(downside) >= 2 else
              (abs(downside[0]) if downside else 0.0))
        sortino = (mean / dd) if dd > 0 else 0.0
    max_dd = getattr(result, "max_drawdown_pct", 0.0) or 0.0
    calmar = (result.total_return_pct / max_dd) if max_dd > 0 else 0.0
    return {"sortino": round(sortino, 2), "calmar": round(calmar, 2)}


def _attribution_report(result) -> str:
    """Where the edge lives: P&L broken down by regime, setup, signal type, and
    trend alignment, plus the risk-adjusted metrics the aggregate return can't
    show. This is the go/no-go evidence for gating entries to the profitable
    buckets — the signal-type and trend-alignment cuts specifically isolate the
    PF<1 bleed (which signal families lose, and whether counter-trend fades are
    the drag)."""
    if not result.trades:
        return ""
    lines = ["", "  ── EDGE ATTRIBUTION " + "─" * 48]
    ra = _risk_adjusted(result)
    lines.append(f"  Risk-adjusted: Sharpe {getattr(result, 'sharpe_ratio', 0.0):.2f}"
                 f" | Sortino {ra['sortino']:.2f} | Calmar {ra['calmar']:.2f}")
    for title, key_fn in (("By regime (at entry)", lambda t: t.entry_regime),
                          ("By setup", lambda t: t.setup),
                          ("By signal type", lambda t: t.signal_type),
                          ("By trend alignment", _trend_alignment)):
        stats = _group_stats(result.trades, key_fn)
        if not stats or (len(stats) == 1 and "(unknown)" in stats):
            continue
        lines.append(f"  {title}:")
        for k, g in stats.items():
            pf = "inf" if g["profit_factor"] == float("inf") else f"{g['profit_factor']:.2f}"
            sign = "+" if g["net_pnl"] >= 0 else ""
            lines.append(f"    {k:<14} {g['trades']:>3} tr  net {sign}${g['net_pnl']:>8,.2f}"
                         f"  win {g['win_rate']:.0%}  PF {pf}")
    return "\n".join(lines)


def _narrative(result, per_symbol: dict | None = None) -> str:
    """Plain-language verdict for a run — deterministic (no LLM), so every
    measurement ships with a summary a human can read at a glance."""
    r = result
    verdict = ("PROFITABLE" if r.total_return_pct > 0.5 else
               "roughly breakeven" if r.total_return_pct > -0.5 else "NEGATIVE")
    density = ("statistically thin (<15 trades — treat as anecdote)"
               if r.total_trades < 15 else
               f"{r.total_trades} trades")
    lines = [
        "",
        "  \u2500\u2500 VERDICT " + "\u2500" * 56,
        f"  This run is {verdict}: {r.total_return_pct:+.2f}% over the period on "
        f"{density}, win rate {r.win_rate:.0%}, profit factor {r.profit_factor:.2f}.",
        f"  Worst drawdown {r.max_drawdown_pct:.2f}% — risk containment "
        + ("held." if r.max_drawdown_pct < 5 else "was STRESSED (>5%)."),
    ]
    if per_symbol:
        best = max(per_symbol.items(), key=lambda kv: kv[1]["net_pnl"])
        worst = min(per_symbol.items(), key=lambda kv: kv[1]["net_pnl"])
        if best[1]["net_pnl"] > 0:
            lines.append(f"  Best symbol {best[0]} (${best[1]['net_pnl']:+,.2f}); "
                         f"worst {worst[0]} (${worst[1]['net_pnl']:+,.2f}).")
    return "\n".join(lines)


async def _run_portfolio(args: argparse.Namespace) -> None:
    """Portfolio backtest across --symbols with shared risk state."""
    from bot.backtest.portfolio_engine import PortfolioBacktester, portfolio_walk_forward

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    config = BacktestConfig(
        symbol=symbols[0], timeframe=args.timeframe,
        initial_balance=args.balance, commission_pct=args.commission,
        slippage_pct=args.slippage, fill_mode=args.fill_mode,
        breaker_reset_bars=args.breaker_reset_bars,
        use_llm=args.use_llm, use_recorded_llm=args.use_recorded_llm,
        use_recorded_order_flow=args.use_recorded_order_flow,
        recorded_order_flow_path=args.of_snapshot_path,
    )
    data = {}
    if getattr(args, "dataset", None):
        # Frozen snapshot: every A/B arm reads byte-identical bars. A requested
        # symbol missing from the snapshot is a hard error, never a silent skip —
        # dropping a symbol would change the universe and thus the measured system.
        from bot.backtest import snapshot as _snap
        man = _snap.load_manifest(args.dataset)
        print(f"\n  Portfolio backtest: {len(symbols)} symbols from FROZEN dataset "
              f"{args.dataset} (dataset_hash={man['dataset_hash'][:12]}…)")
        for sym in symbols:
            try:
                data[sym] = _snap.load_symbol(args.dataset, sym, man)
                print(f"  loaded {sym}: {len(data[sym])} frozen bars")
            except KeyError as exc:
                print(f"  ERROR: {exc}")
                sys.exit(1)
    else:
        print(f"\n  Portfolio backtest: {len(symbols)} symbols, fetching {args.limit} bars each...")
        for sym in symbols:
            try:
                bars = await DataLoader.from_bitget(symbol=sym, timeframe=args.timeframe,
                                                    limit=args.limit)
                if bars:
                    data[sym] = bars
                    print(f"  fetched {sym}: {len(bars)} REAL bars")
            except Exception as exc:
                print(f"  {sym}: fetch failed ({exc}) — skipped")
    if not data:
        print("  ERROR: no data fetched for any symbol. Aborting.")
        sys.exit(1)

    if args.walk_forward and args.walk_forward > 0:
        folds = await portfolio_walk_forward(data, config, n_folds=args.walk_forward)
        print(f"\n  PORTFOLIO {args.walk_forward}-fold walk-forward "
              f"({len(data)} symbols, fill={config.fill_mode}):")
        prof = sum(1 for f in folds if f["return_pct"] > 0)
        for f in folds:
            print(f"  fold {f['fold']}: {f['trades']:>3} trades  ret {f['return_pct']:+6.2f}%  "
                  f"win {f['win_rate']:.0%}  maxDD {f['max_dd_pct']:5.2f}%  PF {f['profit_factor']:.2f}")
        rets = [f["return_pct"] for f in folds]
        print(f"  => profitable folds {prof}/{len(folds)} | mean OOS ret "
              f"{sum(rets)/len(rets):+.2f}% | worst {min(rets):+.2f}%")
        return

    pb = PortfolioBacktester(config, symbols=list(data))
    result = await pb.run(data)
    pb.cleanup()
    print(_format_result_summary(result))
    print("  Per-symbol breakdown:")
    for sym, row in sorted(pb.per_symbol.items()):
        print(f"    {sym:<16} {row['trades']:>3} trades  net ${row['net_pnl']:>9,.2f}  "
              f"win {row['win_rate']:.0%}")
    print(_attribution_report(result))
    print(_narrative(result, pb.per_symbol))
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump({**result.model_dump(mode="json", exclude={"equity_curve", "trades"}),
                       "per_symbol": pb.per_symbol}, f, indent=2, default=str)
        print(f"  Results saved to {args.output}")


async def _run_walk_forward(args: argparse.Namespace) -> None:
    """Execute walk-forward analysis with the given CLI arguments."""
    from bot.backtest.walk_forward import run_walk_forward
    config = BacktestConfig(
        symbol=args.symbol, timeframe=args.timeframe, initial_balance=args.balance,
        commission_pct=args.commission, slippage_pct=args.slippage, use_llm=args.use_llm,
        use_recorded_llm=args.use_recorded_llm,
        use_recorded_order_flow=args.use_recorded_order_flow,
        recorded_order_flow_path=args.of_snapshot_path,
    )
    print(f"\n  Loading data for {config.symbol}...")
    bars, used_synthetic, data_source = await _load_bars(args, config)
    if len(bars) < 220:
        print(f"  ERROR: walk-forward needs more bars (got {len(bars)}). Try --limit 1000+.")
        sys.exit(1)

    base = {"symbol": args.symbol, "timeframe": args.timeframe,
            "initial_balance": args.balance, "commission_pct": args.commission,
            "slippage_pct": args.slippage, "use_llm": args.use_llm,
            "use_recorded_llm": args.use_recorded_llm,
            "use_recorded_order_flow": args.use_recorded_order_flow,
            "recorded_order_flow_path": args.of_snapshot_path}
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
