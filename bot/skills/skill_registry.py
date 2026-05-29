"""
RUNECLAW Skill System -- modular, registerable capabilities.
Each skill is a self-contained unit that the engine or Telegram bot can invoke.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from bot.core.engine import RuneClawEngine
from bot.utils.logger import audit, system_log


class BaseSkill(ABC):
    """Interface every skill must implement."""

    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        """Run the skill and return a human-readable result string."""


class SkillRegistry:
    """Central registry for discovering and invoking skills."""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        audit(system_log, f"Skill registered: {skill.name}", action="register")

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return [f"{s.name} -- {s.description}" for s in self._skills.values()]


# -- Built-in skills --

class ScanMarketSkill(BaseSkill):
    name = "scan_market"
    description = "Scan exchange for top movers and volume anomalies"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        signals = await engine.scanner.scan()
        if not signals:
            return "No significant signals detected."
        lines = [f"{s.symbol}: ${s.price:,.2f} ({s.change_pct_24h:+.1f}%) "
                 f"{'SPIKE' if s.volume_spike else ''}" for s in signals[:5]]
        return "Top movers:\n" + "\n".join(lines)


class AnalyzeAssetSkill(BaseSkill):
    name = "analyze_asset"
    description = "Run AI analysis on a specific asset"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        symbol = kwargs.get("symbol", "BTC/USDT")
        from bot.utils.models import MarketSignal
        from datetime import UTC, datetime
        # Create a minimal signal for the analyzer
        sig = MarketSignal(symbol=symbol, price=0, change_pct_24h=0,
                           volume_usd_24h=0, timestamp=datetime.now(UTC))
        try:
            exchange = await engine.scanner._get_exchange()
            ticker = await exchange.fetch_ticker(symbol)
            sig = MarketSignal(
                symbol=symbol, price=float(ticker.get("last", 0)),
                change_pct_24h=float(ticker.get("percentage", 0) or 0),
                volume_usd_24h=float(ticker.get("quoteVolume", 0) or 0),
                timestamp=datetime.now(UTC),
            )
        except Exception:
            return f"Could not fetch data for {symbol}"

        idea = await engine._analyze_signal(sig)
        if idea is None:
            return f"No actionable trade idea for {symbol}."
        # C2 fix: store the idea in pending so it can be confirmed via trade_id
        engine._pending_ideas[idea.id] = idea
        return (f"Trade Idea [{idea.id}]\n"
                f"{idea.direction.value} {idea.asset}\n"
                f"Entry: ${idea.entry_price:,.2f}\n"
                f"SL: ${idea.stop_loss:,.2f} | TP: ${idea.take_profit:,.2f}\n"
                f"Confidence: {idea.confidence:.0%}\n"
                f"R:R = {idea.risk_reward_ratio}\n"
                f"Reasoning: {idea.reasoning}")


class CheckRiskSkill(BaseSkill):
    name = "check_risk"
    description = "Show current risk metrics and circuit breaker status"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        state = engine.portfolio.snapshot()
        cb = "ACTIVE -- all trades blocked" if engine.risk.circuit_breaker_active else "OK"
        streak = engine.risk.consecutive_losses
        streak_warn = f" (WARNING: {streak} consecutive)" if streak >= 3 else ""

        # Open position groups
        from bot.risk.risk_engine import _CORRELATION_GROUPS
        groups: dict[str, int] = {}
        for pos in engine.portfolio.open_positions:
            g = _CORRELATION_GROUPS.get(pos.asset, pos.asset)
            groups[g] = groups.get(g, 0) + 1
        group_str = ", ".join(f"{g}={c}" for g, c in groups.items()) if groups else "none"

        return (f"Equity: ${state.equity_usd:,.2f}\n"
                f"Daily PnL: ${state.daily_pnl:,.2f}\n"
                f"Drawdown: {state.max_drawdown_pct:.1f}%\n"
                f"Circuit Breaker: {cb}\n"
                f"Loss Streak: {streak}{streak_warn}\n"
                f"Open Positions: {state.open_positions} | Groups: {group_str}\n"
                f"Risk Checks: 16 independent | Fail-closed")


class ExecutePaperTradeSkill(BaseSkill):
    name = "execute_paper_trade"
    description = "Confirm and execute a pending paper trade"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        # C2 fix: accept both 'trade_id' and 'symbol' (CLI sends positional as symbol)
        trade_id = kwargs.get("trade_id") or kwargs.get("symbol", "")
        if not trade_id:
            return "Provide a trade_id to confirm."
        return await engine.confirm_trade(trade_id)


class GetPortfolioSkill(BaseSkill):
    name = "get_portfolio"
    description = "Show paper portfolio summary"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        state = engine.portfolio.snapshot()
        return (f"Balance: ${state.balance_usd:,.2f}\n"
                f"Equity: ${state.equity_usd:,.2f}\n"
                f"Open: {state.open_positions} | Total: {state.total_trades}\n"
                f"Win Rate: {state.win_rate:.0%}\n"
                f"Total PnL: ${state.total_pnl:,.2f}")


class ExplainTradeSkill(BaseSkill):
    name = "explain_trade"
    description = "Explain a pending or historical trade idea"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        trade_id = kwargs.get("trade_id", "")
        for idea in engine.pending_ideas:
            if idea.id == trade_id:
                return (f"[{idea.id}] {idea.direction.value} {idea.asset}\n"
                        f"Confidence: {idea.confidence:.0%}\n"
                        f"Reasoning: {idea.reasoning}\n"
                        f"Signals: {', '.join(idea.signals_used)}")
        return f"Trade {trade_id} not found in pending ideas."


class RunBacktestSkill(BaseSkill):
    name = "run_backtest"
    description = "Run a backtest with synthetic data (bars=N, seed=N)"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.engine import BacktestEngine
        from bot.backtest.models import BacktestConfig

        bars_count = int(kwargs.get("bars", 720))
        seed = int(kwargs.get("seed", 42))

        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=bars_count, seed=seed)

        bt_engine = BacktestEngine(config)
        result = await bt_engine.run(bars)

        return (
            f"Backtest Complete ({result.start_date} → {result.end_date})\n"
            f"Bars: {result.bars_processed} | Duration: {result.duration_seconds:.1f}s\n"
            f"─────────────────────────────────────\n"
            f"Final Equity:   ${result.final_equity:,.2f}\n"
            f"Total Return:   {result.total_return_pct:+.2f}%\n"
            f"Net PnL:        ${result.net_pnl:,.2f}\n"
            f"Commission:     ${result.total_commission:,.2f}\n"
            f"Slippage:       ${result.total_slippage:,.2f}\n"
            f"─────────────────────────────────────\n"
            f"Trades: {result.total_trades} | "
            f"Win Rate: {result.win_rate:.0%} | "
            f"Profit Factor: {result.profit_factor:.2f}\n"
            f"Max Drawdown:   {result.max_drawdown_pct:.2f}%\n"
            f"Sharpe:         {result.sharpe_ratio:.2f} | "
            f"Sortino: {result.sortino_ratio:.2f}\n"
            f"─────────────────────────────────────\n"
            f"Signals: {result.total_signals_generated} → "
            f"Ideas: {result.total_ideas_generated} → "
            f"Rejected: {result.total_ideas_rejected_risk} (risk) "
            f"{result.total_ideas_rejected_confidence} (conf)"
        )


class RejectedTradesSkill(BaseSkill):
    name = "rejected_trades"
    description = "Show recent risk-rejected trade ideas with failure reasons"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        history = engine.risk.rejection_history
        if not history:
            return "No rejected trades recorded yet."
        # Show the most recent N (default 5)
        count = int(kwargs.get("count", 5))
        recent = history[-count:]
        lines = []
        for r in reversed(recent):
            fails = ", ".join(r["checks_failed"][:3])
            if len(r["checks_failed"]) > 3:
                fails += f" (+{len(r['checks_failed']) - 3} more)"
            lines.append(
                f"[{r['trade_id']}] {r['direction']} {r['asset']} "
                f"(conf: {r['confidence']:.0%})\n"
                f"  Failed: {fails}"
            )
        header = f"Recent Rejections ({len(recent)} of {len(history)} total):"
        return header + "\n\n" + "\n\n".join(lines)


class HaltSkill(BaseSkill):
    name = "halt"
    description = "Emergency kill-switch: trip circuit breaker and cancel all pending ideas"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        from bot.utils.models import AgentState

        # Trip the circuit breaker
        engine.risk._circuit_open = True
        engine.risk._circuit_breaker_trips += 1

        # Cancel all pending ideas
        cancelled = list(engine._pending_ideas.keys())
        engine._pending_ideas.clear()
        engine._pending_atr.clear()

        # Transition to HALTED
        engine._transition(AgentState.HALTED, "manual halt via /halt command")

        audit(system_log, f"MANUAL HALT: circuit breaker tripped, {len(cancelled)} ideas cancelled",
              action="halt", result="HALTED",
              data={"cancelled_ids": cancelled})

        return (
            f"HALTED -- Emergency stop activated.\n"
            f"Circuit breaker: TRIPPED\n"
            f"Pending ideas cancelled: {len(cancelled)}\n"
            f"All trading paused. Restart engine to resume."
        )


class WalkForwardSkill(BaseSkill):
    name = "walk_forward"
    description = "Run walk-forward backtest with train/test splits to detect overfitting"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.engine import walk_forward_backtest
        from bot.backtest.models import BacktestConfig

        bars_count = int(kwargs.get("bars", 1440))
        seed = int(kwargs.get("seed", 42))
        folds = int(kwargs.get("folds", 3))

        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=bars_count, seed=seed)

        result = await walk_forward_backtest(bars, config, n_folds=folds)

        lines = ["Walk-Forward Backtest Results", "=" * 40]
        for f in result.folds:
            lines.append(
                f"Fold {f['fold']}: Train {f['train_return_pct']:+.2f}% "
                f"({f['train_trades']} trades) | "
                f"Test {f['test_return_pct']:+.2f}% "
                f"({f['test_trades']} trades)"
            )
        lines.append("─" * 40)
        lines.append(f"Avg Train Return: {result.aggregate_train_return:+.2f}%")
        lines.append(f"Avg Test Return:  {result.aggregate_test_return:+.2f}%")
        lines.append(f"Train-Test Gap:   {result.train_test_gap:+.2f}% "
                      f"({'overfitting risk' if result.train_test_gap > 2 else 'acceptable'})")
        lines.append(f"Consistency:      {result.consistency_score:.0%} folds profitable")

        if result.confidence_calibration:
            lines.append("")
            lines.append("Confidence Calibration:")
            lines.append(f"{'Bucket':<12} {'Avg Conf':>8} {'Win Rate':>9} {'Trades':>7} {'Gap':>6}")
            for c in result.confidence_calibration:
                lines.append(
                    f"{c['bucket']:<12} {c['avg_confidence']:>8.1%} "
                    f"{c['actual_win_rate']:>9.1%} {c['trades']:>7} "
                    f"{c['gap']:>+6.1%}"
                )

        return "\n".join(lines)


def build_default_registry() -> SkillRegistry:
    """Create a registry with all built-in skills pre-loaded."""
    registry = SkillRegistry()
    for skill_cls in (ScanMarketSkill, AnalyzeAssetSkill, CheckRiskSkill,
                      ExecutePaperTradeSkill, GetPortfolioSkill, ExplainTradeSkill,
                      RunBacktestSkill, RejectedTradesSkill, HaltSkill,
                      WalkForwardSkill):
        registry.register(skill_cls())
    return registry
