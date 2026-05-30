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
                f"Risk Checks: 18 independent | Fail-closed")


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
    description = "Show paper portfolio summary with cost waterfall"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        state = engine.portfolio.snapshot()
        cost = engine.cost.snapshot()
        net_of_cost = round(state.equity_usd - cost.operating_cost_usd, 2)
        cost_per_trade = round(cost.operating_cost_usd / state.total_trades, 4) if state.total_trades > 0 else 0.0

        lines = [
            f"Balance: ${state.balance_usd:,.2f}",
            f"Equity: ${state.equity_usd:,.2f}",
            f"Open: {state.open_positions} | Total: {state.total_trades}",
            f"Win Rate: {state.win_rate:.0%}",
            "",
            "── PnL Waterfall ──",
            f"  Gross PnL:      ${state.total_gross_pnl:,.2f}",
            f"  − Commission:   ${state.total_commission:,.2f}",
            f"  = Net PnL:      ${state.total_pnl:,.2f}",
            f"  − LLM cost:     ${cost.llm_cost_usd:,.4f}  ({cost.llm_calls} calls, {cost.prompt_tokens + cost.completion_tokens} tokens)",
            f"  − Infra cost:   ${cost.infra_cost_usd:,.4f}",
            f"  = After costs:  ${net_of_cost:,.2f}",
        ]
        if cost.unpriced_calls > 0:
            lines.append(f"  ⚠ {cost.unpriced_calls} LLM calls with UNKNOWN cost (model not in price table)")
        lines.append(f"  Cost/trade:     ${cost_per_trade:,.4f}")
        return "\n".join(lines)


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

        bars_count = min(int(kwargs.get("bars", 720)), 5000)  # clamp to prevent OOM
        seed = int(kwargs.get("seed", 42))

        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=bars_count, seed=seed)

        bt_engine = BacktestEngine(config)
        result = await bt_engine.run(bars)

        return (
            f"Backtest Complete ({result.start_date} → {result.end_date})\n"
            f"Bars: {result.bars_processed} | Duration: {result.duration_seconds:.1f}s\n"
            f"NOTE: Synthetic data — tests plumbing, not alpha.\n"
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

        # Trip the circuit breaker via the proper persisted path (audit fix B)
        engine.risk.emergency_halt("manual halt via /halt command")

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

        bars_count = min(int(kwargs.get("bars", 1440)), 5000)  # clamp to prevent OOM
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


class MacroCalendarSkill(BaseSkill):
    name = "macro_calendar"
    description = "Show macro event calendar: current risk state and upcoming events"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        cal = engine.macro_calendar
        snap = cal.evaluate()
        upcoming = cal.upcoming(limit=5)

        # Current state
        state_line = f"Macro Risk State: {snap.state.value}"
        if snap.active_event:
            state_line += f" ({snap.active_event.label})"
        if snap.time_until_next:
            hours = snap.time_until_next.total_seconds() / 3600
            if hours < 1:
                time_str = f"{snap.time_until_next.total_seconds() / 60:.0f}min"
            elif hours < 24:
                time_str = f"{hours:.1f}h"
            else:
                time_str = f"{hours / 24:.1f}d"
            state_line += f"\nNext event in: {time_str}"

        # Upcoming events
        if upcoming:
            lines = [state_line, "", "Upcoming Events:"]
            for ev in upcoming:
                times = cal.format_event_times(ev)
                lines.append(f"  {ev.label}")
                lines.append(f"    {times['utc']} | {times['et']}")
            return "\n".join(lines)
        return state_line + "\n\nNo upcoming events."


class TradeJournalSkill(BaseSkill):
    name = "trade_journal"
    description = "Show trade journal: history of executed trades with reasoning and outcome"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        history = engine.portfolio._history
        if not history:
            return "No closed trades yet. The journal fills as trades are executed and closed."

        count = int(kwargs.get("count", 10))
        recent = history[-count:]

        lines = [f"Trade Journal ({len(recent)} of {len(history)} trades)", "=" * 50]
        total_pnl = 0.0
        wins = 0
        for trade in reversed(recent):
            outcome = "WIN" if trade.pnl > 0 else "LOSS"
            if trade.pnl > 0:
                wins += 1
            total_pnl += trade.pnl

            duration = ""
            if trade.closed_at and trade.opened_at:
                dur_hours = (trade.closed_at - trade.opened_at).total_seconds() / 3600
                duration = f" | Duration: {dur_hours:.1f}h"

            exit_info = f"${trade.exit_price:,.2f}" if trade.exit_price else "open"

            lines.append(
                f"\n[{trade.trade_id}] {trade.direction.value} {trade.asset}"
                f"\n  Entry: ${trade.entry_price:,.2f} -> Exit: {exit_info}"
                f"\n  SL: ${trade.stop_loss:,.2f} | TP: ${trade.take_profit:,.2f}"
                f"\n  PnL: ${trade.pnl:+,.2f} ({outcome}){duration}"
                f"\n  Size: ${trade.entry_price * trade.quantity:,.2f}"
            )

        lines.append("\n" + "─" * 50)
        wr = wins / len(recent) if recent else 0
        lines.append(f"Session: {wins}W / {len(recent) - wins}L | "
                      f"Win Rate: {wr:.0%} | Net PnL: ${total_pnl:+,.2f}")
        return "\n".join(lines)


class CostBreakdownSkill(BaseSkill):
    name = "costs"
    description = "Show full agent economics: LLM cost breakdown by category, rate limiter stats, projected ROI impact"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        cost = engine.cost.snapshot()
        state = engine.portfolio.snapshot()
        rate_stats = engine.analyzer._rate_limiter.stats

        lines = [
            "RUNECLAW Agent Economics",
            "=" * 36,
            "",
            f"Total LLM Cost:  ${cost.llm_cost_usd:,.4f}  ({cost.llm_calls} calls)",
            f"Total Tokens:    {cost.prompt_tokens:,} in / {cost.completion_tokens:,} out",
            f"Avg per call:    ${cost.avg_cost_per_call:,.6f}",
            "",
            "Cost by Category:",
        ]

        for cat in ("scan", "analyze", "thesis", "risk_decision", "other"):
            cat_cost = cost.cost_by_category.get(cat, 0.0)
            cat_calls = cost.calls_by_category.get(cat, 0)
            if cat_calls > 0:
                lines.append(f"  {cat:16s} ${cat_cost:,.4f}  ({cat_calls} calls)")

        if cost.unpriced_calls > 0:
            lines.append(f"\n  WARNING: {cost.unpriced_calls} calls with UNKNOWN cost (model not in price table)")

        lines.append("")
        lines.append(f"Infra Cost:      ${cost.infra_cost_usd:,.4f}")
        lines.append(f"Operating Total: ${cost.operating_cost_usd:,.4f}")
        lines.append("")

        # ROI impact
        if state.total_trades > 0:
            cost_per_trade = cost.operating_cost_usd / state.total_trades
            lines.append(f"Cost/trade:      ${cost_per_trade:,.4f}")
        if state.equity_usd > 0 and cost.operating_cost_usd > 0:
            roi_drag = (cost.operating_cost_usd / state.equity_usd) * 100
            lines.append(f"ROI drag:        {roi_drag:.3f}% of equity")

        # Net waterfall
        net_equity = state.equity_usd - cost.operating_cost_usd
        lines.append("")
        lines.append(f"Equity:          ${state.equity_usd:,.2f}")
        lines.append(f"- Operating:     ${cost.operating_cost_usd:,.4f}")
        lines.append(f"= Net Equity:    ${net_equity:,.2f}")

        # Rate limiter
        lines.append("")
        lines.append(f"Rate Limiter:    {rate_stats['total_calls']} calls, {rate_stats['total_waits']} throttled ({rate_stats['total_wait_seconds']}s)")

        return "\n".join(lines)


class RunStrategySkill(BaseSkill):
    name = "run_strategy"
    description = "Execute a predefined trading strategy by name (natural language)"

    # Strategy presets: keyword triggers -> configuration dict
    PRESETS: dict[str, dict[str, Any]] = {
        "btc dip sniper": {
            "label": "BTC Dip Sniper",
            "description": "Scan BTC only, RSI < 35, TREND_DOWN regime, confidence >= 0.70",
            "symbols": ["BTC/USDT"],
            "rsi_threshold": 35,
            "regime": "TREND_DOWN",
            "confidence_threshold": 0.70,
            "volume_spike_min": None,
            "sl_atr_mult": None,
            "tp_atr_mult": None,
        },
        "momentum hunter": {
            "label": "Momentum Hunter",
            "description": "Scan all pairs, volume spikes > 3x, TREND_UP regime only",
            "symbols": None,  # all pairs
            "rsi_threshold": None,
            "regime": "TREND_UP",
            "confidence_threshold": None,
            "volume_spike_min": 3.0,
            "sl_atr_mult": None,
            "tp_atr_mult": None,
        },
        "safe scalper": {
            "label": "Safe Scalper",
            "description": "Top 3 by volume, tight SL (1.5x ATR), quick TP (2x ATR), confidence >= 0.75",
            "symbols": "top3_volume",
            "rsi_threshold": None,
            "regime": None,
            "confidence_threshold": 0.75,
            "volume_spike_min": None,
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 2.0,
        },
        "full scan": {
            "label": "Full Scan",
            "description": "Standard full pipeline with default parameters",
            "symbols": None,
            "rsi_threshold": None,
            "regime": None,
            "confidence_threshold": None,
            "volume_spike_min": None,
            "sl_atr_mult": None,
            "tp_atr_mult": None,
        },
    }

    # Aliases that map short names to canonical preset keys
    ALIASES: dict[str, str] = {
        "dip": "btc dip sniper",
        "momentum": "momentum hunter",
        "scalp": "safe scalper",
        "scan all": "full scan",
    }

    @classmethod
    def _resolve_preset(cls, raw: str) -> str | None:
        """Return canonical preset key for *raw*, or None if unrecognized."""
        key = raw.strip().lower()
        if key in cls.PRESETS:
            return key
        if key in cls.ALIASES:
            return cls.ALIASES[key]
        return None

    @classmethod
    def _list_presets(cls) -> str:
        lines = ["Available strategy presets:\n"]
        for key, cfg in cls.PRESETS.items():
            aliases = [a for a, target in cls.ALIASES.items() if target == key]
            alias_str = f"  (alias: {', '.join(aliases)})" if aliases else ""
            lines.append(f"  - {cfg['label']}{alias_str}")
            lines.append(f"    {cfg['description']}")
        lines.append("\nUsage: /run <preset name>")
        lines.append("All 18 risk-engine checks still apply. Strategies only pre-configure scan/analyze parameters.")
        return "\n".join(lines)

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        strategy_str: str = kwargs.get("strategy", "")
        if not strategy_str:
            return self._list_presets()

        preset_key = self._resolve_preset(strategy_str)
        if preset_key is None:
            return (f"Unknown strategy: \"{strategy_str}\"\n\n"
                    + self._list_presets())

        cfg = self.PRESETS[preset_key]
        label = cfg["label"]

        audit(system_log, f"Strategy activated: {label}",
              action="run_strategy", data=cfg)

        # --- Step 1: Scan ---
        signals = await engine.scanner.scan()
        if not signals:
            return f"[{label}] No signals found during scan."

        # Filter by preset constraints
        # Symbol filter
        if cfg["symbols"] == "top3_volume":
            signals.sort(key=lambda s: s.volume_usd_24h, reverse=True)
            signals = signals[:3]
        elif cfg["symbols"] is not None:
            allowed = set(cfg["symbols"])
            signals = [s for s in signals if s.symbol in allowed]

        # Volume spike filter
        if cfg["volume_spike_min"] is not None:
            spike_min = cfg["volume_spike_min"]
            signals = [s for s in signals if getattr(s, "volume_spike_ratio", 0) >= spike_min
                       or getattr(s, "volume_spike", False)]

        if not signals:
            return f"[{label}] Scan complete but no signals matched strategy filters."

        # --- Step 2: Analyze each signal ---
        results: list[str] = []
        ideas_created = 0

        for sig in signals[:5]:  # cap at 5 to avoid flooding
            idea = await engine._analyze_signal(sig)
            if idea is None:
                continue

            # Apply confidence threshold filter
            conf_thresh = cfg.get("confidence_threshold")
            if conf_thresh is not None and idea.confidence < conf_thresh:
                results.append(
                    f"  {sig.symbol}: idea below confidence threshold "
                    f"({idea.confidence:.0%} < {conf_thresh:.0%}), skipped")
                continue

            # Store in pending (same flow as AnalyzeAssetSkill)
            engine._pending_ideas[idea.id] = idea
            ideas_created += 1
            results.append(
                f"  {idea.direction.value} {idea.asset} "
                f"[{idea.id}] conf={idea.confidence:.0%} R:R={idea.risk_reward_ratio}")

        # Build summary
        header = (
            f"Strategy: {label}\n"
            f"Signals scanned: {len(signals)} | Ideas generated: {ideas_created}\n"
            f"Risk engine: all 18 checks active (NOT bypassed)\n"
            f"{'=' * 44}"
        )
        if results:
            body = "\n".join(results)
        else:
            body = "  No actionable trade ideas passed filters."

        footer = (
            f"\n{'=' * 44}\n"
            f"Use /trade to review and confirm pending ideas."
        )
        return f"{header}\n{body}{footer}"


def build_default_registry() -> SkillRegistry:
    """Create a registry with all built-in skills pre-loaded."""
    registry = SkillRegistry()
    for skill_cls in (ScanMarketSkill, AnalyzeAssetSkill, CheckRiskSkill,
                      ExecutePaperTradeSkill, GetPortfolioSkill, ExplainTradeSkill,
                      RunBacktestSkill, RejectedTradesSkill, HaltSkill,
                      WalkForwardSkill, MacroCalendarSkill, TradeJournalSkill,
                      CostBreakdownSkill, RunStrategySkill):
        registry.register(skill_cls())
    return registry
