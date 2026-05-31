"""
RUNECLAW Trading Engine -- the central orchestrator.
FSM States: IDLE -> SCANNING -> ANALYZING -> RISK_CHECK -> CONFIRMING -> EXECUTING -> MONITORING
Fail-closed: any unhandled error aborts the trade pipeline.
Human confirmation is REQUIRED before execution.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, UTC
from typing import Callable, Optional

from pathlib import Path

from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.core.cost import CostTracker
from bot.core.macro_events import MacroEventProvider
from bot.core.market_scanner import MarketScanner
from bot.core.order_flow import OrderFlowAnalyzer
from bot.compliance.compliance_engine import ComplianceEngine, Permission, default_demo_profile
from bot.learning.orchestrator import LearningOrchestrator
from bot.macro.calendar import MacroCalendar, build_2026_calendar
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.audit_chain import AuditChain, DecisionRecord
from bot.utils.logger import audit, system_log, trade_log
from bot.utils.models import (
    AgentState,
    MarketSignal,
    RiskVerdict,
    StateTransition,
    TradeIdea,
)


class RuneClawEngine:
    """
    Main event loop that ties scanner, analyzer, risk, and execution together.
    Uses a formal FSM via AgentState for every lifecycle transition.
    The engine never executes a trade without explicit human confirmation.
    """

    def __init__(self) -> None:
        self.portfolio = PortfolioTracker()
        self.scanner = MarketScanner()
        self.cost = CostTracker()
        self.analyzer = Analyzer(cost_tracker=self.cost)
        self.order_flow = OrderFlowAnalyzer()
        self.macro_calendar = MacroCalendar(events=build_2026_calendar())
        self.macro_provider = MacroEventProvider(
            seed_path=Path("config/macro_calendar.seed.json"),
        )
        self.risk = RiskEngine(
            self.portfolio,
            macro_calendar=self.macro_calendar,
            macro_provider=self.macro_provider,
        )
        self.compliance = ComplianceEngine()
        self.compliance_profile = default_demo_profile()
        self.audit_chain = AuditChain("logs/audit_chain.jsonl")
        self.learning = LearningOrchestrator()
        # C1 fix: wire trade-close callback so portfolio closes feed risk streak tracking
        self.portfolio._on_trade_close = self.risk.record_trade_result
        self.state: AgentState = AgentState.IDLE
        self._state_history: list[StateTransition] = []
        self._running = False
        self._confirm_callback: Optional[Callable] = None
        self._pending_ideas: dict[str, TradeIdea] = {}
        self._pending_atr: dict[str, Optional[float]] = {}  # H1: store ATR for re-check
        self._cooldown_until: float = 0.0
        self._last_rebalance_check: float = 0.0  # monotonic timestamp
        self._rebalance_interval: float = 4 * 3600  # 4 hours minimum between checks

    # -- State management --

    def _transition(self, new_state: AgentState, reason: str = "") -> None:
        """Transition the FSM to a new state. Every transition is audit-logged."""
        old_state = self.state
        transition = StateTransition(
            from_state=old_state,
            to_state=new_state,
            reason=reason,
        )
        self._state_history.append(transition)
        # L7: cap state history to prevent unbounded growth
        if len(self._state_history) > 1000:
            self._state_history = self._state_history[-500:]
        self.state = new_state
        audit(
            system_log,
            f"State transition: {old_state.value} -> {new_state.value}"
            + (f" ({reason})" if reason else ""),
            action="state_transition",
            data={"from": old_state.value, "to": new_state.value, "reason": reason},
        )

    @property
    def state_history(self) -> list[StateTransition]:
        """Full history of state transitions."""
        return self._state_history

    def set_confirmation_callback(self, cb: Callable) -> None:
        """Register the human-confirmation gate (e.g. Telegram inline keyboard)."""
        self._confirm_callback = cb

    # -- Main loop --

    async def run(self) -> None:
        """Start the continuous scan-analyze-monitor loop."""
        self._running = True
        self._transition(AgentState.IDLE, "engine started")
        audit(
            system_log,
            "Engine started",
            action="start",
            data={"simulation": CONFIG.simulation_mode},
        )

        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                audit(
                    system_log,
                    f"Engine tick error: {exc}",
                    action="tick",
                    result="ERROR",
                )
            await asyncio.sleep(CONFIG.scan_interval_seconds)

    async def stop(self) -> None:
        self._running = False
        await self.scanner.close()
        self._transition(AgentState.IDLE, "engine stopped")
        audit(system_log, "Engine stopped", action="stop")

    # -- Pipeline stages --

    async def _tick(self) -> None:
        """One full scan-analyze cycle."""
        # Check circuit breaker — no new scans, but still monitor open positions
        # so SL/TP can fire even while halted (Fix 2: monitoring while halted).
        if self.risk.circuit_breaker_active:
            if self.state != AgentState.HALTED:
                self._transition(AgentState.HALTED, "circuit breaker active")
            await self._check_open_positions()
            return

        # Check cooldown
        if self._cooldown_until and time.monotonic() < self._cooldown_until:
            if self.state != AgentState.COOLING_DOWN:
                self._transition(AgentState.COOLING_DOWN, "post-loss cooldown active")
            return
        elif self._cooldown_until and time.monotonic() >= self._cooldown_until:
            self._cooldown_until = 0.0

        # TTL: expire stale pending ideas
        now = datetime.now(UTC)
        expired_ids = [
            idea_id
            for idea_id, idea in self._pending_ideas.items()
            if (now - idea.timestamp).total_seconds() > 300
        ]
        for idea_id in expired_ids:
            expired_idea = self._pending_ideas.pop(idea_id)
            self._pending_atr.pop(idea_id, None)  # clean up stored ATR
            audit(
                trade_log,
                f"Trade idea {idea_id} expired (TTL)",
                action="ttl_expire",
                result="EXPIRED",
                data={"asset": expired_idea.asset, "age_seconds": (now - expired_idea.timestamp).total_seconds()},
            )

        self._transition(AgentState.SCANNING, "beginning scan cycle")
        signals = await self.scanner.scan()
        if not signals:
            self._transition(AgentState.IDLE, "no signals found")
            return

        self._transition(AgentState.ANALYZING, "signals detected")
        for signal in signals[:3]:  # Top 3 movers
            idea = await self._analyze_signal(signal)
            if idea:
                self._pending_ideas[idea.id] = idea

        self._transition(AgentState.MONITORING, "checking open positions")
        await self._check_open_positions()
        self._transition(AgentState.IDLE, "tick cycle complete")

    async def _analyze_signal(self, signal: MarketSignal) -> Optional[TradeIdea]:
        """Run full analysis pipeline on a single signal."""
        try:
            exchange = await self.scanner._get_exchange()
            ohlcv = await exchange.fetch_ohlcv(signal.symbol, "1h", limit=100)
        except Exception as exc:
            audit(
                system_log,
                f"OHLCV fetch failed: {exc}",
                action="fetch_candles",
                result="ERROR",
            )
            return None

        # Order flow analysis (fail-closed: returns neutral on any error)
        of_signal = None
        try:
            of_signal = await self.order_flow.analyze(exchange, signal.symbol)
        except Exception as exc:
            audit(system_log, f"Order flow analysis failed: {exc}",
                  action="order_flow", result="ERROR")

        idea = await self.analyzer.analyze(signal, ohlcv, order_flow=of_signal)
        if idea is None:
            return None

        # Compute ATR from candles for the volatility guard (check #16)
        atr_value = None
        if len(ohlcv) >= 15:
            true_ranges = []
            for j in range(1, min(15, len(ohlcv))):
                h = float(ohlcv[-j][2])
                l = float(ohlcv[-j][3])
                pc = float(ohlcv[-j - 1][4])
                tr = max(h - l, abs(h - pc), abs(l - pc))
                true_ranges.append(tr)
            atr_value = sum(true_ranges) / len(true_ranges)

        # Risk gate — pass ATR so all 18 checks run
        self._transition(AgentState.RISK_CHECK, f"evaluating {signal.symbol}")
        risk_check = self.risk.evaluate(idea, atr=atr_value)

        # Check #17: liquidity guard from order flow (fail-open if no data)
        if of_signal is not None:
            liq_reason = self.order_flow.liquidity_guard(of_signal)
            if liq_reason:
                audit(trade_log, f"Trade REJECTED by liquidity guard: {liq_reason}",
                      action="liquidity_guard", result="REJECTED")
                self._transition(AgentState.ANALYZING, "liquidity rejected, continuing")
                return None

        if risk_check.verdict == RiskVerdict.REJECTED:
            audit(
                trade_log,
                f"Trade REJECTED by risk: {risk_check.reason}",
                action="risk_gate",
                result="REJECTED",
            )
            # Learning: log rejected trade decision
            decision = self.learning.log_decision(
                symbol=signal.symbol,
                direction=idea.direction.value,
                confidence=idea.confidence,
                confluence_score=idea.confidence,
                entry_price=idea.entry_price,
                stop_loss=idea.stop_loss,
                take_profit=idea.take_profit,
                risk_reward=idea.risk_reward_ratio,
                position_size_usd=risk_check.position_size_usd,
                risk_engine_result="REJECTED",
                checks_passed=risk_check.checks_passed,
                checks_failed=risk_check.checks_failed,
                rejected_reason=risk_check.reason,
                decision="TRADE_REJECTED_FAIL_CLOSED",
            )
            self.learning.review_rejection(decision)
            self._transition(AgentState.ANALYZING, "risk rejected, continuing analysis")
            return None

        self._transition(AgentState.CONFIRMING, f"awaiting human confirmation for {idea.id}")
        audit(
            trade_log,
            f"Trade idea awaiting human confirmation: {idea.id}",
            action="confirmation_gate",
            result="PENDING",
        )
        # H1: store ATR alongside idea for re-check in confirm_trade
        self._pending_atr[idea.id] = atr_value
        return idea

    async def confirm_trade(self, trade_id: str) -> str:
        """
        Human confirms a pending trade idea.  This is the ONLY path to execution.
        """
        idea = self._pending_ideas.pop(trade_id, None)
        if idea is None:
            return f"Trade {trade_id} not found or expired."

        # H1 fix: re-check with stored ATR so volatility guard runs
        stored_atr = self._pending_atr.pop(trade_id, None)

        # Re-check risk (portfolio state may have changed -- new positions, daily PnL, drawdown.
        # HONEST LIMITATION: this does NOT re-fetch market price or update the idea's
        # entry/SL/TP.  Stale-data check #12 guards against time drift (>300s = reject),
        # but not price drift within that window.  Price drift is bounded to ≤5 min by TTL.
        # To close this gap fully, re-fetch the ticker here and reject if entry has drifted
        # beyond a threshold (e.g. 1 ATR).  Not implemented in this prototype.)
        self._transition(AgentState.RISK_CHECK, f"re-checking risk for {trade_id}")
        try:
            recheck = self.risk.evaluate(idea, atr=stored_atr)
        except Exception as exc:
            # Fix 6: if re-check raises, do NOT silently lose the idea.
            # Log it as a failed re-check and return a clear message.
            audit(
                trade_log,
                f"Risk re-check crashed for {trade_id}: {exc}",
                action="recheck",
                result="ERROR",
                data={"trade_id": trade_id, "asset": idea.asset, "error": str(exc)},
            )
            self._transition(AgentState.IDLE, f"re-check error for {trade_id}")
            return f"Trade {trade_id} re-check failed (error logged): {exc}"
        if recheck.verdict == RiskVerdict.REJECTED:
            self._transition(AgentState.IDLE, f"re-check rejected {trade_id}")
            # Seal rejection to audit chain
            self.audit_chain.seal_decision(DecisionRecord(
                decision_id=trade_id, symbol=idea.asset,
                idea={"direction": idea.direction.value, "confidence": idea.confidence},
                risk={"verdict": "REJECTED", "reason": recheck.reason},
                outcome="REJECTED_ON_RECHECK", is_paper=not CONFIG.is_live(),
            ))
            return f"Trade REJECTED on re-check: {recheck.reason}"

        # Compliance gate: authorize before execution
        action = Permission.LIVE_TRADE if CONFIG.is_live() else Permission.PAPER_TRADE
        macro_ctx = self.macro_provider.get_context(symbol=idea.asset)
        macro_ok = macro_ctx.risk_state != "BLOCK_NEW_ENTRIES"
        compliance_decision = self.compliance.authorize(
            action=action,
            profile=self.compliance_profile,
            live_mode=CONFIG.is_live(),
            risk_passed=(recheck.verdict == RiskVerdict.APPROVED),
            macro_ok=macro_ok,
            notional_usd=recheck.position_size_usd,
            trade_id=trade_id,
        )
        if not compliance_decision.granted:
            self.audit_chain.append("AUTH_DENIED", {
                "trade_id": trade_id, "asset": idea.asset,
                "reasons": compliance_decision.reasons,
                "locks_failed": compliance_decision.locks_failed,
            }, actor=self.compliance_profile.subject_id)
            self._transition(AgentState.IDLE, f"compliance denied {trade_id}")
            return f"Execution denied: {compliance_decision.reasons[-1] if compliance_decision.reasons else 'compliance check failed'}"

        # H2 fix: guard is_live() — only proceed to live execution when is_live() is True
        if not CONFIG.is_live():
            # Not live mode — execute as paper trade
            pass  # fall through to paper trade below
        else:
            # Live mode enabled — block until live execution is implemented
            self._transition(AgentState.IDLE, "live execution not implemented")
            return "LIVE EXECUTION NOT YET IMPLEMENTED. Use simulation mode."

        # Paper trade execution
        self._transition(AgentState.EXECUTING, f"executing paper trade {trade_id}")
        size_usd = recheck.position_size_usd
        try:
            trade = self.portfolio.open_position(idea, size_usd)
        except (ValueError, Exception) as exc:
            # F-04 fix: never silently lose a confirmed trade.
            # Log the failure as an audited rejection and return a clear message.
            audit(
                trade_log,
                f"Confirmed trade failed to execute: {exc}",
                action="execute",
                result="FAILED",
                data={"trade_id": trade_id, "size_usd": size_usd, "error": str(exc)},
            )
            self._transition(AgentState.IDLE, f"execution failed for {trade_id}")
            return f"Trade {trade_id} confirmed but execution failed: {exc}"

        audit(
            trade_log,
            f"Paper trade executed: {trade.trade_id}",
            action="execute",
            result="EXECUTED",
            data={"asset": trade.asset, "size": size_usd},
        )
        # Seal decision to tamper-evident audit chain
        self.audit_chain.seal_decision(DecisionRecord(
            decision_id=trade_id, symbol=idea.asset,
            idea={"direction": idea.direction.value, "confidence": idea.confidence,
                  "entry": idea.entry_price, "sl": idea.stop_loss, "tp": idea.take_profit},
            risk={"verdict": "APPROVED", "passed": len(recheck.checks_passed),
                  "failed": len(recheck.checks_failed), "size_usd": size_usd},
            macro={"risk_state": macro_ctx.risk_state, "multiplier": macro_ctx.size_multiplier},
            compliance={"granted": True, "locks_passed": compliance_decision.locks_passed},
            outcome="EXECUTED_PAPER", is_paper=True,
        ))
        # Learning: log accepted trade decision
        self.learning.log_decision(
            symbol=idea.asset,
            direction=idea.direction.value,
            confidence=idea.confidence,
            confluence_score=idea.confidence,
            entry_price=idea.entry_price,
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            risk_reward=idea.risk_reward_ratio,
            position_size_usd=size_usd,
            risk_engine_result="APPROVED",
            checks_passed=recheck.checks_passed,
            checks_failed=[],
            decision="TRADE_ACCEPTED_PAPER",
            paper_trade_id=trade.trade_id,
        )
        self._transition(AgentState.IDLE, "trade executed")
        return f"Executed paper {trade.direction.value} {trade.asset} (${size_usd:.2f})"

    def reject_trade(self, trade_id: str) -> str:
        """Human explicitly rejects a pending idea."""
        idea = self._pending_ideas.pop(trade_id, None)
        self._pending_atr.pop(trade_id, None)  # clean up stored ATR
        if idea:
            audit(
                trade_log,
                f"Trade manually rejected: {trade_id}",
                action="human_reject",
                result="REJECTED",
            )
            return f"Trade {trade_id} rejected."
        return f"Trade {trade_id} not found."

    async def _check_open_positions(self) -> None:
        """Monitor open positions for SL/TP hits."""
        positions = self.portfolio.open_positions
        if not positions:
            return
        try:
            exchange = await self.scanner._get_exchange()
            tickers = await exchange.fetch_tickers()
            prices = {s: float(t.get("last", 0)) for s, t in tickers.items()}
            # Mark-to-market: feed current prices so snapshot() reflects unrealized PnL
            self.portfolio.mark_to_market(prices)
            closed = self.portfolio.check_stops(prices)
            for c in closed:
                audit(
                    trade_log,
                    f"Position auto-closed: {c.asset} PnL=${c.pnl}",
                    action="auto_close",
                    result="CLOSED",
                )
                # Enter cooldown after a loss
                if c.pnl <= 0:
                    self._cooldown_until = (
                        time.monotonic() + CONFIG.cooldown_after_loss_seconds
                    )
                    self._transition(
                        AgentState.COOLING_DOWN,
                        f"loss on {c.asset} (PnL=${c.pnl}), "
                        f"cooling down {CONFIG.cooldown_after_loss_seconds}s",
                    )
        except Exception as exc:
            audit(
                system_log,
                f"Position monitor error: {exc}",
                action="monitor",
                result="ERROR",
            )

    @property
    def pending_ideas(self) -> list[TradeIdea]:
        return list(self._pending_ideas.values())

    # -- Portfolio Heat / Auto-Rebalance --

    def check_portfolio_heat(self) -> dict:
        """Compute portfolio exposure and determine if rebalancing is needed.

        Returns dict with:
          - total_exposure_pct: sum of position values / equity
          - max_single_exposure_pct: largest single position / equity
          - needs_rebalance: True if total > 60% or single > 30%
          - rebalance_actions: list of suggested reduction actions
        """
        snap = self.portfolio.snapshot()
        equity = snap.equity_usd
        if equity <= 0:
            return {
                "total_exposure_pct": 0.0,
                "max_single_exposure_pct": 0.0,
                "needs_rebalance": False,
                "rebalance_actions": [],
            }

        positions = self.portfolio.open_positions
        if not positions:
            return {
                "total_exposure_pct": 0.0,
                "max_single_exposure_pct": 0.0,
                "needs_rebalance": False,
                "rebalance_actions": [],
            }

        # Compute per-position exposure
        exposures: dict[str, float] = {}
        total_exposure = 0.0
        for pos in positions:
            pos_value = self.portfolio.get_position_value(pos.asset)
            exposure_pct = (pos_value / equity) * 100
            exposures[pos.asset] = exposure_pct
            total_exposure += exposure_pct

        max_single = max(exposures.values()) if exposures else 0.0

        # Determine rebalance need
        needs_rebalance = total_exposure > 60.0 or max_single > 30.0

        # Generate suggested actions
        actions: list[str] = []
        if needs_rebalance:
            for asset, exp_pct in sorted(exposures.items(), key=lambda x: -x[1]):
                if exp_pct > 30.0:
                    # Suggest reducing to 25%
                    reduce_pct = round((1 - 25.0 / exp_pct) * 100)
                    actions.append(f"Reduce {asset} by {reduce_pct}% (currently {exp_pct:.1f}% of equity)")
                elif total_exposure > 60.0 and exp_pct > 15.0:
                    # Suggest reducing larger positions proportionally
                    target = exp_pct * (55.0 / total_exposure)
                    reduce_pct = round((1 - target / exp_pct) * 100)
                    if reduce_pct > 5:
                        actions.append(f"Reduce {asset} by {reduce_pct}% (currently {exp_pct:.1f}% of equity)")

        return {
            "total_exposure_pct": round(total_exposure, 2),
            "max_single_exposure_pct": round(max_single, 2),
            "needs_rebalance": needs_rebalance,
            "rebalance_actions": actions,
        }

    def get_rebalance_signals(self) -> list[dict]:
        """Return rebalance signals for the War Room display.

        Respects a minimum 4-hour interval between checks to avoid
        excessive computation. Returns empty list if checked too recently.
        """
        now = time.monotonic()
        if self._last_rebalance_check and (now - self._last_rebalance_check) < self._rebalance_interval:
            return []

        self._last_rebalance_check = now
        heat = self.check_portfolio_heat()

        if not heat["needs_rebalance"]:
            return []

        signals = []
        for action in heat["rebalance_actions"]:
            signals.append({
                "type": "REBALANCE",
                "action": action,
                "total_exposure_pct": heat["total_exposure_pct"],
                "max_single_exposure_pct": heat["max_single_exposure_pct"],
                "timestamp": datetime.now(UTC).isoformat(),
            })

        if signals:
            audit(
                system_log,
                f"Rebalance needed: total={heat['total_exposure_pct']:.1f}%, "
                f"max_single={heat['max_single_exposure_pct']:.1f}%",
                action="rebalance_check",
                result="REBALANCE_NEEDED",
                data=heat,
            )

        return signals
