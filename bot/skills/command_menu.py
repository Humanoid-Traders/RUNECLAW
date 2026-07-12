"""Curated Telegram command menus — what pops up when a user types "/".

The bot registers ~60 slash commands but never told Telegram about any of them,
so the "/" autocomplete was empty and users had to memorise commands (or read
/help). These curated, role-aware menus fix that: a short, friendly essentials
list for everyone, and a fuller operator list surfaced only in the operator's
chat. The bot's natural-language routing still works — the menu just makes the
common actions discoverable.

Each entry is (command, description). Descriptions stay < 256 chars and lead
with a verb so the "/" list reads like a menu of actions. Every command here is
validated by tests against the handler's actual registered command set, so a
renamed/removed command can never leave a dead menu entry.
"""

from __future__ import annotations

from typing import List, Tuple

# Shown to EVERY user — the high-frequency essentials. Kept short on purpose:
# a wall of 60 commands is worse than a curated dozen.
DEFAULT_MENU: List[Tuple[str, str]] = [
    ("start", "👋 Register & see where things stand"),
    ("scan", "🔎 Scan the market for the best setups"),
    ("analyze", "🔬 Deep-dive one coin — e.g. /analyze SOL"),
    ("portfolio", "💼 Your equity, positions & win rate"),
    ("performance", "📊 Your PnL & trade stats"),
    ("open_positions", "📈 Your open positions"),
    ("orders", "🧾 Your open orders"),
    ("signals", "📡 Latest signals & why they fired"),
    ("risk", "🛡 Risk status & circuit breaker"),
    ("watch", "🔔 Alert me when a symbol sets up"),
    ("connect", "🔑 Link your exchange to trade live"),
    ("help", "❓ All commands & how to talk to the bot"),
]

# Extra controls surfaced ONLY in the operator/admin chat (on top of the
# essentials above). These are the live-trading and administration levers.
ADMIN_EXTRA_MENU: List[Tuple[str, str]] = [
    ("resume", "▶️ Resume trading (clear the breaker)"),
    ("pause", "⏸ Pause trading now"),
    ("drawdownlimit", "📉 Adjust the live drawdown cap"),
    ("venue", "🏦 Show or switch the trading venue"),
    ("classpf", "📊 Live PnL by asset class"),
    ("funding", "📡 Funding rates across venues"),
    ("parity", "📏 Live vs backtest parity report"),
    ("golive", "🔥 Arm live trading"),
    ("livebalance", "💰 Live exchange balance"),
    ("livepositions", "📌 Live exchange positions"),
    ("closeall", "⛔ Close every open position"),
    ("readiness", "✅ Live-readiness checklist"),
    ("flags", "🚩 Feature flags & their state"),
    ("gates", "🚦 Why signals are being gated"),
    ("users", "👥 Manage users"),
    ("health", "🩺 System health"),
]


def _dedupe_keep_order(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    out = []
    for name, desc in pairs:
        if name in seen:
            continue
        seen.add(name)
        out.append((name, desc))
    return out


def default_commands() -> List[Tuple[str, str]]:
    """The menu shown to every user."""
    return _dedupe_keep_order(DEFAULT_MENU)


def admin_commands() -> List[Tuple[str, str]]:
    """The operator menu: essentials first, then the admin controls."""
    return _dedupe_keep_order(DEFAULT_MENU + ADMIN_EXTRA_MENU)
