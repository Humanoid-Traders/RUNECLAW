"""Shared manual-trade parsing and TradeIdea construction.

Used by BOTH the Telegram `/trade` command (bot/skills/telegram_handler.py)
and the web user gateway (bot/web/user_gateway.py) so the two surfaces can
never drift: one grammar, one validation, one idea shape. Extracted verbatim
from TelegramHandler._parse_manual_trade / _cmd_trade.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, Union

# (direction, symbol, entry, sl, tp, margin_usd|None)
ParsedManualTrade = Tuple[str, str, float, float, float, Optional[float]]

_TRADE_PATTERN = re.compile(
    r'^(BUY|LONG|SHORT|SELL)\s+'
    r'([A-Z0-9]{1,15})\s+'
    r'(\$?[\d,]+\.?\d*)\s+'
    r'SL\s+(\$?[\d,]+\.?\d*)\s+'
    r'TP\s+(\$?[\d,]+\.?\d*)'
    r'(?:\s+MARGIN\s+(\$?[\d,]+\.?\d*))?',
    re.IGNORECASE,
)

_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}$')


def parse_manual_trade(text: str) -> Union[ParsedManualTrade, str]:
    """Parse manual trade text. Returns (direction, symbol, entry, sl, tp, margin)
    or an error string (Telegram-HTML) describing what's wrong."""
    text = text.strip().upper()
    m = _TRADE_PATTERN.match(text)
    if not m:
        return ("Invalid format. Use:\n"
                "<code>buy SOL 71.42 sl 70.05 tp 76.42</code>\n"
                "<code>short ETH 1721 sl 1695 tp 1842 margin 250</code>")

    side = m.group(1).upper()
    direction = "LONG" if side in ("BUY", "LONG") else "SHORT"
    symbol = m.group(2).upper()

    def parse_price(s):
        return float(s.replace("$", "").replace(",", ""))

    try:
        entry = parse_price(m.group(3))
        sl = parse_price(m.group(4))
        tp = parse_price(m.group(5))
        margin = parse_price(m.group(6)) if m.group(6) else None
    except (ValueError, TypeError):
        return "Could not parse prices. Use numbers like <code>71.42</code>"

    if entry <= 0 or sl <= 0 or tp <= 0:
        return "All prices must be positive."
    if margin is not None and margin <= 0:
        return "Margin must be positive."

    if direction == "LONG":
        if sl >= entry:
            return f"LONG: SL (${sl:,.4f}) must be below entry (${entry:,.4f})"
        if tp <= entry:
            return f"LONG: TP (${tp:,.4f}) must be above entry (${entry:,.4f})"
    else:
        if sl <= entry:
            return f"SHORT: SL (${sl:,.4f}) must be above entry (${entry:,.4f})"
        if tp >= entry:
            return f"SHORT: TP (${tp:,.4f}) must be below entry (${entry:,.4f})"

    if not _SYMBOL_RE.match(symbol):
        return f"Invalid symbol: {symbol}"

    return (direction, symbol, entry, sl, tp, margin)


def normalize_order_type(order_type) -> str:
    """One place decides the order type: 'market' (open now, taker) or 'limit'
    (rest at entry). Anything unrecognised falls back to 'limit' — the platform
    default (maker, no slippage)."""
    ot = str(order_type or "limit").strip().lower()
    return "market" if ot == "market" else "limit"


def build_manual_idea(direction: str, symbol: str, entry: float,
                      sl: float, tp: float, order_type: str = "limit"):
    """Build the manual TradeIdea exactly as /trade does. Raises ValueError on
    model-level sanity failures (non-finite prices, wrong SL/TP side).

    order_type: 'limit' (default — rests at ``entry``) or 'market' (opens now
    at the current price). Both are already supported by the executor; the
    default keeps the historical limit-only behaviour."""
    from bot.utils.models import TradeIdea, Direction
    pair = f"{symbol}/USDT:USDT"
    return TradeIdea(
        asset=pair,
        direction=Direction.LONG if direction == "LONG" else Direction.SHORT,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=1.0,
        reasoning="Manual trade placed by user",
        signals_used=["manual"],
        source="manual",
        order_type=normalize_order_type(order_type),
    )


def register_manual_idea(engine, idea, margin_usd: Optional[float] = None) -> None:
    """Register a manual idea as pending in the engine (+ optional margin
    override), mirroring the /trade registration block."""
    engine._pending_ideas[idea.id] = idea
    if margin_usd and margin_usd > 0:
        if not hasattr(engine, '_manual_margin_override'):
            engine._manual_margin_override = {}
        engine._manual_margin_override[idea.id] = margin_usd
