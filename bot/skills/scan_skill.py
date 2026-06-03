"""RUNECLAW Deep Scan Skill — Telegram /scan command module."""
from __future__ import annotations
import asyncio, logging
from datetime import datetime
from typing import Optional
import numpy as np
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot.compat import UTC
from bot.core.chart_patterns import scan_all_chart_patterns
from bot.utils.models import Direction, MarketSignal, RiskVerdict, TradeIdea

log = logging.getLogger("runeclaw.scan_skill")

# ── Symbol universe (67) ───────────────────────────────────────────
_SYMS = (
    "BTC ETH SOL TON XRP DOGE BNB SUI ADA LINK BCH AVAX DOT ICP NEAR "
    "LTC AAVE UNI OP WLD WIF ORDI ARB TRX XLM ETC APT HBAR BONK PENDLE "
    "XMR ALGO CRV TIA RENDER INJ JUP FET APE SEI ATOM LDO FIL ENA ONDO "
    "TAO HYPE JTO DYDX DASH ZEC LAB VIRTUAL PUMP FARTCOIN TRUMP BIO M "
    "CHIP B ASTER SIREN SKYAI PENGU WLFI RAVE XPL"
)
UNIVERSE = [f"{s}/USDT" for s in _SYMS.split()]


# ── Helpers ────────────────────────────────────────────────────────

def _compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    g, l = np.mean(np.maximum(deltas[-period:], 0)), np.mean(np.maximum(-deltas[-period:], 0))
    return 100.0 if l == 0 else float(100.0 - 100.0 / (1.0 + g / l))


def _compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    h, l, c = highs[1:], lows[1:], closes[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - c), np.abs(l - c)))
    return float(np.mean(tr[-period:]))


def _dir_emoji(d: str) -> str:
    return "\U0001f7e2" if d == "LONG" else "\U0001f534"

_BARS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

def _score_bar(score: float) -> str:
    return _BARS[min(int(score * len(_BARS)), len(_BARS) - 1)] * 4


async def _scan_symbol(exchange, symbol: str) -> Optional[dict]:
    """Fetch OHLCV and compute scan metrics for one symbol."""
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, "4h", limit=100)
    except Exception:
        return None
    if not ohlcv or len(ohlcv) < 30:
        return None
    o = np.array([c[1] for c in ohlcv], dtype=float)
    h = np.array([c[2] for c in ohlcv], dtype=float)
    l = np.array([c[3] for c in ohlcv], dtype=float)
    c = np.array([c[4] for c in ohlcv], dtype=float)
    v = np.array([c[5] for c in ohlcv], dtype=float)
    price, rsi, atr = float(c[-1]), _compute_rsi(c), _compute_atr(h, l, c)
    vm = float(np.mean(v[-20:])) if len(v) >= 20 else float(np.mean(v))
    vol_ratio = float(v[-1] / vm) if vm > 0 else 1.0
    sma20 = float(np.mean(c[-20:])) if len(c) >= 20 else price
    sma50 = float(np.mean(c[-50:])) if len(c) >= 50 else sma20
    if rsi < 40 and price < sma20:       direction = "SHORT"
    elif rsi > 60 and price > sma20:     direction = "LONG"
    elif price > sma50:                  direction = "LONG"
    else:                                direction = "SHORT"
    mom = abs(rsi - 50.0) / 50.0
    trend_s = min(abs(price - sma50) / sma50 * 10, 1.0) if sma50 > 0 else 0
    score = round(mom * 0.4 + trend_s * 0.3 + min(vol_ratio / 3, 1.0) * 0.3, 3)
    patterns = scan_all_chart_patterns(o, h, l, c)
    return {"sym": symbol, "price": price, "dir": direction, "score": score,
            "rsi": round(rsi, 1), "atr": round(atr, 4),
            "vol_ratio": round(vol_ratio, 2), "sma20": round(sma20, 4),
            "patterns": patterns}


# ── Formatters ─────────────────────────────────────────────────────

def _fmt_quick(r: dict) -> str:
    s = r["sym"].replace("/USDT", "")
    return (f"{_dir_emoji(r['dir'])} <b>{s}</b>  ${r['price']:,.4g}  "
            f"RSI {r['rsi']}  Vol {r['vol_ratio']}x  {_score_bar(r['score'])} {r['score']:.0%}")

def _fmt_detail(r: dict) -> str:
    s = r["sym"].replace("/USDT", "")
    lines = [f"{_dir_emoji(r['dir'])} <b>{s}/USDT</b> -- {r['dir']}",
             f"  Price <code>${r['price']:,.6g}</code>  RSI <code>{r['rsi']}</code>  ATR <code>{r['atr']:.4g}</code>",
             f"  Vol <code>{r['vol_ratio']}x</code>  SMA20 <code>${r['sma20']:,.6g}</code>",
             f"  Score {_score_bar(r['score'])} <b>{r['score']:.0%}</b>"]
    if r.get("patterns"):
        pats = ", ".join(f"{p['name']}({p['signal']},{p['confidence']:.0%})" for p in r["patterns"][:3])
        lines.append(f"  Patterns: {pats}")
    return "\n".join(lines)


# ── Core command handler ───────────────────────────────────────────

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /scan [mode|symbol]."""
    engine = context.bot_data.get("engine")
    if engine is None:
        await update.message.reply_text("Engine not initialized.")
        return

    args = (context.args or [])
    mode = args[0].lower() if args else "quick"

    # Single symbol mode
    if mode.upper() + "/USDT" in UNIVERSE or mode.upper().endswith("/USDT"):
        sym = mode.upper() if "/" in mode else mode.upper() + "/USDT"
        await _scan_single(update, context, engine, sym)
        return

    if mode == "quick":
        await _scan_batch(update, context, engine, top_n=10, patterns=False, ai=False)
    elif mode == "deep":
        await _scan_batch(update, context, engine, top_n=67, patterns=False, ai=False)
    elif mode == "deepall":
        await _scan_batch(update, context, engine, top_n=67, patterns=True, ai=True)
    elif mode == "swing":
        await _scan_filtered(update, context, engine, filter_type="swing")
    elif mode == "scalp":
        await _scan_filtered(update, context, engine, filter_type="scalp")
    else:
        await update.message.reply_text(
            "<b>Usage:</b>\n"
            "/scan — quick top 10\n"
            "/scan deep — full 67 symbols\n"
            "/scan deepall — full + patterns + AI\n"
            "/scan swing — swing filter\n"
            "/scan scalp — scalp filter\n"
            "/scan BTC — single symbol",
            parse_mode="HTML",
        )


async def _scan_batch(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      engine, top_n: int, patterns: bool, ai: bool) -> None:
    msg = await update.message.reply_text("\u23f3 Scanning market...")
    exchange = await engine.scanner._get_exchange()
    raw = await asyncio.gather(*[_scan_symbol(exchange, s) for s in UNIVERSE])
    results = sorted((r for r in raw if r), key=lambda x: x["score"], reverse=True)[:top_n]
    if not results:
        await msg.edit_text("No scannable symbols found."); return
    header = (f"\U0001f9ec <b>RUNECLAW Scan</b> -- "
              f"{len(results)} symbols  |  {datetime.now(UTC).strftime('%H:%M UTC')}\n")
    if top_n <= 10 and not patterns:
        body = "\n".join(_fmt_quick(r) for r in results)
    else:
        body = "\n\n".join(_fmt_detail(r) for r in results[:20])
        if len(results) > 20:
            body += f"\n\n<i>... and {len(results) - 20} more</i>"
    text = header + "\n" + body
    if ai and results:
        text += "\n\n\u23f3 <i>Generating AI summary...</i>"
        await msg.edit_text(text, parse_mode="HTML")
        summary = await _ai_summary(results[:15])
        text = text.replace("\u23f3 <i>Generating AI summary...</i>",
                            f"\U0001f916 <b>AI Summary:</b>\n{summary}")
    top = results[0]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("\u2705 Confirm " + top["sym"].replace("/USDT", ""),
                             callback_data=f"scan_confirm:{top['sym']}:{top['dir']}:{top['price']}"),
        InlineKeyboardButton("\u274c Reject", callback_data=f"scan_reject:{top['sym']}"),
    ]])
    await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)


async def _scan_filtered(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         engine, filter_type: str) -> None:
    msg = await update.message.reply_text(f"\u23f3 Running {filter_type} scan...")
    exchange = await engine.scanner._get_exchange()
    raw = await asyncio.gather(*[_scan_symbol(exchange, s) for s in UNIVERSE])
    results = [r for r in raw if r is not None]
    if filter_type == "swing":
        results = [r for r in results if 30 <= r["rsi"] <= 55 and r["dir"] == "LONG" and r["score"] >= 0.3]
        label = "Swing"
    else:
        results = [r for r in results if r["vol_ratio"] >= 1.5 and r["score"] >= 0.4]
        label = "Scalp"
    results = sorted(results, key=lambda x: x["score"], reverse=True)[:15]
    if not results:
        await msg.edit_text(f"No {label.lower()} setups found right now."); return
    header = f"\U0001f3af <b>RUNECLAW {label} Scan</b> -- {len(results)} setups\n"
    await msg.edit_text(header + "\n" + "\n\n".join(_fmt_detail(r) for r in results), parse_mode="HTML")


async def _scan_single(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       engine, symbol: str) -> None:
    msg = await update.message.reply_text(f"\u23f3 Deep scanning {symbol}...")
    exchange = await engine.scanner._get_exchange()
    result = await _scan_symbol(exchange, symbol)
    if result is None:
        await msg.edit_text(f"Could not fetch data for {symbol}."); return
    text = f"\U0001f50e <b>Deep Scan: {symbol}</b>\n\n" + _fmt_detail(result)
    if result["patterns"]:
        text += "\n\n<b>Chart Patterns:</b>\n"
        for p in result["patterns"][:5]:
            text += (f"  \u2022 <b>{p['name']}</b> -- {p['signal']} ({p.get('confidence',0):.0%})\n"
                     f"    <i>{p.get('description','')}</i>\n")
    try:
        signal = MarketSignal(symbol=symbol, price=result["price"],
                              change_pct_24h=0.0, volume_usd_24h=0.0,
                              momentum_score=result["score"])
        idea: Optional[TradeIdea] = await engine._analyze_signal(signal, timeframe="1h")
        if idea:
            text += (f"\n<b>Trade Idea:</b>\n"
                     f"  {_dir_emoji(idea.direction.value)} {idea.direction.value}  "
                     f"Entry <code>${idea.entry_price:,.6g}</code>\n"
                     f"  SL <code>${idea.stop_loss:,.6g}</code>  TP <code>${idea.take_profit:,.6g}</code>\n"
                     f"  Conf <b>{idea.confidence:.0%}</b>  R:R <code>{idea.risk_reward_ratio}</code>\n"
                     f"  <i>{idea.reasoning[:120]}</i>")
            rc = engine.risk.evaluate(idea, atr=result["atr"])
            ve = "\u2705" if rc.verdict == RiskVerdict.APPROVED else "\u26a0\ufe0f"
            text += f"\n\n<b>Risk:</b> {ve} {rc.verdict.value} -- <i>{rc.reason}</i>"
    except Exception as exc:
        log.warning("Engine analysis failed for %s: %s", symbol, exc)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("\u2705 Confirm",
                             callback_data=f"scan_confirm:{symbol}:{result['dir']}:{result['price']}"),
        InlineKeyboardButton("\u274c Reject", callback_data=f"scan_reject:{symbol}"),
    ]])
    await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ── AI summary helper ─────────────────────────────────────────────

async def _ai_summary(results: list[dict]) -> str:
    try:
        from bot.llm.provider import llm_complete, LLMTier
    except ImportError:
        return "<i>LLM provider unavailable.</i>"
    lines = []
    for r in results:
        pats = ", ".join(p["name"] for p in r.get("patterns", [])[:2]) or "none"
        lines.append(f"{r['sym']}: {r['dir']} score={r['score']:.0%} RSI={r['rsi']} vol={r['vol_ratio']}x pats=[{pats}]")
    prompt = ("You are RUNECLAW, an elite crypto trading AI. Given the scan results below, "
              "write a 2-3 sentence market summary highlighting strongest setups, "
              "prevailing bias, and notable patterns. Be concise.\n\n" + "\n".join(lines))
    try:
        s = await llm_complete(prompt, tier=LLMTier.SCAN, max_tokens=300)
        return s.strip() if s else "<i>No summary generated.</i>"
    except Exception as exc:
        log.warning("AI summary failed: %s", exc)
        return "<i>AI summary unavailable.</i>"


# ── Callback handler ──────────────────────────────────────────────

async def callback_confirm_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callbacks from scan results."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("scan_reject:"):
        sym = data.split(":")[1]
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"\u274c <b>{sym}</b> rejected by operator.", parse_mode="HTML")
        return
    if not data.startswith("scan_confirm:"):
        return
    parts = data.split(":")
    if len(parts) < 4:
        await query.message.reply_text("Invalid callback data."); return
    _, symbol, dir_str, price_str = parts[:4]
    price = float(price_str)
    direction = Direction.LONG if dir_str == "LONG" else Direction.SHORT
    engine = context.bot_data.get("engine")
    if engine is None:
        await query.message.reply_text("Engine not available."); return
    sl = round(price * (0.97 if direction == Direction.LONG else 1.03), 6)
    tp = round(price * (1.06 if direction == Direction.LONG else 0.94), 6)
    try:
        idea = TradeIdea(asset=symbol, direction=direction, entry_price=price,
                         stop_loss=sl, take_profit=tp, confidence=0.6,
                         reasoning=f"Operator-confirmed scan signal for {symbol}",
                         source="scan_skill")
        exchange = await engine.scanner._get_exchange()
        ohlcv = await exchange.fetch_ohlcv(symbol, "4h", limit=30)
        h = np.array([c[2] for c in ohlcv], dtype=float)
        l = np.array([c[3] for c in ohlcv], dtype=float)
        c = np.array([c[4] for c in ohlcv], dtype=float)
        atr = _compute_atr(h, l, c)
        rc = engine.risk.evaluate(idea, atr=atr)
        ve = "\u2705" if rc.verdict == RiskVerdict.APPROVED else "\u26a0\ufe0f"
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"{ve} <b>{symbol} {direction.value}</b> -- Risk: <b>{rc.verdict.value}</b>\n"
            f"  Entry <code>${price:,.6g}</code>  SL <code>${sl:,.6g}</code>  TP <code>${tp:,.6g}</code>\n"
            f"  R:R <code>{idea.risk_reward_ratio}</code>\n  <i>{rc.reason}</i>",
            parse_mode="HTML")
    except Exception as exc:
        log.error("Confirm callback failed for %s: %s", symbol, exc)
        await query.message.reply_text(f"\u26a0\ufe0f Risk evaluation failed: {exc}", parse_mode="HTML")
