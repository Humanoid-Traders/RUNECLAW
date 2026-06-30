"""
RUNECLAW Backtest Engine -- replays historical data through the full pipeline.

Same analyzer. Same risk engine. Same portfolio logic. Different data source.
No human confirmation gate (automated replay). All decisions logged.
"""

from __future__ import annotations

import os
import tempfile
import time

import numpy as np

from bot.backtest.models import (
    BacktestBar, BacktestConfig, BacktestResult, BacktestTrade, EquityPoint,
)
from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.logger import audit, system_log, trade_log
from bot.utils.models import Direction, MarketSignal, RiskVerdict
from bot.utils.trailing import make_trailing_state, update_trailing_stop


def _env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean env flag. Truthy = {1,true,yes,on} (case-insensitive)."""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
        # BT-H1: pass the backtest's commission_pct so the charged fee matches the
        # reported fee (the knob was previously ignored — live CONFIG was used).
        self.portfolio = PortfolioTracker(
            initial_balance=config.initial_balance,
            commission_pct=config.commission_pct,
        )
        # N1 fix: isolate backtest risk state so backtests don't pollute the
        # production circuit breaker / loss streak.  Each backtest gets its own
        # throwaway state file that is cleaned up when the engine is garbage-collected.
        self._bt_state_dir = tempfile.mkdtemp(prefix="runeclaw_bt_")
        bt_state_file = os.path.join(self._bt_state_dir, "risk_state.json")
        self.risk = RiskEngine(self.portfolio, state_file=bt_state_file)
        # C1 fix: wire trade-close callback so portfolio closes feed risk streak tracking
        self.portfolio._on_trade_close = self.risk.record_trade_result
        # Fix F: respect use_llm flag for reproducibility.
        # When use_llm=False (default), null out the LLM client so the analyzer
        # always uses the deterministic rule-based path regardless of env config.
        self.analyzer = Analyzer()
        if not config.use_llm:
            self.analyzer._llm = None
        # Deterministic parity: replay recorded LLM theses so the backtest runs
        # the SAME blended path live uses, with no network call. Takes precedence
        # over use_llm; the offline hook short-circuits the network LLM and falls
        # back to the rule engine for (symbol, time) pairs with no recording.
        if config.use_recorded_llm:
            from bot.backtest.recorded_llm import RecordedLLM
            self._recorded_llm = RecordedLLM.from_jsonl(config.recorded_llm_path)
            self.analyzer._offline_thesis_fn = self._recorded_llm.thesis_at

        # #17: replay shadow-recorded order-flow snapshots into analyze() so the
        # backtest runs the same microstructure path live does. Default OFF →
        # _recorded_order_flow stays None and analyze() gets order_flow=None,
        # byte-identical to the legacy backtest.
        self._recorded_order_flow = None
        if config.use_recorded_order_flow:
            from bot.backtest.recorded_order_flow import RecordedOrderFlow
            self._recorded_order_flow = RecordedOrderFlow.from_jsonl(
                config.recorded_order_flow_path
            )

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

        # #18: backtest partial-TP/pyramiding parity. Default OFF so the
        # default backtest output is byte-identical to the single full-exit
        # model. When enabled, open positions are managed through the SAME
        # partial-TP ladder live uses (TP1 50% @1.5R + SL→breakeven, TP2 30%
        # @2.5R + lock 1R, runner 20% ATR-trail), so backtest exit behavior
        # reflects live. Gated by its own env flag (NOT CONFIG.partial_tp.enabled,
        # which defaults TRUE for live) to keep default backtests unchanged.
        self._partial_tp_enabled = _env_bool("BACKTEST_PARTIAL_TP", False)

    @staticmethod
    def _below_confidence_gate(confidence: float, threshold: float) -> bool:
        """True if a trade should be skipped because its confidence is below the
        per-run ``confidence_threshold``. ``threshold <= 0`` disables the gate. A
        confidence exactly AT the threshold passes (gate is strict ``<``)."""
        return threshold > 0.0 and float(confidence) < float(threshold)

    def cleanup(self) -> None:
        """Explicitly remove the temp state directory. Call after backtest completes."""
        import shutil
        try:
            shutil.rmtree(self._bt_state_dir, ignore_errors=True)
        except Exception:
            pass

    def __del__(self) -> None:
        """Best-effort cleanup on GC. Prefer calling cleanup() explicitly."""
        self.cleanup()

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
                # F-05 fix: mark open positions to market before snapshot
                # so equity curve reflects unrealized PnL and drawdown is accurate
                self.portfolio.mark_to_market({
                    p.asset: current_bar.close
                    for p in self.portfolio._positions.values()
                })
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

        # #17: replay the causal order-flow snapshot for this bar (None when
        # disabled or no record → analyzer runs without order flow as before).
        order_flow = None
        if self._recorded_order_flow is not None:
            order_flow = self._recorded_order_flow.signal_at(signal.symbol, as_of=bar.timestamp)

        # 3. Run analyzer (same as live). BT-H2: pass the simulated bar time so
        # session-aware confidence is causal/reproducible (not wall-clock).
        idea = await self.analyzer.analyze(
            signal, candles, order_flow=order_flow, as_of=bar.timestamp
        )
        if idea is None:
            self._ideas_rejected_confidence += 1
            return

        # Per-run confidence gate. The walk-forward optimizer sweeps
        # config.confidence_threshold; honor it here as an explicit minimum so the
        # swept value actually filters trades (otherwise every grid entry produced
        # identical results and the optimization was a no-op). 0 = no extra gate.
        thr = getattr(self.config, "confidence_threshold", 0.0) or 0.0
        if self._below_confidence_gate(idea.confidence, thr):
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

        # 4b. Risk gate (same as live). BT-H2: pass bar time for session sizing.
        # Regime-aware sizing (gated, same bridge as live): set the analyzer's
        # per-symbol regime so the per-regime multiplier applies, keeping backtest
        # ↔ live parity. No-op when REGIME_SIZING_ENABLED is off.
        if CONFIG.risk.regime_sizing_enabled:
            try:
                _reg = self.analyzer._current_regimes.get(signal.symbol)
                if _reg is not None:
                    self.risk.set_regime(_reg.value, "NORMAL")
            except Exception:
                pass
        risk_check = self.risk.evaluate(idea, atr=atr_value, as_of=bar.timestamp)
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

        # STRATEGY: trailing stop after 1R profit -- use shared utility
        initial_risk = abs(idea.entry_price - idea.stop_loss)
        # M2 fix: read sl_mult from config instead of hardcoding
        sl_mult = CONFIG.analyzer.sl_atr_mult_default
        canonical_atr = initial_risk / sl_mult if initial_risk > 0 else (atr_value or idea.entry_price * 0.02)
        trailing = make_trailing_state(adjusted_entry, idea.direction.value, initial_risk, canonical_atr)
        trailing["entry_price"] = adjusted_entry
        self._open_bt_positions[idea.id] = {
            "entry_time": bar.timestamp,
            "adjusted_entry": adjusted_entry,
            "commission_entry": size_usd * (self.config.commission_pct / 100),
            "slippage_entry": slippage * trade.quantity,
            "idea": idea,
            "risk_verdict": risk_check.verdict.value,
            **trailing,
        }

        # #18: when the partial-TP ladder is enabled, create the SAME state object
        # live uses so the backtest scales out identically. The ladder then owns the
        # stop (breakeven after TP1, lock-1R after TP2, ATR-trail for the runner);
        # the legacy single-exit trailing path above is skipped for this position.
        if self._partial_tp_enabled:
            from bot.core.partial_tp import create_partial_tp_state
            self._open_bt_positions[idea.id]["ptp_state"] = create_partial_tp_state(
                trade_id=idea.id,
                direction=idea.direction.value,
                entry_price=adjusted_entry,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                quantity=trade.quantity,
                atr=canonical_atr,
            )

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

            # #18: positions opened under the partial-TP ladder are driven
            # entirely by the ladder (scale-outs + ladder-owned stop). Only
            # reached when BACKTEST_PARTIAL_TP is on, so the default single-exit
            # path below is byte-identical when the flag is off.
            if self._partial_tp_enabled and "ptp_state" in bt_meta:
                self._check_ladder_intrabar(tid, pos, bt_meta, bar)
                continue

            direction = pos.direction
            sl = pos.stop_loss
            tp = pos.take_profit

            # C2-38 FIX: Check SL against adverse extreme FIRST, before updating
            # trailing with the favorable extreme.  Previously, trailing was updated
            # first (e.g. bar.high for LONG), which could tighten the stop, and then
            # bar.low was checked against the NEW tighter stop — causing phantom
            # stop-outs on bars where the old stop would not have been hit.
            # C2-39 NOTE: When both SL and TP are breachable within the same bar,
            # SL is checked first. This is a conservative (pessimistic) assumption —
            # in reality, which fires first depends on intrabar price path which is
            # not available at this resolution.
            # Gap-aware stop fills (roadmap): a stop-loss is a stop/market order.
            # If the bar GAPS through the stop at the open, it fills at the open
            # (worse than the stop level), not magically at the stop price. Filling
            # stops exactly at `sl` understates loss tails and overstates win rate
            # on gap-throughs. A take-profit is a limit order, so it fills at its
            # level even on a favorable gap (you don't get better than the limit).
            if direction == Direction.LONG:
                if bar.low <= sl:
                    trailing_active = bt_meta.get("trailing_active", False)
                    reason = "TRAILING_SL" if trailing_active else "SL"
                    sl_fill = min(sl, bar.open)  # gap-down through the stop
                    self._close_position(tid, sl_fill, bar, reason)
                    continue
                if bar.high >= tp:
                    self._close_position(tid, tp, bar, "TP")
                    continue
                # Not stopped out — now update trailing with favorable extreme
                check_price = bar.high
                sl, trailing_active = update_trailing_stop(
                    bt_meta, check_price, sl, direction.value,
                    rule=CONFIG.trailing.trail_rule,
                    playbook_atr_mult=CONFIG.trailing.playbook_atr_mult,
                )
                pos.stop_loss = sl
            else:
                if bar.high >= sl:
                    trailing_active = bt_meta.get("trailing_active", False)
                    reason = "TRAILING_SL" if trailing_active else "SL"
                    sl_fill = max(sl, bar.open)  # gap-up through the stop
                    self._close_position(tid, sl_fill, bar, reason)
                    continue
                if bar.low <= tp:
                    self._close_position(tid, tp, bar, "TP")
                    continue
                # Not stopped out — now update trailing with favorable extreme
                check_price = bar.low
                sl, trailing_active = update_trailing_stop(
                    bt_meta, check_price, sl, direction.value,
                    rule=CONFIG.trailing.trail_rule,
                    playbook_atr_mult=CONFIG.trailing.playbook_atr_mult,
                )
                pos.stop_loss = sl

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

        # N2 fix: portfolio._close_position_locked already deducts commission
        # from PnL and balance. Use the portfolio's authoritative values to
        # avoid double-counting.  Slippage is baked into adjusted entry/exit
        # prices, so it's already reflected in portfolio PnL.
        # LB-6 FIX: closed.pnl is net. Compute gross separately so the
        # BacktestResult waterfall doesn't double-count commission.
        total_slippage = bt_meta["slippage_entry"] + slippage_exit * closed.quantity
        net_pnl = closed.pnl  # already net of commission from portfolio
        gross_pnl = closed.pnl + closed.commission  # add back for gross field

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
            pnl_usd=round(gross_pnl, 2),  # LB-6: gross PnL (before commission)
            pnl_pct=round((gross_pnl / size_usd * 100) if size_usd > 0 else 0, 2),
            commission_usd=round(closed.commission, 2),
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
        # Signed: positive when trade moved in the right direction
        risk_dist = abs(bt_meta["adjusted_entry"] - idea.stop_loss)
        if risk_dist > 0:
            if idea.direction == Direction.LONG:
                reward_dist = adjusted_exit - bt_meta["adjusted_entry"]
            else:
                reward_dist = bt_meta["adjusted_entry"] - adjusted_exit
            self._rr_values.append(reward_dist / risk_dist)

        audit(trade_log, f"[BT] Closed {idea.asset} reason={reason} PnL=${net_pnl:.2f}",
              action="backtest_close", result=reason,
              data={"trade_id": trade_id, "pnl": net_pnl, "duration_h": duration_hours})

    def _check_ladder_intrabar(
        self, tid: str, pos, bt_meta: dict, bar: BacktestBar
    ) -> None:
        """#18: drive one position through the live partial-TP ladder against the
        bar's high/low. Mirrors ``bot.core.partial_tp.check_partial_tp`` exactly but
        is bar-aware: TP / runner-trail levels are tested against the FAVORABLE
        extreme (limit-style fills at the ladder price), while the stop is tested
        against the ADVERSE extreme first — the same pessimistic SL-before-TP
        convention the single-exit path uses. A runner stop hit is caught by the
        stop check on a subsequent bar, consistent with how the legacy trailing
        path defers stop fills."""
        cfg = CONFIG.partial_tp
        state = bt_meta["ptp_state"]
        is_long = pos.direction == Direction.LONG
        adverse = bar.low if is_long else bar.high
        favorable = bar.high if is_long else bar.low

        # 1. Stop first (pessimistic). current_sl already reflects breakeven /
        #    lock-1R / runner-trail moves from prior bars. A gap through the stop
        #    fills at the bar open (worse than the stop), not magically at the level.
        if (is_long and adverse <= state.current_sl) or (
            not is_long and adverse >= state.current_sl
        ):
            sl_fill = min(state.current_sl, bar.open) if is_long else max(state.current_sl, bar.open)
            reason = "TRAILING_SL" if state.tp1_hit else "SL"
            self._close_position(tid, sl_fill, bar, reason)
            return

        # 2. TP1 — close tp1_close_pct of the original qty at 1.5R, SL→breakeven.
        if not state.tp1_hit and state.initial_risk > 0:
            tp1_price = (
                state.entry_price + cfg.tp1_r_multiple * state.initial_risk if is_long
                else state.entry_price - cfg.tp1_r_multiple * state.initial_risk
            )
            reached = favorable >= tp1_price if is_long else favorable <= tp1_price
            if reached:
                close_qty = min(
                    state.original_qty * (cfg.tp1_close_pct / 100.0), state.remaining_qty
                )
                if close_qty > 0:
                    state.tp1_hit = True
                    state.tp1_qty_closed = close_qty
                    state.remaining_qty -= close_qty
                    fee_buffer = state.entry_price * 0.001
                    state.current_sl = (
                        state.entry_price + fee_buffer if is_long
                        else state.entry_price - fee_buffer
                    )
                    pos.stop_loss = state.current_sl
                    self._partial_close(tid, bt_meta, pos, close_qty, tp1_price, bar, "TP1")

        # 3. TP2 — close tp2_close_pct at 2.5R, tighten SL to lock 1R of profit.
        if state.tp1_hit and not state.tp2_hit and state.initial_risk > 0:
            tp2_price = (
                state.entry_price + cfg.tp2_r_multiple * state.initial_risk if is_long
                else state.entry_price - cfg.tp2_r_multiple * state.initial_risk
            )
            reached = favorable >= tp2_price if is_long else favorable <= tp2_price
            if reached:
                close_qty = min(
                    state.original_qty * (cfg.tp2_close_pct / 100.0), state.remaining_qty
                )
                if close_qty > 0:
                    state.tp2_hit = True
                    state.tp2_qty_closed = close_qty
                    state.remaining_qty -= close_qty
                    lock = state.initial_risk
                    state.current_sl = (
                        state.entry_price + lock if is_long else state.entry_price - lock
                    )
                    pos.stop_loss = state.current_sl
                    self._partial_close(tid, bt_meta, pos, close_qty, tp2_price, bar, "TP2")

        # 4. Runner — ATR-trail the remaining qty using the favorable extreme;
        #    never loosen the stop. The trailed stop is filled by step 1 next bar.
        if state.tp2_hit and state.remaining_qty > 0:
            trail_dist = state.atr * cfg.runner_trail_atr_mult
            if is_long:
                state.runner_trail_best = max(state.runner_trail_best, favorable)
                new_sl = max(state.runner_trail_best - trail_dist, state.current_sl)
            else:
                state.runner_trail_best = min(state.runner_trail_best, favorable)
                new_sl = min(state.runner_trail_best + trail_dist, state.current_sl)
            state.current_sl = new_sl
            pos.stop_loss = new_sl

    def _partial_close(
        self, tid: str, bt_meta: dict, pos, close_qty: float,
        exit_price: float, bar: BacktestBar, reason: str,
    ) -> None:
        """#18: realize a fraction (``close_qty``) of an open position at a ladder
        price. Credits freed margin + net PnL back to the portfolio balance,
        reduces the portfolio position quantity in place, and records a partial
        BacktestTrade. The residual is closed later via ``_close_position``. Margin /
        commission accounting mirrors ``PortfolioTracker._close_position_locked`` so
        the equity curve stays consistent; the next mark-to-market refreshes peak /
        drawdown off the reduced position."""
        if close_qty <= 0:
            return

        # Exit slippage — limit fills are adjusted adversely by slippage_pct
        # (same model as _close_position).
        slippage_exit = exit_price * (self.config.slippage_pct / 100)
        if pos.direction == Direction.LONG:
            adjusted_exit = exit_price - slippage_exit
            pnl = (adjusted_exit - pos.entry_price) * close_qty
        else:
            adjusted_exit = exit_price + slippage_exit
            pnl = (pos.entry_price - adjusted_exit) * close_qty

        size_usd = pos.entry_price * close_qty
        exit_notional = adjusted_exit * close_qty
        commission = (size_usd + exit_notional) * (self.config.commission_pct / 100.0)
        net_pnl = pnl - commission
        lev = getattr(pos, "leverage", 1) or 1
        margin = size_usd / lev

        # Realize into the shared portfolio balance + daily PnL. Reducing
        # pos.quantity shrinks the residual's locked margin / unrealized PnL so
        # total equity is conserved across the scale-out.
        self.portfolio.balance += margin + net_pnl
        try:
            self.portfolio._record_daily_pnl(net_pnl)
        except Exception:
            pass
        pos.quantity = round(pos.quantity - close_qty, 10)

        # Attribute this fill's share of entry slippage; shrink the residual's so
        # _close_position doesn't re-charge the whole entry slippage to the runner.
        orig_qty = bt_meta["ptp_state"].original_qty
        per_unit_entry_slip = (bt_meta["slippage_entry"] / orig_qty) if orig_qty > 0 else 0.0
        this_entry_slip = per_unit_entry_slip * close_qty
        bt_meta["slippage_entry"] = max(0.0, bt_meta["slippage_entry"] - this_entry_slip)
        total_slippage = this_entry_slip + slippage_exit * close_qty

        idea = bt_meta["idea"]
        bt_trade = BacktestTrade(
            trade_id=tid,
            symbol=idea.asset,
            direction=idea.direction.value,
            entry_price=bt_meta["adjusted_entry"],
            exit_price=adjusted_exit,
            entry_time=bt_meta["entry_time"],
            exit_time=bar.timestamp,
            quantity=round(close_qty, 8),
            size_usd=round(size_usd, 2),
            pnl_usd=round(pnl, 2),  # gross (before commission), matches _close_position
            pnl_pct=round((pnl / size_usd * 100) if size_usd > 0 else 0, 2),
            commission_usd=round(commission, 2),
            slippage_usd=round(total_slippage, 2),
            net_pnl_usd=round(net_pnl, 2),
            exit_reason=reason,
            confidence=idea.confidence,
            risk_verdict=bt_meta["risk_verdict"],
            reasoning=idea.reasoning,
            signals_used=idea.signals_used,
        )
        self._trades.append(bt_trade)

        # Realized R:R for this scale-out (reward measured to the ladder fill).
        risk_dist = abs(bt_meta["adjusted_entry"] - idea.stop_loss)
        if risk_dist > 0:
            if idea.direction == Direction.LONG:
                reward_dist = adjusted_exit - bt_meta["adjusted_entry"]
            else:
                reward_dist = bt_meta["adjusted_entry"] - adjusted_exit
            self._rr_values.append(reward_dist / risk_dist)

        audit(trade_log, f"[BT] Partial {idea.asset} {reason} qty={close_qty:.6f} PnL=${net_pnl:.2f}",
              action="backtest_partial_close", result=reason,
              data={"trade_id": tid, "pnl": net_pnl, "remaining_qty": pos.quantity})

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
        # BT-L: treat exact-breakeven (net_pnl == 0) as neither win nor loss,
        # matching the risk engine's neutral handling. Previously net_pnl <= 0
        # counted breakeven as a loss, depressing win rate / inflating the
        # consecutive-loss streak.
        winners = [t for t in trades if t.net_pnl_usd > 0]
        losers = [t for t in trades if t.net_pnl_usd < 0]
        win_rate = len(winners) / total if total > 0 else 0

        # C2-40 FIX: Renamed from gross_profit/gross_loss — these values use
        # net PnL (after commission), not gross PnL. Names now match semantics.
        net_profit = sum(t.net_pnl_usd for t in winners) if winners else 0
        net_loss = abs(sum(t.net_pnl_usd for t in losers)) if losers else 0

        avg_win = (net_profit / len(winners)) if winners else 0
        avg_loss = (net_loss / len(losers)) if losers else 0
        largest_win = max((t.net_pnl_usd for t in winners), default=0)
        largest_loss = min((t.net_pnl_usd for t in losers), default=0)

        # Duration
        durations = [(t.exit_time - t.entry_time).total_seconds() / 3600 for t in trades]
        avg_duration = sum(durations) / len(durations) if durations else 0

        # Consecutive losses
        max_consec = 0
        current_consec = 0
        for t in trades:
            if t.net_pnl_usd < 0:  # BT-L: breakeven does not extend a loss streak
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            elif t.net_pnl_usd > 0:
                current_consec = 0
            # net_pnl == 0: neutral — neither extends nor resets the streak

        # Risk metrics from equity curve
        max_dd_pct = max((p.drawdown_pct for p in self._equity_curve), default=0)
        # M1 fix: compute max_dd_usd using running peak (consistent with pct calculation)
        running_peak = self.config.initial_balance
        max_dd_usd = 0.0
        for p in self._equity_curve:
            if p.equity > running_peak:
                running_peak = p.equity
            dd_usd = running_peak - p.equity
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd

        # Profit factor
        profit_factor = (net_profit / net_loss) if net_loss > 0 else (999.99 if net_profit > 0 else 0)

        # Sharpe, Sortino, Calmar from equity curve returns
        sharpe = self._compute_sharpe()
        sortino = self._compute_sortino()
        total_return = ((snap.equity_usd - self.config.initial_balance) /
                        self.config.initial_balance * 100)
        # BT-L: Calmar conventionally uses the ANNUALIZED return, not the raw
        # period return — otherwise it isn't comparable across different backtest
        # lengths. Annualize linearly over the equity-curve span (consistent with
        # the Sharpe annualization style above).
        annualized_return = total_return
        if len(self._equity_curve) >= 2:
            span_seconds = (self._equity_curve[-1].timestamp
                            - self._equity_curve[0].timestamp).total_seconds()
            if span_seconds > 0:
                annualized_return = total_return * (365.25 * 24 * 3600) / span_seconds
        calmar = (annualized_return / max_dd_pct) if max_dd_pct > 0 else 0

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
            # Projected LLM cost (when use_llm=False, estimate from signal count)
            projected_llm_cost_usd=round(self._signals_generated * CONFIG.llm.est_cost_per_analysis, 4),
            est_cost_per_analysis=CONFIG.llm.est_cost_per_analysis,
            net_pnl_after_projected_cost=round(
                sum(t.net_pnl_usd for t in trades) - (self._signals_generated * CONFIG.llm.est_cost_per_analysis), 2
            ),
            trades=trades,
            equity_curve=self._equity_curve,
            duration_seconds=round(duration, 2),
            bars_processed=len(bars),
            # Fix L: snapshot effective risk + analyzer config for reproducibility
            effective_config={
                "risk": {
                    "max_position_pct": CONFIG.risk.max_position_pct,
                    "min_confidence": CONFIG.risk.min_confidence,
                    "min_risk_reward": CONFIG.risk.min_risk_reward,
                    "max_daily_loss_pct": CONFIG.risk.max_daily_loss_pct,
                    "max_drawdown_pct": CONFIG.risk.max_drawdown_pct,
                    "volatility_guard_atr_pct": CONFIG.risk.volatility_guard_atr_pct,
                    "max_open_positions": CONFIG.risk.max_open_positions,
                    "max_consecutive_losses": CONFIG.risk.max_consecutive_losses,
                },
                "analyzer": {
                    "llm_weight": CONFIG.analyzer.llm_weight,
                    "confluence_weight": CONFIG.analyzer.confluence_weight,
                    "sl_atr_mult_default": CONFIG.analyzer.sl_atr_mult_default,
                    "tp_atr_mult_default": CONFIG.analyzer.tp_atr_mult_default,
                    "sl_atr_mult_trending": CONFIG.analyzer.sl_atr_mult_trending,
                    "tp_atr_mult_trending": CONFIG.analyzer.tp_atr_mult_trending,
                },
                "backtest": {
                    "use_llm": self.config.use_llm,
                    "lookback_size": self.config.lookback_size,
                    "scan_interval": self.config.scan_interval,
                },
            },
        )

    def _compute_sharpe(self, risk_free_rate: float = 0.04) -> float:
        """Annualized Sharpe ratio from equity curve.
        Computes annualization factor from actual observation frequency,
        not a hardcoded 2190."""
        if len(self._equity_curve) < 2:
            return 0.0
        equities = [p.equity for p in self._equity_curve]
        returns = np.diff(equities) / equities[:-1]
        # BT-L: sample stddev (ddof=1) is the unbiased estimator for a sample of
        # period returns; population stddev (ddof=0) understates variance and
        # overstates Sharpe. Needs >= 2 observations.
        if len(returns) < 2:
            return 0.0
        std = np.std(returns, ddof=1)
        if std == 0:
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
        return float(excess / std * np.sqrt(periods_per_year))

    def _compute_sortino(self, risk_free_rate: float = 0.04) -> float:
        """Annualized Sortino ratio (downside deviation only).
        Uses actual observation frequency for annualization."""
        if len(self._equity_curve) < 2:
            return 0.0
        equities = [p.equity for p in self._equity_curve]
        returns = np.diff(equities) / equities[:-1]
        if len(returns) < 2:
            return 0.0
        downside = returns[returns < 0]
        # BT-L: sample stddev (ddof=1); needs >= 2 downside observations.
        if len(downside) < 2:
            return 0.0
        dstd = np.std(downside, ddof=1)
        if dstd == 0:
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
        return float(excess / dstd * np.sqrt(periods_per_year))


# ── Walk-Forward Backtest ──────────────────────────────────────────

from pydantic import BaseModel as PydanticBaseModel, Field as PydanticField


class WalkForwardResult(PydanticBaseModel):
    """Results of a walk-forward backtest with train/test splits."""
    folds: list[dict] = PydanticField(default_factory=list)
    aggregate_train_return: float = 0.0
    aggregate_test_return: float = 0.0
    train_test_gap: float = 0.0  # positive = overfitting indicator
    consistency_score: float = 0.0  # % of folds where test is profitable
    confidence_calibration: list[dict] = PydanticField(default_factory=list)


async def walk_forward_backtest(
    bars: list[BacktestBar],
    config: BacktestConfig,
    n_folds: int = 3,
    train_ratio: float = 0.7,
) -> WalkForwardResult:
    """
    Walk-forward backtest: split data into N folds, each with a train and test period.
    Trains on the first portion and validates on the unseen remainder.
    This detects overfitting by comparing train vs test performance.
    """
    total_bars = len(bars)
    fold_size = total_bars // n_folds
    if fold_size < 200:
        # Need at least 200 bars per fold (100 lookback + 100 tradeable)
        n_folds = max(1, total_bars // 200)
        fold_size = total_bars // n_folds if n_folds > 0 else total_bars

    folds = []
    confidence_buckets: dict[str, dict] = {}  # bucket -> {total, wins}

    for fold_idx in range(n_folds):
        start = fold_idx * fold_size
        end = min(start + fold_size, total_bars)
        fold_bars = bars[start:end]

        split_point = int(len(fold_bars) * train_ratio)
        # LB-3 FIX: Embargo gap must exclude bars from BOTH train and test
        # to prevent lookahead contamination. Previously only test was shifted.
        embargo = min(50, max(10, len(fold_bars) // 10))
        train_bars = fold_bars[:split_point - embargo]  # stop before embargo zone
        test_bars = fold_bars[split_point + embargo:]    # start after embargo zone

        # Run train period
        train_engine = BacktestEngine(config)
        train_result = await train_engine.run(train_bars)
        train_engine.cleanup()  # W6 FIX: prevent temp directory leak

        # Run test period
        test_engine = BacktestEngine(config)
        test_result = await test_engine.run(test_bars)
        test_engine.cleanup()  # W6 FIX: prevent temp directory leak

        # Collect confidence calibration data from test trades
        for trade in test_result.trades:
            bucket = _confidence_bucket(trade.confidence)
            if bucket not in confidence_buckets:
                confidence_buckets[bucket] = {"total": 0, "wins": 0, "sum_conf": 0.0}
            confidence_buckets[bucket]["total"] += 1
            if trade.net_pnl_usd > 0:
                confidence_buckets[bucket]["wins"] += 1
            confidence_buckets[bucket]["sum_conf"] += trade.confidence

        folds.append({
            "fold": fold_idx + 1,
            "train_bars": len(train_bars),
            "test_bars": len(test_bars),
            "train_return_pct": train_result.total_return_pct,
            "test_return_pct": test_result.total_return_pct,
            "train_win_rate": train_result.win_rate,
            "test_win_rate": test_result.win_rate,
            "train_trades": train_result.total_trades,
            "test_trades": test_result.total_trades,
            "train_sharpe": train_result.sharpe_ratio,
            "test_sharpe": test_result.sharpe_ratio,
            "train_max_dd": train_result.max_drawdown_pct,
            "test_max_dd": test_result.max_drawdown_pct,
        })

    # Aggregate metrics
    train_returns = [f["train_return_pct"] for f in folds]
    test_returns = [f["test_return_pct"] for f in folds]
    avg_train = sum(train_returns) / len(train_returns) if train_returns else 0
    avg_test = sum(test_returns) / len(test_returns) if test_returns else 0
    profitable_tests = sum(1 for r in test_returns if r > 0)
    consistency = profitable_tests / len(test_returns) if test_returns else 0

    # Confidence calibration table
    calibration = []
    for bucket in sorted(confidence_buckets.keys()):
        data = confidence_buckets[bucket]
        actual_wr = data["wins"] / data["total"] if data["total"] > 0 else 0
        avg_conf = data["sum_conf"] / data["total"] if data["total"] > 0 else 0
        calibration.append({
            "bucket": bucket,
            "avg_confidence": round(avg_conf, 3),
            "actual_win_rate": round(actual_wr, 3),
            "trades": data["total"],
            "gap": round(avg_conf - actual_wr, 3),  # positive = overconfident
        })

    return WalkForwardResult(
        folds=folds,
        aggregate_train_return=round(avg_train, 2),
        aggregate_test_return=round(avg_test, 2),
        train_test_gap=round(avg_train - avg_test, 2),
        consistency_score=round(consistency, 2),
        confidence_calibration=calibration,
    )


def _confidence_bucket(confidence: float) -> str:
    """Bin confidence into 10% buckets for calibration analysis."""
    if confidence < 0.5:
        return "0.00-0.49"
    elif confidence < 0.6:
        return "0.50-0.59"
    elif confidence < 0.7:
        return "0.60-0.69"
    elif confidence < 0.8:
        return "0.70-0.79"
    elif confidence < 0.9:
        return "0.80-0.89"
    else:
        return "0.90-1.00"
