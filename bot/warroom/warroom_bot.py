"""MuleRun War Room — Telegram bot interface for the RUNECLAW Signal Engine.

This module provides message template functions that return formatted HTML strings
compatible with Telegram's HTML parse mode. Each function produces a dict containing
a ``text`` key (HTML string) and optionally a ``reply_markup`` key (serializable
inline keyboard structure).

No external dependencies beyond the Python standard library are required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------
PRODUCT = "MuleRun War Room"
ENGINE = "RUNECLAW Signal Engine"
ENGINE_VERSION = "v3.1"
UPTIME = "99.7%"

# ---------------------------------------------------------------------------
# Keyboard helpers
# ---------------------------------------------------------------------------
_Btn = Dict[str, str]
_Row = List[_Btn]
_Keyboard = List[_Row]


def _btn(text: str, callback_data: str) -> _Btn:
    return {"text": text, "callback_data": callback_data}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _pnl_color(val: float) -> str:
    """Return emoji indicator for PnL value."""
    if val > 0:
        return "\U0001f7e2"  # green
    if val < 0:
        return "\U0001f534"  # red
    return "\u26aa"  # white


def _progress_bar(val: float, max_val: float, width: int = 10) -> str:
    """Return a Unicode block progress bar like ``██░░░░░░░░``."""
    if max_val <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(val / max_val, 1.0))
    filled = round(ratio * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _dot_leader(label: str, value: str, width: int = 20) -> str:
    """Return ``Label ···· Value`` with dot leaders."""
    dots_needed = max(1, width - len(label) - len(value))
    return f"{label} {'·' * dots_needed} {value}"


def _confidence_dots(pct: int) -> str:
    """Return ``●●●●○`` style confidence indicator (out of 5)."""
    filled = round(pct / 100 * 5)
    filled = max(0, min(5, filled))
    return "\u25cf" * filled + "\u25cb" * (5 - filled)


def _win_rate_dots(pct: float) -> str:
    """Return ``●●●●●●○○○○`` style win-rate indicator (out of 10)."""
    filled = round(pct / 100 * 10)
    filled = max(0, min(10, filled))
    return "\u25cf" * filled + "\u25cb" * (10 - filled)


def _format_number(n: float) -> str:
    """Return comma-separated number string."""
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# render_start
# ---------------------------------------------------------------------------

def render_start() -> Dict[str, Any]:
    """Welcome message and main menu keyboard."""
    text = (
        "<b>\u2694\ufe0f MULERUN WAR ROOM</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Powered by <b>{ENGINE}</b>\n\n"
        "Signal locked. Risk checked. Claw ready.\n\n"
        "Status: <b>ACTIVE</b> \U0001f7e2\n"
        f"Engine: {ENGINE_VERSION} | Uptime: {UPTIME}"
    )
    keyboard: _Keyboard = [
        [_btn("\u2694\ufe0f Open War Room", "open_warroom"), _btn("\U0001f4ca Latest Signal", "latest_signal")],
        [_btn("\U0001f4c8 Performance", "performance"), _btn("\U0001f6e1 Risk Control", "risk_control")],
        [_btn("\u2699\ufe0f Strategy Mode", "strategy_mode"), _btn("\U0001f4c2 Positions", "positions")],
        [_btn("\u26d4 Emergency Stop", "risk_emergency_stop")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ---------------------------------------------------------------------------
# render_status
# ---------------------------------------------------------------------------

def render_status(data: Dict[str, Any]) -> Dict[str, Any]:
    """Bot status overview."""
    active_str = "ACTIVE \U0001f7e2" if data.get("active") else "INACTIVE \U0001f534"
    pnl = data.get("daily_pnl", 0.0)
    pnl_sign = "+" if pnl >= 0 else ""
    risk = data.get("risk_used", 0.0)
    risk_bar = _progress_bar(risk, 10.0, 8)

    text = (
        "<b>\u2694\ufe0f MuleRun / RUNECLAW Status</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"\u25c9 {_dot_leader('Bot', active_str)}\n"
        f"\u25c9 {_dot_leader('Mode', data.get('mode', 'N/A'))}\n"
        f"\u25c9 {_dot_leader('Exchange', data.get('exchange', 'N/A'))}\n"
        f"\u25c9 {_dot_leader('Open Trades', str(data.get('open_trades', 0)))}\n"
        f"\u25c9 {_dot_leader('Daily PnL', f'{pnl_sign}{pnl}% {_pnl_color(pnl)}')}\n"
        f"\u25c9 {_dot_leader('Risk Used', f'{risk_bar} {risk}%')}\n"
        f"\u25c9 {_dot_leader('Market Bias', data.get('market_bias', 'N/A') + ' \U0001f4c8')}\n"
        f"\u25c9 {_dot_leader('Last Signal', data.get('last_signal', 'N/A'))}\n\n"
        f"\u23f1 Updated: {_timestamp()}"
    )
    return {"text": text}


# ---------------------------------------------------------------------------
# render_signal
# ---------------------------------------------------------------------------

def render_signal(data: Dict[str, Any]) -> Dict[str, Any]:
    """Tactical signal card."""
    pair = data.get("pair", "N/A")
    direction = data.get("direction", "LONG")
    dir_emoji = "\U0001f7e2" if direction.upper() == "LONG" else "\U0001f534"
    confidence = data.get("confidence", 0)
    risk_level = data.get("risk_level", "Medium")
    entry_low = _format_number(data.get("entry_low", 0))
    entry_high = _format_number(data.get("entry_high", 0))
    sl = _format_number(data.get("sl", 0))
    tp1 = _format_number(data.get("tp1", 0))
    tp2 = _format_number(data.get("tp2", 0))
    reason = data.get("reason", "")

    text = (
        "<b>\u2694\ufe0f RUNECLAW SIGNAL</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"{_dot_leader('Pair', pair)}\n"
        f"{_dot_leader('Direction', f'{direction} {dir_emoji}')}\n"
        f"{_dot_leader('Confidence', f'{_confidence_dots(confidence)} {confidence}%')}\n"
        f"{_dot_leader('Risk', f'\u26a0\ufe0f {risk_level}')}\n\n"
        "<pre>"
        "\u250c\u2500 LEVELS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n"
        f"\u2502 Entry  {entry_low} \u2013 {entry_high}\n"
        f"\u2502 SL     {sl}\n"
        f"\u2502 TP1    {tp1}\n"
        f"\u2502 TP2    {tp2}\n"
        "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518"
        "</pre>\n\n"
        f"\U0001f4e1 <i>{reason}</i>"
    )
    keyboard: _Keyboard = [
        [_btn("\u2705 Approve Trade", f"signal_approve_{pair}")],
        [_btn("\U0001f441 Watch Only", f"signal_watch_{pair}")],
        [_btn("\u274c Reject", f"signal_reject_{pair}")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ---------------------------------------------------------------------------
# render_risk
# ---------------------------------------------------------------------------

def render_risk(data: Dict[str, Any]) -> Dict[str, Any]:
    """Risk control panel."""
    dd = data.get("current_drawdown", 0.0)
    dd_bar = _progress_bar(dd, 10.0, 10)
    dll = data.get("daily_loss_limit", 0.0)
    max_t = data.get("max_open_trades", 0)
    open_t = data.get("open_trades", 0)
    lev = data.get("leverage_cap", 1)

    healthy = dd < dll
    status = "HEALTHY \U0001f7e2" if healthy else "WARNING \U0001f534"

    text = (
        "<b>\U0001f6e1 RISK CONTROL PANEL</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"{_dot_leader('Daily Loss Limit', f'{dll}%')}\n"
        f"{_dot_leader('Current Drawdown', f'{dd_bar} {dd}%')}\n"
        f"{_dot_leader('Max Open Trades', str(max_t))}\n"
        f"{_dot_leader('Open Trades Now', str(open_t))}\n"
        f"{_dot_leader('Leverage Cap', f'{lev}x')}\n\n"
        f"Risk Status: <b>{status}</b>"
    )
    keyboard: _Keyboard = [
        [_btn("\U0001f6e1 Safe Mode", "risk_safe_mode"), _btn("\u23f8 Pause Bot", "risk_pause")],
        [_btn("\u26d4 Emergency Stop", "risk_emergency_stop")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ---------------------------------------------------------------------------
# render_performance
# ---------------------------------------------------------------------------

def render_performance(data: Dict[str, Any]) -> Dict[str, Any]:
    """Performance summary."""
    today = data.get("today_pnl", 0.0)
    week = data.get("week_pnl", 0.0)
    wr = data.get("win_rate", 0.0)
    trades = data.get("trades_today", 0)
    best = data.get("best_pair", "N/A")
    worst = data.get("worst_pair", "N/A")

    t_sign = "+" if today >= 0 else ""
    w_sign = "+" if week >= 0 else ""

    text = (
        "<b>\U0001f4ca PERFORMANCE</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"{_dot_leader('Today', f'{t_sign}{today}% {_pnl_color(today)}')}\n"
        f"{_dot_leader('7D PnL', f'{w_sign}{week}% {_pnl_color(week)}')}\n"
        f"{_dot_leader('Win Rate', f'{_win_rate_dots(wr)} {int(wr)}%')}\n"
        f"{_dot_leader('Trades Today', str(trades))}\n"
        f"{_dot_leader('Best Pair', f'{best} \U0001f3c6')}\n"
        f"{_dot_leader('Worst Pair', worst)}\n\n"
        "\u2581\u2582\u2583\u2585\u2587\u2588\u2587\u2585\u2586\u2587 PnL Trend"
    )
    return {"text": text}


# ---------------------------------------------------------------------------
# render_positions
# ---------------------------------------------------------------------------

def render_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Open positions list."""
    count = len(positions)
    lines = [
        f"<b>\U0001f4c8 OPEN POSITIONS ({count})</b>",
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
    ]
    keyboard: _Keyboard = []

    for pos in positions:
        pair = pos.get("pair", "N/A")
        direction = pos.get("direction", "LONG")
        dir_emoji = "\U0001f7e2" if direction.upper() == "LONG" else "\U0001f534"
        entry = _format_number(pos.get("entry", 0))
        current = _format_number(pos.get("current", 0))
        pnl = pos.get("pnl", 0.0)
        pnl_sign = "+" if pnl >= 0 else ""
        sl = _format_number(pos.get("sl", 0))
        tp1 = _format_number(pos.get("tp1", 0))

        lines.append("")
        lines.append(f"\u250c {pair} \u00b7 {direction} {dir_emoji}")
        lines.append(f"\u2502 {_dot_leader('Entry', entry)}")
        lines.append(f"\u2502 {_dot_leader('Current', current)}")
        lines.append(f"\u2502 {_dot_leader('PnL', f'{pnl_sign}{pnl}% {_pnl_color(pnl)}')}")
        lines.append(f"\u2502 {_dot_leader('SL', sl)}")
        lines.append(f"\u2502 {_dot_leader('TP1', tp1)}")
        lines.append("\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")

        keyboard.append([
            _btn("\U0001f4cb Details", f"pos_details_{pair}"),
            _btn("\u274c Close", f"pos_close_{pair}"),
        ])

    return {"text": "\n".join(lines), "reply_markup": keyboard}


# ---------------------------------------------------------------------------
# render_daily_report
# ---------------------------------------------------------------------------

def render_daily_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """End-of-day report."""
    trades = data.get("trades", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    net = data.get("net_pnl", 0.0)
    net_sign = "+" if net >= 0 else ""
    best_t = data.get("best_trade", "N/A")
    best_p = data.get("best_pnl", 0.0)
    best_sign = "+" if best_p >= 0 else ""
    worst_t = data.get("worst_trade", "N/A")
    worst_p = data.get("worst_pnl", 0.0)
    worst_sign = "+" if worst_p >= 0 else ""
    risk_s = data.get("risk_status", "Healthy")
    risk_emoji = "\U0001f7e2" if risk_s.lower() == "healthy" else "\U0001f534"

    wr = (wins / trades * 100) if trades > 0 else 0.0

    text = (
        "<b>\U0001f4d3 DAILY RUNECLAW REPORT</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"{_dot_leader('Trades', str(trades))}\n"
        f"{_dot_leader('Wins', f'{wins} \U0001f7e2')}\n"
        f"{_dot_leader('Losses', f'{losses} \U0001f534')}\n"
        f"{_dot_leader('Net PnL', f'{net_sign}{net}%')}\n"
        f"{_dot_leader('Best Trade', f'{best_t} {best_sign}{best_p}% \U0001f3c6')}\n"
        f"{_dot_leader('Worst Trade', f'{worst_t} {worst_sign}{worst_p}%')}\n"
        f"{_dot_leader('Risk Status', f'{risk_s} {risk_emoji}')}\n\n"
        f"{_dot_leader('Win Rate', f'{_win_rate_dots(wr)} {int(wr)}%')}"
    )
    return {"text": text}


# ---------------------------------------------------------------------------
# render_strategy_mode
# ---------------------------------------------------------------------------

def render_strategy_mode(current_mode: str) -> Dict[str, Any]:
    """Strategy mode selector."""
    mode_icons = {
        "defensive": "\U0001f6e1",
        "balanced": "\u2694\ufe0f",
        "aggressive": "\U0001f525",
        "manual": "\U0001f9d8",
    }
    icon = mode_icons.get(current_mode.lower(), "\u2694\ufe0f")

    text = (
        "<b>\u2699\ufe0f STRATEGY MODE</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"Current Mode: <b>{icon} {current_mode.capitalize()}</b>\n\n"
        "\U0001f6e1 <b>Defensive</b> \u2014 Lower risk, fewer trades\n"
        "\u2694\ufe0f <b>Balanced</b> \u2014 Default mode\n"
        "\U0001f525 <b>Aggressive</b> \u2014 More signals, higher risk\n"
        "\U0001f9d8 <b>Manual</b> \u2014 Bot suggests, you approve"
    )
    keyboard: _Keyboard = [
        [_btn("\U0001f6e1 Defensive", "mode_defensive"), _btn("\u2694\ufe0f Balanced", "mode_balanced")],
        [_btn("\U0001f525 Aggressive", "mode_aggressive"), _btn("\U0001f9d8 Manual", "mode_manual")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ---------------------------------------------------------------------------
# render_pause / render_resume
# ---------------------------------------------------------------------------

def render_pause() -> Dict[str, Any]:
    """Bot paused confirmation."""
    text = (
        "<b>\u23f8 BOT PAUSED</b>\n\n"
        "All trading activity suspended.\n"
        "Open positions remain unchanged.\n\n"
        "Use /resume to reactivate."
    )
    return {"text": text}


def render_resume() -> Dict[str, Any]:
    """Bot resumed confirmation."""
    text = (
        "<b>\u25b6\ufe0f BOT RESUMED</b>\n\n"
        f"{ENGINE} is back online.\n"
        "Signal scanning active."
    )
    return {"text": text}


# ---------------------------------------------------------------------------
# render_emergency_stop
# ---------------------------------------------------------------------------

def render_emergency_stop() -> Dict[str, Any]:
    """Emergency stop confirmation prompt."""
    text = (
        "<b>\u26d4 EMERGENCY STOP</b>\n\n"
        "This will immediately:\n"
        "\u2022 Cancel all pending orders\n"
        "\u2022 Close all open positions\n"
        "\u2022 Pause the bot\n\n"
        "<b>Are you sure?</b>"
    )
    keyboard: _Keyboard = [
        [_btn("\u26d4 CONFIRM STOP", "emergency_confirm"), _btn("\u21a9\ufe0f Cancel", "emergency_cancel")],
    ]
    return {"text": text, "reply_markup": keyboard}


# ---------------------------------------------------------------------------
# Callback router
# ---------------------------------------------------------------------------

def handle_callback(callback_data: str) -> Dict[str, Any]:
    """Route a Telegram callback_data string to the appropriate response."""

    if callback_data == "open_warroom":
        return render_start()

    if callback_data == "latest_signal":
        return {"text": "\U0001f4e1 Fetching latest signal from RUNECLAW\u2026"}

    if callback_data == "performance":
        return {"text": "\U0001f4ca Loading performance data\u2026"}

    if callback_data == "risk_control":
        return {"text": "\U0001f6e1 Opening Risk Control Panel\u2026"}

    if callback_data == "strategy_mode":
        return render_strategy_mode("balanced")

    if callback_data == "positions":
        return {"text": "\U0001f4c2 Loading open positions\u2026"}

    # Signal actions
    if callback_data.startswith("signal_approve_"):
        pair = callback_data.removeprefix("signal_approve_")
        return {"text": f"\u2705 Trade <b>approved</b> for {pair}. Executing."}

    if callback_data.startswith("signal_watch_"):
        pair = callback_data.removeprefix("signal_watch_")
        return {"text": f"\U0001f441 Watching {pair}. You will be notified on trigger."}

    if callback_data.startswith("signal_reject_"):
        pair = callback_data.removeprefix("signal_reject_")
        return {"text": f"\u274c Signal for {pair} rejected."}

    # Risk actions
    if callback_data == "risk_safe_mode":
        return {"text": "\U0001f6e1 <b>Safe Mode</b> activated. Reduced exposure."}

    if callback_data == "risk_pause":
        return render_pause()

    if callback_data == "risk_emergency_stop":
        return render_emergency_stop()

    if callback_data == "emergency_confirm":
        return {"text": "\u26d4 <b>EMERGENCY STOP EXECUTED.</b>\nAll orders cancelled. Positions closed. Bot paused."}

    if callback_data == "emergency_cancel":
        return {"text": "\u21a9\ufe0f Emergency stop cancelled. Bot continues."}

    # Mode switches
    if callback_data.startswith("mode_"):
        mode = callback_data.removeprefix("mode_")
        return render_strategy_mode(mode)

    # Position actions
    if callback_data.startswith("pos_details_"):
        pair = callback_data.removeprefix("pos_details_")
        return {"text": f"\U0001f4cb Loading details for {pair}\u2026"}

    if callback_data.startswith("pos_close_"):
        pair = callback_data.removeprefix("pos_close_")
        return {"text": f"\u274c Closing position for {pair}\u2026"}

    return {"text": "\u26a0\ufe0f Unknown command."}
