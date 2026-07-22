"""
Universe expansion round 1 (2026-07-12): TradFi catalog refresh,
*STOCK auto-discovery, stock session-risk wiring, per-class PF.

Catalog-verified against the live Bitget USDT-FUTURES API on 2026-07-12:
all pre-existing 32 TradFi perps still listed; 15 additional live TradFi
perps were missing from config (CRCL $4.7M/day, QQQ $1.1M/day, SPY, TQQQ,
ORCL, NFLX, OPEN, MCD, GME + six *STOCK-suffix listings).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import ETF_PERPETUALS, STOCK_PERPETUALS, TRADFI_PERPETUALS
from bot.core.market_scanner import (_classify_symbol, _is_stock_suffix_base)


# ── config catalog refresh ───────────────────────────────────────────
def test_new_etf_perps_present():
    for s in ("QQQ/USDT:USDT", "SPY/USDT:USDT", "TQQQ/USDT:USDT"):
        assert s in ETF_PERPETUALS
        assert s in TRADFI_PERPETUALS


def test_new_stock_perps_present():
    for s in ("CRCL/USDT:USDT", "ORCL/USDT:USDT", "NFLX/USDT:USDT",
              "OPEN/USDT:USDT", "MCD/USDT:USDT", "GME/USDT:USDT",
              "QNTSTOCK/USDT:USDT", "RTXSTOCK/USDT:USDT"):
        assert s in STOCK_PERPETUALS
        assert s in TRADFI_PERPETUALS


def test_new_perps_classify_correctly():
    assert _classify_symbol("QQQ/USDT:USDT") == "ETF"
    assert _classify_symbol("CRCL/USDT:USDT") == "Stock"
    assert _classify_symbol("RTXSTOCK/USDT:USDT") == "Stock"


# ── *STOCK auto-discovery (future listings, no config release needed) ─
def test_stock_suffix_autodiscovery():
    # A hypothetical FUTURE listing not in any config list:
    assert _is_stock_suffix_base("FORDSTOCK/USDT:USDT") is True
    assert _classify_symbol("FORDSTOCK/USDT:USDT") == "Stock"
    # A base named exactly "STOCK" would be ambiguous — excluded:
    assert _is_stock_suffix_base("STOCK/USDT:USDT") is False
    # Crypto bases don't false-positive:
    assert _is_stock_suffix_base("BTC/USDT:USDT") is False
    assert _classify_symbol("WOOFSTOCKCOIN/USDT") == "Crypto"  # STOCK mid-name, not suffix
    assert _classify_symbol("BTC/USDT") == "Crypto"


def test_scanner_futures_filter_accepts_stock_suffix():
    """The all_markets futures side must admit a *STOCK listing that is
    not in the curated _TRADFI_SET (source pin on the filter)."""
    from bot.core.market_scanner import MarketScanner
    src = inspect.getsource(MarketScanner._scan_all_markets)
    assert "_is_stock_suffix_base" in src


# ── stock session-risk wiring ────────────────────────────────────────
def test_session_gate_is_wired_into_confirm_path():
    """get_stock_risk_params/get_market_session were dead code — the
    session multiplier must now appear in the engine's confirm path."""
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    assert "get_market_session" in src
    assert "size_multiplier" in src
    assert "stock_session_gate" in src      # block audit trail
    assert "stock_session_sizing" in src    # reduce audit trail


def test_market_session_multipliers_shape():
    """Regular hours full size; extended reduced; weekend 0.25 (or 0 when
    hard-block is on)."""
    from bot.core.stock_trading import get_market_session
    UTC = timezone.utc
    # Wednesday 15:00 UTC = regular hours
    s = get_market_session(datetime(2026, 7, 8, 15, 0, tzinfo=UTC))
    assert s.session_name == "regular" and s.size_multiplier == 1.0
    # Wednesday 10:00 UTC = pre-market
    s = get_market_session(datetime(2026, 7, 8, 10, 0, tzinfo=UTC))
    assert s.session_name == "pre_market" and 0 < s.size_multiplier < 1.0
    # Saturday = weekend
    s = get_market_session(datetime(2026, 7, 11, 12, 0, tzinfo=UTC))
    assert s.is_weekend and s.size_multiplier <= 0.25


# ── /classpf command ─────────────────────────────────────────────────
def _make_update(user_id=6307156912, args=None):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "TestUser"
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _replies(update) -> str:
    out = []
    for call in update.message.reply_text.call_args_list:
        out.append(call[0][0] if call[0] else call.kwargs.get("text", ""))
    return "\n".join(out)


@pytest.mark.asyncio
async def test_classpf_buckets_by_asset_class(monkeypatch):
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(6307156912))

    fake_trades = [
        SimpleNamespace(symbol="BTC/USDT:USDT", pnl_usd=10.0),
        SimpleNamespace(symbol="ETH/USDT:USDT", pnl_usd=-4.0),
        SimpleNamespace(symbol="XAU/USDT:USDT", pnl_usd=3.0),
        SimpleNamespace(symbol="TSLA/USDT:USDT", pnl_usd=-2.0),
        SimpleNamespace(symbol="QQQ/USDT:USDT", pnl_usd=1.0),
    ]
    monkeypatch.setattr(type(engine.live_executor), "closed_positions",
                        property(lambda self: fake_trades))
    update, ctx = _make_update()
    await handler._cmd_classpf(update, ctx)
    text = _replies(update)
    assert "by asset class" in text
    assert "Crypto" in text and "Metal" in text and "Stock" in text and "ETF" in text
    assert "PF" in text and "5 filled trades" in text


@pytest.mark.asyncio
async def test_classpf_empty_history():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(6307156912))
    engine.live_executor._closed_trades = []
    engine.live_executor._positions = {}
    update, ctx = _make_update()
    await handler._cmd_classpf(update, ctx)
    assert "No closed live trades" in _replies(update)
