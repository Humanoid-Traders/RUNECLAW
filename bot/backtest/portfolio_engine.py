"""
RUNECLAW Portfolio Backtester — multi-symbol, shared risk state.

The single-symbol BacktestEngine measures one pair in isolation, but RUNECLAW
is a PORTFOLIO system: live it scans a ~67-symbol universe and its risk
machinery (correlation sizing, per-strategy notional caps, covariance VaR,
daily-loss breaker) operates on combined state. One selective strategy per
symbol × many symbols is where the live trade cadence — and any statistical
power — comes from.

This orchestrator runs one BacktestEngine per symbol but injects a SINGLE
shared PortfolioTracker + RiskEngine + Analyzer into all of them, then drives
the per-symbol bar pipelines in merged timestamp order. Every idea from every
symbol flows through the same risk gate against the same equity, exposure and
breaker state — exactly like live. The equity curve, drawdown and metrics are
the SYSTEM's, with a per-symbol breakdown on the side.

Usage:
    data = {sym: [BacktestBar, ...], ...}     # e.g. DataLoader.from_bitget per symbol
    pb = PortfolioBacktester(BacktestConfig(...), symbols=list(data))
    result = await pb.run(data)               # aggregate BacktestResult
    pb.per_symbol                              # {symbol: {trades, net_pnl, win_rate}}
    pb.cleanup()
"""
from __future__ import annotations

import time
from typing import Optional

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig, BacktestResult, EquityPoint
from bot.config import CONFIG
from bot.utils.logger import system_log, audit


class PortfolioBacktester:
    """Drives N single-symbol engines over one shared portfolio/risk state."""

    def __init__(self, config: BacktestConfig, symbols: list[str]) -> None:
        if not symbols:
            raise ValueError("PortfolioBacktester needs at least one symbol")
        self.config = config
        self.symbols = list(symbols)
        self.per_symbol: dict[str, dict] = {}

        # Save the operator's learning flags ONCE here; each sub-engine ctor
        # also saves-and-forces them, but after the first ctor it would save
        # the already-forced False values — restoring those on cleanup would
        # clobber the operator's real settings. The orchestrator owns the
        # save/restore; sub-engine restores are neutralized below.
        self._saved_learning_flags = (
            CONFIG.analyzer.confidence_calibration_enabled,
            CONFIG.analyzer.setup_expectancy_enabled,
        )

        # One engine per symbol; the FIRST engine's portfolio/risk/analyzer
        # become the shared instances injected into the rest.
        self._engines: dict[str, BacktestEngine] = {}
        for i, sym in enumerate(self.symbols):
            cfg = config.model_copy(update={"symbol": sym})
            eng = BacktestEngine(cfg)
            eng._saved_learning_flags = (None, None)  # orchestrator restores
            if i == 0:
                self._portfolio = eng.portfolio
                self._risk = eng.risk
                self._analyzer = eng.analyzer
            else:
                eng.portfolio = self._portfolio
                eng.risk = self._risk
                eng.analyzer = self._analyzer
            self._engines[sym] = eng
        # Shared close-callback: portfolio closes feed the ONE risk streak.
        self._portfolio._on_trade_close = self._risk.record_trade_result

        self._equity_curve: list[EquityPoint] = []

    # ── lifecycle ────────────────────────────────────────────────

    def cleanup(self) -> None:
        for eng in self._engines.values():
            eng.cleanup()
        try:
            _cal, _exp = self._saved_learning_flags
            object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", _cal)
            object.__setattr__(CONFIG.analyzer, "setup_expectancy_enabled", _exp)
        except Exception:
            pass

    # ── main loop ────────────────────────────────────────────────

    async def run(self, data: dict[str, list]) -> BacktestResult:
        """Run the merged multi-symbol backtest. ``data`` maps each configured
        symbol to its (ascending) BacktestBar list; symbols absent from the
        map are skipped with a log line."""
        start_time = time.time()
        streams = {s: bars for s, bars in data.items()
                   if s in self._engines and bars}
        if not streams:
            raise ValueError("no bar data for any configured symbol")

        audit(system_log, f"Portfolio backtest started: {len(streams)} symbols",
              action="portfolio_backtest_start",
              data={"symbols": list(streams),
                    "bars": {s: len(b) for s, b in streams.items()},
                    "balance": self.config.initial_balance})

        lookback = self.config.lookback_size
        scan_interval = self.config.scan_interval

        # Merged ascending timeline of every timestamp any symbol has a bar at.
        timeline = sorted({b.timestamp for bars in streams.values() for b in bars})
        # Per-symbol cursors + per-symbol bar counters (scan cadence is per
        # symbol, exactly like N independent run() loops).
        idx = {s: 0 for s in streams}
        last_close: dict[str, float] = {}
        for eng in self._engines.values():
            eng._pending_entry = None

        snap_counter = 0
        for ts in timeline:
            # Simulated clock: cooldown-after-loss must elapse in BAR time.
            self._risk.set_sim_time(ts)
            for sym, bars in streams.items():
                i = idx[sym]
                if i >= len(bars) or bars[i].timestamp != ts:
                    continue
                idx[sym] = i + 1
                eng = self._engines[sym]
                bar = bars[i]
                last_close[sym] = bar.close

                if i < lookback:
                    continue  # warmup — indicators need history

                # Same per-bar pipeline as BacktestEngine.run():
                if eng._pending_entry is not None:
                    _p_idea, _p_risk = eng._pending_entry
                    eng._pending_entry = None
                    eng._execute_fill(_p_idea, _p_risk, bar.open, bar)

                eng._check_stops_intrabar(bar)

                if i % scan_interval == 0:
                    window = bars[max(0, i - lookback):i + 1]
                    await eng._process_bar(bar, window, i)

            # Portfolio-level equity snapshot (throttled like run()'s cadence).
            snap_counter += 1
            if snap_counter % scan_interval == 0 or ts == timeline[-1]:
                open_assets = {p.asset for p in self._portfolio._positions.values()}
                assets_px = {a: last_close[a] for a in open_assets if a in last_close}
                self._portfolio.mark_to_market(assets_px)
                snap = self._portfolio.snapshot()
                peak = self._portfolio._peak_equity
                dd = ((peak - snap.equity_usd) / peak * 100) if peak > 0 else 0
                self._equity_curve.append(EquityPoint(
                    timestamp=ts, equity=snap.equity_usd,
                    drawdown_pct=round(dd, 2),
                    open_positions=snap.open_positions))

        # Close whatever is still open at each symbol's LAST bar (its own
        # price — never another symbol's).
        for sym, bars in streams.items():
            self._engines[sym]._close_all_at_bar(bars[-1], "END_OF_DATA")

        # ── Aggregate compile ────────────────────────────────────
        # Reuse the single-engine compiler with merged inputs: the shared
        # portfolio snapshot is already system-level; inject the merged trade
        # list + portfolio equity curve + summed pipeline counters into the
        # first engine and let _compile_result do the math.
        first = self._engines[self.symbols[0]]
        merged_trades = sorted(
            (t for eng in self._engines.values() for t in eng._trades),
            key=lambda t: t.exit_time)
        first._trades = merged_trades
        first._equity_curve = self._equity_curve
        first._rr_values = [rr for eng in self._engines.values()
                            for rr in eng._rr_values]
        first._signals_generated = sum(e._signals_generated for e in self._engines.values())
        first._ideas_generated = sum(e._ideas_generated for e in self._engines.values())
        first._ideas_rejected_risk = sum(e._ideas_rejected_risk for e in self._engines.values())
        first._ideas_rejected_confidence = sum(
            e._ideas_rejected_confidence for e in self._engines.values())

        longest = max(streams.values(), key=len)
        result = first._compile_result(longest, time.time() - start_time)
        result.symbol = "+".join(sorted(streams))

        # Per-symbol breakdown for reporting.
        self.per_symbol = {}
        for sym in streams:
            st = [t for t in merged_trades if t.symbol == sym]
            wins = sum(1 for t in st if t.net_pnl_usd > 0)
            self.per_symbol[sym] = {
                "trades": len(st),
                "net_pnl": round(sum(t.net_pnl_usd for t in st), 2),
                "win_rate": round(wins / len(st), 4) if st else 0.0,
            }

        audit(system_log,
              f"Portfolio backtest complete: {result.total_trades} trades, "
              f"return={result.total_return_pct:.2f}%",
              action="portfolio_backtest_complete",
              data={"per_symbol": self.per_symbol})
        return result


async def portfolio_walk_forward(
    data: dict[str, list],
    config: BacktestConfig,
    n_folds: int = 5,
    is_min_frac: float = 0.4,
) -> list[dict]:
    """Rolling OOS robustness over the merged timeline, portfolio-wide.

    Splits the merged timestamp range into ``n_folds`` out-of-sample blocks
    after an ``is_min_frac`` warmup prefix (mirroring walk_forward.make_folds)
    and runs a fresh PortfolioBacktester per block. Each block's data starts
    exactly ``lookback_size`` timestamps before its OOS start, so the engine's
    indicator warmup consumes that prefix and TRADING begins at the OOS
    boundary — no optimizer runs here, so there is nothing to embargo.
    """
    timeline = sorted({b.timestamp for bars in data.values() for b in bars})
    n = len(timeline)
    warmup = int(n * is_min_frac)
    block = max(1, (n - warmup) // n_folds)
    out = []
    for k in range(n_folds):
        oos_start = warmup + k * block
        oos_end = warmup + (k + 1) * block if k < n_folds - 1 else n
        # Exactly lookback_size warmup bars before the block: the engine skips
        # them as indicator warmup, so trading starts at the OOS boundary.
        slice_start_i = max(0, oos_start - config.lookback_size)
        t0 = timeline[slice_start_i]
        t_start = timeline[oos_start]
        t_end = timeline[oos_end - 1]
        fold_data = {
            s: [b for b in bars if t0 <= b.timestamp <= t_end]
            for s, bars in data.items()
        }
        fold_data = {s: b for s, b in fold_data.items() if len(b) > config.lookback_size}
        if not fold_data:
            continue
        pb = PortfolioBacktester(config, symbols=list(fold_data))
        res = await pb.run(fold_data)
        pb.cleanup()
        out.append({
            "fold": k,
            "oos_start": str(t_start), "oos_end": str(t_end),
            "trades": res.total_trades,
            "return_pct": res.total_return_pct,
            "win_rate": res.win_rate,
            "max_dd_pct": res.max_drawdown_pct,
            "profit_factor": res.profit_factor,
            "per_symbol": dict(pb.per_symbol),
        })
    return out
