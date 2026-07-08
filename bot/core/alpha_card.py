"""Daily Alpha insight card — the bot's own answer to exchange "AI insight"
panels (GetAgent/Bybit-style), assembled ENTIRELY from data RUNECLAW already
computes or can fetch free from Bitget's public API:

  • Chart analysis   — MTF trend (1D/4H/1H), key support/resistance from
                       swing structure, MACD/RSI/ADX strength tags
  • Positioning      — funding rate (who pays), open interest, long/short
                       account ratio (the crypto-native stand-in for
                       equity "analyst ratings", which don't exist for perps)
  • Sentiment        — Fear & Greed regime

No licensed data, no scraping — and unlike an exchange card, this is the SAME
analysis the bot trades on, so it doubles as transparency into the gate.

Design: ``build_alpha_insight`` does the async data gathering with every
section fail-open (a missing feed renders "n/a", never an error), and
``format_alpha_card`` is a PURE formatter over the gathered dict so the
layout is unit-testable without a network.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from bot.compat import UTC
from datetime import datetime

logger = logging.getLogger("runeclaw.alpha")


# ── pure helpers (unit-tested) ────────────────────────────────────────

def normalize_alpha_symbol(raw: str) -> str:
    """User input → ccxt perp symbol: 'btc' → 'BTC/USDT:USDT'."""
    s = (raw or "").upper().strip().replace(":USDT", "").replace("USDT", "").strip("/")
    s = s.replace("/", "")
    return f"{s}/USDT:USDT" if s else ""


def pick_levels(swing_highs: list, swing_lows: list, price: float) -> dict:
    """Nearest/deeper S/R from swing points around the current price.

    swing_highs/lows are [(idx, price), ...] as returned by the swing
    detectors. Returns supports (below price, nearest first) and resistances
    (above price, nearest first), deduped within 0.1%.
    """
    def _dedupe(vals: list[float]) -> list[float]:
        out: list[float] = []
        for v in vals:
            if not any(abs(v - o) / o < 0.001 for o in out if o > 0):
                out.append(v)
        return out

    sups = _dedupe(sorted((p for _, p in swing_lows if 0 < p < price), reverse=True))
    ress = _dedupe(sorted(p for _, p in swing_highs if p > price))
    return {"supports": sups[:3], "resistances": ress[:3]}


def trend_word(direction: float) -> str:
    if direction > 0:
        return "up"
    if direction < 0:
        return "down"
    return "flat"


def overall_trend_label(htf_trend: str, bos_dir: int, choch_dir: int) -> str:
    """Headline label in the exchange-card idiom."""
    t = (htf_trend or "").lower()
    if t == "bullish":
        return "Breakout Continuation" if bos_dir > 0 else "Uptrend"
    if t == "bearish":
        return "Breakdown Continuation" if bos_dir < 0 else "Downtrend"
    if choch_dir > 0:
        return "Possible Reversal Up"
    if choch_dir < 0:
        return "Possible Reversal Down"
    return "Range / Mixed"


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.6f}"


# ── async gathering (every section fail-open) ─────────────────────────

async def build_alpha_insight(engine: Any, symbol: str) -> dict:
    """Gather everything for the card. Each section independently fail-open."""
    d: dict[str, Any] = {"symbol": symbol, "generated": datetime.now(UTC)}

    exchange = None
    try:
        exchange = await engine.scanner._get_futures_exchange()
    except Exception:
        try:
            exchange = await engine.scanner._get_exchange()
        except Exception:
            exchange = None
    if exchange is None:
        d["error"] = "exchange unavailable"
        return d

    # Price
    try:
        tk = await exchange.fetch_ticker(symbol)
        d["price"] = float(tk.get("last") or 0)
        d["change_24h_pct"] = float(tk.get("percentage") or 0)
    except Exception as exc:
        d["error"] = f"unknown symbol or no ticker ({str(exc)[:80]})"
        return d

    # Candles for MTF + indicators + levels
    candles: dict[str, list] = {}
    for tf in ("1h", "4h", "1d"):
        try:
            candles[tf] = await exchange.fetch_ohlcv(symbol, tf, limit=200)
        except Exception:
            candles[tf] = []

    # MTF trend + structure
    try:
        from bot.core.multi_timeframe import MTFConfluence
        mtf = MTFConfluence().analyze(
            candles_1h=candles.get("1h") or None,
            candles_4h=candles.get("4h") or None,
            candles_1d=candles.get("1d") or None,
        )
        d["htf_trend"] = mtf.htf_trend
        d["bos_dir"] = mtf.bos_dir
        d["choch_dir"] = mtf.choch_dir
        d["per_tf"] = {
            tf: trend_word(float((info or {}).get("trend_score", 0)))
            for tf, info in (mtf.per_tf or {}).items()
        }
    except Exception as exc:
        logger.debug("alpha MTF failed: %s", exc)

    # Key levels from 1h swing structure
    try:
        from bot.core.multi_timeframe import _find_swings
        c1 = candles.get("1h") or []
        if len(c1) >= 30:
            highs = np.array([c[2] for c in c1], dtype=float)
            lows = np.array([c[3] for c in c1], dtype=float)
            swings = _find_swings(highs, lows)
            d["levels"] = pick_levels(
                swings.get("swing_highs", []), swings.get("swing_lows", []),
                d.get("price", 0.0))
    except Exception as exc:
        logger.debug("alpha levels failed: %s", exc)

    # Strength indicators
    try:
        from bot.core.ta_utils import _compute_adx, macd_histogram_series, rsi_series
        strength: dict[str, Any] = {}
        c1 = candles.get("1h") or []
        if len(c1) >= 40:
            closes1 = np.array([c[4] for c in c1], dtype=float)
            strength["rsi_1h"] = float(rsi_series(closes1)[-1])
            highs1 = np.array([c[2] for c in c1], dtype=float)
            lows1 = np.array([c[3] for c in c1], dtype=float)
            adx = _compute_adx(highs1, lows1, closes1)
            strength["adx_1h"] = float(adx.get("adx", 0) or 0)
        for tf_key, tf in (("macd_4h", "4h"), ("macd_1d", "1d")):
            ctf = candles.get(tf) or []
            if len(ctf) >= 40:
                closes = np.array([c[4] for c in ctf], dtype=float)
                strength[tf_key] = float(macd_histogram_series(closes)[-1])
        d["strength"] = strength
    except Exception as exc:
        logger.debug("alpha strength failed: %s", exc)

    # Positioning: funding / OI / long-short (each independently fail-open)
    try:
        fr = await exchange.fetch_funding_rate(symbol)
        d["funding_rate"] = float(fr.get("fundingRate") or 0)
    except Exception:
        pass
    try:
        oi = await exchange.fetch_open_interest(symbol)
        # Bitget reports openInterestAmount in BASE units (e.g. BTC) with
        # openInterestValue=None — convert via last price when needed.
        oi_usd = float(oi.get("openInterestValue") or 0)
        if oi_usd <= 0:
            oi_base = float(oi.get("openInterestAmount") or 0)
            oi_usd = oi_base * float(d.get("price") or 0)
        if oi_usd > 0:
            d["open_interest_usd"] = oi_usd
    except Exception:
        pass
    try:
        ls = await exchange.fetch_long_short_ratio_history(symbol, limit=1)
        if ls:
            d["long_short_ratio"] = float(ls[-1].get("longShortRatio") or 0)
    except Exception:
        pass

    # Sentiment (Fear & Greed) — read the analyzer's CACHED value (refreshed
    # during scans); never trigger a fetch from a display command.
    try:
        fg = getattr(engine.analyzer._sentiment, "_fear_greed_value", None)
        if fg is not None:
            d["fear_greed"] = float(fg)
            d["sentiment_regime"] = (
                "extreme fear" if fg < 25 else "fear" if fg < 45
                else "neutral" if fg < 55 else "greed" if fg < 75
                else "extreme greed")
    except Exception:
        pass

    return d


# ── pure formatter ────────────────────────────────────────────────────

_DOT = {"up": "🟢", "down": "🔴", "flat": "⚪"}


def format_alpha_card(d: dict) -> str:
    """Render the gathered insight as a Telegram-HTML card. Pure."""
    import html as _html

    sym_disp = _html.escape(str(d.get("symbol", "")).replace("/USDT:USDT", ""))
    if d.get("error"):
        return f"⚠️ Alpha card unavailable for <b>{sym_disp}</b>: {_html.escape(str(d['error']))}"

    price = float(d.get("price") or 0)
    chg = float(d.get("change_24h_pct") or 0)
    lines: list[str] = []
    lines.append(f"📡 <b>{sym_disp} Daily Alpha</b>")
    lines.append(f"Price <code>${fmt_price(price)}</code> ({chg:+.2f}% 24h)")
    lines.append("")

    # ── Chart analysis ──
    label = overall_trend_label(
        d.get("htf_trend", ""), int(d.get("bos_dir", 0)), int(d.get("choch_dir", 0)))
    lines.append(f"📈 <b>Chart analysis</b> — {label}")
    per_tf = d.get("per_tf") or {}
    if per_tf:
        order = [tf for tf in ("1d", "4h", "1h") if tf in per_tf]
        lines.append("  " + "  ".join(
            f"{_DOT.get(per_tf[tf], '⚪')} {tf.upper()}" for tf in order))
    lv = d.get("levels") or {}
    sups, ress = lv.get("supports") or [], lv.get("resistances") or []
    if sups:
        lines.append("  Support: " + ", ".join(f"<code>{fmt_price(s)}</code>" for s in sups))
    if ress:
        lines.append("  Resistance: " + ", ".join(f"<code>{fmt_price(r)}</code>" for r in ress))

    # ── Strength ──
    st = d.get("strength") or {}
    if st:
        lines.append("")
        lines.append("💪 <b>Strength</b>")
        if "macd_1d" in st or "macd_4h" in st:
            parts = []
            for k, tf in (("macd_1d", "1D"), ("macd_4h", "4H")):
                if k in st:
                    tag = "Buy" if st[k] > 0 else "Sell"
                    parts.append(f"{tf} hist {st[k]:+.4g} [{tag}]")
            lines.append("  MACD: " + " | ".join(parts))
        if "rsi_1h" in st:
            r = st["rsi_1h"]
            zone = "oversold" if r < 30 else ("overbought" if r > 70 else "neutral")
            lines.append(f"  RSI(1H): {r:.1f} ({zone})")
        if "adx_1h" in st:
            a = st["adx_1h"]
            lines.append(f"  ADX(1H): {a:.1f} ({'trending' if a >= 20 else 'weak trend'})")

    # ── Positioning (the crypto-native "analyst insights") ──
    pos_lines = []
    if "funding_rate" in d:
        f = d["funding_rate"] * 100
        payer = "longs pay" if f > 0 else ("shorts pay" if f < 0 else "flat")
        pos_lines.append(f"  Funding: {f:+.4f}% ({payer})")
    if "open_interest_usd" in d and d["open_interest_usd"] > 0:
        oi = d["open_interest_usd"]
        oi_s = f"${oi / 1e9:.2f}B" if oi >= 1e9 else (
            f"${oi / 1e6:.1f}M" if oi >= 1e6 else f"${oi:,.0f}")
        pos_lines.append(f"  Open interest: {oi_s}")
    if "long_short_ratio" in d and d["long_short_ratio"] > 0:
        r = d["long_short_ratio"]
        long_pct = r / (1 + r) * 100
        pos_lines.append(
            f"  Accounts: {long_pct:.0f}% long / {100 - long_pct:.0f}% short "
            f"(ratio {r:.2f})")
    if pos_lines:
        lines.append("")
        lines.append("⚖️ <b>Positioning</b>")
        lines.extend(pos_lines)

    # ── Sentiment ──
    if d.get("fear_greed"):
        lines.append("")
        regime = str(d.get("sentiment_regime", "")).replace("SentimentRegime.", "")
        lines.append(f"🧭 <b>Sentiment</b>: Fear&Greed {d['fear_greed']:.0f}"
                     + (f" ({_html.escape(regime.lower())})" if regime else ""))

    lines.append("")
    lines.append("<i>Same data the bot trades on — not investment advice.</i>")
    return "\n".join(lines)
