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
_OK = "\U0001f7e2"        # green circle
_WARN = "\U0001f7e1"      # yellow circle
_BAD = "\U0001f534"       # red circle
_NEU = "\u26aa"           # white circle

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

def _kv(key: str, val: str, w: int = 28) -> str:
    """Key-value with dot-leader alignment for <pre> blocks."""
    dots = w - len(key) - len(val) - 4
    if dots < 2:
        dots = 2
    return f"  {key} {'·' * dots} {val}"

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

        top = signals[:8]
        lines = [
            _header("\U0001f50e", "MARKET SCANNER"),
            f"   <i>{len(signals)} pairs detected</i>\n",
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
                f"<pre>"
                f"{_kv('Price', f'${sig.price:,.2f}')}\n"
                f"{_kv('24h', f'{sig.change_pct_24h:+.1f}%')}\n"
                f"{_kv('Volume', f'${vol_m:,.0f}M')}"
                f"</pre>\n\n"
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
            f"{_header(d_icon, f'{d}  {_esc(idea.asset)}')}\n\n"
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
            f"\U0001f43e <b>RUNECLAW STATUS</b> {'━' * 18}\n\n"
            f"  {cb_s}  \u2502  {mode}  \u2502  {m_icon} {m_label}\n"
            f"  {health_ring} System Health {_pill(f'{overall:.0f}%')}\n\n"
            # ── Capital card ──
            f"\U0001f4b0 <b>Capital</b>\n"
            f"<pre>"
            f"{_kv('Equity', _money(state.equity_usd))}\n"
            f"{_kv('Net', _money(net))}\n"
            f"{_kv('Daily PnL', _money(state.daily_pnl, sign=True))}\n"
            f"{_kv('Drawdown', f'{state.max_drawdown_pct:.1f}%')}"
            f"</pre>\n\n"
            # ── Positions card ──
            f"\U0001f4ca <b>Positions</b>\n"
            f"<pre>"
            f"{_kv('Open', f'{state.open_positions} / {CONFIG.risk.max_open_positions}')}\n"
            f"{_kv('Total', str(state.total_trades))}\n"
            f"{_kv('Win Rate', f'{state.win_rate:.0%}')}\n"
            f"{_kv('Exposure', f'{exp_pct:.0f}%')}"
            f"</pre>\n\n"
            # ── Risk gate ──
            f"\U0001f6e1 <b>Risk Gate</b>\n"
            f"<pre>"
            f"{_kv('Breaker', 'TRIPPED' if cb else 'CLEAR')}\n"
            f"{_kv('Streak', f'{streak} / {CONFIG.risk.max_consecutive_losses}')}\n"
            f"{_kv('Checks', _traffic_light(18 if not cb else 14, 18))}"
            f"</pre>\n\n"
            # ── Costs ──
            f"\u26a1 <b>Costs</b>\n"
            f"<pre>"
            f"{_kv('LLM', f'${cost.llm_cost_usd:,.4f}')}\n"
            f"{_kv('Infra', f'${cost.infra_cost_usd:,.4f}')}"
            f"</pre>"
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
            f"{_header('\U0001f6e1', 'RISK DASHBOARD')}\n\n"
            f"  {cb_icon} Circuit Breaker: <b>{cb_label}</b>\n"
            f"  \u25cf Health Score \u2502{health_bar}\u2502 {_pill(f'{risk_score}%')}\n\n"
            # ── Visual gauges ──
            f"{_gauge('Drawdown', state.max_drawdown_pct, CONFIG.risk.max_drawdown_pct)}\n"
            f"{_gauge('Exposure', exp_pct, CONFIG.risk.max_portfolio_exposure_pct)}\n"
            f"{_gauge('Streak', streak, CONFIG.risk.max_consecutive_losses, unit='#')}\n\n"
            # ── Capital breakdown ──
            f"\U0001f4b0 <b>Capital</b>\n"
            f"<pre>"
            f"{_kv('Equity', _money(state.equity_usd))}\n"
            f"{_kv('Daily PnL', _money(state.daily_pnl, sign=True))}\n"
            f"{_kv('Exposure', _money(total_exp))}\n"
            f"{_kv('Positions', f'{state.open_positions} / {CONFIG.risk.max_open_positions}')}\n"
            f"{_kv('Groups', grp)}"
            f"</pre>\n\n"
            # ── Configured limits ──
            f"\U0001f512 <b>Limits</b>\n"
            f"<pre>"
            f"{_kv('Min Conf', f'{CONFIG.risk.min_confidence:.0%}')}\n"
            f"{_kv('Min R:R', f'{CONFIG.risk.min_risk_reward}x')}\n"
            f"{_kv('Max DD', f'{CONFIG.risk.max_drawdown_pct}%')}\n"
            f"{_kv('Max Daily', f'{CONFIG.risk.max_daily_loss_pct}%')}\n"
            f"{_kv('Vol Guard', f'{CONFIG.risk.volatility_guard_atr_pct}% ATR')}\n"
            f"{_kv('Checks', _traffic_light(18 if not cb else 14, 18))}"
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
        pnl_icon = _pnl_arrow(state.total_pnl)

        lines = [
            _header("\U0001f4b0", "PORTFOLIO"),
            f"   {pnl_icon} {_pill(_money(state.total_pnl, sign=True))}\n",
            # ── Balance card ──
            f"\U0001f4b3 <b>Balance</b>",
            "<pre>",
            _kv("Cash", _money(state.balance_usd)),
            _kv("Equity", _money(state.equity_usd)),
            _kv("Win Rate", f"{state.win_rate:.0%}"),
            "</pre>\n",
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
            for pos in open_pos:
                d_icon = _OK if pos.direction.value == "LONG" else _BAD
                d_arrow = "\u25b2" if pos.direction.value == "LONG" else "\u25bc"
                size = pos.entry_price * pos.quantity
                lines.append(
                    f"  {d_icon}{d_arrow} <b>{_esc(pos.asset)}</b>  "
                    f"{_pill(f'${pos.entry_price:,.2f}')}  "
                    f"size {_pill(f'${size:,.0f}')}"
                )
        else:
            lines.append(f"\n<i>\u25c7 {state.total_trades} trades \u2022 no open positions</i>")

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
                    f"{_header('\U0001f4d6', 'EXPLANATION')}\n\n"
                    f"  {d_icon} {_pill(idea.id)}\n"
                    f"  {idea.direction.value} {_esc(idea.asset)}\n\n"
                    f"<pre>"
                    f"{_kv('Confidence', f'{idea.confidence:.0%}')}\n"
                    f"{_kv('Signals', ', '.join(idea.signals_used))}"
                    f"</pre>\n\n"
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
            f"{_header('\U0001f4ca', 'BACKTEST')}\n"
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
            f"<pre>"
            f"{_kv('Signals', str(r.total_signals_generated))}\n"
            f"{_kv('Ideas', str(r.total_ideas_generated))}\n"
            f"{_kv('Risk Reject', str(r.total_ideas_rejected_risk))}\n"
            f"{_kv('Conf Reject', str(r.total_ideas_rejected_confidence))}"
            f"</pre>\n\n"
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

        lines = [_header(_WARN, f"REJECTED TRADES  ({len(recent)}/{len(history)})")]
        lines.append("")
        for r in reversed(recent):
            d_icon = _OK if r["direction"] == "LONG" else _BAD
            d_arrow = "\u25b2" if r["direction"] == "LONG" else "\u25bc"
            fails = r["checks_failed"]
            fail_str = _esc(fails[0]) if fails else "unknown"
            extra = f" +{len(fails) - 1}" if len(fails) > 1 else ""
            conf_val = r["confidence"]
            lines.append(
                f"  {d_icon}{d_arrow} <b>{_esc(r['asset'])}</b>  {r['direction']}  "
                f"{_pill(f'{conf_val:.0%}')}\n"
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
            f"\U0001f6a8 <b>EMERGENCY HALT</b> {'━' * 16}\n\n"
            f"  {_BAD} Circuit Breaker: <b>TRIPPED</b>\n"
            f"  \u2718 Ideas Cancelled: {_pill(str(len(cancelled)))}\n"
            f"  \u25cf Engine: {_pill('HALTED')}\n\n"
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
            _header("\U0001f4c8", "WALK-FORWARD"),
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
        icon = state_icons.get(snap.state.value, _NEU)

        lines = [
            _header("\U0001f4c5", "MACRO CALENDAR"),
            "",
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
            return f"{_NEU} <b>TRADE JOURNAL</b>\n\n<i>\u25c7 No closed trades yet.</i>"

        count = int(kwargs.get("count", 10))
        recent = history[-count:]

        lines = [_header("\U0001f4d3", f"TRADE JOURNAL  ({len(recent)}/{len(history)})")]
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
                f"  {icon}{arrow} <b>{_esc(trade.asset)}</b>  {trade.direction.value}\n"
                f"     {_pill(f'${trade.pnl:+,.2f}')} {tag}{dur}\n"
                f"     ${trade.entry_price:,.2f} \u2192 {exit_p}  size ${size:,.0f}"
            )

        wr = wins / len(recent) if recent else 0
        wr_bar = _bar(wr, 1.0, 8)
        lines.append(
            f"\n<b>{wins}W / {len(recent)-wins}L</b>  "
            f"\u2502{wr_bar}\u2502 {_pill(f'{wr:.0%}')}  "
            f"Net {_pill(f'${total_pnl:+,.2f}')}"
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
            _header("\U0001f4b0", "AGENT ECONOMICS"),
            "",
            f"\u26a1 <b>LLM Usage</b>",
            "<pre>",
            _kv("Total", f"${cost.llm_cost_usd:,.4f} ({cost.llm_calls} calls)"),
            _kv("Tokens In", f"{cost.prompt_tokens:,}"),
            _kv("Tokens Out", f"{cost.completion_tokens:,}"),
            _kv("Avg/Call", f"${cost.avg_cost_per_call:,.6f}"),
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
                lines.append(_kv(cat.title(), f"${c:,.4f} ({n})"))
        if cats_found:
            lines.append("</pre>")

        lines.extend([
            f"\n\U0001f4b3 <b>Operating Total</b>",
            "<pre>",
            _kv("LLM", f"${cost.llm_cost_usd:,.4f}"),
            _kv("Infra", f"${cost.infra_cost_usd:,.4f}"),
            _kv("Total", f"${cost.operating_cost_usd:,.4f}"),
        ])
        if state.total_trades > 0:
            cpt = cost.operating_cost_usd / state.total_trades
            lines.append(_kv("Per Trade", f"${cpt:,.4f}"))

        lines.extend([
            "</pre>",
            f"\n\U0001f4c8 <b>Net</b>",
            "<pre>",
            _kv("Equity", _money(state.equity_usd)),
            _kv("- Costs", f"${cost.operating_cost_usd:,.4f}"),
            f"  {'━' * 26}",
            _kv("= Net", _money(net)),
            "</pre>",
            f"\n<i>\u26a1 Rate limiter: {rate_stats['total_calls']} calls, "
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
        lines = [_header("\U0001f3af", "STRATEGY PRESETS")]
        lines.append("")
        for key, cfg in cls.PRESETS.items():
            aliases = [a for a, t in cls.ALIASES.items() if t == key]
            a = f"  <i>/{aliases[0]}</i>" if aliases else ""
            lines.append(f"  {cfg['icon']} <b>{cfg['label']}</b>{a}")
            lines.append(f"     <i>{cfg['desc']}</i>")
        lines.append(f"\n<i>\u25b8 Usage: /run &lt;name&gt; \u2022 18 checks active</i>")
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
                dt = datetime.fromisoformat(str(ts))
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
        lines.append("<pre>")
        total = sum(stats.values())
        lines.append(_row("Total records", str(total)))
        lines.append(_row("Strategies scored", str(score.get('strategies_evaluated', 0))))
        lines.append(_row("Feedback entries", str(score.get('feedback_total', 0))))
        lines.append("</pre>\n")

        # ── Proposals ────────────────────────────────────────
        lines.extend([
            f"\U0001f4cb <b>Proposals</b>",
            "<pre>",
            _row("Pending", str(dash['pending_proposals'])),
            _row("Blocked", str(dash['blocked_proposals'])),
            "</pre>",
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
        lines = [_header("\U0001f50d", "PATTERNS")]
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
        lines = [_header("\U0001f4cb", f"PROPOSALS  ({len(proposals)})")]
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
            _header("\u26a1", "TOKEN OPTIMIZER"),
            "",
            f"\U0001f4be <b>Cache</b>",
            "<pre>",
            _kv("Size", f"{cache.get('size', 0)}/{cache.get('max_size', 0)}"),
            _kv("Hit Rate", f"{cache.get('hit_rate', 0):.0%}"),
            _kv("Evictions", str(cache.get('evictions', 0))),
            "</pre>",
            f"\n\U0001f4ca <b>Tier Distribution</b>",
            "<pre>",
            _kv("T1 Rules", f"{tiers.get('tier1_rules', 0)} (free)"),
            _kv("T2 Mini", f"{tiers.get('tier2_mini', 0)} (cheap)"),
            _kv("T3 Full", f"{tiers.get('tier3_full', 0)} (best)"),
        ]
        if total > 0:
            lines.append(_kv("Free %", f"{tiers.get('tier1_rules', 0) / total * 100:.0f}%"))

        saved = savings.get("total_estimated_cost_saved_usd", 0)
        lines.extend([
            "</pre>",
            f"\n\U0001f4b0 <b>Savings</b>",
            "<pre>",
            _kv("Tokens", f"~{savings.get('total_estimated_tokens_saved', 0):,}"),
            _kv("Cost", f"~${saved:,.4f}"),
        ])
        if cost.llm_cost_usd > 0:
            would_have = cost.llm_cost_usd + saved
            pct = (saved / would_have * 100) if would_have > 0 else 0
            lines.append(_kv("Reduction", f"{pct:.0f}%"))
        lines.append("</pre>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# REGISTRY
# ══════════════════════════════════════════════════════════════

def build_default_registry() -> SkillRegistry:
    from bot.skills.getclaw_wrapper import register_getclaw_wrapper
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
    register_getclaw_wrapper(registry)
    return registry
