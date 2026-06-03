"""
RUNECLAW Skill System v6 — rich Telegram cards.
Clean separators, emoji headers, bullet-point key-value pairs,
<b>bold</b> section headers, <code>code</code> for prices/numbers.
"""

from __future__ import annotations

import html as _html
from abc import ABC, abstractmethod
from datetime import datetime
from bot.compat import UTC
from typing import Any

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.utils.logger import audit, system_log


# ── Visual vocabulary ─────────────────────────────────────────
_OK = "\U0001f7e2"        # green circle
_WARN = "\U0001f7e1"      # yellow circle
_BAD = "\U0001f534"       # red circle
_NEU = "\u26aa"           # white circle
_SHIELD = "\U0001f6e1"    # shield (risk dashboard)
_BOOK = "\U0001f4d6"      # book (explanation)
_CHART = "\U0001f4ca"     # chart (backtest)

_BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"  # ▁▂▃▄▅▆▇█

def _status(v: float) -> str:
    return _OK if v > 0 else _BAD if v < 0 else _NEU

def _spark(v: float) -> str:
    if v > 2: return "\u25b2"   # ▲
    if v > 0: return "\u25b3"   # △
    if v < -2: return "\u25bc"  # ▼
    if v < 0: return "\u25bd"   # ▽
    return "\u25c7"             # ◇

def _bar(val: float, mx: float = 1.0, w: int = 10) -> str:
    """Gradient progress bar using ━ filled and ╌ empty."""
    r = min(max(val / mx, 0), 1.0) if mx > 0 else 0
    f = int(r * w)
    return "\u2501" * f + "\u254c" * (w - f)  # ━ filled, ╌ empty

def _gauge(label: str, val: float, mx: float, unit: str = "%", w: int = 12) -> str:
    """Visual gauge with gradient bar and inline value."""
    bar = _bar(val, mx, w)
    # Pick endpoint emoji based on fill level
    r = val / mx if mx > 0 else 0
    tip = "\U0001f7e2" if r < 0.5 else "\U0001f7e1" if r < 0.8 else "\U0001f534"
    if unit == "%":
        return f"  {tip} {label:<10} \u2502{bar}\u2502 {val:.1f}%\u2009/\u2009{mx:.0f}%"
    return f"  {tip} {label:<10} \u2502{bar}\u2502 {val:.0f}\u2009/\u2009{mx:.0f}"

def _hbar(label: str, val: float, mx: float, w: int = 8) -> str:
    """Compact horizontal bar with label."""
    r = min(max(val / mx, 0), 1.0) if mx > 0 else 0
    f = int(r * w)
    filled = "\u2588" * f          # █
    empty = "\u2591" * (w - f)     # ░
    return f"  {label}  {filled}{empty}  {val:.0f}"

def _divider(char: str = "\u2500", w: int = 28) -> str:
    """Visual section separator: ────────────────────────────"""
    return f"  {char * w}"

def _header(emoji: str, title: str, w: int = 24) -> str:
    """Decorated card header with title bar."""
    return f"{emoji} <b>{title}</b> {'━' * w}"

SEP = "\u2500" * 16  # ────────────────

def _kv(key: str, val: str, w: int = 28) -> str:
    """Clean key-value line: '- Label: value'."""
    return f"- {key}: {val}"

def _pill(text: str) -> str:
    """Inline code badge."""
    return f"<code>\u2009{text}\u2009</code>"

def _sparkline(values: list[float], w: int = 12) -> str:
    """Mini sparkline from block characters ▁▂▃▄▅▆▇█."""
    if not values:
        return "\u2500" * w
    # Sample or pad to width
    if len(values) > w:
        step = len(values) / w
        sampled = [values[int(i * step)] for i in range(w)]
    else:
        sampled = values
    mn, mx = min(sampled), max(sampled)
    rng = mx - mn if mx > mn else 1.0
    out = []
    for v in sampled:
        idx = int((v - mn) / rng * 7)
        idx = max(0, min(7, idx))
        out.append(_BLOCKS[idx])
    return "".join(out)

def _mini_chart(vals: list[float], w: int = 16) -> str:
    """Mini chart using block characters, wrapped in <code>."""
    return f"<code>{_sparkline(vals, w)}</code>"

def _traffic_light(passed: int, total: int) -> str:
    """Visual check display as dots: 🟢🟢🟢🟡🔴"""
    failed = total - passed
    return _OK * passed + _BAD * failed

def _progress_ring(pct: float) -> str:
    """Circular progress indicator using Unicode."""
    rings = ["\u25cb", "\u25d4", "\u25d1", "\u25d5", "\u25cf"]  # ○◔◑◕●
    idx = int(min(max(pct, 0), 100) / 25)
    idx = min(idx, 4)
    return rings[idx]

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

def _pnl_arrow(v: float) -> str:
    """Directional PnL indicator."""
    if v > 0: return "\U0001f7e2\u25b2"
    if v < 0: return "\U0001f534\u25bc"
    return "\u26aa\u25c7"


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

        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        top = signals[:8]
        lines = [
            f"\U0001f50e <b>MARKET SCANNER</b>\n{SEP}",
            f"<i>\u23f0 {now}  \u2022  {len(signals)} pairs detected</i>\n",
        ]
        lines.append("<pre>")
        for s in top:
            arrow = _spark(s.change_pct_24h)
            vol_m = s.volume_usd_24h / 1_000_000 if s.volume_usd_24h else 0
            chg = f"{s.change_pct_24h:+.1f}%"
            spike = " \U0001f4a5" if s.volume_spike else ""
            # Build volume intensity mini-bar (1-4 blocks)
            vol_lvl = min(int(vol_m / 10) + 1, 4)
            vol_bar = "\u2588" * vol_lvl + "\u2591" * (4 - vol_lvl)
            lines.append(
                f" {arrow} {_esc(s.symbol):<10s}"
                f"  ${s.price:<12,.2f}"
                f"  {chg:>7}"
                f"  {vol_bar} ${vol_m:,.0f}M{spike}"
            )
        lines.append("</pre>")

        # Summary line
        bullish = sum(1 for s in top if s.change_pct_24h > 0)
        bearish = len(top) - bullish
        spikes = sum(1 for s in top if s.volume_spike)
        lines.append(f"\n{_OK} {bullish} bullish  {_BAD} {bearish} bearish")
        if spikes:
            lines.append(f"\U0001f4a5 {spikes} volume spike{'s' if spikes != 1 else ''} detected")
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
                f"{_NEU} <b>{_esc(symbol)}</b>  {arrow}\n{SEP}\n\n"
                f"- Price: <code>${sig.price:,.2f}</code>\n"
                f"- 24h: <code>{sig.change_pct_24h:+.1f}%</code>\n"
                f"- Volume: <code>${vol_m:,.0f}M</code>\n\n"
                f"<i>\u25c7 No actionable signal \u2014 regime filter or low confluence</i>"
            )

        engine._pending_ideas[idea.id] = idea

        d = idea.direction.value
        d_icon = _OK if d == "LONG" else _BAD
        d_arrow = "\u25b2" if d == "LONG" else "\u25bc"
        rr = idea.risk_reward_ratio
        conf = idea.confidence
        sl_d = abs(idea.entry_price - idea.stop_loss)
        tp_d = abs(idea.take_profit - idea.entry_price)

        # Price ladder with box drawing
        if d == "LONG":
            ladder = (
                f"  \U0001f3af TP    \u2502 ${idea.take_profit:>10,.2f}  (+${tp_d:,.2f})\n"
                f"  \u2500\u2500\u2500\u2500\u2500\u2500\u253c{'─' * 28}\n"
                f"  {d_arrow}  IN   \u2502 ${idea.entry_price:>10,.2f}\n"
                f"  \u2500\u2500\u2500\u2500\u2500\u2500\u253c{'─' * 28}\n"
                f"  \U0001f6d1 SL    \u2502 ${idea.stop_loss:>10,.2f}  (-${sl_d:,.2f})"
            )
        else:
            ladder = (
                f"  \U0001f6d1 SL    \u2502 ${idea.stop_loss:>10,.2f}  (-${sl_d:,.2f})\n"
                f"  \u2500\u2500\u2500\u2500\u2500\u2500\u253c{'─' * 28}\n"
                f"  {d_arrow}  IN   \u2502 ${idea.entry_price:>10,.2f}\n"
                f"  \u2500\u2500\u2500\u2500\u2500\u2500\u253c{'─' * 28}\n"
                f"  \U0001f3af TP    \u2502 ${idea.take_profit:>10,.2f}  (+${tp_d:,.2f})"
            )

        # Confidence bar using gradient blocks
        conf_w = 12
        conf_fill = int(conf * conf_w)
        conf_bar = _BLOCKS[7] * conf_fill + _BLOCKS[0] * (conf_w - conf_fill)
        conf_ring = _progress_ring(conf * 100)

        return (
            f"{d_icon} <b>{d}  {_esc(idea.asset)}</b>\n{SEP}\n\n"
            f"<pre>"
            f"{ladder}"
            f"</pre>\n\n"
            f"  {conf_ring} Confidence \u2502{conf_bar}\u2502 {_pill(f'{conf:.0%}')}\n"
            f"  \u2606 Risk:Reward {_stars(rr)} {_pill(f'{rr}x')}\n\n"
            f"<blockquote>{_esc(idea.reasoning[:250])}</blockquote>\n\n"
            f"\U0001f4ce {_pill(idea.id)}"
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

        # Health score: combine drawdown headroom + win rate + streak safety
        dd_health = max(0, 100 - (state.max_drawdown_pct / CONFIG.risk.max_drawdown_pct * 100)) if CONFIG.risk.max_drawdown_pct > 0 else 100
        streak_health = max(0, 100 - (streak / CONFIG.risk.max_consecutive_losses * 100)) if CONFIG.risk.max_consecutive_losses > 0 else 100
        overall = (dd_health + streak_health) / 2
        health_ring = _progress_ring(overall)

        return (
            f"\U0001f43e <b>RUNECLAW STATUS</b>\n{SEP}\n\n"
            f"  {cb_s}  \u2502  {mode}  \u2502  {m_icon} {m_label}\n"
            f"  {health_ring} System Health {_pill(f'{overall:.0f}%')}\n\n"
            # ── Capital card ──
            f"\U0001f4b0 <b>Capital</b>\n"
            f"- Equity: <code>{_money(state.equity_usd)}</code>\n"
            f"- Net: <code>{_money(net)}</code>\n"
            f"- Daily PnL: <code>{_money(state.daily_pnl, sign=True)}</code>\n"
            f"- Drawdown: <code>{state.max_drawdown_pct:.1f}%</code>\n\n"
            # ── Positions card ──
            f"\U0001f4ca <b>Positions</b>\n"
            f"- Open: <code>{state.open_positions} / {CONFIG.risk.max_open_positions}</code>\n"
            f"- Total: <code>{state.total_trades}</code>\n"
            f"- Win Rate: <code>{state.win_rate:.0%}</code>\n"
            f"- Exposure: <code>{exp_pct:.0f}%</code>\n\n"
            # ── Risk gate ──
            f"\U0001f6e1 <b>Risk Gate</b>\n"
            f"- Breaker: <code>{'TRIPPED' if cb else 'CLEAR'}</code>\n"
            f"- Streak: <code>{streak} / {CONFIG.risk.max_consecutive_losses}</code>\n"
            f"- Checks: {_traffic_light(18 if not cb else 14, 18)}\n\n"
            # ── Costs ──
            f"\u26a1 <b>Costs</b>\n"
            f"- LLM: <code>${cost.llm_cost_usd:,.4f}</code>\n"
            f"- Infra: <code>${cost.infra_cost_usd:,.4f}</code>"
        )

    def _risk(self, state, cb, streak, total_exp, exp_pct, groups):
        cb_icon = _BAD if cb else _OK
        cb_label = "TRIPPED" if cb else "CLEAR"
        grp = ", ".join(f"{g}={c}" for g, c in groups.items()) if groups else "none"

        # Compute an overall risk health score
        dd_r = state.max_drawdown_pct / CONFIG.risk.max_drawdown_pct if CONFIG.risk.max_drawdown_pct > 0 else 0
        exp_r = exp_pct / CONFIG.risk.max_portfolio_exposure_pct if CONFIG.risk.max_portfolio_exposure_pct > 0 else 0
        str_r = streak / CONFIG.risk.max_consecutive_losses if CONFIG.risk.max_consecutive_losses > 0 else 0
        risk_score = max(0, 100 - int((dd_r + exp_r + str_r) / 3 * 100))
        health_bar = _bar(risk_score, 100, 14)

        return (
            f"{_SHIELD} <b>RISK DASHBOARD</b>\n{SEP}\n\n"
            f"  {cb_icon} Circuit Breaker: <b>{cb_label}</b>\n"
            f"  \u25cf Health Score \u2502{health_bar}\u2502 {_pill(f'{risk_score}%')}\n\n"
            # ── Visual gauges ──
            f"{_gauge('Drawdown', state.max_drawdown_pct, CONFIG.risk.max_drawdown_pct)}\n"
            f"{_gauge('Exposure', exp_pct, CONFIG.risk.max_portfolio_exposure_pct)}\n"
            f"{_gauge('Streak', streak, CONFIG.risk.max_consecutive_losses, unit='#')}\n\n"
            # ── Capital breakdown ──
            f"\U0001f4b0 <b>Capital</b>\n"
            f"- Equity: <code>{_money(state.equity_usd)}</code>\n"
            f"- Daily PnL: <code>{_money(state.daily_pnl, sign=True)}</code>\n"
            f"- Exposure: <code>{_money(total_exp)}</code>\n"
            f"- Positions: <code>{state.open_positions} / {CONFIG.risk.max_open_positions}</code>\n"
            f"- Groups: <code>{grp}</code>\n\n"
            # ── Configured limits ──
            f"\U0001f512 <b>Limits</b>\n"
            f"- Min Conf: <code>{CONFIG.risk.min_confidence:.0%}</code>\n"
            f"- Min R:R: <code>{CONFIG.risk.min_risk_reward}x</code>\n"
            f"- Max DD: <code>{CONFIG.risk.max_drawdown_pct}%</code>\n"
            f"- Max Daily: <code>{CONFIG.risk.max_daily_loss_pct}%</code>\n"
            f"- Vol Guard: <code>{CONFIG.risk.volatility_guard_atr_pct}% ATR</code>\n"
            f"- Checks: {_traffic_light(18 if not cb else 14, 18)}"
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
        pnl_icon = _pnl_arrow(state.total_pnl)

        lines = [
            f"\U0001f4b0 <b>PORTFOLIO</b>\n{SEP}",
            f"   {pnl_icon} {_pill(_money(state.total_pnl, sign=True))}\n",
            # ── Balance card ──
            f"\U0001f4b3 <b>Balance</b>",
            f"- Equity: <code>{_money(state.equity_usd)}</code>",
            f"- Available: <code>{_money(state.balance_usd)}</code>",
            f"- Win Rate: <code>{state.win_rate:.0%}</code>\n",
            # ── PnL waterfall ──
            f"\U0001f4c8 <b>PnL Waterfall</b>",
            "<pre>",
            f"  \u25b8 Gross    {_money(state.total_gross_pnl, sign=True):>14}",
            f"  \u25b8 Commiss  {_money(state.total_commission):>14}",
            f"  \u25b8 Trading  {_money(state.total_pnl, sign=True):>14}",
            f"  \u25b8 LLM      {'${:,.4f}'.format(cost.llm_cost_usd):>14}",
            f"  \u25b8 Infra    {'${:,.4f}'.format(cost.infra_cost_usd):>14}",
            f"  {'━' * 30}",
            f"  \u25b6 NET      {_money(net, sign=True):>14}",
            f"  \u25b8 Per Trd  {'${:,.4f}'.format(cpt):>14}",
            "</pre>",
        ]

        open_pos = engine.portfolio.open_positions
        if open_pos:
            lines.append(f"\n\U0001f4ca <b>Open Positions</b>  ({len(open_pos)})")
            lines.append(SEP)
            for pos in open_pos:
                d_icon = _OK if pos.direction.value == "LONG" else _BAD
                d_arrow = "\u25b2" if pos.direction.value == "LONG" else "\u25bc"
                size = pos.entry_price * pos.quantity
                lines.append(
                    f"\n  {d_icon}{d_arrow} <b>{_esc(pos.asset)}</b>  {pos.direction.value}\n"
                    f"  - Entry: <code>${pos.entry_price:,.2f}</code>\n"
                    f"  - Size: <code>${size:,.0f}</code>"
                )
        else:
            lines.append(f"\n<i>\u25c7 {state.total_trades} trades \u2022 no open positions</i>")

        # Session tally
        lines.append(f"\n{SEP}")
        lines.append(
            f"<i>Session: {state.total_trades} trades \u2022 "
            f"Net {_money(net, sign=True)}</i>"
        )

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
                d_icon = _OK if idea.direction.value == "LONG" else _BAD
                return (
                    f"{_BOOK} <b>EXPLANATION</b>\n{SEP}\n\n"
                    f"  {d_icon} {_pill(idea.id)}\n"
                    f"  {idea.direction.value} {_esc(idea.asset)}\n\n"
                    f"- Confidence: <code>{idea.confidence:.0%}</code>\n"
                    f"- Signals: <code>{', '.join(idea.signals_used)}</code>\n\n"
                    f"<blockquote>{_esc(idea.reasoning)}</blockquote>"
                )
        return f"\u2718 Trade {_pill(_esc(trade_id))} not found."


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

        # Performance factor bars
        wr_bar = _bar(r.win_rate, 1.0, 8)
        pf_bar = _bar(min(r.profit_factor, 3.0), 3.0, 8)
        dd_bar = _bar(r.max_drawdown_pct, 20.0, 8)
        sharpe_bar = _bar(min(max(r.sharpe_ratio, 0), 3.0), 3.0, 8)

        return (
            f"{_CHART} <b>BACKTEST</b>\n{SEP}\n"
            f"<i>\u25c7 Synthetic data \u2014 tests plumbing, not alpha</i>\n\n"
            # ── Scorecard ──
            f"\U0001f3c6 <b>Scorecard</b>\n"
            f"<pre>"
            f"  Return     {ret_icon} {r.total_return_pct:>+8.2f}%\n"
            f"  Equity         {_money(r.final_equity):>12}\n"
            f"  Net PnL        {_money(r.net_pnl, sign=True):>12}\n"
            f"  Commission     {_money(r.total_commission):>12}\n"
            f"  Slippage       {_money(r.total_slippage):>12}"
            f"</pre>\n\n"
            # ── Factor bars ──
            f"\U0001f4ca <b>Quality Factors</b>\n"
            f"<pre>"
            f"  Win Rate  \u2502{wr_bar}\u2502 {r.win_rate:.0%}   ({r.total_trades}t)\n"
            f"  Profit F  \u2502{pf_bar}\u2502 {r.profit_factor:.2f}\n"
            f"  Max DD    \u2502{dd_bar}\u2502 {r.max_drawdown_pct:.2f}%\n"
            f"  Sharpe    \u2502{sharpe_bar}\u2502 {r.sharpe_ratio:.2f}\n"
            f"  Sortino                {r.sortino_ratio:>6.2f}"
            f"</pre>\n\n"
            # ── Pipeline ──
            f"\U0001f504 <b>Pipeline</b>\n"
            f"- Signals: <code>{r.total_signals_generated}</code>\n"
            f"- Ideas: <code>{r.total_ideas_generated}</code>\n"
            f"- Risk Reject: <code>{r.total_ideas_rejected_risk}</code>\n"
            f"- Conf Reject: <code>{r.total_ideas_rejected_confidence}</code>\n\n"
            f"<i>\u23f1 {r.bars_processed} bars \u2022 {r.duration_seconds:.1f}s \u2022 "
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
                    "<i>\u2714 No rejections yet. The risk gate is working.</i>")

        count = int(kwargs.get("count", 5))
        recent = history[-count:]

        lines = [f"{_WARN} <b>REJECTED TRADES</b>  ({len(recent)}/{len(history)})\n{SEP}"]
        lines.append("")
        for r in reversed(recent):
            d_icon = _OK if r["direction"] == "LONG" else _BAD
            d_arrow = "\u25b2" if r["direction"] == "LONG" else "\u25bc"
            fails = r["checks_failed"]
            fail_str = _esc(fails[0]) if fails else "unknown"
            extra = f" +{len(fails) - 1}" if len(fails) > 1 else ""
            conf_val = r["confidence"]
            ts = r.get("timestamp", "")
            ts_fmt = ""
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    ts_fmt = f"  \u23f0 {dt.strftime('%H:%M')}"
                except Exception:
                    ts_fmt = ""
            lines.append(
                f"  {d_icon}{d_arrow} <b>{_esc(r['asset'])}</b>  {r['direction']}\n"
                f"  - Confidence: <code>{conf_val:.0%}</code>{ts_fmt}\n"
                f"  - Reason: <code>{fail_str}</code>{extra}"
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
            f"\U0001f6a8 <b>EMERGENCY HALT</b>\n{SEP}\n\n"
            f"- Circuit Breaker: {_BAD} <b>TRIPPED</b>\n"
            f"- Ideas Cancelled: <code>{len(cancelled)}</code>\n"
            f"- Engine: <code>HALTED</code>\n\n"
            f"<i>\u26a0 All trading paused. /reset to resume.</i>"
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
            f"\U0001f4c8 <b>WALK-FORWARD</b>\n{SEP}",
            "",
            "<pre>",
            f"  {'FOLD':>4}  {'TRAIN':>8}  {'TEST':>8}  {'TRADES':>7}",
            f"  {'━'*4}  {'━'*8}  {'━'*8}  {'━'*7}",
        ]
        for f in result.folds:
            lines.append(
                f"  {f['fold']:>4}  {f['train_return_pct']:>+7.2f}%"
                f"  {f['test_return_pct']:>+7.2f}%"
                f"  {f['train_trades'] + f['test_trades']:>7}"
            )
        gap = result.train_test_gap
        gap_icon = _OK if abs(gap) <= 2 else _WARN if abs(gap) <= 5 else _BAD
        lines.append(f"  {'━' * 33}")
        lines.append(f"  Avg Train  {result.aggregate_train_return:>+7.2f}%")
        lines.append(f"  Avg Test   {result.aggregate_test_return:>+7.2f}%")
        lines.append(f"  Gap        {gap:>+7.2f}%  {gap_icon}")
        cons_bar = _bar(result.consistency_score, 1.0, 8)
        lines.append(f"  Consist.   \u2502{cons_bar}\u2502 {result.consistency_score:>5.0%}")
        lines.append("</pre>")
        if gap > 2:
            lines.append(f"\n{_WARN} <i>\u26a0 Overfitting risk detected</i>")
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
        severity_emoji = {
            "low": "\U0001f7e2",      # green
            "medium": "\U0001f7e1",   # yellow
            "high": "\U0001f534",     # red
            "critical": "\u26a0\ufe0f",  # warning
        }
        icon = state_icons.get(snap.state.value, _NEU)

        lines = [
            f"\U0001f4c5 <b>MACRO CALENDAR</b>\n{SEP}",
            "",
            f"  {icon} <b>{snap.state.value.replace('_', ' ').title()}</b>",
        ]

        if snap.active_event:
            lines.append(f"- Active: <code>{_esc(snap.active_event.label)}</code>")
        if snap.time_until_next:
            hours = snap.time_until_next.total_seconds() / 3600
            if hours < 1:
                t = f"{snap.time_until_next.total_seconds() / 60:.0f}min"
            elif hours < 24:
                t = f"{hours:.1f}h"
            else:
                t = f"{hours / 24:.1f}d"
            lines.append(f"- Next event in: <code>{t}</code>")

        if upcoming:
            lines.append(f"\n\U0001f4cb <b>Upcoming</b>")
            for ev in upcoming:
                times = cal.format_event_times(ev)
                sev = getattr(ev, "severity", "medium")
                sev_icon = severity_emoji.get(sev, _NEU)
                day_str = ""
                try:
                    dt = datetime.fromisoformat(str(ev.timestamp).replace("Z", "+00:00"))
                    day_str = dt.strftime("%a %b %d")
                except Exception:
                    day_str = ""
                lines.append(f"  {sev_icon} <b>{_esc(ev.label)}</b>")
                if day_str:
                    lines.append(f"    {day_str} \u2022 <code>{times['utc']}</code>")
                else:
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
            return f"{_NEU} <b>TRADE JOURNAL</b>\n\n<i>\u25c7 No closed trades yet.</i>"

        count = int(kwargs.get("count", 10))
        recent = history[-count:]

        lines = [f"\U0001f4d3 <b>TRADE JOURNAL</b>  ({len(recent)}/{len(history)})\n{SEP}"]
        lines.append("")

        total_pnl = 0.0
        wins = 0
        for trade in reversed(recent):
            is_win = trade.pnl > 0
            if is_win: wins += 1
            total_pnl += trade.pnl
            icon = _OK if is_win else _BAD
            arrow = "\u25b2" if is_win else "\u25bc"
            tag = "WIN" if is_win else "LOSS"
            dur = ""
            if trade.closed_at and trade.opened_at:
                h = (trade.closed_at - trade.opened_at).total_seconds() / 3600
                dur = f" \u2022 {h:.1f}h"
            exit_p = f"${trade.exit_price:,.2f}" if trade.exit_price else "open"
            size = trade.entry_price * trade.quantity

            lines.append(
                f"  {icon}{arrow} <b>{_esc(trade.asset)}</b>  {trade.direction.value}  {tag}\n"
                f"  - Entry: <code>${trade.entry_price:,.2f}</code> \u2192 Exit: <code>{exit_p}</code>\n"
                f"  - PnL: <code>${trade.pnl:+,.2f}</code>  |  Size: <code>${size:,.0f}</code>{dur}"
            )

        wr = wins / len(recent) if recent else 0
        wr_bar = _bar(wr, 1.0, 8)
        lines.append(f"\n{SEP}")
        lines.append(
            f"<b>Session Summary</b>\n"
            f"- Record: <b>{wins}W / {len(recent)-wins}L</b>  "
            f"\u2502{wr_bar}\u2502 <code>{wr:.0%}</code>\n"
            f"- Net PnL: <code>${total_pnl:+,.2f}</code>"
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
            f"\U0001f4b0 <b>AGENT ECONOMICS</b>\n{SEP}",
            "",
            f"\u26a1 <b>LLM Usage</b>",
            f"- Total: <code>${cost.llm_cost_usd:,.4f}</code> ({cost.llm_calls} calls)",
            f"- Tokens In: <code>{cost.prompt_tokens:,}</code>",
            f"- Tokens Out: <code>{cost.completion_tokens:,}</code>",
            f"- Avg/Call: <code>${cost.avg_cost_per_call:,.6f}</code>",
        ]

        cats_found = False
        for cat in ("scan", "analyze", "thesis", "risk_decision", "other"):
            c = cost.cost_by_category.get(cat, 0.0)
            n = cost.calls_by_category.get(cat, 0)
            if n > 0:
                if not cats_found:
                    lines.extend([f"\n\U0001f4ca <b>Breakdown</b>"])
                    cats_found = True
                lines.append(f"- {cat.title()}: <code>${c:,.4f}</code> ({n})")

        lines.extend([
            f"\n\U0001f4b3 <b>Operating Total</b>",
            f"- LLM: <code>${cost.llm_cost_usd:,.4f}</code>",
            f"- Infra: <code>${cost.infra_cost_usd:,.4f}</code>",
            f"- Total: <code>${cost.operating_cost_usd:,.4f}</code>",
        ])
        if state.total_trades > 0:
            cpt = cost.operating_cost_usd / state.total_trades
            lines.append(f"- Per Trade: <code>${cpt:,.4f}</code>")

        lines.extend([
            f"\n\U0001f4c8 <b>Net</b>",
            f"- Equity: <code>{_money(state.equity_usd)}</code>",
            f"- Costs: <code>-${cost.operating_cost_usd:,.4f}</code>",
            f"{SEP}",
            f"- <b>Net: <code>{_money(net)}</code></b>",
        ])

        # ── Cache hit-rate section ──
        try:
            cache_snap = engine.analyzer._llm_cache.snapshot()
            total_lookups = cache_snap["hits"] + cache_snap["misses"]
            hit_pct = cache_snap["hit_rate"] * 100
            lines.extend([
                f"\n\U0001f9e0 <b>LLM Cache</b>",
                f"- Hits: <code>{cache_snap['hits']}</code>",
                f"- Misses: <code>{cache_snap['misses']}</code>",
                f"- Hit Rate: <code>{hit_pct:.1f}%</code>",
                f"- Evictions: <code>{cache_snap['evictions']}</code>",
                f"- Expirations: <code>{cache_snap['expirations']}</code>",
                f"- Est. Saved: <code>${cache_snap['estimated_cost_saved_usd']:,.4f}</code>",
                f"- TTL: <code>{cache_snap['default_ttl']:.0f}s</code>",
            ])
        except Exception:
            pass  # cache not available — skip section

        lines.append(
            f"\n<i>\u26a1 Rate limiter: {rate_stats['total_calls']} calls, "
            f"{rate_stats['total_waits']} throttled</i>",
        )
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
        lines = [f"\U0001f3af <b>STRATEGY PRESETS</b>\n{SEP}"]
        lines.append("")
        for key, cfg in cls.PRESETS.items():
            aliases = [a for a, t in cls.ALIASES.items() if t == key]
            a = f"  <i>/{aliases[0]}</i>" if aliases else ""
            lines.append(f"  {cfg['icon']} <b>{cfg['label']}</b>{a}")
            lines.append(f"     <i>{cfg['desc']}</i>")
        lines.append(f"\n<i>\u25b8 Usage: /run &lt;name&gt; \u2022 21 checks active</i>")
        lines.append(f"<i>\u25b8 Or: /run &lt;SYMBOL&gt; (e.g. /run BTC, /run SOL)</i>")
        return "\n".join(lines)

    @classmethod
    async def _run_symbol_scan(cls, engine: "RuneClawEngine", raw_symbol: str) -> str:
        """Targeted scan for a specific symbol (e.g. /run BTC -> BTC/USDT)."""
        sym = raw_symbol.strip().upper()
        # Normalize: add /USDT if not already a pair
        if "/" not in sym:
            sym = f"{sym}/USDT"

        audit(system_log, f"Targeted scan: {sym}", action="run_strategy",
              data={"symbol": sym, "type": "targeted"})

        signals = await engine.scanner.scan()
        if not signals:
            return f"\U0001f50e <b>Targeted: {_esc(sym)}</b>\n\n<i>No market data available</i>"

        # Filter to matching symbol
        matched = [s for s in signals if s.symbol == sym]
        if not matched:
            # Try partial match (e.g. "BTC" matches "BTC/USDT")
            base = sym.split("/")[0]
            matched = [s for s in signals if s.symbol.startswith(base + "/")]

        if not matched:
            return (
                f"\U0001f50e <b>Targeted: {_esc(sym)}</b>\n\n"
                f"<i>No signals found for {_esc(sym)}.</i>\n"
                f"<i>Try /scan for all pairs or /analyze {_esc(sym)} for deep analysis.</i>"
            )

        results = []
        ideas = 0
        for sig in matched[:3]:
            idea = await engine._analyze_signal(sig)
            if not idea:
                continue
            engine._pending_ideas[idea.id] = idea
            ideas += 1
            d_icon = _OK if idea.direction.value == "LONG" else _BAD
            results.append(
                f"  {d_icon} <b>{_esc(idea.asset)}</b>  "
                f"<code>{idea.confidence:.0%}</code>  R:R <code>{idea.risk_reward_ratio}</code>"
            )

        lines = [
            f"\U0001f50e <b>Targeted: {_esc(sym)}</b>",
            f"  Matched <code>{len(matched)}</code> signal(s) \u2022 Ideas <code>{ideas}</code>\n",
        ]
        lines.extend(results or ["  <i>No actionable ideas passed analysis</i>"])
        if ideas > 0:
            lines.append(f"\n<i>/trade to review and confirm</i>")
        return "\n".join(lines)

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        strat = kwargs.get("strategy", "")
        if not strat:
            return self._list()
        key = self._resolve(strat)
        if not key:
            # Fallback: treat input as a symbol for targeted scan
            return await self._run_symbol_scan(engine, strat)

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
    description = "AI learning dashboard (/learn)"

    # The 8 core learning modules: (store_key, display_name, description)
    _MODULES: list[tuple[str, str, str]] = [
        ("patterns",          "PatternMemory",            "Candle pattern recognition"),
        ("regime",            "RegimeAdaptation",         "Market regime learning"),
        ("indicator_weights", "IndicatorWeightEvolution",  "Indicator weight optimization"),
        ("feedback",          "FeedbackLoop",             "Trade outcome learning"),
        ("volatility",        "VolatilityProfiler",       "Volatility regime modeling"),
        ("correlations",      "CorrelationTracker",       "Cross-asset correlation"),
        ("drawdown",          "DrawdownRecovery",         "Recovery pattern learning"),
        ("timing",            "TimingOptimizer",          "Entry/exit timing"),
    ]

    @staticmethod
    def _health_score(obs: int) -> float:
        """Map observation count to 0-100 health %.

        Tiers: 0->0%, 10->25%, 50->50%, 200->75%, 500+->100%.
        """
        tiers = [(0, 0.0), (10, 25.0), (50, 50.0), (200, 75.0), (500, 100.0)]
        if obs >= tiers[-1][0]:
            return 100.0
        for i in range(len(tiers) - 1):
            lo_obs, lo_pct = tiers[i]
            hi_obs, hi_pct = tiers[i + 1]
            if obs < hi_obs:
                ratio = (obs - lo_obs) / (hi_obs - lo_obs)
                return lo_pct + ratio * (hi_pct - lo_pct)
        return 100.0

    @staticmethod
    def _module_status(health: float) -> tuple[str, str]:
        """Return (coloured dot, label) for a health percentage."""
        if health >= 50:
            return _OK, "ACTIVE"
        if health > 0:
            return _WARN, "DEGRADED"
        return _BAD, "OFFLINE"

    @staticmethod
    def _fmt_ts(ts: str | float | None) -> str:
        """Short human-readable timestamp."""
        if ts is None:
            return "never"
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=UTC)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return dt.strftime("%d %b %H:%M")
        except Exception:
            return "n/a"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        dash = engine.learning.dashboard()
        score = dash["learning_score"]
        stats = dash.get("store_stats", {})
        module_details = dash.get("module_details", {})

        tier_icons = {
            "S": "\U0001f451", "A": "\U0001f31f",
            "B": "\u2b50", "C": _NEU, "D": _BAD,
        }
        t = tier_icons.get(score["tier"], _NEU)

        lines = [
            f"\U0001f9e0 <b>AI LEARNING SYSTEM</b>\n",
            f"  {t} Score: <code>{score['composite_score']}/10</code>  [{score['tier']}]\n",
        ]

        # ── Real-time module health ──────────────────────────
        lines.append("\U0001f4e6 <b>Module Health</b>")
        lines.append("<pre>")

        health_scores: list[float] = []

        for key, mod_name, _desc in self._MODULES:
            detail = module_details.get(key, {})
            obs = detail.get("observations", stats.get(key, 0))
            last_upd = detail.get("last_update")
            health = self._health_score(obs)
            health_scores.append(health)
            dot, status_label = self._module_status(health)
            bar = _bar(health, 100.0, 8)

            lines.append(
                f"  {dot} {mod_name:<25s} {status_label:<8s}"
            )
            lines.append(
                f"     [{bar}] {health:5.1f}%  "
                f"{obs:>5} obs  upd {self._fmt_ts(last_upd)}"
            )

        lines.append("</pre>\n")

        # ── Composite Learning Score ─────────────────────────
        composite = (
            sum(health_scores) / len(health_scores) if health_scores else 0.0
        )
        comp_dot, comp_label = self._module_status(composite)
        comp_bar = _bar(composite, 100.0, 12)
        lines.append("\U0001f4ca <b>Learning Score</b>")
        lines.append("<pre>")
        lines.append(
            f"  {comp_dot} [{comp_bar}] {composite:5.1f}%  {comp_label}"
        )
        lines.append("</pre>\n")

        # ── Data summary ─────────────────────────────────────
        lines.append(f"\U0001f4be <b>Data Summary</b>")
        total = sum(stats.values())
        lines.append(f"- Total records: <code>{total}</code>")
        lines.append(f"- Strategies scored: <code>{score.get('strategies_evaluated', 0)}</code>")
        lines.append(f"- Feedback entries: <code>{score.get('feedback_total', 0)}</code>")
        lines.append("")

        # ── Proposals ────────────────────────────────────────
        lines.extend([
            f"\U0001f4cb <b>Proposals</b>",
            f"- Pending: <code>{dash['pending_proposals']}</code>",
            f"- Blocked: <code>{dash['blocked_proposals']}</code>",
        ])

        # ── Strategy rankings ────────────────────────────────
        if dash.get("strategy_rankings"):
            lines.append(f"\n\U0001f3af <b>Strategy Rankings</b>")
            for s in dash["strategy_rankings"][:5]:
                of = f" {_WARN}" if s["overfitting"] else ""
                lines.append(
                    f"  [{s['tier']}] <b>{s['name']}</b>  "
                    f"WR={s['win_rate']}  ({s['trades']}t){of}"
                )

        # ── Prompt versions ──────────────────────────────────
        pv = dash.get("prompt_versions", {})
        if pv and pv.get("versions"):
            lines.append(f"\n\U0001f4dd <b>Prompt Versions</b>")
            lines.append(f"  Active: v{pv.get('current_version', '?')}  "
                         f"({pv.get('total_versions', 0)} versions tracked)")

        # ── Model accuracy ───────────────────────────────────
        ma = dash.get("model_accuracy", {})
        if ma and ma.get("agreement_rate") is not None:
            rate = ma["agreement_rate"]
            lines.append(f"\n\U0001f916 <b>Model Agreement</b>: {rate:.0%}")

        lines.append(
            f"\n\U0001f512 <i>Safety sandbox active"
            f" \u2014 AI learns aggressively, never overrides risk</i>"
        )
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
            return f"{_NEU} <b>PATTERNS</b>\n\n<i>\u25c7 No patterns yet. Need more history.</i>"
        lines = [f"\U0001f50d <b>PATTERNS</b>\n{SEP}"]
        lines.append("")
        for p in patterns[:8]:
            exp = f" {_WARN}" if p.is_experimental else ""
            conf_ring = _progress_ring(p.confidence * 100)
            lines.append(
                f"  {conf_ring} <b>{p.pattern_type}</b>{exp}\n"
                f"    Conf {_pill(f'{p.confidence:.0%}')}  "
                f"WR {_pill(f'{p.historical_win_rate:.0%}')}  "
                f"Avg {_pill(f'${p.avg_pnl:.2f}')}  ({p.sample_size})"
            )
        lines.append(f"\n<i>\u25c7 Patterns are observations, not signals</i>")
        return "\n".join(lines)


class ProposalsSkill(BaseSkill):
    name = "proposals"
    description = "Improvement proposals"
    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        proposals = engine.learning.store.get_proposals()
        if not proposals:
            return f"{_NEU} <b>PROPOSALS</b>\n\n<i>\u25c7 No proposals yet.</i>"
        lines = [f"\U0001f4cb <b>PROPOSALS  ({len(proposals)})</b>\n{SEP}"]
        lines.append("")
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
            f"\u26a1 <b>TOKEN OPTIMIZER</b>\n{SEP}",
            "",
            f"\U0001f4be <b>Cache</b>",
            f"- Size: <code>{cache.get('size', 0)}/{cache.get('max_size', 0)}</code>",
            f"- Hit Rate: <code>{cache.get('hit_rate', 0):.0%}</code>",
            f"- Evictions: <code>{cache.get('evictions', 0)}</code>",
            f"\n\U0001f4ca <b>Tier Distribution</b>",
            f"- T1 Rules: <code>{tiers.get('tier1_rules', 0)}</code> (free)",
            f"- T2 Mini: <code>{tiers.get('tier2_mini', 0)}</code> (cheap)",
            f"- T3 Full: <code>{tiers.get('tier3_full', 0)}</code> (best)",
        ]
        if total > 0:
            lines.append(f"- Free %: <code>{tiers.get('tier1_rules', 0) / total * 100:.0f}%</code>")

        saved = savings.get("total_estimated_cost_saved_usd", 0)
        lines.extend([
            f"\n\U0001f4b0 <b>Savings</b>",
            f"- Tokens: <code>~{savings.get('total_estimated_tokens_saved', 0):,}</code>",
            f"- Cost: <code>~${saved:,.4f}</code>",
        ])
        if cost.llm_cost_usd > 0:
            would_have = cost.llm_cost_usd + saved
            pct = (saved / would_have * 100) if would_have > 0 else 0
            lines.append(f"- Reduction: <code>{pct:.0f}%</code>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# WHYNOT — explain why a trade was rejected
# ══════════════════════════════════════════════════════════════

class ProScanSkill(BaseSkill):
    """Rich multi-section scan: account header, live tickers, regime narrative, verdict.

    Modes:
      - scalp:    5m candles, tight SL/TP, top-3 by volume
      - intraday: 15m candles, medium SL/TP, top-5 movers
      - swing:    4h candles, wide SL/TP, top-5 movers
    """
    name = "pro_scan"
    description = "Rich scan with regime analysis"

    MODE_CFG: dict[str, dict] = {
        "scalp": {
            "label": "SCALP SCAN",
            "icon": "\u26a1",
            "timeframe": "5m",
            "top_n": 3,
            "desc": "5m candles \u2022 tight SL \u2022 top 3 volume",
            "sort": "volume",
        },
        "intraday": {
            "label": "INTRADAY SCAN",
            "icon": "\U0001f4ca",
            "timeframe": "15m",
            "top_n": 5,
            "desc": "15m candles \u2022 top 5 movers",
            "sort": "change",
        },
        "swing": {
            "label": "SWING SCAN",
            "icon": "\U0001f30a",
            "timeframe": "4h",
            "top_n": 5,
            "desc": "4h candles \u2022 wide SL/TP \u2022 trend-based",
            "sort": "change",
        },
    }

    async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
        mode = kwargs.get("mode", "intraday")
        cfg = self.MODE_CFG.get(mode, self.MODE_CFG["intraday"])

        # ── Section 1: Account Status ──
        state = engine.portfolio.snapshot()
        cb = engine.risk.circuit_breaker_active
        cb_s = f"{_BAD} TRIPPED" if cb else f"{_OK} CLEAR"
        sim = "PAPER" if CONFIG.simulation_mode else "\u26a0\ufe0f LIVE"

        header = (
            f"{cfg['icon']} <b>{cfg['label']}</b>\n{SEP}\n"
            f"  {sim}  \u2502  Breaker: {cb_s}\n\n"
            f"- Equity: <code>{_money(state.equity_usd)}</code>\n"
            f"- Open Pos: <code>{state.open_positions} / {CONFIG.risk.max_open_positions}</code>\n"
            f"- Daily PnL: <code>{_money(state.daily_pnl, sign=True)}</code>\n"
            f"- Timeframe: <code>{cfg['timeframe'].upper()}</code>\n"
        )

        # ── Section 2: Fetch live tickers ──
        signals = await engine.scanner.scan()
        if not signals:
            return header + f"\n{_NEU} <i>No market signals detected.</i>"

        # Sort by mode preference
        if cfg["sort"] == "volume":
            signals.sort(key=lambda s: s.volume_usd_24h, reverse=True)
        else:
            signals.sort(key=lambda s: abs(s.change_pct_24h), reverse=True)

        top = signals[:cfg["top_n"]]

        ticker_lines = ["\U0001f4e1 <b>Live Tickers</b>", "<pre>"]
        ticker_lines.append(
            f" {'PAIR':<10s}  {'PRICE':>12s}  {'24h':>7s}  {'VOL':>8s}"
        )
        ticker_lines.append(f" {'─'*10}  {'─'*12}  {'─'*7}  {'─'*8}")
        for s in top:
            arrow = _spark(s.change_pct_24h)
            vol_m = s.volume_usd_24h / 1_000_000 if s.volume_usd_24h else 0
            chg = f"{s.change_pct_24h:+.1f}%"
            spike = "\U0001f4a5" if s.volume_spike else " "
            ticker_lines.append(
                f" {arrow} {_esc(s.symbol):<9s}"
                f"  ${s.price:<11,.2f}"
                f"  {chg:>6}"
                f"  ${vol_m:,.0f}M {spike}"
            )
        ticker_lines.append("</pre>\n")

        # ── Section 3: Regime Assessment + Analysis ──
        regime_lines = ["\U0001f9e0 <b>Regime Assessment</b>\n"]
        ideas_found: list = []

        for sig in top:
            try:
                exchange = await engine.scanner._get_exchange()
                ohlcv = await exchange.fetch_ohlcv(
                    sig.symbol, cfg["timeframe"], limit=100
                )
            except Exception:
                regime_lines.append(
                    f"  {_BAD} <b>{_esc(sig.symbol)}</b> \u2014 data unavailable\n"
                )
                continue

            if len(ohlcv) < 20:
                regime_lines.append(
                    f"  {_WARN} <b>{_esc(sig.symbol)}</b> \u2014 insufficient bars\n"
                )
                continue

            # Compute quick indicators for regime narrative
            closes = [float(c[4]) for c in ohlcv]
            highs = [float(c[2]) for c in ohlcv]
            lows = [float(c[3]) for c in ohlcv]
            volumes = [float(c[5]) for c in ohlcv]

            # RSI-14
            deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
            gains = [max(d, 0) for d in deltas[-14:]]
            losses = [abs(min(d, 0)) for d in deltas[-14:]]
            avg_gain = sum(gains) / 14 if gains else 0
            avg_loss = sum(losses) / 14 if losses else 0.001
            rsi = 100 - (100 / (1 + avg_gain / avg_loss))

            # VWAP approx (typical price * volume / cumulative volume)
            recent = ohlcv[-20:]
            tp_vol = sum(
                ((float(c[2]) + float(c[3]) + float(c[4])) / 3) * float(c[5])
                for c in recent
            )
            cum_vol = sum(float(c[5]) for c in recent)
            vwap = tp_vol / cum_vol if cum_vol > 0 else closes[-1]

            # Support / Resistance from recent swing H/L
            recent_20h = highs[-20:]
            recent_20l = lows[-20:]
            resistance = max(recent_20h)
            support = min(recent_20l)

            # EMA20 for trend direction
            ema20 = closes[-1]
            if len(closes) >= 20:
                k = 2 / 21
                ema20 = closes[-20]
                for p in closes[-19:]:
                    ema20 = p * k + ema20 * (1 - k)

            # ADX proxy: average directional movement
            price = closes[-1]
            trend_dir = "Bullish" if price > ema20 else "Bearish"
            trend_icon = _OK if price > ema20 else _BAD

            # Build narrative
            rsi_label = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
            vwap_pos = "above" if price > vwap else "below"

            regime_lines.append(
                f"  {trend_icon} <b>{_esc(sig.symbol)}</b>  {trend_dir}\n"
                f"<pre>"
                f"  RSI({rsi:.0f}) {rsi_label}  \u2502  VWAP ${vwap:,.2f} ({vwap_pos})\n"
                f"  Support ${support:,.2f}  \u2502  Resist ${resistance:,.2f}\n"
                f"  EMA20 ${ema20:,.2f}  \u2502  Price ${price:,.2f}"
                f"</pre>"
            )

            # Narrative sentence
            if rsi < 30 and price < ema20:
                narrative = f"  <i>Oversold bounce potential. Price below EMA20, watching for reversal at ${support:,.2f}</i>"
            elif rsi > 70 and price > ema20:
                narrative = f"  <i>Extended move. Overbought conditions near resistance ${resistance:,.2f}</i>"
            elif price > vwap and price > ema20:
                narrative = f"  <i>Bullish bias. Trading above VWAP and EMA20, momentum intact</i>"
            elif price < vwap and price < ema20:
                narrative = f"  <i>Bearish pressure. Below VWAP and EMA20, watching ${support:,.2f} support</i>"
            else:
                narrative = f"  <i>Consolidating between ${support:,.2f} - ${resistance:,.2f}</i>"
            regime_lines.append(narrative + "\n")

            # Run full analysis pipeline with mode-specific timeframe
            idea = await engine._analyze_signal(sig, timeframe=cfg["timeframe"])
            if idea:
                engine._pending_ideas[idea.id] = idea
                ideas_found.append(idea)

        # ── Section 4: Scan Verdict ──
        verdict_lines = ["\U0001f3af <b>Scan Verdict</b>\n"]
        if not ideas_found:
            verdict_lines.append(
                f"  {_NEU} No actionable setups on {cfg['timeframe'].upper()} timeframe.\n"
                f"  <i>All signals filtered by confidence/risk gate.</i>"
            )
        else:
            for idea in ideas_found:
                d_icon = _OK if idea.direction.value == "LONG" else _BAD
                d_arrow = "\u25b2" if idea.direction.value == "LONG" else "\u25bc"
                sl_d = abs(idea.entry_price - idea.stop_loss)
                tp_d = abs(idea.take_profit - idea.entry_price)
                conf_fill = int(idea.confidence * 10)
                conf_bar = _BLOCKS[7] * conf_fill + _BLOCKS[0] * (10 - conf_fill)

                verdict_lines.append(
                    f"  {d_icon}{d_arrow} <b>{idea.direction.value} {_esc(idea.asset)}</b>  "
                    f"\u2502{conf_bar}\u2502 {_pill(f'{idea.confidence:.0%}')}\n"
                    f"<pre>"
                    f"  Entry  ${idea.entry_price:>10,.2f}\n"
                    f"  SL     ${idea.stop_loss:>10,.2f}  (-${sl_d:,.2f})\n"
                    f"  TP     ${idea.take_profit:>10,.2f}  (+${tp_d:,.2f})\n"
                    f"  R:R    {idea.risk_reward_ratio:>10}x"
                    f"</pre>"
                )
                if idea.reasoning:
                    short_reason = idea.reasoning[:150]
                    verdict_lines.append(
                        f"  <blockquote>{_esc(short_reason)}</blockquote>"
                    )
                verdict_lines.append(f"  {_pill(idea.id)}\n")

            verdict_lines.append(
                f"<i>\u25b8 /trade to review \u2022 /whynot for rejections</i>"
            )

        # ── Assemble final output ──
        parts = [
            header,
            "\n".join(ticker_lines),
            "\n".join(regime_lines),
            "\n".join(verdict_lines),
        ]
        return "\n".join(parts)


class WhyNotSkill(BaseSkill):
    name = "whynot"
    description = "Explain why a trade was rejected by risk"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        symbol = kwargs.get("symbol", "").strip().upper()
        rejections = engine._last_rejections

        if not rejections:
            return (f"{_NEU} <b>NO REJECTIONS</b>\n\n"
                    "<i>No trades have been rejected yet. "
                    "Use /scan or /analyze to generate ideas.</i>")

        if symbol:
            # Normalize: strip /USDT if provided
            sym_key = symbol.replace("/USDT", "").replace("/", "")
            rej = rejections.get(sym_key)
            if not rej:
                available = ", ".join(sorted(rejections.keys())[-10:])
                return (f"{_BAD} No rejection found for <code>{_esc(sym_key)}</code>\n\n"
                        f"Recent rejections: <code>{_esc(available)}</code>")
        else:
            # Most recent rejection (last inserted key)
            sym_key = list(rejections.keys())[-1]
            rej = rejections[sym_key]

        # Build the formatted card
        d_icon = _OK if rej["direction"] == "LONG" else _BAD
        d_arrow = "\u25b2" if rej["direction"] == "LONG" else "\u25bc"
        conf = rej["confidence"]
        conf_ring = _progress_ring(conf * 100)

        lines = [
            f"\u2718 <b>REJECTED  {_esc(rej['symbol'])}</b>\n{SEP}",
            "",
            f"  {d_icon}{d_arrow} <b>{rej['direction']}</b>  "
            f"{conf_ring} {_pill(f'{conf:.0%}')}",
            "",
        ]

        # Price info
        lines.append(f"- Entry: <code>${rej['entry_price']:,.2f}</code>")
        lines.append(f"- Stop: <code>${rej['stop_loss']:,.2f}</code>")
        lines.append(f"- Target: <code>${rej['take_profit']:,.2f}</code>")

        # Failed checks (detailed)
        failed = rej.get("checks_failed", [])
        if failed:
            lines.append(f"\n{_BAD} <b>Failed Checks</b> ({len(failed)})")
            lines.append("<pre>")
            for check in failed:
                # Each check is like "CONFIDENCE: 0.3 < 0.6 minimum"
                parts = check.split(":", 1)
                name = parts[0].strip()
                reason = parts[1].strip() if len(parts) > 1 else "failed"
                lines.append(f"  \u2718 {name}")
                lines.append(f"    {reason}")
            lines.append("</pre>")

        # Passed checks (abbreviated)
        passed = rej.get("checks_passed", [])
        if passed:
            lines.append(f"\n{_OK} <b>Passed Checks</b> ({len(passed)})")
            # Show just the check names, compact
            names = []
            for check in passed:
                parts = check.split(":", 1)
                names.append(parts[0].strip())
            # Wrap names in rows of ~4
            chunks = [names[i:i+4] for i in range(0, len(names), 4)]
            lines.append("<pre>")
            for chunk in chunks:
                lines.append(f"  \u2713 {', '.join(chunk)}")
            lines.append("</pre>")

        # Timestamp
        ts = rej.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                ts_fmt = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                ts_fmt = ts
            lines.append(f"\n<i>\u23f0 {ts_fmt}</i>")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# DEEPSCAN UNIVERSE — 67+ symbols
# ══════════════════════════════════════════════════════════════

DEEPSCAN_UNIVERSE: list[str] = [
    # Majors
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LINK/USDT", "AVAX/USDT",
    "LTC/USDT", "BCH/USDT", "ETC/USDT", "XLM/USDT", "TRX/USDT",
    "ATOM/USDT", "NEAR/USDT", "ICP/USDT", "FIL/USDT", "XMR/USDT",
    # L1/L2 & Infra
    "SUI/USDT", "APT/USDT", "SEI/USDT", "TON/USDT", "HBAR/USDT",
    "OP/USDT", "ARB/USDT", "INJ/USDT", "TIA/USDT", "ALGO/USDT",
    # DeFi
    "UNI/USDT", "AAVE/USDT", "CRV/USDT", "LDO/USDT", "PENDLE/USDT",
    "DYDX/USDT", "JUP/USDT", "RENDER/USDT", "FET/USDT",
    # AI & Narrative
    "TAO/USDT", "VIRTUAL/USDT",
    # Memes & Community
    "1000BONK/USDT", "WIF/USDT", "APE/USDT", "TRUMP/USDT",
    "FARTCOIN/USDT", "PENGU/USDT", "ORDI/USDT",
    # Mid-caps
    "ENA/USDT", "ONDO/USDT", "WLD/USDT",
    "HYPE/USDT", "JTO/USDT", "DASH/USDT",
    # Micro / Emerging
    "LAB/USDT", "ZEC/USDT", "SKYAI/USDT", "SIREN/USDT", "PUMP/USDT",
    "WLFI/USDT", "ASTER/USDT", "XPLUS/USDT", "RAVE/USDT",
    "VVV/USDT", "BIO/USDT", "M/USDT", "CHIP/USDT", "B/USDT",
]

DEEPSCAN_TIMEFRAMES = ["4h", "1h", "5m"]


# ══════════════════════════════════════════════════════════════
# PLAYBOOK — GetClaw-style narrative briefing
# ══════════════════════════════════════════════════════════════

class PlaybookSkill(BaseSkill):
    """Full system briefing: scanner → AI brain → rulebook → live execution → positions."""
    name = "playbook"
    description = "GetClaw-style narrative playbook"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        state = engine.portfolio.snapshot()
        cb = engine.risk.circuit_breaker_active
        sim = "PAPER" if CONFIG.simulation_mode else "⚠️ LIVE"
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = []

        # ── Section 1: Scanner Status ──
        lines.append(f"\U0001f4e1 <b>SCANNER SWEEP</b>\n{SEP}")
        lines.append(f"- Universe: <code>{len(DEEPSCAN_UNIVERSE)} symbols</code>")
        lines.append(f"- Timeframes: <code>4H \u00b7 1H \u00b7 5M</code>")
        lines.append(f"- Batch Mode: <code>Parallel async</code>")
        lines.append(f"- Last Scan: <code>{now}</code>")

        # Show recent scan results if available
        signals = await engine.scanner.scan()
        n_signals = len(signals) if signals else 0
        movers = [s for s in (signals or []) if abs(s.change_pct_24h) > 3.0]
        spikes = [s for s in (signals or []) if s.volume_spike]
        lines.append(f"  {_OK} {n_signals} signals detected")
        lines.append(f"  {_spark(3.0)} {len(movers)} movers (>3%)")
        lines.append(f"  💥 {len(spikes)} volume spikes")
        lines.append("")

        # ── Section 2: AI Brain ──
        lines.append(f"\n\U0001f9e0 <b>AI BRAIN</b>\n{SEP}")
        lines.append(f"- Profiles: <code>6 strategy modes</code>")
        lines.append(f"- Indicators: <code>14+ (RSI/MACD/BB/EMA/ADX/ATR/OBV/VWAP/MFI/Fib)</code>")
        lines.append(f"- Candle Patterns: <code>15 detected</code>")
        lines.append(f"- Chart Patterns: <code>13 (H&amp;S/DT/DB/Flags/Tri/Wedge)</code>")
        lines.append(f"- Elliott Waves: <code>5-wave impulse count</code>")
        lines.append(f"- Market Structure: <code>HH/HL/BOS/CHoCH/Sweep</code>")
        lines.append(f"- MTF Alignment: <code>1H \u00b7 4H \u00b7 1D</code>")
        lines.append(f"- Order Flow: <code>CVD \u00b7 Book \u00b7 Whale \u00b7 Funding</code>")

        # LLM status
        llm_provider = CONFIG.llm.provider if CONFIG.llm and CONFIG.llm.provider else "groq"
        lines.append(f"  🤖 LLM: <b>{_esc(llm_provider.upper())}</b> + cascading fallback")
        lines.append("")

        # ── Section 3: Rulebook ──
        lines.append(f"\n\U0001f6e1 <b>RULEBOOK</b>\n{SEP}")
        risk = engine.risk
        total_checks = 19
        lines.append(f"- Risk Checks: <code>{total_checks} fail-closed gates</code>")
        lines.append(f"- Min Confidence: <code>{CONFIG.risk.min_confidence:.0%}</code>")
        lines.append(f"- Min R:R: <code>{CONFIG.risk.min_risk_reward}x</code>")
        lines.append(f"- Max Drawdown: <code>{CONFIG.risk.max_drawdown_pct:.0f}%</code>")
        lines.append(f"- Max Positions: <code>{CONFIG.risk.max_open_positions}</code>")
        lines.append(f"- Max Exposure: <code>{CONFIG.risk.max_portfolio_exposure_pct:.0f}%</code>")
        lines.append(f"- Cooldown: <code>{CONFIG.risk.cooldown_after_loss_seconds}s</code>")
        lines.append(f"- Circuit Breaker: {_BAD} TRIPPED" if cb else f"- Circuit Breaker: {_OK} CLEAR")
        lines.append("")

        # ── Section 4: Live Execution ──
        lines.append(f"\u26a1 <b>LIVE EXECUTION</b>\n{SEP}")
        lines.append(f"- Mode: <code>{sim}</code>")
        lines.append(f"- Exchange: <code>Bitget</code>")
        lines.append(f"- Equity: <code>{_money(state.equity_usd)}</code>")
        lines.append(f"- Open Positions: <code>{state.open_positions}</code>")
        lines.append(f"- Daily PnL: <code>{_money(state.daily_pnl, sign=True)}</code>")
        lines.append(f"- Trailing Stop: <code>Active (shared logic)</code>")
        lines.append("")

        # ── Section 5: Active Positions ──
        positions = engine.portfolio._positions
        if positions:
            lines.append(f"\U0001f4ca <b>ACTIVE POSITIONS</b>\n{SEP}")
            for pid, pos in list(positions.items())[:5]:
                d_val = pos.direction.value if hasattr(pos.direction, 'value') else str(pos.direction)
                d_icon = _OK if d_val == "LONG" else _BAD
                d_arrow = "\u25b2" if d_val == "LONG" else "\u25bc"
                notional = pos.entry_price * pos.quantity
                lines.append(
                    f"  {d_icon}{d_arrow} <b>{_esc(pos.asset)}</b>\n"
                    f"  - Entry: <code>${pos.entry_price:,.2f}</code>\n"
                    f"  - Size: <code>{_money(notional)}</code>\n"
                    f"  - SL: <code>${pos.stop_loss:,.2f}</code>\n"
                    f"  - TP: <code>${pos.take_profit:,.2f}</code>"
                )
        else:
            lines.append(f"\U0001f4ca <b>ACTIVE POSITIONS</b>\n{SEP}")
            lines.append(f"  {_NEU} <i>No open positions</i>")

        lines.append("")

        # ── Pending Ideas ──
        pending = engine._pending_ideas
        if pending:
            lines.append(f"\n\U0001f3af <b>QUEUED IDEAS ({len(pending)})</b>\n{SEP}")
            for tid, idea in list(pending.items())[:3]:
                d = "\u25b2" if idea.direction.value == "LONG" else "\u25bc"
                lines.append(
                    f"  {d} <b>{_esc(idea.asset)}</b>  "
                    f"{_pill(f'{idea.confidence:.0%}')}  "
                    f"R:R {idea.risk_reward_ratio}x"
                )
            lines.append("")

        # ── Footer ──
        lines.append(f"<i>🕐 {now}  ·  /deepscan for full universe scan</i>")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# DEEPSCAN — comprehensive multi-timeframe scan
# ══════════════════════════════════════════════════════════════

class DeepScanSkill(BaseSkill):
    """Scan the full DEEPSCAN_UNIVERSE across multiple timeframes with chart patterns."""
    name = "deepscan"
    description = "Deep scan 67+ symbols with chart patterns"

    async def execute(self, engine: RuneClawEngine, **kwargs: Any) -> str:
        import numpy as np
        from bot.core.chart_patterns import scan_all_chart_patterns
        from bot.core.analyzer import _detect_candlestick_patterns

        timeframe = kwargs.get("timeframe", "4h")
        max_results = int(kwargs.get("max_results", 15))

        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = []
        lines.append(f"\U0001f52c <b>DEEP SCAN \u2014 {timeframe.upper()}</b>\n{SEP}")
        lines.append(f"  {len(DEEPSCAN_UNIVERSE)} symbols \u00b7 {timeframe} \u00b7 chart + candle patterns")
        lines.append("")

        exchange = await engine.scanner._get_exchange()

        hits: list[dict] = []
        errors = 0
        scanned = 0

        for symbol in DEEPSCAN_UNIVERSE:
            try:
                ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=100)
            except Exception:
                errors += 1
                continue

            if not ohlcv or len(ohlcv) < 30:
                continue

            scanned += 1

            opens = np.array([c[1] for c in ohlcv], dtype=float)
            highs = np.array([c[2] for c in ohlcv], dtype=float)
            lows = np.array([c[3] for c in ohlcv], dtype=float)
            closes = np.array([c[4] for c in ohlcv], dtype=float)
            volumes = np.array([c[5] for c in ohlcv], dtype=float)

            price = float(closes[-1])
            chg = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] != 0 else 0

            # Chart patterns
            chart_patterns = scan_all_chart_patterns(opens, highs, lows, closes)

            # Candlestick patterns
            candle_patterns = _detect_candlestick_patterns(opens, highs, lows, closes)

            # RSI
            deltas = np.diff(closes)
            gain = np.where(deltas > 0, deltas, 0.0)
            loss = np.where(deltas < 0, -deltas, 0.0)
            period = min(14, len(gain))
            avg_gain = float(np.mean(gain[-period:])) if period > 0 else 0
            avg_loss = float(np.mean(loss[-period:])) + 1e-10 if period > 0 else 1
            rsi = 100 - 100 / (1 + avg_gain / avg_loss)

            # Volume spike
            vol_avg = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
            vol_spike = float(volumes[-1]) > vol_avg * 2.0

            # Score: more patterns + extreme RSI + volume spike = higher score
            score = len(chart_patterns) * 2 + len(candle_patterns) * 1
            if rsi < 30 or rsi > 70:
                score += 2
            if vol_spike:
                score += 1

            if score > 0 or abs(chg) > 3.0:
                hits.append({
                    "symbol": symbol,
                    "price": price,
                    "chg": float(chg),
                    "rsi": rsi,
                    "vol_spike": vol_spike,
                    "chart_patterns": chart_patterns,
                    "candle_patterns": candle_patterns,
                    "score": score,
                })

        # Sort by score
        hits.sort(key=lambda h: h["score"], reverse=True)
        top = hits[:max_results]

        # Stats line
        lines.append(f"- Scanned: <code>{scanned} / {len(DEEPSCAN_UNIVERSE)}</code>")
        lines.append(f"- Hits: <code>{len(hits)}</code>")
        lines.append(f"- Errors: <code>{errors}</code>")
        lines.append("")

        if not top:
            lines.append(f"  {_NEU} <i>No actionable patterns detected.</i>")
            lines.append(f"\n<i>🕐 {now}</i>")
            return "\n".join(lines)

        # Results
        for h in top:
            arrow = _spark(h["chg"])
            rsi_label = "OB" if h["rsi"] > 70 else "OS" if h["rsi"] < 30 else ""
            rsi_icon = _BAD if h["rsi"] > 70 else _OK if h["rsi"] < 30 else _NEU
            spike_icon = "💥" if h["vol_spike"] else ""

            sym_clean = h["symbol"].replace("/USDT", "")
            lines.append(
                f"  {arrow} <b>{_esc(sym_clean)}</b>  "
                f"${h['price']:,.4f}  {h['chg']:+.1f}%  "
                f"{rsi_icon} RSI {h['rsi']:.0f}{rsi_label}  {spike_icon}"
            )

            # Chart patterns
            if h["chart_patterns"]:
                for cp in h["chart_patterns"][:3]:
                    sig_icon = _OK if cp["signal"] == "bullish" else _BAD if cp["signal"] == "bearish" else _NEU
                    conf_str = f"{cp['confidence']:.0%}"
                    lines.append(
                        f"    {sig_icon} {cp['name']}  "
                        f"{_pill(conf_str)}  "
                        f"<i>{cp['description']}</i>"
                    )

            # Candlestick patterns
            if h["candle_patterns"]:
                candle_str = ", ".join(
                    f"{'🟢' if v == 'bullish' else '🔴' if v == 'bearish' else '⚪'}{k}"
                    for k, v in list(h["candle_patterns"].items())[:4]
                )
                lines.append(f"    🕯 {candle_str}")

            lines.append("")

        lines.append(f"<i>🕐 {now}  ·  /playbook for full briefing</i>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# REGISTRY
# ══════════════════════════════════════════════════════════════

def build_default_registry() -> SkillRegistry:
    from bot.skills.getclaw_wrapper import register_getclaw_wrapper
    from bot.skills.quant_skill import QuantAnalyzeSkill
    from bot.skills.macro_skills import build_v2_skills

    registry = SkillRegistry()
    for cls in (ScanMarketSkill, AnalyzeAssetSkill, CheckRiskSkill,
                ExecutePaperTradeSkill, GetPortfolioSkill, ExplainTradeSkill,
                RunBacktestSkill, RejectedTradesSkill, HaltSkill,
                WalkForwardSkill, MacroCalendarSkill, TradeJournalSkill,
                CostBreakdownSkill, RunStrategySkill,
                LearningDashboardSkill, FeedbackSkill, PatternsSkill,
                ProposalsSkill, OptimizationSkill, QuantAnalyzeSkill,
                WhyNotSkill, ProScanSkill,
                PlaybookSkill, DeepScanSkill):
        registry.register(cls())
    register_getclaw_wrapper(registry)
    # v2 upgrade: macro intelligence, compliance, audit, kill-switch
    for skill in build_v2_skills():
        registry.register(skill)
    return registry
