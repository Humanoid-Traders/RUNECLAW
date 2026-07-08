"""
Daily Alpha card: the pure pieces (symbol normalization, level picking,
trend labels, formatter) are unit-tested without a network; the async
gatherer is exercised with a mocked engine/exchange to prove every section
fails open (missing feeds render "n/a"-style omissions, never an error).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from bot.core.alpha_card import (
    build_alpha_insight,
    fmt_price,
    format_alpha_card,
    normalize_alpha_symbol,
    overall_trend_label,
    pick_levels,
)


# ── symbol normalization ─────────────────────────────────────────────
def test_normalize_variants():
    assert normalize_alpha_symbol("btc") == "BTC/USDT:USDT"
    assert normalize_alpha_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert normalize_alpha_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    assert normalize_alpha_symbol("BTCUSDT") == "BTC/USDT:USDT"
    assert normalize_alpha_symbol("") == ""


# ── level picking ────────────────────────────────────────────────────
def test_pick_levels_orders_and_sides():
    swing_highs = [(10, 105.0), (20, 110.0), (30, 102.0)]
    swing_lows = [(5, 95.0), (15, 90.0), (25, 97.0)]
    lv = pick_levels(swing_highs, swing_lows, price=100.0)
    assert lv["supports"] == [97.0, 95.0, 90.0]        # nearest first, below price
    assert lv["resistances"] == [102.0, 105.0, 110.0]  # nearest first, above price


def test_pick_levels_dedupes_near_identical():
    lv = pick_levels([(1, 105.0), (2, 105.05)], [(1, 95.0), (2, 94.96)], 100.0)
    assert len(lv["resistances"]) == 1
    assert len(lv["supports"]) == 1


# ── trend labels ─────────────────────────────────────────────────────
def test_overall_trend_labels():
    assert overall_trend_label("bullish", 1, 0) == "Breakout Continuation"
    assert overall_trend_label("bullish", 0, 0) == "Uptrend"
    assert overall_trend_label("bearish", -1, 0) == "Breakdown Continuation"
    assert overall_trend_label("neutral", 0, 1) == "Possible Reversal Up"
    assert overall_trend_label("", 0, 0) == "Range / Mixed"


# ── formatter ────────────────────────────────────────────────────────
def _full_data():
    return {
        "symbol": "BTC/USDT:USDT", "price": 63105.0, "change_24h_pct": 1.23,
        "htf_trend": "bullish", "bos_dir": 1, "choch_dir": 0,
        "per_tf": {"1d": "up", "4h": "up", "1h": "flat"},
        "levels": {"supports": [62800.0, 61500.0], "resistances": [63500.0, 64200.0]},
        "strength": {"rsi_1h": 55.2, "adx_1h": 24.1, "macd_1d": 120.5, "macd_4h": -3.2},
        "funding_rate": 0.0001, "open_interest_usd": 2.4e9,
        "long_short_ratio": 1.63, "fear_greed": 62.0, "sentiment_regime": "greed",
    }


def test_format_full_card_contains_all_sections():
    card = format_alpha_card(_full_data())
    assert "BTC Daily Alpha" in card
    assert "Breakout Continuation" in card
    assert "Support:" in card and "62,800" in card
    assert "Resistance:" in card and "63,500" in card
    assert "MACD:" in card and "[Buy]" in card and "[Sell]" in card
    assert "RSI(1H): 55.2 (neutral)" in card
    assert "Funding: +0.0100% (longs pay)" in card
    assert "Open interest: $2.40B" in card
    assert "62% long / 38% short" in card
    assert "Fear&Greed 62 (greed)" in card
    assert "not investment advice" in card


def test_format_minimal_card_fails_open():
    """Only price known — every other section silently omitted, no crash."""
    card = format_alpha_card({"symbol": "XPT/USDT:USDT", "price": 1640.92,
                              "change_24h_pct": -0.5})
    assert "XPT Daily Alpha" in card
    assert "Funding" not in card and "MACD" not in card
    assert "Support" not in card


def test_format_error_card():
    card = format_alpha_card({"symbol": "ZZZ/USDT:USDT", "error": "unknown symbol"})
    assert "unavailable" in card and "ZZZ" in card


def test_fmt_price_magnitudes():
    assert fmt_price(63105.0) == "63,105.00"
    assert fmt_price(1.2345) == "1.2345"
    assert fmt_price(0.004879) == "0.004879"


# ── async gatherer fails open per section ────────────────────────────
def test_build_insight_sections_fail_open():
    """Ticker works; every other feed raises → card still renders with price
    only (no exception, no 'error' key)."""
    engine = MagicMock()
    exchange = MagicMock()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0, "percentage": 2.0})
    exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("feed down"))
    exchange.fetch_funding_rate = AsyncMock(side_effect=Exception("no"))
    exchange.fetch_open_interest = AsyncMock(side_effect=Exception("no"))
    exchange.fetch_long_short_ratio_history = AsyncMock(side_effect=Exception("no"))
    engine.scanner._get_futures_exchange = AsyncMock(return_value=exchange)
    engine.analyzer._sentiment = MagicMock(_fear_greed_value=None)

    d = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        build_alpha_insight(engine, "AAA/USDT:USDT"))
    assert "error" not in d
    assert d["price"] == 100.0
    card = format_alpha_card(d)
    assert "AAA Daily Alpha" in card


def test_build_insight_unknown_symbol_errors_cleanly():
    engine = MagicMock()
    exchange = MagicMock()
    exchange.fetch_ticker = AsyncMock(side_effect=Exception("symbol not found"))
    engine.scanner._get_futures_exchange = AsyncMock(return_value=exchange)

    d = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        build_alpha_insight(engine, "NOPE/USDT:USDT"))
    assert "error" in d
    assert "unavailable" in format_alpha_card(d)
