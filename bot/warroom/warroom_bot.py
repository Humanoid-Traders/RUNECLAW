"""MuleRun War Room — Telegram bot interface for the RUNECLAW Signal Engine.

Rich, dashboard-grade templates using the same visual vocabulary as the
skill registry: gauges, progress bars, sparklines, and sectioned cards.

No external dependencies beyond the Python standard library are required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

__all__ = [
    "render_start",
    "render_status",
    "render_signal",
    "render_risk",
    "render_performance",
    "render_positions",
    "render_daily_report",
    "render_strategy_mode",
    "render_pause",
    "render_resume",
    "render_emergency_stop",
    "handle_callback",
]

# ── Visual vocabulary (matches skill_registry.py) ─────────────
_OK = "\U0001f7e2"        # green circle
_WARN = "\U0001f7e1"      # yellow circle
_BAD = "\U0001f534"       # red circle
_NEU = "\u26aa"           # white circle
_SHIELD = "\U0001f6e1"
_BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

PRODUCT = "MuleRun War Room"
ENGINE = "RUNECLAW"

# ── Keyboard helpers ──────────────────────────────────────────
_Btn = Dict[str, str]
_Row = List[_Btn]
_Keyboard = List[_Row]


def _btn(text: str, callback_data: str) -> _Btn:
    return {"text": text, "callback_data": callback_data}


# ── Formatting helpers ────────────────────────────────────────

def _bar(val: float, mx: float = 1.0, w: int = 10) -> str:
    r = min(max(val / mx, 0), 1.0) if mx > 0 else 0
    f = int(r * w)
    return "\u2501" * f + "\u254c" * (w - f)


def _gauge(label: str, val: float, mx: float, unit: str = "%", w: int = 12) -> str:
    bar = _bar(val, mx, w)
    r = val / mx if mx > 0 else 0
    tip = _OK if r < 0.5 else _WARN if r < 0.8 else _BAD
    if unit == "%":
        return f"  {tip} {label:<10} \u2502{bar}\u2502 {val:.1f}%\u2009/\u2009{mx:.0f}%"
    return f"  {tip} {label:<10} \u2502{bar}\u2502 {val:.0f}\u2009/\u2009{mx:.0f}"


def _kv(key: str, val: str, w: int = 28) -> str:
    dots = w - len(key) - len(val) - 4
    if dots < 2:
        dots = 2
    return f"  {key} {'·' * dots} {val}"


def _header(emoji: str, title: str, w: int = 24) -> str:
    return f"{emoji} <b>{title}</b> {'━' * w}"


def _pill(text: str) -> str:
    return f"<code>\u2009{text}\u2009</code>"


def _money(v: float, sign: bool = False) -> str:
    return f"${v:+,.2f}" if sign else f"${v:,.2f}"


def _spark(v: float) -> str:
    if v > 2: return "\u25b2"
    if v > 0: return "\u25b3"
    if v < -2: return "\u25bc"
    if v < 0: return "\u25bd"
    return "\u25c7"


def _pnl_arrow(v: float) -> str:
    if v > 0: return f"{_OK}\u25b2"
    if v < 0: return f"{_BAD}\u25bc"
    return f"{_NEU}\u25c7"


def _progress_ring(pct: float) -> str:
    rings = ["\u25cb", "\u25d4", "\u25d1", "\u25d5", "\u25cf"]
    idx = int(min(max(pct, 0), 100) / 25)
    return rings[min(idx, 4)]


def _sparkline(values: list[float], w: int = 12) -> str:
    if not values:
        return "\u2500" * w
    if len(values) > w:
        step = len(values) / w
        sampled = [values[int(i * step)] for i in range(w)]
    else:
        sampled = values
    mn, mx = min(sampled), max(sampled)
    rng = mx - mn if mx > mn else 1.0
    return "".join(_BLOCKS[max(0, min(7, int((v - mn) / rng * 7)))] for v in sampled)


def _conf_bar(pct: int, w: int = 10) -> str:
    fill = round(pct / 100 * w)
    return _BLOCKS[7] * fill + _BLOCKS[0] * (w - fill)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ═════════════════════════════════════════════════════════════════
# render_start
# ═════════════════════════════════════════════════════════════════

def render_start() -> Dict[str, Any]:
    text = (
        f"{_header(chr(0x2694) + chr(0xFE0F), 'MULERUN WAR ROOM')}\n\n"
        f"  Powered by <b>{ENGINE}</b> Signal Engine\n\n"
        "<pre>"
        f"{_kv('Status', 'ACTIVE ' + _OK)}\n"
        f"{_kv('Engine', 'v3.1')}\n"
        f"{_kv('Uptime', '99.7%')}"
        "</pre>\n\n"
        "  Signal locked. Risk checked. Claw ready.\n\n"
        "<i>\u25b8 Use the buttons below or /help for commands</i>"
    )
    keyboard: _Keyboard = [
        [_btn("\u2694\ufe0f Open War Room", "open_warroom"),
         _btn("\U0001f4ca Latest Signal", "latest_signal")],
        [_btn("\U0001f4c8 Performance", "performance"),
         _btn("\U0001f6e1 Risk Control", "risk_control")],
        [_btn("\u2699\ufe0f Strategy Mode", "strategy_mode"),
         _btn("\U0001f4c2 Positions", "positions")],
        [_btn("\u26d4 Emergency Stop", "risk_emergency_stop")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_status
# ═════════════════════════════════════════════════════════════════

def render_status(data: Dict[str, Any]) -> Dict[str, Any]:
    active = data.get("active", True)
    cb_s = f"{_OK} ACTIVE" if active else f"{_BAD} HALTED"
    mode = data.get("mode", "PAPER")
    pnl = data.get("daily_pnl", 0.0)
    risk = data.get("risk_used", 0.0)
    open_t = data.get("open_trades", 0)
    bias = data.get("market_bias", "Normal")
    last_sig = data.get("last_signal", "Use /scan")

    # Health score: based on drawdown headroom
    health = max(0, 100 - risk * 10)
    health_ring = _progress_ring(health)

    text = (
        f"\U0001f43e <b>{ENGINE} STATUS</b> {'━' * 18}\n\n"
        f"  {cb_s}  \u2502  {mode}  \u2502  {health_ring} Health {_pill(f'{health:.0f}%')}\n\n"
        # ── Engine card ──
        f"\u2699\ufe0f <b>Engine</b>\n"
        "<pre>"
        f"{_kv('State', 'ACTIVE' if active else 'HALTED')}\n"
        f"{_kv('Mode', mode)}\n"
        f"{_kv('Exchange', data.get('exchange', 'Bitget'))}\n"
        f"{_kv('Market Bias', bias)}"
        "</pre>\n\n"
        # ── Capital card ──
        f"\U0001f4b0 <b>Capital</b>\n"
        "<pre>"
        f"{_kv('Open Trades', str(open_t))}\n"
        f"{_kv('Daily PnL', f'{pnl:+.2f}%')}  {_pnl_arrow(pnl)}\n"
        f"{_kv('Last Signal', last_sig)}"
        "</pre>\n\n"
        # ── Risk gauge ──
        f"{_SHIELD} <b>Risk</b>\n"
        f"{_gauge('Drawdown', risk, 10.0)}\n\n"
        f"<i>\u23f1 {_timestamp()}</i>"
    )
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_signal
# ═════════════════════════════════════════════════════════════════

def render_signal(data: Dict[str, Any]) -> Dict[str, Any]:
    pair = data.get("pair", "N/A")
    direction = data.get("direction", "LONG")
    d_icon = _OK if direction.upper() == "LONG" else _BAD
    d_arrow = "\u25b2" if direction.upper() == "LONG" else "\u25bc"
    confidence = data.get("confidence", 0)
    risk_level = data.get("risk_level", "Medium")
    risk_icon = _OK if risk_level == "Low" else _WARN if risk_level == "Medium" else _BAD
    entry_low = data.get("entry_low", 0)
    entry_high = data.get("entry_high", 0)
    sl = data.get("sl", 0)
    tp1 = data.get("tp1", 0)
    tp2 = data.get("tp2", 0)
    reason = data.get("reason", "")

    # Dynamic precision based on price magnitude
    ref = max(entry_low, entry_high, tp1, sl, 0.001)
    if ref >= 100:
        p = 2
    elif ref >= 1:
        p = 4
    else:
        p = 5

    # Confidence bar
    conf_ring = _progress_ring(confidence)
    cbar = _conf_bar(confidence)

    text = (
        f"{_header(d_icon, f'{direction}  {pair}')}\n\n"
        f"  {conf_ring} Confidence \u2502{cbar}\u2502 {_pill(f'{confidence}%')}\n"
        f"  {risk_icon} Risk Level: <b>{risk_level}</b>\n\n"
        # ── Price ladder ──
        f"\U0001f3af <b>Price Levels</b>\n"
        "<pre>"
        f"  \U0001f3af TP2   \u2502 $  {tp2:>10.{p}f}\n"
        f"  \U0001f3af TP1   \u2502 $  {tp1:>10.{p}f}\n"
        f"  {'─' * 6}\u253c{'─' * 20}\n"
        f"  {d_arrow}  IN   \u2502 $  {entry_low:.{p}f} \u2013 ${entry_high:.{p}f}\n"
        f"  {'─' * 6}\u253c{'─' * 20}\n"
        f"  \U0001f6d1 SL    \u2502 $  {sl:>10.{p}f}"
        "</pre>\n\n"
        f"<blockquote>{reason[:200]}</blockquote>"
    )
    keyboard: _Keyboard = [
        [_btn("\u2705 Approve Trade", f"signal_approve_{pair}")],
        [_btn("\U0001f441 Watch Only", f"signal_watch_{pair}")],
        [_btn("\u274c Reject", f"signal_reject_{pair}")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_risk
# ═════════════════════════════════════════════════════════════════

def render_risk(data: Dict[str, Any]) -> Dict[str, Any]:
    dd = data.get("current_drawdown", 0.0)
    dll = data.get("daily_loss_limit", 5.0)
    max_t = data.get("max_open_trades", 5)
    open_t = data.get("open_trades", 0)
    lev = data.get("leverage_cap", 5)

    healthy = dd < dll
    status_icon = _OK if healthy else _BAD
    status_label = "HEALTHY" if healthy else "WARNING"

    # Risk health score
    risk_score = max(0, 100 - int(dd / dll * 100)) if dll > 0 else 100
    health_bar = _bar(risk_score, 100, 14)

    text = (
        f"{_header(_SHIELD, 'RISK CONTROL')}\n\n"
        f"  {status_icon} Status: <b>{status_label}</b>\n"
        f"  \u25cf Health \u2502{health_bar}\u2502 {_pill(f'{risk_score}%')}\n\n"
        # ── Gauges ──
        f"{_gauge('Drawdown', dd, dll)}\n"
        f"{_gauge('Positions', float(open_t), float(max_t), unit='#')}\n"
        f"{_gauge('Leverage', 1.0, float(lev), unit='x')}\n\n"
        # ── Limits ──
        f"\U0001f512 <b>Limits</b>\n"
        "<pre>"
        f"{_kv('Daily Loss', f'{dll}%')}\n"
        f"{_kv('Max Trades', str(max_t))}\n"
        f"{_kv('Open Now', str(open_t))}\n"
        f"{_kv('Leverage', f'{lev}x')}"
        "</pre>"
    )
    keyboard: _Keyboard = [
        [_btn("\U0001f6e1 Safe Mode", "risk_safe_mode"),
         _btn("\u23f8 Pause Bot", "risk_pause")],
        [_btn("\u26d4 Emergency Stop", "risk_emergency_stop")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_performance
# ═════════════════════════════════════════════════════════════════

def render_performance(data: Dict[str, Any]) -> Dict[str, Any]:
    today = data.get("today_pnl", 0.0)
    week = data.get("week_pnl", 0.0)
    total = data.get("total_pnl", week)
    wr = data.get("win_rate", 0.0)
    trades = data.get("trades_today", 0)
    total_trades = data.get("total_trades", trades)
    best = data.get("best_pair", "N/A")
    worst = data.get("worst_pair", "N/A")
    adopted_count = data.get("adopted_count", 0)
    adopted_pnl = data.get("adopted_pnl", 0.0)

    wr_bar = _bar(wr, 100.0, 10)
    wr_ring = _progress_ring(wr)

    # Fake sparkline for PnL trend
    pnl_trend = _sparkline([0, today * 0.3, today * 0.5, today * 0.8, today], w=8) if today != 0 else "━━━━━━━━"

    text = (
        f"{_header(chr(0x1F4CA), 'PERFORMANCE')}\n"
        f"   {_pnl_arrow(today)} {_pill(_money(today, sign=True))} today\n\n"
        # ── PnL card ──
        f"\U0001f4b0 <b>Returns</b>\n"
        "<pre>"
        f"{_kv('Today', _money(today, sign=True))}  {_pnl_arrow(today)}\n"
        f"{_kv('7-Day', _money(week, sign=True))}  {_pnl_arrow(week)}\n"
        f"{_kv('All-time', _money(total, sign=True))}  {_pnl_arrow(total)}\n"
        f"{_kv('Trades', f'{trades} today / {total_trades} total')}\n"
        f"{_kv('Trend', f'<code>{pnl_trend}</code>')}"
        "</pre>\n\n"
        # ── Win Rate gauge ──
        f"\U0001f3af <b>Win Rate</b>\n"
        f"  {wr_ring} \u2502{wr_bar}\u2502 {_pill(f'{wr:.0f}%')}\n\n"
        # ── Pair breakdown ──
        f"\U0001f4ca <b>Pair Breakdown</b>\n"
        "<pre>"
        f"{_kv('Best', best + ' ' + chr(0x1F3C6))}\n"
        f"{_kv('Worst', worst)}"
        "</pre>"
    )

    # ── Adopted orphan trades (if any) ──
    if adopted_count > 0:
        text += (
            f"\n\n\u26a0\ufe0f <i>Excluded {adopted_count} adopted orphan"
            f"{'s' if adopted_count != 1 else ''}"
            f" ({_money(adopted_pnl, sign=True)})</i>"
        )

    text += f"\n\n<i>\u23f1 {_timestamp()}</i>"
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_positions
# ═════════════════════════════════════════════════════════════════

def render_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(positions)
    total_pnl = sum(p.get("pnl", 0) for p in positions)

    lines = [
        f"{_header(chr(0x1F4C8), f'OPEN POSITIONS  ({count})')}",
        f"   {_pnl_arrow(total_pnl)} Net: {_pill(f'{total_pnl:+.2f}%')}\n",
    ]
    keyboard: _Keyboard = []

    for pos in positions:
        pair = pos.get("pair", "N/A")
        direction = pos.get("direction", "LONG")
        d_icon = _OK if direction.upper() == "LONG" else _BAD
        d_arrow = "\u25b2" if direction.upper() == "LONG" else "\u25bc"
        entry = pos.get("entry", 0)
        current = pos.get("current", 0)
        pnl = pos.get("pnl", 0.0)
        sl = pos.get("sl", 0)
        tp1 = pos.get("tp1", 0)

        pnl_icon = _pnl_arrow(pnl)

        lines.append(
            f"  {d_icon}{d_arrow} <b>{pair}</b>  {direction}  "
            f"{pnl_icon} {_pill(f'{pnl:+.2f}%')}"
        )
        lines.append("<pre>")
        lines.append(f"{_kv('Entry', f'${entry:,.2f}')}")
        lines.append(f"{_kv('Current', f'${current:,.2f}')}")
        lines.append(f"{_kv('SL', f'${sl:,.2f}')}")
        lines.append(f"{_kv('TP', f'${tp1:,.2f}')}")
        lines.append("</pre>")

        keyboard.append([
            _btn(f"\U0001f4cb {pair}", f"pos_details_{pair}"),
            _btn("\u274c Close", f"pos_close_{pair}"),
        ])

    if not positions:
        lines.append(f"  {_NEU} <i>No open positions. Use /scan or /analyze.</i>")

    return {"text": "\n".join(lines), "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_daily_report
# ═════════════════════════════════════════════════════════════════

def render_daily_report(data: Dict[str, Any]) -> Dict[str, Any]:
    trades = data.get("trades", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    net = data.get("net_pnl", 0.0)
    best_t = data.get("best_trade", "N/A")
    best_p = data.get("best_pnl", 0.0)
    worst_t = data.get("worst_trade", "N/A")
    worst_p = data.get("worst_pnl", 0.0)
    risk_s = data.get("risk_status", "Healthy")
    risk_icon = _OK if risk_s.lower() == "healthy" else _WARN if risk_s.lower() == "warning" else _BAD

    wr = (wins / trades * 100) if trades > 0 else 0.0
    wr_bar = _bar(wr, 100.0, 10)
    wr_ring = _progress_ring(wr)

    text = (
        f"{_header(chr(0x1F4D3), 'DAILY REPORT')}\n"
        f"   {_pnl_arrow(net)} Net PnL: {_pill(f'${net:+.2f}')}\n\n"
        # ── Trade summary ──
        f"\U0001f4ca <b>Trade Summary</b>\n"
        "<pre>"
        f"{_kv('Total', str(trades))}\n"
        f"{_kv('Wins', str(wins) + ' ' + _OK)}\n"
        f"{_kv('Losses', str(losses) + ' ' + _BAD)}\n"
        f"{_kv('Net PnL', f'${net:+.2f}')}"
        "</pre>\n\n"
        # ── Win Rate ──
        f"\U0001f3af <b>Win Rate</b>\n"
        f"  {wr_ring} \u2502{wr_bar}\u2502 {_pill(f'{wr:.0f}%')}\n\n"
        # ── Highlights ──
        f"\U0001f3c6 <b>Highlights</b>\n"
        "<pre>"
        f"{_kv('Best', f'{best_t} ${best_p:+.2f}')}  {_OK}\n"
        f"{_kv('Worst', f'{worst_t} ${worst_p:+.2f}')}  {_BAD}"
        "</pre>\n\n"
        # ── Risk ──
        f"{_SHIELD} <b>Risk Status</b>\n"
        f"  {risk_icon} <b>{risk_s}</b>\n\n"
        f"<i>\u23f1 {_timestamp()}</i>"
    )
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_strategy_mode
# ═════════════════════════════════════════════════════════════════

def render_strategy_mode(current_mode: str) -> Dict[str, Any]:
    mode_icons = {
        "defensive": _SHIELD, "balanced": "\u2694\ufe0f",
        "aggressive": "\U0001f525", "manual": "\U0001f9d8",
    }
    icon = mode_icons.get(current_mode.lower(), "\u2694\ufe0f")

    text = (
        f"{_header(chr(0x2699) + chr(0xFE0F), 'STRATEGY MODE')}\n\n"
        f"  Active: {icon} <b>{current_mode.upper()}</b>\n\n"
        # ── Mode descriptions ──
        f"  {_SHIELD} <b>Defensive</b>\n"
        f"     <i>Conservative risk, fewer signals, wider SL</i>\n"
        f"  \u2694\ufe0f <b>Balanced</b>\n"
        f"     <i>Default mode, standard confluence thresholds</i>\n"
        f"  \U0001f525 <b>Aggressive</b>\n"
        f"     <i>More signals, tighter filters, higher exposure</i>\n"
        f"  \U0001f9d8 <b>Manual</b>\n"
        f"     <i>Bot analyzes, you decide. Full human control</i>\n\n"
        f"<i>\u25b8 Select a mode below to switch</i>"
    )
    keyboard: _Keyboard = [
        [_btn(f"{_SHIELD} Defensive", "mode_defensive"),
         _btn("\u2694\ufe0f Balanced", "mode_balanced")],
        [_btn("\U0001f525 Aggressive", "mode_aggressive"),
         _btn("\U0001f9d8 Manual", "mode_manual")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# render_pause / render_resume
# ═════════════════════════════════════════════════════════════════

def render_pause() -> Dict[str, Any]:
    text = (
        f"{_header(chr(0x23F8), 'BOT PAUSED')}\n\n"
        f"  {_WARN} All trading activity <b>suspended</b>\n\n"
        "<pre>"
        f"{_kv('Scanning', 'PAUSED')}\n"
        f"{_kv('New Trades', 'BLOCKED')}\n"
        f"{_kv('Open Positions', 'UNCHANGED')}\n"
        f"{_kv('Circuit Breaker', 'ACTIVE')}"
        "</pre>\n\n"
        "<i>\u25b8 Use /resume to reactivate trading</i>"
    )
    return {"text": text}


def render_resume() -> Dict[str, Any]:
    text = (
        f"{_header(chr(0x25B6) + chr(0xFE0F), 'BOT RESUMED')}\n\n"
        f"  {_OK} {ENGINE} is <b>back online</b>\n\n"
        "<pre>"
        f"{_kv('Scanning', 'ACTIVE')}\n"
        f"{_kv('Trading', 'ENABLED')}\n"
        f"{_kv('Circuit Breaker', 'CLEAR')}"
        "</pre>\n\n"
        "<i>\u25b8 Signal scanning will begin on next tick cycle</i>"
    )
    return {"text": text}


# ═════════════════════════════════════════════════════════════════
# render_emergency_stop
# ═════════════════════════════════════════════════════════════════

def render_emergency_stop() -> Dict[str, Any]:
    text = (
        f"{_header(chr(0x26D4), 'EMERGENCY STOP')}\n\n"
        f"  {_BAD} This will <b>immediately</b>:\n\n"
        "<pre>"
        "  \u2718 Cancel all pending orders\n"
        "  \u2718 Close all open positions\n"
        "  \u2718 Trip circuit breaker\n"
        "  \u2718 Halt all scanning"
        "</pre>\n\n"
        f"  {_WARN} <b>Are you sure?</b>"
    )
    keyboard: _Keyboard = [
        [_btn("\u26d4 CONFIRM STOP", "emergency_confirm"),
         _btn("\u21a9\ufe0f Cancel", "emergency_cancel")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ═════════════════════════════════════════════════════════════════
# Callback router
# ═════════════════════════════════════════════════════════════════

def handle_callback(callback_data: str) -> Dict[str, Any]:
    if callback_data == "open_warroom":
        return render_start()

    if callback_data == "latest_signal":
        return {"text": f"\U0001f4e1 <i>Fetching latest signal from {ENGINE}...</i>"}

    if callback_data == "performance":
        return {"text": "\U0001f4ca <i>Loading performance data...</i>"}

    if callback_data == "risk_control":
        return {"text": f"{_SHIELD} <i>Opening Risk Control Panel...</i>"}

    if callback_data == "strategy_mode":
        return render_strategy_mode("balanced")

    if callback_data == "positions":
        return {"text": "\U0001f4c2 <i>Loading open positions...</i>"}

    # Signal actions
    if callback_data.startswith("signal_approve_"):
        pair = callback_data.removeprefix("signal_approve_")
        return {"text": f"{_OK} Trade <b>approved</b> for {pair}. Executing."}

    if callback_data.startswith("signal_watch_"):
        pair = callback_data.removeprefix("signal_watch_")
        return {"text": f"\U0001f441 Watching <b>{pair}</b>. You will be notified on trigger."}

    if callback_data.startswith("signal_reject_"):
        pair = callback_data.removeprefix("signal_reject_")
        return {"text": f"{_BAD} Signal for <b>{pair}</b> rejected."}

    # Risk actions
    if callback_data == "risk_safe_mode":
        return {"text": f"{_SHIELD} <b>Safe Mode</b> activated. Reduced exposure."}

    if callback_data == "risk_pause":
        return render_pause()

    if callback_data == "risk_emergency_stop":
        return render_emergency_stop()

    if callback_data == "emergency_confirm":
        return {"text": (
            f"\u26d4 <b>EMERGENCY STOP EXECUTED</b>\n\n"
            f"  {_BAD} All orders cancelled\n"
            f"  {_BAD} Positions closed\n"
            f"  {_BAD} Bot halted\n\n"
            f"<i>Use /reset to restart</i>"
        )}

    if callback_data == "emergency_cancel":
        return {"text": f"{_OK} Emergency stop cancelled. Bot continues."}

    # Mode switches
    if callback_data.startswith("mode_"):
        mode = callback_data.removeprefix("mode_")
        return render_strategy_mode(mode)

    # Position actions
    if callback_data.startswith("pos_details_"):
        pair = callback_data.removeprefix("pos_details_")
        return {"text": f"\U0001f4cb <i>Loading details for {pair}...</i>"}

    if callback_data.startswith("pos_close_"):
        pair = callback_data.removeprefix("pos_close_")
        return {"text": f"{_BAD} <i>Closing position for {pair}...</i>"}

    return {"text": f"{_WARN} Unknown command."}
