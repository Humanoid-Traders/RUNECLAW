"""
RUNECLAW US Stock Tokenized Trading Module (Track 3).

Extends RUNECLAW's crypto trading infrastructure to tokenized US stock
perpetual contracts. These are USDT-denominated derivatives that track
US equity prices, tradeable 24/7 on Bitget but with liquidity and
spread conditions that follow traditional market hours.

Key differences from crypto:
  - Lower baseline volatility (ATR 1-3% vs crypto 3-10%)
  - Market-hours liquidity premium (tighter spreads 9:30-16:00 ET)
  - Earnings/macro event sensitivity (FOMC, NFP, earnings calls)
  - Sector correlation (tech stocks move together)
  - Index beta (most stocks track SPY/QQQ)

This module provides:
  1. Market hours awareness (session detection)
  2. Stock-specific risk parameter overrides
  3. Stock universe scanning and filtering
  4. Sector rotation detection
  5. Index correlation tracking
"""

from __future__ import annotations

import logging
from datetime import datetime
from bot.compat import UTC
from dataclasses import dataclass
from typing import Optional

from bot.config import CONFIG, US_STOCK_SYMBOLS

logger = logging.getLogger(__name__)


# ── US Market Session Detection ──────────────────────────────────────

@dataclass
class MarketSession:
    """Current US market session state."""
    is_regular_hours: bool     # 09:30-16:00 ET
    is_extended_hours: bool    # pre-market or after-hours
    is_weekend: bool           # Saturday or Sunday
    session_name: str          # "regular", "pre_market", "after_hours", "closed", "weekend"
    hours_until_open: float    # hours until next regular session open
    hours_until_close: float   # hours until current session close (0 if closed)
    size_multiplier: float     # position size adjustment (1.0 = full, 0.5 = half)


def get_market_session(now: Optional[datetime] = None) -> MarketSession:
    """Determine the current US stock market session.

    All times in UTC. Regular session: 13:30-20:00 UTC (9:30-16:00 ET).
    Pre-market: 08:00-13:30 UTC (4:00-9:30 ET).
    After-hours: 20:00-00:00 UTC (16:00-20:00 ET).
    """
    if now is None:
        now = datetime.now(UTC)

    weekday = now.weekday()  # 0=Monday, 6=Sunday
    hour = now.hour
    minute = now.minute
    time_decimal = hour + minute / 60.0

    # Weekend check
    if weekday >= 5:  # Saturday or Sunday
        # Calculate hours until Monday 13:30 UTC
        days_until_monday = (7 - weekday) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        hours_until = days_until_monday * 24 - time_decimal + 13.5
        return MarketSession(
            is_regular_hours=False,
            is_extended_hours=False,
            is_weekend=True,
            session_name="weekend",
            hours_until_open=round(hours_until, 1),
            hours_until_close=0,
            size_multiplier=0.0 if CONFIG.stocks.block_outside_hours else 0.25,
        )

    # Regular session: 13:30 - 20:00 UTC
    regular_open = 13.5   # 13:30 UTC = 9:30 ET
    regular_close = 20.0  # 20:00 UTC = 16:00 ET

    # Pre-market: 08:00 - 13:30 UTC
    pre_open = 8.0
    # After-hours: 20:00 - 00:00 UTC
    after_close = 24.0

    if regular_open <= time_decimal < regular_close:
        return MarketSession(
            is_regular_hours=True,
            is_extended_hours=False,
            is_weekend=False,
            session_name="regular",
            hours_until_open=0,
            hours_until_close=round(regular_close - time_decimal, 1),
            size_multiplier=1.0,
        )
    elif pre_open <= time_decimal < regular_open:
        return MarketSession(
            is_regular_hours=False,
            is_extended_hours=True,
            is_weekend=False,
            session_name="pre_market",
            hours_until_open=round(regular_open - time_decimal, 1),
            hours_until_close=round(regular_open - time_decimal, 1),
            size_multiplier=CONFIG.stocks.reduce_size_outside_hours,
        )
    elif regular_close <= time_decimal < after_close:
        return MarketSession(
            is_regular_hours=False,
            is_extended_hours=True,
            is_weekend=False,
            session_name="after_hours",
            hours_until_open=round(24 - time_decimal + regular_open, 1),
            hours_until_close=round(after_close - time_decimal, 1),
            size_multiplier=CONFIG.stocks.reduce_size_outside_hours,
        )
    else:
        # Overnight: 00:00 - 08:00 UTC
        hours_until = pre_open - time_decimal if time_decimal < pre_open else 24 - time_decimal + pre_open
        return MarketSession(
            is_regular_hours=False,
            is_extended_hours=False,
            is_weekend=False,
            session_name="closed",
            hours_until_open=round(hours_until, 1),
            hours_until_close=0,
            size_multiplier=0.0 if CONFIG.stocks.block_outside_hours else 0.25,
        )


# ── Stock Asset Classification ───────────────────────────────────────

# Sector mapping for correlation awareness
STOCK_SECTORS: dict[str, str] = {
    "AAPL/USDT": "Technology", "MSFT/USDT": "Technology",
    "GOOGL/USDT": "Technology", "AMZN/USDT": "Technology",
    "META/USDT": "Technology", "NVDA/USDT": "Technology",
    "AMD/USDT": "Technology", "NFLX/USDT": "Technology",
    "CRM/USDT": "Technology", "TSLA/USDT": "Consumer/EV",
    "JPM/USDT": "Financials", "GS/USDT": "Financials",
    "V/USDT": "Financials", "MA/USDT": "Financials",
    "COIN/USDT": "Crypto-Adjacent", "MSTR/USDT": "Crypto-Adjacent",
    "MARA/USDT": "Crypto-Adjacent", "RIOT/USDT": "Crypto-Adjacent",
    "SPY/USDT": "Index", "QQQ/USDT": "Index",
}


def is_stock_symbol(symbol: str) -> bool:
    """Check if a symbol is a US stock tokenized perpetual."""
    return symbol in set(US_STOCK_SYMBOLS)


def get_stock_sector(symbol: str) -> Optional[str]:
    """Get the sector for a stock symbol."""
    return STOCK_SECTORS.get(symbol)


def get_stock_risk_params(symbol: str) -> dict:
    """Get stock-specific risk parameters that override default crypto params.

    Returns a dict of overrides that the risk engine should apply
    when evaluating a stock symbol.
    """
    cfg = CONFIG.stocks
    session = get_market_session()

    params = {
        "is_stock": True,
        "volatility_guard_atr_pct": cfg.volatility_guard_atr_pct,
        "min_risk_reward": cfg.min_risk_reward,
        "max_position_pct": cfg.max_position_pct,
        "max_symbol_exposure_pct": cfg.max_symbol_exposure_pct,
        "sl_atr_mult": cfg.sl_atr_mult,
        "tp_atr_mult": cfg.tp_atr_mult,
        "session": session,
        "size_multiplier": session.size_multiplier,
        "sector": get_stock_sector(symbol),
    }

    # Block or reduce outside regular hours
    if session.is_weekend and cfg.block_outside_hours:
        params["blocked"] = True
        params["block_reason"] = "US markets closed (weekend)"
    elif session.session_name == "closed" and cfg.block_outside_hours:
        params["blocked"] = True
        params["block_reason"] = f"US markets closed (overnight, opens in {session.hours_until_open:.1f}h)"

    return params


# ── Stock Scan Formatter ─────────────────────────────────────────────

def format_stock_scan_header(session: MarketSession) -> str:
    """Format the market session header for stock scans."""
    icons = {
        "regular": "\U0001f7e2",       # Green circle
        "pre_market": "\U0001f7e1",    # Yellow circle
        "after_hours": "\U0001f7e0",   # Orange circle
        "closed": "\U0001f534",        # Red circle
        "weekend": "\u26ab",           # Black circle
    }
    icon = icons.get(session.session_name, "\u2753")

    if session.is_regular_hours:
        status = f"REGULAR SESSION ({session.hours_until_close:.1f}h remaining)"
    elif session.is_extended_hours:
        status = f"{session.session_name.upper().replace('_', ' ')} ({session.hours_until_open:.1f}h to open)"
    elif session.is_weekend:
        status = f"WEEKEND (opens in {session.hours_until_open:.1f}h)"
    else:
        status = f"CLOSED (opens in {session.hours_until_open:.1f}h)"

    return f"{icon} US Market: <b>{status}</b>"


def format_stock_signal_line(symbol: str, price: float, change_pct: float,
                              rsi: Optional[float] = None,
                              volume_ratio: Optional[float] = None,
                              confidence: float = 0.0) -> str:
    """Format a single stock signal line for Telegram output."""
    # Direction emoji
    if change_pct > 0.5:
        emoji = "\U0001f7e2"  # Green
    elif change_pct < -0.5:
        emoji = "\U0001f534"  # Red
    else:
        emoji = "\u26aa"      # Gray (flat)

    # Ticker without /USDT
    ticker = symbol.replace("/USDT", "")
    sector = get_stock_sector(symbol) or ""
    sector_tag = f" [{sector}]" if sector else ""

    rsi_str = f"RSI {rsi:.0f}" if rsi is not None else ""
    vol_str = f"Vol {volume_ratio:.1f}x" if volume_ratio is not None else ""
    conf_bar = "\u2586" * max(1, int(confidence * 8))
    conf_pct = f"{confidence:.0%}" if confidence > 0 else ""

    parts = [f"${price:,.2f}", f"{change_pct:+.1f}%"]
    if rsi_str:
        parts.append(rsi_str)
    if vol_str:
        parts.append(vol_str)

    return f"{emoji} <b>{ticker}</b>{sector_tag}  {'  '.join(parts)}  {conf_bar} {conf_pct}"
