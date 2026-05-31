"""
RUNECLAW Skill System v5 — polished dashboard cards.
Compact, mobile-friendly Telegram layouts with visual gauges,
sectioned cards, and consistent status vocabulary.
"""

from __future__ import annotations

import html as _html
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.utils.logger import audit, system_log


# ── Visual vocabulary ─────────────────────────────────────────
_OK = "\U0001f7e2"
_WARN = "\U0001f7e1"
_BAD = "\U0001f534"
_NEU = "\u26aa"

def _status(v: float) -> str:
    return _OK if v > 0 else _BAD if v < 0 else _NEU

def _spark(v: float) -> str:
    if v > 2: return "\u25b2"
    if v > 0: return "\u25b3"
    if v < -2: return "\u25bc"
    if v < 0: return "\u25bd"
    return "\u25c7"

def _bar(val: float, mx: float = 1.0, w: int = 10) -> str:
    r = min(max(val / mx, 0), 1.0) if mx > 0 else 0
    f = int(r * w)
    return "\u2588" * f + "\u2591" * (w - f)

def _gauge(label: str, val: float, mx: float, unit: str = "%", w: int = 10) -> str:
    """Labeled progress gauge: Drawdown  [████░░░░░░] 3.2% / 10%"""
    bar = _bar(val, mx, w)
    if unit == "%":
        return f"  {label:<10} [{bar}] {val:.1f}% / {mx:.0f}%"
    return f"  {label:<10} [{bar}] {val:.0f} / {mx:.0f}"

def _stars(v: float) -> str:
    if v >= 2.0: return "\u2605\u2605\u2605"
    if v >= 1.5: return "\u2605\u2605\u2606"
    return "\u2605\u2606\u2606"

def _esc(s: str) -> str:
    return _html.escape(str(s))

def _money(v: float, sign: bool = False) -> str:
    if sign:
        return f"${v:+,.2f}"
    return f"${v:,.2f}"

def _row(label: str, value: str, w: int = 28) -> str:
    """Right-aligned row inside <pre>: '  Label     $1,234.56'"""
    gap = w - len(label) - len(value) - 4
    if gap < 1: gap = 1
    return f"  {label}{' ' * gap}{value}"


class BaseSkill(ABC):
    name: str = "unnamed"
    description: str = ""
    @abstractmethod
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str: ...


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}
    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        audit(system_log, f"Skill registered: {skill.name}", action="register")
    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)
    def list_skills(self) -> list[str]:
        return [f"{s.name} -- {s.description}" for s in self._skills.values()]


# ══════════════════════════════════════════════════════════════
# SCAN
# ══════════════════════════════════════════════════════════════

class ScanMarketSkill(BaseSkill):
    name = "scan_market"
    description = "Scan exchange for top movers"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        signals = await engine.scanner.scan()
        if not signals:
            return f"{_NEU} <b>SCANNER</b>\n\n<i>No signals detected.</i>"

        top = signals[:8]
        lines = [f"\U0001f50d <b>MARKET SCANNER</b>  \u2022  {len(signals)} pairs\n"]

        for s in top:
            arrow = _spark(s.change_pct_24h)
            vol_m = s.volume_usd_24h / 1_000_000 if s.volume_usd_24h else 0
            chg = f"{s.change_pct_24h:+.1f}%"
            spike = " \U0001f4a5" if s.volume_spike else ""
            lines.append(
                f"  {arrow} <b>{_esc(s.symbol)}</b>  "
                f"<code>${s.price:,.2f}</code>  "
                f"<code>{chg:>7}</code>  "
                f"<code>${vol_m:,.0f}M</code>{spike}"
            )

        if any(s.volume_spike for s in top):
            lines.append(f"\n<i>\U0001f4a5 = volume spike detected</i>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# ANALYZE
# ══════════════════════════════════════════════════════════════

class AnalyzeAssetSkill(BaseSkill):
    name = "analyze_asset"
    description = "Run AI analysis on a specific asset"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        symbol = kwargs.get("symbol", "BTC/USDT")
        from bot.utils.models import MarketSignal

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
            return f"{_BAD} <b>ANALYSIS</b>\n\n<i>Could not fetch data for</i> <code>{_esc(symbol)}</code>"

        idea = await engine._analyze_signal(sig)
        if idea is None:
            vol_m = sig.volume_usd_24h / 1_000_000 if sig.volume_usd_24h else 0
            arrow = _spark(sig.change_pct_24h)
            return (
                f"{_NEU} <b>{_esc(symbol)}</b>  {arrow}\n\n"
                f"  Price     <code>${sig.price:,.2f}</code>\n"
                f"  24h       <code>{sig.change_pct_24h:+.1f}%</code>\n"
                f"  Volume    <code>${vol_m:,.0f}M</code>\n\n"
                f"<i>No actionable signal \u2014 regime filter or low confluence</i>"
            )

        engine._pending_ideas[idea.id] = idea

        d = idea.direction.value
        d_icon = _OK if d == "LONG" else _BAD
        rr = idea.risk_reward_ratio
        conf = idea.confidence
        sl_d = abs(idea.entry_price - idea.stop_loss)
        tp_d = abs(idea.take_profit - idea.entry_price)

        # Visual level diagram
        if d == "LONG":
            tp_label = f"\U0001f3af TP      ${idea.take_profit:>10,.2f}   +${tp_d:,.2f}"
            en_label = f"\u25b6\ufe0f ENTRY   ${idea.entry_price:>10,.2f}"
            sl_label = f"\U0001f6d1 SL      ${idea.stop_loss:>10,.2f}   -${sl_d:,.2f}"
        else:
            sl_label = f"\U0001f6d1 SL      ${idea.stop_loss:>10,.2f}   -${sl_d:,.2f}"
            en_label = f"\u25b6\ufe0f ENTRY   ${idea.entry_price:>10,.2f}"
            tp_label = f"\U0001f3af TP      ${idea.take_profit:>10,.2f}   +${tp_d:,.2f}"

        conf_bar = _bar(conf, 1.0, 10)

        return (
            f"{d_icon} <b>{d}  {_esc(idea.asset)}</b>\n\n"
            f"<pre>"
            f"  {tp_label}\n"
            f"  {'─' * 36}\n"
            f"  {en_label}\n"
            f"  {'─' * 36}\n"
            f"  {sl_label}"
            f"</pre>\n\n"
            f"  Confidence [{conf_bar}] <code>{conf:.0%}</code>\n"
            f"  Risk:Reward {_stars(rr)} <code>{rr}x</code>\n\n"
            f"<i>{_esc(idea.reasoning[:250])}</i>\n\n"
            f"<code>{idea.id}</code>"
        )


# ══════════════════════════════════════════════════════════════
# STATUS / RISK
# ══════════════════════════════════════════════════════════════

class CheckRiskSkill(BaseSkill):
    name = "check_risk"
    description = "Risk dashboard"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        mode = kwargs.get("mode", "risk")
        state = engine.portfolio.snapshot()
        cb = engine.risk.circuit_breaker_active
        streak = engine.risk.consecutive_losses
        cost = engine.cost.snapshot()
        total_exp = sum(p.entry_price * p.quantity for p in engine.portfolio.open_positions)
        exp_pct = (total_exp / state.equity_usd * 100) if state.equity_usd > 0 else 0

        from bot.risk.risk_engine import _CORRELATION_GROUPS
        groups: dict[str, int] = {}
        for pos in engine.portfolio.open_positions:
            g = _CORRELATION_GROUPS.get(pos.asset, pos.asset)
            groups[g] = groups.get(g, 0) + 1

        if mode == "status":
            return self._status(engine, state, cb, streak, cost, exp_pct)
        return self._risk(state, cb, streak, total_exp, exp_pct, groups)

    def _status(self, engine, state, cb, streak, cost, exp_pct):
        mode = "PAPER" if CONFIG.simulation_mode else "\u26a0\ufe0f LIVE"
        cb_s = f"{_BAD} TRIPPED" if cb else f"{_OK} CLEAR"
        macro = engine.macro_calendar.evaluate()
        macro_icons = {
            "NORMAL": _OK, "PRE_EVENT_CAUTION": _WARN,
            "EVENT_LOCKDOWN": _BAD, "POST_EVENT_VOLATILITY": "\U0001f7e0",
            "BLACKOUT": "\u26ab",
        }
        m_icon = macro_icons.get(macro.state.value, _NEU)
        m_label = macro.state.value.replace("_", " ").title()
        net = state.equity_usd - cost.operating_cost_usd
        pnl_icon = _status(state.daily_pnl)

        return (
            f"\U0001f43e <b>RUNECLAW STATUS</b>\n\n"
            # ── Quick glance header ──
            f"  {cb_s}  \u2022  {mode}  \u2022  {m_icon} {m_label}\n\n"
            # ── Capital card ──
            f"\U0001f4b0 <b>Capital</b>\n"
            f"<pre>"
            f"{_row('Equity', _money(state.equity_usd))}\n"
            f"{_row('Net', _money(net))}\n"
            f"{_row('Daily PnL', _money(state.daily_pnl, sign=True))}\n"
            f"{_row('Drawdown', f'{state.max_drawdown_pct:.1f}%')}"
            f"</pre>\n\n"
            # ── Positions card ──
            f"\U0001f4ca <b>Positions</b>\n"
            f"<pre>"
            f"{_row('Open', f'{state.open_positions} / {CONFIG.risk.max_open_positions}')}\n"
            f"{_row('Total', str(state.total_trades))}\n"
            f"{_row('Win Rate', f'{state.win_rate:.0%}')}\n"
            f"{_row('Exposure', f'{exp_pct:.0f}%')}"
            f"</pre>\n\n"
            # ── Risk gate ──
            f"\U0001f6e1 <b>Risk Gate</b>\n"
            f"<pre>"
            f"{_row('Breaker', 'TRIPPED' if cb else 'CLEAR')}\n"
            f"{_row('Streak', f'{streak} / {CONFIG.risk.max_consecutive_losses}')}\n"
            f"{_row('Checks', '18 fail-closed')}"
            f"</pre>\n\n"
            # ── Costs ──
            f"\u26a1 <b>Costs</b>\n"
            f"<pre>"
            f"{_row('LLM', f'${cost.llm_cost_usd:,.4f}')}\n"
            f"{_row('Infra', f'${cost.infra_cost_usd:,.4f}')}"
            f"</pre>"
        )

    def _risk(self, state, cb, streak, total_exp, exp_pct, groups):
        cb_icon = _BAD if cb else _OK
        cb_label = "TRIPPED" if cb else "CLEAR"
        grp = ", ".join(f"{g}={c}" for g, c in groups.items()) if groups else "none"

        return (
            f"\U0001f6e1 <b>RISK DASHBOARD</b>\n\n"
            f"  {cb_icon} Circuit Breaker: <b>{cb_label}</b>\n\n"
            # ── Visual gauges ──
            f"{_gauge('Drawdown', state.max_drawdown_pct, CONFIG.risk.max_drawdown_pct)}\n"
            f"{_gauge('Exposure', exp_pct, CONFIG.risk.max_portfolio_exposure_pct)}\n"
            f"{_gauge('Streak', streak, CONFIG.risk.max_consecutive_losses, unit='#')}\n\n"
            # ── Capital breakdown ──
            f"\U0001f4b0 <b>Capital</b>\n"
            f"<pre>"
            f"{_row('Equity', _money(state.equity_usd))}\n"
            f"{_row('Daily PnL', _money(state.daily_pnl, sign=True))}\n"
            f"{_row('Exposure', _money(total_exp))}\n"
            f"{_row('Positions', f'{state.open_positions} / {CONFIG.risk.max_open_positions}')}\n"
            f"{_row('Groups', grp)}"
            f"</pre>\n\n"
            # ── Configured limits ──
            f"\U0001f512 <b>Limits</b>\n"
            f"<pre>"
            f"{_row('Min Conf', f'{CONFIG.risk.min_confidence:.0%}')}\n"
            f"{_row('Min R:R', f'{CONFIG.risk.min_risk_reward}x')}\n"
            f"{_row('Max DD', f'{CONFIG.risk.max_drawdown_pct}%')}\n"
            f"{_row('Max Daily', f'{CONFIG.risk.max_daily_loss_pct}%')}\n"
            f"{_row('Vol Guard', f'{CONFIG.risk.volatility_guard_atr_pct}% ATR')}\n"
            f"{_row('Checks', '18 fail-closed')}"
            f"</pre>"
        )


# ══════════════════════════════════════════════════════════════
# PORTFOLIO
# ══════════════════════════════════════════════════════════════

class GetPortfolioSkill(BaseSkill):
    name = "get_portfolio"
    description = "Portfolio with PnL waterfall"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        state = engine.portfolio.snapshot()
        cost = engine.cost.snapshot()
        net = state.equity_usd - cost.operating_cost_usd
        cpt = cost.operating_cost_usd / state.total_trades if state.total_trades > 0 else 0
        pnl_icon = _status(state.total_pnl)

        lines = [
            f"\U0001f4b0 <b>PORTFOLIO</b>  {pnl_icon}\n",
            # ── Balance card ──
            f"\U0001f4b3 <b>Balance</b>",
            "<pre>",
            _row("Cash", _money(state.balance_usd)),
            _row("Equity", _money(state.equity_usd)),
            _row("Win Rate", f"{state.win_rate:.0%}"),
            "</pre>\n",
            # ── PnL waterfall ──
            f"\U0001f4c8 <b>PnL Waterfall</b>",
            "<pre>",
            _row("Gross", _money(state.total_gross_pnl, sign=True)),
            _row("Commission", _money(state.total_commission)),
            _row("Net Trading", _money(state.total_pnl, sign=True)),
            _row("LLM Cost", f"${cost.llm_cost_usd:,.4f}"),
            _row("Infra Cost", f"${cost.infra_cost_usd:,.4f}"),
            f"  {'─' * 26}",
            _row("NET EQUITY", _money(net, sign=True)),
            _row("Cost/Trade", f"${cpt:,.4f}"),
            "</pre>",
        ]

        open_pos = engine.portfolio.open_positions
        if open_pos:
            lines.append(f"\n\U0001f4ca <b>Open Positions</b>  ({len(open_pos)})")
            for pos in open_pos:
                d_icon = _OK if pos.direction.value == "LONG" else _BAD
                d_tag = "L" if pos.direction.value == "LONG" else "S"
                size = pos.entry_price * pos.quantity
                lines.append(
                    f"  {d_icon} <b>{_esc(pos.asset)}</b>  {d_tag}  "
                    f"<code>${pos.entry_price:,.2f}</code>  "
                    f"<code>${size:,.0f}</code>"
                )
        else:
            lines.append(f"\n<i>{state.total_trades} trades \u2022 no open positions</i>")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# EXECUTE / EXPLAIN
# ══════════════════════════════════════════════════════════════

class ExecutePaperTradeSkill(BaseSkill):
    name = "execute_paper_trade"
    description = "Confirm and execute a pending paper trade"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        trade_id = kwargs.get("trade_id") or kwargs.get("symbol", "")
        if not trade_id:
            return "Provide a trade_id to confirm."
        return await engine.confirm_trade(trade_id)

class ExplainTradeSkill(BaseSkill):
    name = "explain_trade"
    description = "Explain a trade idea"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        trade_id = kwargs.get("trade_id", "")
        for idea in engine.pending_ideas:
            if idea.id == trade_id:
                return (
                    f"\U0001f4d6 <b>EXPLANATION</b>\n\n"
                    f"<code>{idea.id}</code>  {idea.direction.value} {_esc(idea.asset)}\n"
                    f"Confidence: <code>{idea.confidence:.0%}</code>\n"
                    f"Signals: <code>{', '.join(idea.signals_used)}</code>\n\n"
                    f"<i>{_esc(idea.reasoning)}</i>"
                )
        return f"Trade <code>{_esc(trade_id)}</code> not found."


# ══════════════════════════════════════════════════════════════
# BACKTEST
# ══════════════════════════════════════════════════════════════

class RunBacktestSkill(BaseSkill):
    name = "run_backtest"
    description = "Run backtest with synthetic data"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.engine import BacktestEngine
        from bot.backtest.models import BacktestConfig

        bars_count = min(int(kwargs.get("bars", 720)), 5000)
        seed = int(kwargs.get("seed", 42))
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=bars_count, seed=seed)
        bt = BacktestEngine(config)
        r = await bt.run(bars)
        bt.cleanup()

        ret_icon = _status(r.total_return_pct)

        return (
            f"\U0001f4ca <b>BACKTEST</b>  {ret_icon}\n"
            f"<i>Synthetic data \u2014 tests plumbing, not alpha</i>\n\n"
            # ── Performance ──
            f"\U0001f4b0 <b>Performance</b>\n"
            f"<pre>"
            f"{_row('Return', f'{r.total_return_pct:+.2f}%')}\n"
            f"{_row('Equity', _money(r.final_equity))}\n"
            f"{_row('Net PnL', _money(r.net_pnl, sign=True))}\n"
            f"{_row('Commission', _money(r.total_commission))}\n"
            f"{_row('Slippage', _money(r.total_slippage))}"
            f"</pre>\n\n"
            # ── Quality ──
            f"\U0001f3af <b>Quality</b>\n"
            f"<pre>"
            f"{_row('Trades', str(r.total_trades))}\n"
            f"{_row('Win Rate', f'{r.win_rate:.0%}')}\n"
            f"{_row('Profit F.', f'{r.profit_factor:.2f}')}\n"
            f"{_row('Max DD', f'{r.max_drawdown_pct:.2f}%')}\n"
            f"{_row('Sharpe', f'{r.sharpe_ratio:.2f}')}\n"
            f"{_row('Sortino', f'{r.sortino_ratio:.2f}')}"
            f"</pre>\n\n"
            # ── Pipeline ──
            f"\U0001f504 <b>Pipeline</b>\n"
            f"<pre>"
            f"{_row('Signals', str(r.total_signals_generated))}\n"
            f"{_row('Ideas', str(r.total_ideas_generated))}\n"
            f"{_row('Risk Reject', str(r.total_ideas_rejected_risk))}\n"
            f"{_row('Conf Reject', str(r.total_ideas_rejected_confidence))}"
            f"</pre>\n\n"
            f"<i>{r.bars_processed} bars \u2022 {r.duration_seconds:.1f}s \u2022 "
            f"{r.start_date} \u2192 {r.end_date}</i>"
        )


# ══════════════════════════════════════════════════════════════
# REJECTED
# ══════════════════════════════════════════════════════════════

class RejectedTradesSkill(BaseSkill):
    name = "rejected_trades"
    description = "Recent risk-rejected trades"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        history = engine.risk.rejection_history
        if not history:
            return (f"{_NEU} <b>REJECTED TRADES</b>\n\n"
                    "<i>No rejections yet. The risk gate is working.</i>")

        count = int(kwargs.get("count", 5))
        recent = history[-count:]

        lines = [f"{_WARN} <b>REJECTED TRADES</b>  ({len(recent)}/{len(history)})\n"]
        for r in reversed(recent):
            d_icon = _OK if r["direction"] == "LONG" else _BAD
            fails = r["checks_failed"]
            fail_str = _esc(fails[0]) if fails else "unknown"
            extra = f" +{len(fails) - 1}" if len(fails) > 1 else ""
            lines.append(
                f"  {d_icon} <b>{_esc(r['asset'])}</b>  {r['direction']}  "
                f"<code>{r['confidence']:.0%}</code>\n"
                f"     \u2718 <code>{fail_str}</code>{extra}"
            )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# HALT
# ══════════════════════════════════════════════════════════════

class HaltSkill(BaseSkill):
    name = "halt"
    description = "Emergency kill-switch"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        from bot.utils.models import AgentState
        engine.risk.emergency_halt("manual halt via /halt command")
        cancelled = list(engine._pending_ideas.keys())
        engine._pending_ideas.clear()
        engine._pending_atr.clear()
        engine._transition(AgentState.HALTED, "manual halt via /halt command")
        audit(system_log, f"MANUAL HALT: {len(cancelled)} ideas cancelled",
              action="halt", result="HALTED", data={"cancelled_ids": cancelled})
        return (
            f"\U0001f6a8 <b>EMERGENCY HALT</b>\n\n"
            f"  {_BAD} Circuit Breaker: <b>TRIPPED</b>\n"
            f"  Ideas Cancelled: <code>{len(cancelled)}</code>\n"
            f"  Engine: <code>HALTED</code>\n\n"
            f"<i>All trading paused. /reset to resume.</i>"
        )


# ══════════════════════════════════════════════════════════════
# WALK FORWARD
# ══════════════════════════════════════════════════════════════

class WalkForwardSkill(BaseSkill):
    name = "walk_forward"
    description = "Walk-forward backtest"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.engine import walk_forward_backtest
        from bot.backtest.models import BacktestConfig

        bars_count = min(int(kwargs.get("bars", 1440)), 5000)
        seed = int(kwargs.get("seed", 42))
        folds = int(kwargs.get("folds", 3))
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=bars_count, seed=seed)
        result = await walk_forward_backtest(bars, config, n_folds=folds)

        lines = [
            f"\U0001f4c8 <b>WALK-FORWARD</b>\n",
            "<pre>",
            f"  {'FOLD':>4}  {'TRAIN':>8}  {'TEST':>8}  {'TRADES':>7}",
            f"  {'─'*4}  {'─'*8}  {'─'*8}  {'─'*7}",
        ]
        for f in result.folds:
            lines.append(
                f"  {f['fold']:>4}  {f['train_return_pct']:>+7.2f}%"
                f"  {f['test_return_pct']:>+7.2f}%"
                f"  {f['train_trades'] + f['test_trades']:>7}"
            )
        gap = result.train_test_gap
        lines.append("")
        lines.append(f"  Avg Train  {result.aggregate_train_return:>+7.2f}%")
        lines.append(f"  Avg Test   {result.aggregate_test_return:>+7.2f}%")
        lines.append(f"  Gap        {gap:>+7.2f}%")
        lines.append(f"  Consist.   {result.consistency_score:>6.0%}")
        lines.append("</pre>")
        if gap > 2:
            lines.append(f"\n{_WARN} <i>Overfitting risk detected</i>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# MACRO
# ══════════════════════════════════════════════════════════════

class MacroCalendarSkill(BaseSkill):
    name = "macro_calendar"
    description = "Macro event calendar"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        cal = engine.macro_calendar
        snap = cal.evaluate()
        upcoming = cal.upcoming(limit=5)

        state_icons = {
            "NORMAL": _OK, "PRE_EVENT_CAUTION": _WARN,
            "EVENT_LOCKDOWN": _BAD, "POST_EVENT_VOLATILITY": "\U0001f7e0",
            "BLACKOUT": "\u26ab",
        }
        icon = state_icons.get(snap.state.value, _NEU)

        lines = [
            f"\U0001f4c5 <b>MACRO CALENDAR</b>\n",
            f"  {icon} <b>{snap.state.value.replace('_', ' ').title()}</b>",
        ]

        if snap.active_event:
            lines.append(f"  Active: <code>{_esc(snap.active_event.label)}</code>")
        if snap.time_until_next:
            hours = snap.time_until_next.total_seconds() / 3600
            if hours < 1:
                t = f"{snap.time_until_next.total_seconds() / 60:.0f}min"
            elif hours < 24:
                t = f"{hours:.1f}h"
            else:
                t = f"{hours / 24:.1f}d"
            lines.append(f"  Next event in: <code>{t}</code>")

        if upcoming:
            lines.append(f"\n\U0001f4cb <b>Upcoming</b>")
            for ev in upcoming:
                times = cal.format_event_times(ev)
                lines.append(f"  \u2022 <b>{_esc(ev.label)}</b>")
                lines.append(f"    <code>{times['utc']}</code>")
                lines.append(f"    <code>{times['et']}</code>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# JOURNAL
# ══════════════════════════════════════════════════════════════

class TradeJournalSkill(BaseSkill):
    name = "trade_journal"
    description = "Trade history"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        history = engine.portfolio._history
        if not history:
            return f"{_NEU} <b>TRADE JOURNAL</b>\n\n<i>No closed trades yet.</i>"

        count = int(kwargs.get("count", 10))
        recent = history[-count:]

        lines = [f"\U0001f4d3 <b>TRADE JOURNAL</b>  ({len(recent)}/{len(history)})\n"]

        total_pnl = 0.0
        wins = 0
        for trade in reversed(recent):
            is_win = trade.pnl > 0
            if is_win: wins += 1
            total_pnl += trade.pnl
            icon = _OK if is_win else _BAD
            tag = "WIN" if is_win else "LOSS"
            dur = ""
            if trade.closed_at and trade.opened_at:
                h = (trade.closed_at - trade.opened_at).total_seconds() / 3600
                dur = f" \u2022 {h:.1f}h"
            exit_p = f"${trade.exit_price:,.2f}" if trade.exit_price else "open"
            size = trade.entry_price * trade.quantity

            lines.append(
                f"  {icon} <b>{_esc(trade.asset)}</b>  {trade.direction.value}\n"
                f"     <code>${trade.pnl:+,.2f}</code> {tag}{dur}\n"
                f"     ${trade.entry_price:,.2f} \u2192 {exit_p}  size ${size:,.0f}"
            )

        wr = wins / len(recent) if recent else 0
        lines.append(
            f"\n<b>{wins}W / {len(recent)-wins}L</b>  "
            f"WR <code>{wr:.0%}</code>  "
            f"Net <code>${total_pnl:+,.2f}</code>"
        )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# COSTS
# ══════════════════════════════════════════════════════════════

class CostBreakdownSkill(BaseSkill):
    name = "costs"
    description = "Agent economics"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        cost = engine.cost.snapshot()
        state = engine.portfolio.snapshot()
        rate_stats = engine.analyzer._rate_limiter.stats
        net = state.equity_usd - cost.operating_cost_usd

        lines = [
            f"\U0001f4b0 <b>AGENT ECONOMICS</b>\n",
            f"\u26a1 <b>LLM Usage</b>",
            "<pre>",
            _row("Total", f"${cost.llm_cost_usd:,.4f} ({cost.llm_calls} calls)"),
            _row("Tokens In", f"{cost.prompt_tokens:,}"),
            _row("Tokens Out", f"{cost.completion_tokens:,}"),
            _row("Avg/Call", f"${cost.avg_cost_per_call:,.6f}"),
            "</pre>",
        ]

        cats_found = False
        for cat in ("scan", "analyze", "thesis", "risk_decision", "other"):
            c = cost.cost_by_category.get(cat, 0.0)
            n = cost.calls_by_category.get(cat, 0)
            if n > 0:
                if not cats_found:
                    lines.extend([f"\n\U0001f4ca <b>Breakdown</b>", "<pre>"])
                    cats_found = True
                lines.append(_row(cat.title(), f"${c:,.4f} ({n})"))
        if cats_found:
            lines.append("</pre>")

        lines.extend([
            f"\n\U0001f4b3 <b>Operating Total</b>",
            "<pre>",
            _row("LLM", f"${cost.llm_cost_usd:,.4f}"),
            _row("Infra", f"${cost.infra_cost_usd:,.4f}"),
            _row("Total", f"${cost.operating_cost_usd:,.4f}"),
        ])
        if state.total_trades > 0:
            cpt = cost.operating_cost_usd / state.total_trades
            lines.append(_row("Per Trade", f"${cpt:,.4f}"))

        lines.extend([
            "</pre>",
            f"\n\U0001f4c8 <b>Net</b>",
            "<pre>",
            _row("Equity", _money(state.equity_usd)),
            _row("- Costs", f"${cost.operating_cost_usd:,.4f}"),
            _row("= Net", _money(net)),
            "</pre>",
            f"\n<i>Rate limiter: {rate_stats['total_calls']} calls, "
            f"{rate_stats['total_waits']} throttled</i>",
        ])
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# STRATEGY
# ══════════════════════════════════════════════════════════════

class RunStrategySkill(BaseSkill):
    name = "run_strategy"
    description = "Execute a strategy preset"

    PRESETS: dict[str, dict[str, Any]] = {
        "btc dip sniper": {
            "label": "BTC Dip Sniper", "icon": "\U0001f3af",
            "desc": "BTC only \u2022 RSI &lt; 35 \u2022 TREND_DOWN \u2022 conf \u2265 70%",
            "symbols": ["BTC/USDT"], "rsi_threshold": 35,
            "regime": "TREND_DOWN", "confidence_threshold": 0.70,
            "volume_spike_min": None, "sl_atr_mult": None, "tp_atr_mult": None,
        },
        "momentum hunter": {
            "label": "Momentum Hunter", "icon": "\U0001f680",
            "desc": "All pairs \u2022 vol spike &gt; 3x \u2022 TREND_UP",
            "symbols": None, "rsi_threshold": None, "regime": "TREND_UP",
            "confidence_threshold": None, "volume_spike_min": 3.0,
            "sl_atr_mult": None, "tp_atr_mult": None,
        },
        "safe scalper": {
            "label": "Safe Scalper", "icon": "\u26a1",
            "desc": "Top 3 vol \u2022 tight SL 1.5 ATR \u2022 conf \u2265 75%",
            "symbols": "top3_volume", "rsi_threshold": None, "regime": None,
            "confidence_threshold": 0.75, "volume_spike_min": None,
            "sl_atr_mult": 1.5, "tp_atr_mult": 2.0,
        },
        "full scan": {
            "label": "Full Scan", "icon": "\U0001f50d",
            "desc": "All defaults \u2022 standard pipeline",
            "symbols": None, "rsi_threshold": None, "regime": None,
            "confidence_threshold": None, "volume_spike_min": None,
            "sl_atr_mult": None, "tp_atr_mult": None,
        },
    }
    ALIASES: dict[str, str] = {
        "dip": "btc dip sniper", "momentum": "momentum hunter",
        "scalp": "safe scalper", "scan all": "full scan",
    }

    @classmethod
    def _resolve(cls, raw: str) -> str | None:
        key = raw.strip().lower()
        return key if key in cls.PRESETS else cls.ALIASES.get(key)

    @classmethod
    def _list(cls) -> str:
        lines = ["\U0001f3af <b>STRATEGY PRESETS</b>\n"]
        for key, cfg in cls.PRESETS.items():
            aliases = [a for a, t in cls.ALIASES.items() if t == key]
            a = f"  <i>/{aliases[0]}</i>" if aliases else ""
            lines.append(f"  {cfg['icon']} <b>{cfg['label']}</b>{a}")
            lines.append(f"     <i>{cfg['desc']}</i>")
        lines.append(f"\n<i>Usage: /run &lt;name&gt; \u2022 18 checks active</i>")
        return "\n".join(lines)

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        strat = kwargs.get("strategy", "")
        if not strat:
            return self._list()
        key = self._resolve(strat)
        if not key:
            return f"Unknown: <code>{_esc(strat)}</code>\n\n" + self._list()

        cfg = self.PRESETS[key]
        audit(system_log, f"Strategy: {cfg['label']}", action="run_strategy", data=cfg)

        signals = await engine.scanner.scan()
        if not signals:
            return f"{cfg['icon']} <b>{cfg['label']}</b>\n\n<i>No signals</i>"

        if cfg["symbols"] == "top3_volume":
            signals.sort(key=lambda s: s.volume_usd_24h, reverse=True)
            signals = signals[:3]
        elif cfg["symbols"] is not None:
            signals = [s for s in signals if s.symbol in set(cfg["symbols"])]

        if cfg["volume_spike_min"] is not None:
            signals = [s for s in signals if getattr(s, "volume_spike_ratio", 0) >= cfg["volume_spike_min"]
                       or getattr(s, "volume_spike", False)]

        if not signals:
            return f"{cfg['icon']} <b>{cfg['label']}</b>\n\n<i>No signals matched filters</i>"

        results = []
        ideas = 0
        for sig in signals[:5]:
            idea = await engine._analyze_signal(sig)
            if not idea:
                continue
            ct = cfg.get("confidence_threshold")
            if ct and idea.confidence < ct:
                continue
            engine._pending_ideas[idea.id] = idea
            ideas += 1
            d_icon = _OK if idea.direction.value == "LONG" else _BAD
            results.append(
                f"  {d_icon} <b>{_esc(idea.asset)}</b>  "
                f"<code>{idea.confidence:.0%}</code>  R:R <code>{idea.risk_reward_ratio}</code>"
            )

        lines = [
            f"{cfg['icon']} <b>{cfg['label']}</b>",
            f"  Scanned <code>{len(signals)}</code> \u2022 Ideas <code>{ideas}</code>\n",
        ]
        lines.extend(results or ["  <i>No actionable ideas passed filters</i>"])
        if ideas > 0:
            lines.append(f"\n<i>/trade to review and confirm</i>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# LEARNING
# ══════════════════════════════════════════════════════════════

class LearningDashboardSkill(BaseSkill):
    name = "learning"
    description = "AI learning dashboard"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        dash = engine.learning.dashboard()
        score = dash["learning_score"]
        stats = dash["store_stats"]
        tier_icons = {"S": "\U0001f451", "A": "\U0001f31f", "B": "\u2b50", "C": _NEU, "D": _BAD}
        t = tier_icons.get(score["tier"], _NEU)

        lines = [
            f"\U0001f9e0 <b>AI LEARNING SYSTEM</b>\n",
            f"  {t} Score: <code>{score['composite_score']}/10</code>  [{score['tier']}]\n",
        ]

        # Per-module health status
        lines.append("\U0001f4e6 <b>Module Health</b>")
        lines.append("<pre>")
        module_names = [
            ("decisions", "Experience Memory"),
            ("reflections", "Reflection Engine"),
            ("strategies", "Strategy Evaluator"),
            ("patterns", "Pattern Learner"),
            ("macro_events", "Macro Learner"),
            ("model_comparisons", "Model Comparer"),
            ("prompt_versions", "Prompt Optimizer"),
            ("feedback", "Feedback Collector"),
        ]
        for key, label in module_names:
            count = stats.get(key, 0)
            if count > 0:
                icon = "\u2705"
            else:
                icon = "\u26aa"
            lines.append(f"  {icon} {label:<20s} {count:>4} records")
        lines.append("</pre>\n")

        # Data stores
        lines.append(f"\U0001f4be <b>Data Summary</b>")
        lines.append("<pre>")
        total = sum(stats.values())
        lines.append(_row("Total records", str(total)))
        lines.append(_row("Strategies scored", str(score.get('strategies_evaluated', 0))))
        lines.append(_row("Feedback entries", str(score.get('feedback_total', 0))))
        lines.append("</pre>\n")

        # Proposals
        lines.extend([
            f"\U0001f4cb <b>Proposals</b>",
            "<pre>",
            _row("Pending", str(dash['pending_proposals'])),
            _row("Blocked", str(dash['blocked_proposals'])),
            "</pre>",
        ])

        if dash.get("strategy_rankings"):
            lines.append(f"\n\U0001f3af <b>Strategy Rankings</b>")
            for s in dash["strategy_rankings"][:5]:
                of = f" {_WARN}" if s["overfitting"] else ""
                lines.append(
                    f"  [{s['tier']}] <b>{s['name']}</b>  "
                    f"WR={s['win_rate']}  ({s['trades']}t){of}"
                )

        # Prompt versions
        pv = dash.get("prompt_versions", {})
        if pv and pv.get("versions"):
            lines.append(f"\n\U0001f4dd <b>Prompt Versions</b>")
            lines.append(f"  Active: v{pv.get('current_version', '?')}  "
                         f"({pv.get('total_versions', 0)} versions tracked)")

        # Model accuracy
        ma = dash.get("model_accuracy", {})
        if ma and ma.get("agreement_rate") is not None:
            rate = ma["agreement_rate"]
            lines.append(f"\n\U0001f916 <b>Model Agreement</b>: {rate:.0%}")

        lines.append(f"\n\U0001f512 <i>Safety sandbox active \u2014 AI learns aggressively, never overrides risk</i>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# FEEDBACK / PATTERNS / PROPOSALS / OPTIMIZER
# ══════════════════════════════════════════════════════════════

class FeedbackSkill(BaseSkill):
    name = "feedback"
    description = "Submit feedback"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        did = kwargs.get("decision_id", "")
        ft = kwargs.get("feedback_type", "")
        text = kwargs.get("text", "")
        if not did or not ft:
            return (
                f"\U0001f4ac <b>FEEDBACK</b>\n\n"
                f"<code>/feedback &lt;id&gt; &lt;type&gt; [text]</code>\n\n"
                f"Types: <code>correct</code>, <code>incorrect</code>, "
                f"<code>too_risky</code>, <code>too_conservative</code>, "
                f"<code>good_rejection</code>, <code>good_explanation</code>"
            )
        fb = engine.learning.submit_feedback(decision_audit_id=did, feedback_type=ft, feedback_text=text)
        return (f"{_OK} Feedback recorded: <code>{fb.audit_id}</code>\n"
                f"Type: <code>{_esc(ft)}</code>")


class PatternsSkill(BaseSkill):
    name = "patterns"
    description = "Detected patterns"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        patterns = engine.learning.detect_patterns()
        if not patterns:
            return f"{_NEU} <b>PATTERNS</b>\n\n<i>No patterns yet. Need more history.</i>"
        lines = [f"\U0001f50d <b>PATTERNS</b>\n"]
        for p in patterns[:8]:
            exp = f" {_WARN}" if p.is_experimental else ""
            lines.append(
                f"  \u2022 <b>{p.pattern_type}</b>{exp}\n"
                f"    Conf <code>{p.confidence:.0%}</code>  "
                f"WR <code>{p.historical_win_rate:.0%}</code>  "
                f"Avg <code>${p.avg_pnl:.2f}</code>  ({p.sample_size})"
            )
        lines.append(f"\n<i>Patterns are observations, not signals</i>")
        return "\n".join(lines)


class ProposalsSkill(BaseSkill):
    name = "proposals"
    description = "Improvement proposals"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        proposals = engine.learning.store.get_proposals()
        if not proposals:
            return f"{_NEU} <b>PROPOSALS</b>\n\n<i>No proposals yet.</i>"
        lines = [f"\U0001f4cb <b>PROPOSALS</b>  ({len(proposals)})\n"]
        for p in proposals[-6:]:
            icons = {"approved": _OK, "pending": _WARN, "rejected": _BAD, "blocked": "\u26ab"}
            s = icons.get(p.status, _NEU)
            lines.append(
                f"  {s} <b>[{_esc(p.classification)}]</b> {p.status}\n"
                f"     {_esc(p.problem[:70])}\n"
                f"     \u2192 {_esc(p.proposed_change[:70])}"
            )
        return "\n".join(lines)


class OptimizationSkill(BaseSkill):
    name = "optimize"
    description = "Token optimizer stats"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        opt = engine.analyzer.optimization_stats
        cost = engine.cost.snapshot()
        cache = opt.get("cache", {})
        tiers = opt.get("tier_distribution", {})
        adaptive = opt.get("adaptive_frequency", {})
        savings = opt.get("savings", {})
        total = tiers.get("total", 0)

        lines = [
            f"\u26a1 <b>TOKEN OPTIMIZER</b>\n",
            f"\U0001f4be <b>Cache</b>",
            "<pre>",
            _row("Size", f"{cache.get('size', 0)}/{cache.get('max_size', 0)}"),
            _row("Hit Rate", f"{cache.get('hit_rate', 0):.0%}"),
            _row("Evictions", str(cache.get('evictions', 0))),
            "</pre>",
            f"\n\U0001f4ca <b>Tier Distribution</b>",
            "<pre>",
            _row("T1 Rules", f"{tiers.get('tier1_rules', 0)} (free)"),
            _row("T2 Mini", f"{tiers.get('tier2_mini', 0)} (cheap)"),
            _row("T3 Full", f"{tiers.get('tier3_full', 0)} (best)"),
        ]
        if total > 0:
            lines.append(_row("Free %", f"{tiers.get('tier1_rules', 0) / total * 100:.0f}%"))

        saved = savings.get("total_estimated_cost_saved_usd", 0)
        lines.extend([
            "</pre>",
            f"\n\U0001f4b0 <b>Savings</b>",
            "<pre>",
            _row("Tokens", f"~{savings.get('total_estimated_tokens_saved', 0):,}"),
            _row("Cost", f"~${saved:,.4f}"),
        ])
        if cost.llm_cost_usd > 0:
            would_have = cost.llm_cost_usd + saved
            pct = (saved / would_have * 100) if would_have > 0 else 0
            lines.append(_row("Reduction", f"{pct:.0f}%"))
        lines.append("</pre>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# REGISTRY
# ══════════════════════════════════════════════════════════════

def build_default_registry() -> SkillRegistry:
    from bot.skills.quant_skill import QuantAnalyzeSkill

    registry = SkillRegistry()
    for cls in (ScanMarketSkill, AnalyzeAssetSkill, CheckRiskSkill,
                ExecutePaperTradeSkill, GetPortfolioSkill, ExplainTradeSkill,
                RunBacktestSkill, RejectedTradesSkill, HaltSkill,
                WalkForwardSkill, MacroCalendarSkill, TradeJournalSkill,
                CostBreakdownSkill, RunStrategySkill,
                LearningDashboardSkill, FeedbackSkill, PatternsSkill,
                ProposalsSkill, OptimizationSkill, QuantAnalyzeSkill):
        registry.register(cls())
    return registry
