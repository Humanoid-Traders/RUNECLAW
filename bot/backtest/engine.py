"""
RUNECLAW Backtest Engine -- replays historical data through the full pipeline.

Same analyzer. Same risk engine. Same portfolio logic. Different data source.
No human confirmation gate (automated replay). All decisions logged.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Optional

import numpy as np

from bot.backtest.models import (
    BacktestBar, BacktestConfig, BacktestResult, BacktestTrade, EquityPoint,
)
from bot.core.analyzer import Analyzer
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.logger import audit, system_log, trade_log
from bot.utils.models import Direction, MarketSignal, RiskVerdict


class BacktestEngine:
    """
    Event-driven backtesting engine.

    Replays OHLCV bars through the RUNECLAW pipeline:
      1. Build lookback window (perception)
      2. Generate MarketSignal from bar context
      3. Run Analyzer on accumulated candles (decision)
      4. Run RiskEngine on trade idea (validation)
      5. Execute in backtest portfolio (paper, with costs)
      6. Monitor SL/TP against intrabar high/low
      7. Record everything

    Key design decisions:
      - Uses the SAME Analyzer and RiskEngine as live trading
      - Adds commission and slippage modeling
      - SL/TP checked against bar high/low (not just close)
      - No human confirmation gate (would defeat replay purpose)
      - LLM disabled by default for reproducibility (rule-based fallback)
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.portfolio = PortfolioTracker(initial_balance=config.initial_balance)
        self.risk = RiskEngine(self.portfolio)
        self.analyzer = Analyzer()

        # Tracking
        self._trades: list[BacktestTrade] = []
        self._equity_curve: list[EquityPoint] = []
        self._rr_values: list[float] = []  # realized R:R for each closed trade
        self._signals_generated = 0
        self._ideas_generated = 0
        self._ideas_rejected_risk = 0
        self._ideas_rejected_confidence = 0

        # Open position tracking with backtest metadata
        self._open_bt_positions: dict[str, dict] = {}

    async def run(self, bars: list[BacktestBar]) -> BacktestResult:
        """
        Execute a full backtest over the provided bar series.
        Returns a BacktestResult with all metrics and trade records.
        """
        start_time = time.time()

        audit(system_log, f"Backtest started: {self.config.symbol}",
              action="backtest_start", data={
                  "symbol": self.config.symbol,
                  "bars": len(bars),
                  "balance": self.config.initial_balance,
              })

        lookback_size = self.config.lookback_size
        scan_interval = self.config.scan_interval

        for i in range(lookback_size, len(bars)):
            current_bar = bars[i]

            # --- Monitor open positions against this bar's high/low ---
            self._check_stops_intrabar(current_bar)

            # --- Generate signal every scan_interval bars ---
            if i % scan_interval == 0:
                window = bars[max(0, i - lookback_size):i + 1]
                await self._process_bar(current_bar, window, i)

            # --- Record equity curve ---
            if i % scan_interval == 0 or i == len(bars) - 1:
                snap = self.portfolio.snapshot()
                peak = self.portfolio._peak_equity
                dd = ((peak - snap.equity_usd) / peak * 100) if peak > 0 else 0
                self._equity_curve.append(EquityPoint(
                    timestamp=current_bar.timestamp,
                    equity=snap.equity_usd,
                    drawdown_pct=round(dd, 2),
                    open_positions=snap.open_positions,
                ))

        # --- Close remaining positions at last bar's close ---
        if bars:
            self._close_all_at_bar(bars[-1], "END_OF_DATA")

        duration = time.time() - start_time
        result = self._compile_result(bars, duration)

        audit(system_log, f"Backtest complete: {result.total_trades} trades, "
              f"return={result.total_return_pct:.2f}%",
              action="backtest_complete", data=result.model_dump(
                  mode="json", exclude={"trades", "equity_curve"}))

        return result

    # ── Pipeline stages ──────────────────────────────────────────

    async def _process_bar(
        self, bar: BacktestBar, window: list[BacktestBar], bar_index: int
    ) -> None:
        """Run the perception → decision → risk pipeline on a single bar."""

        # 1. Build a MarketSignal from bar context
        signal = self._bar_to_signal(bar, window)
        self._signals_generated += 1

        # 2. Build OHLCV array for analyzer (ccxt format)
        candles = [
            [int(b.timestamp.timestamp() * 1000), b.open, b.high, b.low, b.close, b.volume]
            for b in window
        ]

        if len(candles) < 30:
            return

        # 3. Run analyzer (same as live)
        idea = await self.analyzer.analyze(signal, candles)
        if idea is None:
            self._ideas_rejected_confidence += 1
            return

        self._ideas_generated += 1

        # 4. Compute ATR from the window for the volatility guard
        atr_value = None
        if len(window) >= 15:
            true_ranges = []
            for j in range(1, min(15, len(window))):
                h = window[-j].high
                l = window[-j].low
                pc = window[-j - 1].close
                tr = max(h - l, abs(h - pc), abs(l - pc))
                true_ranges.append(tr)
            atr_value = sum(true_ranges) / len(true_ranges)

        # 4b. Risk gate (same as live)
        risk_check = self.risk.evaluate(idea, atr=atr_value)
        if risk_check.verdict == RiskVerdict.REJECTED:
            self._ideas_rejected_risk += 1
            audit(trade_log, f"[BT] Trade REJECTED: {risk_check.reason}",
                  action="backtest_risk", result="REJECTED")
            return

        # 5. Execute (no human confirmation in backtest)
        size_usd = risk_check.position_size_usd

        # Apply entry slippage BEFORE opening portfolio position so the
        # portfolio's internal equity/drawdown curve reflects slipped entries.
        slippage = idea.entry_price * (self.config.slippage_pct / 100)
        if idea.direction == Direction.LONG:
            adjusted_entry = idea.entry_price + slippage
        else:
            adjusted_entry = idea.entry_price - slippage

        # Create a slippage-adjusted copy of the idea for portfolio
        slipped_idea = idea.model_copy(update={"entry_price": round(adjusted_entry, 6)})
        trade = self.portfolio.open_position(slipped_idea, size_usd)

        # Store backtest metadata
        # STRATEGY: trailing stop after 1R profit -- track best_price and trailing state
        initial_risk = abs(idea.entry_price - idea.stop_loss)
        # Canonical ATR: recover the ATR used to set the stop (initial_risk / sl_mult).
        # This is used for trailing distance (1.5 * canonical_atr) and is consistent
        # across backtest, live, and portfolio paths.
        sl_mult = 2.5  # matches CONFIG.analyzer.sl_atr_mult_default
        canonical_atr = initial_risk / sl_mult if initial_risk > 0 else (atr_value or idea.entry_price * 0.02)
        self._open_bt_positions[idea.id] = {
            "entry_time": bar.timestamp,
            "adjusted_entry": adjusted_entry,
            "commission_entry": size_usd * (self.config.commission_pct / 100),
            "slippage_entry": slippage * trade.quantity,
            "idea": idea,
            "risk_verdict": risk_check.verdict.value,
            "best_price": adjusted_entry,  # best favorable price since entry
            "trailing_active": False,       # activated once profit >= 1R
            "initial_risk": initial_risk,   # 1R distance for trailing activation
            "atr_value": canonical_atr,     # canonical ATR for trailing stop distance
        }

        audit(trade_log, f"[BT] Opened {idea.direction.value} {idea.asset}",
              action="backtest_execute", result="OPENED",
              data={"trade_id": idea.id, "entry": adjusted_entry, "size": size_usd})

    def _check_stops_intrabar(self, bar: BacktestBar) -> None:
        """
        Check open positions against the bar's high and low.
        This is more realistic than checking only close prices --
        a stop-loss at $66,000 should trigger if the low was $65,800
        even if the close was $67,000.
        """
        for tid, pos in list(self.portfolio._positions.items()):
            if tid not in self._open_bt_positions:
                continue

            bt_meta = self._open_bt_positions[tid]
            direction = pos.direction
            sl = pos.stop_loss
            tp = pos.take_profit

            # STRATEGY: trailing stop after 1R profit
            # Update best_price and check if trailing stop should activate
            entry = bt_meta["adjusted_entry"]
            initial_risk = bt_meta.get("initial_risk", 0)
            atr_val = bt_meta.get("atr_value", 0)  # canonical ATR, set at entry

            if direction == Direction.LONG:
                # Track the highest price seen since entry
                if bar.high > bt_meta["best_price"]:
                    bt_meta["best_price"] = bar.high

                # Activate trailing once unrealized profit >= 1R
                if not bt_meta["trailing_active"] and initial_risk > 0:
                    if bt_meta["best_price"] - entry >= initial_risk:
                        bt_meta["trailing_active"] = True

                # If trailing is active, compute trailing stop (1.5x ATR below best)
                if bt_meta["trailing_active"] and atr_val > 0:
                    trailing_sl = bt_meta["best_price"] - 1.5 * atr_val
                    # Only tighten, never widen -- use the higher of original SL and trailing SL
                    if trailing_sl > sl:
                        sl = trailing_sl
            else:
                # SHORT: track the lowest price seen since entry
                if bar.low < bt_meta["best_price"]:
                    bt_meta["best_price"] = bar.low

                # Activate trailing once unrealized profit >= 1R
                if not bt_meta["trailing_active"] and initial_risk > 0:
                    if entry - bt_meta["best_price"] >= initial_risk:
                        bt_meta["trailing_active"] = True

                # If trailing is active, compute trailing stop (1.5x ATR above best)
                if bt_meta["trailing_active"] and atr_val > 0:
                    trailing_sl = bt_meta["best_price"] + 1.5 * atr_val
                    # Only tighten, never widen -- use the lower of original SL and trailing SL
                    if trailing_sl < sl:
                        sl = trailing_sl

            # Check SL: use bar low for LONG, bar high for SHORT
            if direction == Direction.LONG:
                if bar.low <= sl:
                    reason = "TRAILING_SL" if bt_meta["trailing_active"] else "SL"
                    self._close_position(tid, sl, bar, reason)
                    continue
                if bar.high >= tp:
                    self._close_position(tid, tp, bar, "TP")
                    continue
            else:
                if bar.high >= sl:
                    reason = "TRAILING_SL" if bt_meta["trailing_active"] else "SL"
                    self._close_position(tid, sl, bar, reason)
                    continue
                if bar.low <= tp:
                    self._close_position(tid, tp, bar, "TP")
                    continue

    def _close_position(
        self, trade_id: str, exit_price: float, bar: BacktestBar, reason: str
    ) -> None:
        """Close a position and record the backtest trade."""
        bt_meta = self._open_bt_positions.pop(trade_id, None)
        if bt_meta is None:
            return

        pos = self.portfolio._positions.get(trade_id)
        if pos is None:
            return

        # Apply exit slippage
        slippage_exit = exit_price * (self.config.slippage_pct / 100)
        if pos.direction == Direction.LONG:
            adjusted_exit = exit_price - slippage_exit
        else:
            adjusted_exit = exit_price + slippage_exit

        # Close in portfolio tracker
        closed = self.portfolio.close_position(trade_id, adjusted_exit)
        if closed is None:
            return

        # Commission on exit
        exit_value = adjusted_exit * closed.quantity
        commission_exit = exit_value * (self.config.commission_pct / 100)
        total_commission = bt_meta["commission_entry"] + commission_exit
        total_slippage = bt_meta["slippage_entry"] + slippage_exit * closed.quantity
        net_pnl = closed.pnl - total_commission - total_slippage

        # Duration
        entry_time = bt_meta["entry_time"]
        duration_hours = (bar.timestamp - entry_time).total_seconds() / 3600

        idea = bt_meta["idea"]
        size_usd = bt_meta["adjusted_entry"] * closed.quantity

        bt_trade = BacktestTrade(
            trade_id=trade_id,
            symbol=idea.asset,
            direction=idea.direction.value,
            entry_price=bt_meta["adjusted_entry"],
            exit_price=adjusted_exit,
            entry_time=entry_time,
            exit_time=bar.timestamp,
            quantity=closed.quantity,
            size_usd=round(size_usd, 2),
            pnl_usd=round(closed.pnl, 2),
            pnl_pct=round((closed.pnl / size_usd * 100) if size_usd > 0 else 0, 2),
            commission_usd=round(total_commission, 2),
            slippage_usd=round(total_slippage, 2),
            net_pnl_usd=round(net_pnl, 2),
            exit_reason=reason,
            confidence=idea.confidence,
            risk_verdict=bt_meta["risk_verdict"],
            reasoning=idea.reasoning,
            signals_used=idea.signals_used,
        )
        self._trades.append(bt_trade)

        # Record realized R:R using actual entry/SL risk distance
        risk_dist = abs(bt_meta["adjusted_entry"] - idea.stop_loss)
        if risk_dist > 0:
            reward_dist = abs(adjusted_exit - bt_meta["adjusted_entry"])
            self._rr_values.append(reward_dist / risk_dist)

        audit(trade_log, f"[BT] Closed {idea.asset} reason={reason} PnL=${net_pnl:.2f}",
              action="backtest_close", result=reason,
              data={"trade_id": trade_id, "pnl": net_pnl, "duration_h": duration_hours})

    def _close_all_at_bar(self, bar: BacktestBar, reason: str) -> None:
        """Force-close all remaining open positions at bar close."""
        for tid in list(self._open_bt_positions.keys()):
            self._close_position(tid, bar.close, bar, reason)

    # ── Helpers ───────────────────────────────────────────────────

    def _bar_to_signal(self, bar: BacktestBar, window: list[BacktestBar]) -> MarketSignal:
        """Convert a bar + context window into a MarketSignal."""
        if len(window) >= 2:
            prev_close = window[-2].close
            change_pct = ((bar.close - prev_close) / prev_close * 100) if prev_close > 0 else 0
        else:
            change_pct = 0

        # Volume spike: compare to rolling average
        if len(window) >= 6:
            avg_vol = sum(b.volume for b in window[-6:-1]) / 5
            volume_spike = bar.volume > avg_vol * 2.0
        else:
            volume_spike = False

        momentum = max(min(change_pct / 10.0, 1.0), -1.0)
        if volume_spike:
            momentum = max(min(momentum * 1.3, 1.0), -1.0)

        return MarketSignal(
            symbol=self.config.symbol,
            price=bar.close,
            change_pct_24h=round(change_pct, 2),
            volume_usd_24h=round(bar.volume, 2),
            volume_spike=volume_spike,
            momentum_score=round(momentum, 3),
            timestamp=bar.timestamp,
        )

    def _compile_result(self, bars: list[BacktestBar], duration: float) -> BacktestResult:
        """Compute all metrics from recorded trades and equity curve."""
        snap = self.portfolio.snapshot()
        trades = self._trades

        # Basic stats
        total = len(trades)
        winners = [t for t in trades if t.net_pnl_usd > 0]
        losers = [t for t in trades if t.net_pnl_usd <= 0]
        win_rate = len(winners) / total if total > 0 else 0

        gross_profit = sum(t.net_pnl_usd for t in winners) if winners else 0
        gross_loss = abs(sum(t.net_pnl_usd for t in losers)) if losers else 0

        avg_win = (gross_profit / len(winners)) if winners else 0
        avg_loss = (gross_loss / len(losers)) if losers else 0
        largest_win = max((t.net_pnl_usd for t in winners), default=0)
        largest_loss = min((t.net_pnl_usd for t in losers), default=0)

        # Duration
        durations = [(t.exit_time - t.entry_time).total_seconds() / 3600 for t in trades]
        avg_duration = sum(durations) / len(durations) if durations else 0

        # Consecutive losses
        max_consec = 0
        current_consec = 0
        for t in trades:
            if t.net_pnl_usd <= 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0

        # Risk metrics from equity curve
        max_dd_pct = max((p.drawdown_pct for p in self._equity_curve), default=0)
        peak_equity = max((p.equity for p in self._equity_curve), default=self.config.initial_balance)
        max_dd_usd = max(
            (peak_equity - p.equity for p in self._equity_curve), default=0
        )

        # Profit factor
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.99 if gross_profit > 0 else 0)

        # Sharpe, Sortino, Calmar from equity curve returns
        sharpe = self._compute_sharpe()
        sortino = self._compute_sortino()
        total_return = ((snap.equity_usd - self.config.initial_balance) /
                        self.config.initial_balance * 100)
        calmar = (total_return / max_dd_pct) if max_dd_pct > 0 else 0

        # Commission and slippage totals
        total_comm = sum(t.commission_usd for t in trades)
        total_slip = sum(t.slippage_usd for t in trades)

        # Average R:R -- use realized values computed at trade close time
        avg_rr = sum(self._rr_values) / len(self._rr_values) if self._rr_values else 0

        # Date range
        start_date = bars[0].timestamp.strftime("%Y-%m-%d") if bars else ""
        end_date = bars[-1].timestamp.strftime("%Y-%m-%d") if bars else ""

        return BacktestResult(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_balance=self.config.initial_balance,
            commission_pct=self.config.commission_pct,
            slippage_pct=self.config.slippage_pct,
            final_equity=round(snap.equity_usd, 2),
            total_return_pct=round(total_return, 2),
            total_pnl=round(sum(t.pnl_usd for t in trades), 2),
            total_commission=round(total_comm, 2),
            total_slippage=round(total_slip, 2),
            net_pnl=round(sum(t.net_pnl_usd for t in trades), 2),
            total_trades=total,
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=round(win_rate, 4),
            avg_win_usd=round(avg_win, 2),
            avg_loss_usd=round(avg_loss, 2),
            largest_win_usd=round(largest_win, 2),
            largest_loss_usd=round(largest_loss, 2),
            avg_trade_duration_hours=round(avg_duration, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_drawdown_usd=round(max_dd_usd, 2),
            max_consecutive_losses=max_consec,
            profit_factor=round(profit_factor, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            risk_reward_avg=round(avg_rr, 2),
            total_signals_generated=self._signals_generated,
            total_ideas_generated=self._ideas_generated,
            total_ideas_rejected_risk=self._ideas_rejected_risk,
            total_ideas_rejected_confidence=self._ideas_rejected_confidence,
            trades=trades,
            equity_curve=self._equity_curve,
            duration_seconds=round(duration, 2),
            bars_processed=len(bars),
        )

    def _compute_sharpe(self, risk_free_rate: float = 0.04) -> float:
        """Annualized Sharpe ratio from equity curve.
        Computes annualization factor from actual observation frequency,
        not a hardcoded 2190."""
        if len(self._equity_curve) < 2:
            return 0.0
        equities = [p.equity for p in self._equity_curve]
        returns = np.diff(equities) / equities[:-1]
        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0
        # Compute actual periods per year from timestamps
        ts = [p.timestamp for p in self._equity_curve]
        total_seconds = (ts[-1] - ts[0]).total_seconds()
        if total_seconds <= 0:
            return 0.0
        observations = len(returns)
        seconds_per_obs = total_seconds / observations
        periods_per_year = (365.25 * 24 * 3600) / seconds_per_obs if seconds_per_obs > 0 else 2190
        excess = np.mean(returns) - risk_free_rate / periods_per_year
        return float(excess / np.std(returns) * np.sqrt(periods_per_year))

    def _compute_sortino(self, risk_free_rate: float = 0.04) -> float:
        """Annualized Sortino ratio (downside deviation only).
        Uses actual observation frequency for annualization."""
        if len(self._equity_curve) < 2:
            return 0.0
        equities = [p.equity for p in self._equity_curve]
        returns = np.diff(equities) / equities[:-1]
        downside = returns[returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0
        # Compute actual periods per year from timestamps
        ts = [p.timestamp for p in self._equity_curve]
        total_seconds = (ts[-1] - ts[0]).total_seconds()
        if total_seconds <= 0:
            return 0.0
        observations = len(returns)
        seconds_per_obs = total_seconds / observations
        periods_per_year = (365.25 * 24 * 3600) / seconds_per_obs if seconds_per_obs > 0 else 2190
        excess = np.mean(returns) - risk_free_rate / periods_per_year
        return float(excess / np.std(downside) * np.sqrt(periods_per_year))
