"""
Evidence-driven universe tuning (2026-07-12).

Source: live /classpf over 292 closed trades — Commodity PF 2.30 (32
trades, +$48.87) and Stock PF 1.23 (18, +$13.97) earn their slots;
Pre-IPO PF 0.24 (10 trades, −$16.77) on $40–100k/day books is
spread-bleed and ships DISABLED by default. Metals (PF 0.10, 9 trades)
and ETFs (PF 0.07, 7) stay ON — samples too small to condemn.

Also: /parity brings the live↔backtest parity report to Telegram with a
per-asset-class bucket, so the crypto bleed (PF 0.89 over 216 trades)
can be dissected without shell access.
"""

from __future__ import annotations

import json

import pytest

import bot.core.market_scanner as ms
from bot.core.market_scanner import MarketScanner, _class_scan_enabled


class _CfgProxy:
    def __init__(self, real, **over):
        self._real = real
        self._over = over

    def __getattr__(self, k):
        if k in ("_real", "_over"):
            raise AttributeError(k)
        if k in self._over:
            return self._over[k]
        return getattr(self._real, k)


# ── defaults ─────────────────────────────────────────────────────────
def test_shipped_defaults_match_the_evidence():
    from bot.config import CONFIG
    assert CONFIG.scan_class_commodities is True   # PF 2.30 / 32 trades
    assert CONFIG.scan_class_stocks is True        # PF 1.23 / 18 trades
    assert CONFIG.scan_class_metals is True        # small sample — keep scoring
    assert CONFIG.scan_class_etfs is True          # small sample — keep scoring
    assert CONFIG.scan_class_pre_ipo is False      # PF 0.24 + illiquid books


def test_class_enabled_mapping(monkeypatch):
    monkeypatch.setattr(ms, "CONFIG",
                        _CfgProxy(ms.CONFIG, scan_class_metals=False))
    assert _class_scan_enabled("Metal") is False
    assert _class_scan_enabled("Commodity") is True
    assert _class_scan_enabled("Crypto") is True    # never gated here
    assert _class_scan_enabled("???") is True       # unknown fail-open


# ── scan integration ─────────────────────────────────────────────────
def _tick(volume, pct=1.0):
    return {"last": 1.0, "percentage": pct, "quoteVolume": volume}


FUTURES = {
    "BTC/USDT:USDT": _tick(2_000_000_000),
    "CL/USDT:USDT": _tick(150_000_000),       # Commodity — the winner class
    "XAU/USDT:USDT": _tick(15_000_000),       # Metal
    "OPENAI/USDT:USDT": _tick(100_000),       # Pre-IPO — gated out by default
    "ANTHROPIC/USDT:USDT": _tick(40_000),     # Pre-IPO
}
SPOT = {"BTC/USDT": _tick(500_000_000)}


async def _scan(monkeypatch, **cfg_over):
    scanner = MarketScanner()
    monkeypatch.setattr(ms, "CONFIG", _CfgProxy(ms.CONFIG, **cfg_over))

    async def _spot():
        return dict(SPOT)

    async def _fut():
        return dict(FUTURES)

    monkeypatch.setattr(scanner, "_fetch_spot_tickers", _spot)
    monkeypatch.setattr(scanner, "_fetch_futures_tickers", _fut)
    return await scanner._scan_all_markets()


@pytest.mark.asyncio
async def test_pre_ipo_gated_out_of_all_markets_by_default(monkeypatch):
    signals = await _scan(monkeypatch)
    syms = {s.symbol for s in signals}
    assert "CL/USDT:USDT" in syms            # commodity still in
    assert "XAU/USDT:USDT" in syms           # metal still in
    assert "OPENAI/USDT:USDT" not in syms    # pre-IPO gated
    assert "ANTHROPIC/USDT:USDT" not in syms


@pytest.mark.asyncio
async def test_pre_ipo_can_be_re_enabled(monkeypatch):
    signals = await _scan(monkeypatch, scan_class_pre_ipo=True)
    syms = {s.symbol for s in signals}
    assert "OPENAI/USDT:USDT" in syms


@pytest.mark.asyncio
async def test_other_class_toggles_govern(monkeypatch):
    signals = await _scan(monkeypatch, scan_class_metals=False)
    syms = {s.symbol for s in signals}
    assert "XAU/USDT:USDT" not in syms
    assert "CL/USDT:USDT" in syms


def test_explicit_single_category_universe_bypasses_toggles():
    """ASSET_UNIVERSE=pre_ipo is an explicit operator ask — the per-class
    toggles only apply to the default all_markets scan (source pin)."""
    import inspect
    src = inspect.getsource(MarketScanner._scan_futures)
    assert "_class_scan_enabled" not in src
    src_all = inspect.getsource(MarketScanner._scan_all_markets)
    assert "_class_scan_enabled" in src_all


# ── /parity command ──────────────────────────────────────────────────
def _make_update(user_id=6307156912, args=None):
    from unittest.mock import AsyncMock, MagicMock
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = args or []
    return update, ctx


def _replies(update) -> str:
    return "\n".join(
        c[0][0] if c[0] else c.kwargs.get("text", "")
        for c in update.message.reply_text.call_args_list)


def _handler():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(6307156912))
    return handler


@pytest.mark.asyncio
async def test_parity_command_is_admin_only():
    handler = _handler()
    update, ctx = _make_update(user_id=999)
    await handler._cmd_parity(update, ctx)
    text = _replies(update)
    assert "parity" not in text.lower() or "🔒" in text


@pytest.mark.asyncio
async def test_parity_command_renders_asset_class_bucket(tmp_path):
    handler = _handler()
    trades = [
        {"symbol": "BTC/USDT:USDT", "pnl_usd": -5.0, "gross_pnl": -4.0,
         "commission": 1.0, "cost_usd": 20.0, "leverage": 5,
         "signal_type": "trend", "strategy_type": "swing",
         "close_reason": "stop_loss"},
        {"symbol": "CL/USDT:USDT", "pnl_usd": 8.0, "gross_pnl": 9.0,
         "commission": 1.0, "cost_usd": 20.0, "leverage": 5,
         "signal_type": "trend", "strategy_type": "swing",
         "close_reason": "take_profit"},
    ]
    f = tmp_path / "closed.json"
    f.write_text(json.dumps(trades))
    handler.engine.live_executor._closed_trades_file = str(f)

    update, ctx = _make_update()
    await handler._cmd_parity(update, ctx)
    text = _replies(update)
    assert "parity" in text.lower()
    assert "By asset class" in text
    assert "Commodity" in text and "Crypto" in text


@pytest.mark.asyncio
async def test_parity_command_empty_history(tmp_path):
    handler = _handler()
    f = tmp_path / "closed.json"
    f.write_text("[]")
    handler.engine.live_executor._closed_trades_file = str(f)
    update, ctx = _make_update()
    await handler._cmd_parity(update, ctx)
    assert "No closed live trades" in _replies(update)
