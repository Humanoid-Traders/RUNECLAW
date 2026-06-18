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
from bot.formatters.rich_cards import (
    fetch_analysis_data,
    render_analysis_card,
    compute_rsi as _compute_rsi,
    compute_atr as _compute_atr,
)

log = logging.getLogger("runeclaw.scan_skill")


# ── Dashboard sync helper ─────────────────────────────────────────

def _build_scan_payload(results: list[dict], engine=None) -> dict:
    """Convert raw scan results into the website dashboard schema and push.

    Transforms _scan_symbol() dicts into the format expected by the War Room:
    regime, symbols, entry_cards, key_call, circuit_breaker.
    """
    from bot.config import CONFIG

    now = datetime.now(UTC)

    # ── Regime from BTC ──
    btc = next((r for r in results if "BTC" in r["sym"]), None)
    regime = {"label": "NEUTRAL", "score": 0.0, "gate": 0, "long_short": "", "funding": ""}
    if btc:
        regime["gate"] = btc["price"]
        if btc["rsi"] > 60 and btc["dir"] == "LONG":
            regime["label"] = "BULLISH"
            regime["score"] = min((btc["rsi"] - 50) / 30, 1.0)
        elif btc["rsi"] < 40 and btc["dir"] == "SHORT":
            regime["label"] = "BEARISH"
            regime["score"] = -min((50 - btc["rsi"]) / 30, 1.0)
        else:
            regime["label"] = "NEUTRAL"
            regime["score"] = (btc["rsi"] - 50) / 50

    # ── Circuit breaker from engine ──
    cb_rules = []
    if engine:
        try:
            from bot.risk.risk_engine import RiskEngine
            risk = engine.risk
            portfolio = engine.portfolio
            state = portfolio.snapshot()
            cb_active = risk.circuit_breaker_active
            cb_rules = [
                {"label": "Circuit Breaker", "active": cb_active},
                {"label": f"Daily PnL: ${state.daily_pnl:+.2f}", "active": state.daily_pnl < -state.equity_usd * 0.05},
                {"label": f"Open Positions: {state.open_positions}/{CONFIG.risk.max_open_positions}", "active": state.open_positions >= CONFIG.risk.max_open_positions},
            ]
        except Exception as exc:
            log.warning("CB data unavailable: %s", exc)

    # ── Symbols table ──
    symbols = {}
    for r in results:
        sym_key = r["sym"].replace("/", "")
        score = r["score"]
        if score >= 0.6:
            status, label = "setup", "SETUP"
        elif score >= 0.4:
            status, label = "alert", "ALERT"
        elif score >= 0.2:
            status, label = "watch", "WATCH"
        else:
            status, label = "skip", "SKIP"

        # Book ratio from vol_ratio (capped at reasonable range)
        vr = r.get("vol_ratio", 1.0) or 1.0
        book_ratio = round(vr, 2)
        book_side = "BID" if r["dir"] == "LONG" else "ASK"

        symbols[sym_key] = {
            "book_ratio": book_ratio,
            "book_side": book_side,
            "status": status,
            "status_label": label,
            "price": r["price"],
            "rsi": r["rsi"],
            "score": score,
            "direction": r["dir"],
            "vol_ratio": vr,
            "atr": r.get("atr", 0),
        }

    # ── Entry cards (top setups only) ──
    entry_cards = []
    setups = [r for r in results if r["score"] >= 0.4]
    for r in setups[:8]:
        price = r["price"]
        # ATR fallback: if 0 or missing, use 2% of price as proxy
        atr = r.get("atr", 0) or 0
        if atr <= 0:
            atr = price * 0.02

        if r["dir"] == "LONG":
            sl = round(price - atr * 2.5, 8)
            tp1 = round(price + atr * 3.0, 8)
            tp2 = round(price + atr * 5.0, 8)
        else:
            sl = round(price + atr * 2.5, 8)
            tp1 = round(price - atr * 3.0, 8)
            tp2 = round(price - atr * 5.0, 8)

        risk_dist = abs(price - sl)
        reward_dist = abs(tp1 - price)
        rr = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0

        # Patterns as trigger description
        pat_names = [p["name"] for p in r.get("patterns", [])[:2]]
        trigger = ", ".join(pat_names) if pat_names else f"RSI {r['rsi']}, Vol {r.get('vol_ratio', 1.0):.1f}x"

        # Margin: ~5% of a $100 notional position
        margin = round(100 * 0.05, 2)  # $5 margin per $100 at 20x

        vr = r.get("vol_ratio", 1.0) or 1.0
        entry_cards.append({
            "symbol": r["sym"].replace("/USDT", ""),
            "direction": r["dir"],
            "score": r["score"],
            "entry": str(round(price, 8)),
            "stop_loss": str(sl),
            "tp1": str(tp1),
            "tp2": str(tp2),
            "margin": str(margin),
            "rr": str(rr),
            "book_ratio": round(vr, 2),
            "trigger": trigger,
            "thesis": f"{r['dir']} bias | RSI {r['rsi']} | Score {r['score']:.0%} | Vol {vr:.1f}x avg",
        })

    # ── Key call narrative ──
    if results:
        longs = sum(1 for r in results if r["dir"] == "LONG")
        shorts = len(results) - longs
        top3 = results[:3]
        top_names = ", ".join(r["sym"].replace("/USDT", "") for r in top3)
        bias = "LONG" if longs > shorts else "SHORT" if shorts > longs else "MIXED"
        key_call = (
            f"<b>Market bias: {bias}</b> ({longs}L / {shorts}S across {len(results)} symbols)\n"
            f"Top movers: {top_names}\n"
        )
        if btc:
            key_call += f"BTC RSI: {btc['rsi']:.0f} | Price: ${btc['price']:,.2f}\n"
        key_call += f"Scanned at {now.strftime('%H:%M UTC')}"
    else:
        key_call = "No scan data available."

    return {
        "regime": regime,
        "circuit_breaker": {"rules": cb_rules},
        "symbols": symbols,
        "entry_cards": entry_cards,
        "key_call": key_call,
        "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _push_scan_to_dashboard(results: list[dict], engine=None) -> None:
    """Build scan payload and push to website in background."""
    try:
        from bot.utils.website_sync import sync_scan_in_background
        payload = _build_scan_payload(results, engine)
        sync_scan_in_background(payload)
    except Exception as exc:
        log.warning("Dashboard scan push failed: %s", exc)


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

# RSI and ATR imported from bot.formatters.rich_cards


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
    change = r.get("change_pct", 0)
    change_str = f" | {'+' if change >= 0 else ''}{change:.1f}%" if change else ""
    return (f"{_dir_emoji(r['dir'])} <b>{s}</b>  ${r['price']:,.4g}{change_str}  "
            f"RSI {r['rsi']}  Vol {r['vol_ratio']}x  {_score_bar(r['score'])} {r['score']:.0%}")

def _fmt_detail(r: dict) -> str:
    s = r["sym"].replace("/USDT", "")
    change = r.get("change_pct", 0)
    change_str = f" | {'+' if change >= 0 else ''}{change:.1f}%" if change else ""
    lines = [f"{_dir_emoji(r['dir'])} <b>{s}/USDT</b> \u2014 {r['dir']}{change_str}",
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

    # Push scan data to website dashboard
    all_results = sorted((r for r in raw if r), key=lambda x: x["score"], reverse=True)
    _push_scan_to_dashboard(all_results, engine)


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

    # Push all scan results (not just filtered) to dashboard
    all_results = sorted((r for r in raw if r is not None), key=lambda x: x["score"], reverse=True)
    _push_scan_to_dashboard(all_results, engine)


async def _scan_single(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       engine, symbol: str) -> None:
    msg = await update.message.reply_text(f"\u2694\ufe0f <i>Deep scanning {symbol}...</i>", parse_mode="HTML")
    exchange = await engine.scanner._get_exchange()

    # Fetch rich analysis data
    data = await fetch_analysis_data(exchange, symbol, timeframe="1h")
    result = await _scan_symbol(exchange, symbol)

    if data is None and result is None:
        await msg.edit_text(f"Could not fetch data for {symbol}.")
        return

    # Try to generate a trade idea
    idea = None
    try:
        if result:
            signal = MarketSignal(symbol=symbol, price=result["price"],
                                  change_pct_24h=data["change_pct"] if data else 0.0,
                                  volume_usd_24h=data["volume_24h_usd"] if data else 0.0,
                                  momentum_score=result["score"])
            idea = await engine._analyze_signal(signal, timeframe="1h")
    except Exception as exc:
        log.warning("Engine analysis failed for %s: %s", symbol, exc)

    if data:
        # Rich analysis card
        text = render_analysis_card(data, idea)

        # Add chart patterns if available
        if result and result.get("patterns"):
            text += "\n\n<b>Chart Patterns:</b>\n"
            for p in result["patterns"][:5]:
                text += (f"  \u2022 <b>{p['name']}</b> \u2014 {p['signal']} ({p.get('confidence',0):.0%})\n"
                         f"    <i>{p.get('description','')}</i>\n")

        # Add risk verdict
        if idea and result:
            rc = engine.risk.evaluate(idea, atr=result["atr"])
            ve = "\u2705" if rc.verdict == RiskVerdict.APPROVED else "\u26a0\ufe0f"
            text += f"\n<b>Risk:</b> {ve} {rc.verdict.value} \u2014 <i>{rc.reason}</i>"
    else:
        # Fallback to old format
        text = f"\U0001f50e <b>Deep Scan: {symbol}</b>\n\n" + _fmt_detail(result)
        if result and result["patterns"]:
            text += "\n\n<b>Chart Patterns:</b>\n"
            for p in result["patterns"][:5]:
                text += (f"  \u2022 <b>{p['name']}</b> -- {p['signal']} ({p.get('confidence',0):.0%})\n"
                         f"    <i>{p.get('description','')}</i>\n")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("\u2705 Confirm",
                             callback_data=f"scan_confirm:{symbol}:{result['dir'] if result else 'LONG'}:{result['price'] if result else 0}"),
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
