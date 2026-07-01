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


# ── Live exchange data fetcher ───────────────────────────────────

def _fetch_live_exchange_data() -> Optional[dict]:
    """Fetch real account balance, positions, and trade history from Bitget.
    Returns dict with equity, net_pnl, win_rate, total_trades, open_count,
    open_positions, closed_trades. Returns None on failure.
    """
    import json, os

    result = {
        "equity": 0, "net_pnl": 0, "win_rate": 0,
        "total_trades": 0, "open_count": 0,
        "open_positions": [], "closed_trades": [],
    }

    # 1. Read closed trades from disk (bot's trade log)
    trades_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "closed_trades.json"
    )
    # Also check parent directory
    if not os.path.exists(trades_file):
        trades_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "closed_trades.json"
        )

    closed_trades = []
    if os.path.exists(trades_file):
        try:
            with open(trades_file) as f:
                closed_trades = json.load(f)
            if isinstance(closed_trades, dict):
                closed_trades = closed_trades.get("trades", [])
        except Exception as exc:
            log.warning("Failed to read closed_trades.json: %s", exc)

    total = len(closed_trades)
    total_pnl = sum(float(t.get("net_pnl", t.get("pnl", 0)) or 0) for t in closed_trades)
    wins = sum(1 for t in closed_trades if float(t.get("net_pnl", t.get("pnl", 0)) or 0) > 0)
    win_rate = (wins / total * 100) if total > 0 else 0

    # Format closed trades for dashboard (last 20)
    for t in closed_trades[-20:]:
        result["closed_trades"].append({
            "symbol": t.get("symbol", ""),
            "direction": t.get("direction", t.get("side", "")),
            "entry_price": float(t.get("entry_price", t.get("entry", 0)) or 0),
            "exit_price": float(t.get("exit_price", t.get("exit", 0)) or 0),
            "pnl": float(t.get("net_pnl", t.get("pnl", 0)) or 0),
            "closed_at": t.get("closed_at", t.get("timestamp", "")),
        })

    # 2. Fetch real balance + positions from Bitget via ccxt (sync)
    try:
        import ccxt as ccxt_sync
        exchange = ccxt_sync.bitget({
            "apiKey": os.getenv("BITGET_API_KEY", ""),
            "secret": os.getenv("BITGET_API_SECRET", ""),
            "password": os.getenv("BITGET_PASSPHRASE", ""),
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "uta": True,  # Unified Trading Account mode
            },
        })

        # Fetch balance (try multiple approaches for UTA compatibility)
        try:
            bal = exchange.fetch_balance({"type": "swap", "productType": "USDT-FUTURES"})
        except Exception:
            try:
                bal = exchange.fetch_balance({"type": "swap"})
            except Exception:
                bal = exchange.fetch_balance()

        usdt = bal.get("USDT", {})
        if isinstance(usdt, dict):
            equity = float(usdt.get("total", 0) or 0)
        else:
            equity = float(usdt or 0)
        if equity == 0:
            equity = float(bal.get("total", {}).get("USDT", 0) or 0)

        # Fetch open positions (try with and without productType)
        try:
            positions = exchange.fetch_positions(params={"productType": "USDT-FUTURES"})
        except Exception:
            try:
                positions = exchange.fetch_positions()
            except Exception:
                positions = []
        open_pos = [p for p in positions if abs(float(p.get("contracts", 0) or 0)) > 0]

        unrealized_pnl = 0
        for p in open_pos:
            upnl = float(p.get("unrealizedPnl", 0) or 0)
            unrealized_pnl += upnl
            result["open_positions"].append({
                "symbol": p.get("symbol", "").replace(":USDT", "").replace("/", ""),
                "direction": (p.get("side", "") or "").upper(),
                "entry_price": float(p.get("entryPrice", 0) or 0),
                "contracts": float(p.get("contracts", 0) or 0),
                "notional": float(p.get("notional", 0) or 0),
                "unrealized_pnl": round(upnl, 2),
                "margin": float(p.get("initialMargin", p.get("collateral", 0)) or 0),
                "leverage": p.get("leverage", ""),
            })

        result["equity"] = round(equity, 2)
        result["net_pnl"] = round(total_pnl + unrealized_pnl, 2)
        result["win_rate"] = round(win_rate, 1)
        result["total_trades"] = total
        result["open_count"] = len(open_pos)

        log.info("Live data: equity=$%.2f, pnl=$%.2f, %d trades (%d wins), %d open",
                 equity, total_pnl, total, wins, len(open_pos))
        return result

    except ImportError:
        log.warning("ccxt not available for sync exchange fetch")
        # Still return trade file data with 0 equity
        if total > 0:
            result["net_pnl"] = round(total_pnl, 2)
            result["win_rate"] = round(win_rate, 1)
            result["total_trades"] = total
            return result
        return None
    except Exception as exc:
        log.warning("Exchange fetch failed: %s", exc)
        # Still return trade file data
        if total > 0:
            result["net_pnl"] = round(total_pnl, 2)
            result["win_rate"] = round(win_rate, 1)
            result["total_trades"] = total
            return result
        return None


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

    # ── Circuit breaker from engine + real exchange data ──
    cb_rules = []
    cb_equity = 0
    cb_net_pnl = 0
    cb_win_rate = 0
    cb_total_trades = 0
    cb_open_count = 0
    cb_open_positions = []  # Actual position details for dashboard
    cb_closed_trades = []   # Recent closed trades for dashboard

    # Try to get REAL exchange data first (live mode)
    live_data_loaded = False
    if not CONFIG.simulation_mode and CONFIG.live_trading_enabled:
        try:
            live_data = _fetch_live_exchange_data()
            if live_data:
                cb_equity = live_data["equity"]
                cb_net_pnl = live_data["net_pnl"]
                cb_win_rate = live_data["win_rate"]
                cb_total_trades = live_data["total_trades"]
                cb_open_count = live_data["open_count"]
                cb_open_positions = live_data.get("open_positions", [])
                cb_closed_trades = live_data.get("closed_trades", [])
                live_data_loaded = True
                log.info("Live exchange data loaded: equity=$%.2f, %d trades, %d open",
                         cb_equity, cb_total_trades, cb_open_count)
        except Exception as exc:
            log.warning("Failed to fetch live exchange data, falling back to paper: %s", exc)

    # Fallback to paper portfolio if live data not available
    if engine and not live_data_loaded:
        try:
            risk = engine.risk
            portfolio = engine.portfolio
            state = portfolio.snapshot()
            cb_active = risk.circuit_breaker_active
            cb_equity = state.equity_usd
            cb_open_count = state.open_positions
            # Compute stats from portfolio history
            history = list(portfolio._history) if hasattr(portfolio, '_history') else []
            cb_total_trades = len(history)
            wins = sum(1 for t in history if getattr(t, 'net_pnl', 0) > 0)
            cb_win_rate = (wins / cb_total_trades * 100) if cb_total_trades > 0 else 0
            cb_net_pnl = sum(getattr(t, 'net_pnl', 0) for t in history)
        except Exception as exc:
            log.warning("Paper portfolio data unavailable: %s", exc)

    if engine:
        try:
            risk = engine.risk
            portfolio = engine.portfolio
            state = portfolio.snapshot()
            cb_active = risk.circuit_breaker_active
            cb_rules = [
                {"label": "Circuit Breaker", "active": cb_active},
                {"label": f"Daily PnL: ${state.daily_pnl:+.2f}", "active": state.daily_pnl < -state.equity_usd * 0.05},
                {"label": f"Open Positions: {cb_open_count}/{CONFIG.risk.max_open_positions}", "active": cb_open_count >= CONFIG.risk.max_open_positions},
            ]
        except Exception as exc:
            log.warning("CB rules unavailable: %s", exc)

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
        "circuit_breaker": {
            "rules": cb_rules,
            "equity": cb_equity,
            "net_pnl": round(cb_net_pnl, 2),
            "win_rate": round(cb_win_rate, 1),
            "total_trades": cb_total_trades,
            "open_count": cb_open_count,
            "open_positions": cb_open_positions,
            "closed_trades": cb_closed_trades,
            "live_mode": not CONFIG.simulation_mode and CONFIG.live_trading_enabled,
        },
        "symbols": symbols,
        "entry_cards": entry_cards,
        "key_call": key_call,
        "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _scan_signal_rows(payload: dict) -> list[dict]:
    """Map a scan payload's entry_cards into signal-stream rows (pure)."""
    regime = ""
    try:
        regime = str((payload.get("regime") or {}).get("label", "") or "")
    except Exception:
        regime = ""
    ts = str(payload.get("timestamp", "") or "")
    # Compact, stable-per-(symbol,direction,scan) key so re-pushing the same scan
    # signal UPSERTs (carries an outcome later) instead of duplicating.
    ts_key = "".join(ch for ch in ts if ch.isalnum())
    rows = []
    for c in payload.get("entry_cards", []) or []:
        sym = c.get("symbol", "")
        direction = c.get("direction", "")
        if not sym or not direction:
            continue
        try:
            score = float(c.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        rows.append({
            "signal_key": f"{sym}-{direction}-{ts_key}",
            "symbol": sym,
            "direction": direction,
            "confidence": score,          # scan score is a 0..1 confidence proxy
            "score": score,
            "pattern": c.get("trigger") or None,
            "regime": regime,
            "entry_price": float(c.get("entry", 0) or 0),
            "stop_loss": float(c.get("stop_loss", 0) or 0),
            "take_profit": float(c.get("tp1", 0) or 0),
            "rr": float(c.get("rr", 0) or 0),
            "thesis": c.get("thesis", "") or "",
            "status": "NEW",
            "pnl": None,
            "created_at": ts,
            "resolved_at": "",
        })
    return rows


def _push_scan_to_dashboard(results: list[dict], engine=None) -> None:
    """Build scan payload and push to website in background (scan + signal stream)."""
    try:
        from bot.utils.website_sync import (
            sync_scan_in_background, sync_signals_in_background)
        payload = _build_scan_payload(results, engine)
        sync_scan_in_background(payload)
        # Also append the scan's signals to the global signal-stream (every
        # generated signal, taken or not). Best-effort, non-blocking.
        sync_signals_in_background(_scan_signal_rows(payload))
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
    # 24h change (~6 bars of 4h) for the scan display. The formatters already
    # read "change_pct"; without this it was always 0 → the %-change was never
    # shown (deep-audit low: dead change_str).
    change_pct = (((price - float(c[-7])) / float(c[-7])) * 100.0
                  if len(c) >= 7 and c[-7] > 0 else 0.0)
    return {"sym": symbol, "price": price, "dir": direction, "score": score,
            "rsi": round(rsi, 1), "atr": round(atr, 4),
            "vol_ratio": round(vol_ratio, 2), "sma20": round(sma20, 4),
            "change_pct": round(change_pct, 2),
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

    # ── BTC Gate Check ──
    btc_gate = ""
    btc_r = next((r for r in raw if r and r["sym"] == "BTC/USDT"), None)
    if btc_r:
        btc_price = btc_r["price"]
        btc_vwap = btc_r["sma20"]  # Use SMA20 as VWAP proxy
        btc_rsi = btc_r["rsi"]
        btc_vs_vwap = (btc_price - btc_vwap) / btc_vwap * 100 if btc_vwap > 0 else 0
        gate_icon = "\u2705" if btc_vs_vwap > -0.5 else "\u26a0\ufe0f" if btc_vs_vwap > -2 else "\u274c"
        gate_label = "OPEN" if btc_vs_vwap > -0.5 else "CAUTION" if btc_vs_vwap > -2 else "CLOSED"
        btc_gate = (
            f"<b>BTC GATE</b>  {gate_icon} {gate_label}\n"
            f"Price <code>${btc_price:,.0f}</code>  |  SMA20 <code>${btc_vwap:,.0f}</code>  "
            f"({'+' if btc_vs_vwap >= 0 else ''}{btc_vs_vwap:.1f}%)  RSI {btc_rsi}\n"
            f"{'━' * 28}\n"
        )

    # ── Summary header ──
    now_str = datetime.now(UTC).strftime('%H:%M UTC')
    header = f"\u2694\ufe0f <b>RUNECLAW Live Scan</b> — {now_str}\n{'━' * 28}\n"
    header += btc_gate

    # ── Top setups with details ──
    from bot.config import CONFIG
    leverage = CONFIG.exchange.default_leverage
    top_setups = [r for r in results if r["score"] >= 0.4][:6]  # Max 6 setups
    skipped = [r for r in results if r["score"] < 0.4]

    # Compute entry/SL/TP for each setup
    for r in top_setups:
        price = r["price"]
        atr = r["atr"]
        direction = r["dir"]
        if direction == "LONG":
            r["sl"] = round(price - atr * 2.5, 8)
            r["tp"] = round(price + atr * 3.0, 8)
            r["entry"] = round(price - atr * 0.3, 8)
        else:
            r["sl"] = round(price + atr * 2.5, 8)
            r["tp"] = round(price - atr * 3.0, 8)
            r["entry"] = round(price + atr * 0.3, 8)
        sl_dist = abs(r["entry"] - r["sl"]) / r["entry"] * 100 if r["entry"] > 0 else 0
        tp_dist = abs(r["tp"] - r["entry"]) / r["entry"] * 100 if r["entry"] > 0 else 0
        r["rr"] = tp_dist / sl_dist if sl_dist > 0 else 0

    # ── Render scan results card image ──
    card_sent = False
    try:
        from bot.formatters.signal_card import render_scan_results_card
        btc_gate_data = None
        if btc_r:
            btc_vs = (btc_r["price"] - btc_r["sma20"]) / btc_r["sma20"] * 100 if btc_r["sma20"] > 0 else 0
            btc_gate_data = {
                "price": btc_r["price"],
                "sma20": btc_r["sma20"],
                "rsi": btc_r["rsi"],
                "vs_vwap": btc_vs,
                "label": "OPEN" if btc_vs > -0.5 else "CAUTION" if btc_vs > -2 else "CLOSED",
            }
        card_png = render_scan_results_card(
            top_setups, btc_gate=btc_gate_data,
            scan_label="LIVE SCAN", timestamp=now_str)
        if card_png:
            import io as _io
            buf = _io.BytesIO(card_png)
            buf.name = "scan.png"
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=buf,
                    caption=f"\u2694\ufe0f <b>RUNECLAW Live Scan</b> — {now_str}",
                    parse_mode="HTML")
                card_sent = True
    except Exception as exc:
        log.warning("Scan card render failed: %s", exc, exc_info=True)

    setup_lines = []
    for i, r in enumerate(top_setups, 1):
        sym = r["sym"].replace("/USDT", "")
        entry = r["entry"]
        sl = r["sl"]
        tp = r["tp"]
        rr = r["rr"]
        direction = r["dir"]
        sl_dist = abs(entry - sl) / entry * 100 if entry > 0 else 0
        tp_dist = abs(tp - entry) / entry * 100 if entry > 0 else 0

        # Score threshold display
        score_pct = int(r["score"] * 100)
        score_icon = "\u2705" if score_pct >= 75 else "\u26a0\ufe0f" if score_pct >= 60 else "\u274c"

        setup_lines.append(
            f"<b>#{i} — {sym}USDT</b>  {_dir_emoji(direction)} {direction}\n"
            f"  Entry: <code>${entry:,.6g}</code>  ({'+' if direction == 'LONG' else '-'}"
            f"pullback)\n"
            f"  TP:    <code>${tp:,.6g}</code>  (+{tp_dist:.1f}%)\n"
            f"  SL:    <code>${sl:,.6g}</code>  (-{sl_dist:.1f}%)\n"
            f"  R:R:   <code>{rr:.1f}:1</code>  |  RSI {r['rsi']}  |  Vol {r['vol_ratio']}x\n"
            f"  Score: {score_icon} {score_pct}/100"
        )

    body = "\n\n".join(setup_lines) if setup_lines else "No setups above threshold."

    if skipped:
        body += f"\n\n<i>{len(skipped)} symbols below gate threshold</i>"

    text = header + "\n" + body

    if ai and results and not card_sent:
        text += "\n\n\u23f3 <i>Generating AI summary...</i>"
        await msg.edit_text(text, parse_mode="HTML")
        summary = await _ai_summary(results[:15])
        text = text.replace("\u23f3 <i>Generating AI summary...</i>",
                            f"\U0001f916 <b>AI Summary:</b>\n{summary}")

    # ── Build per-setup action buttons (max 6 rows) ──
    buttons = []
    for r in top_setups:
        sym_short = r["sym"].replace("/USDT", "")
        buttons.append([
            InlineKeyboardButton(f"\u2705 {sym_short}",
                                 callback_data=f"scan_confirm:{r['sym']}:{r['dir']}:{r['price']}"),
            InlineKeyboardButton("Limit",
                                 callback_data=f"scan_limit:{r['sym']}:{r['dir']}:{r['price']}"),
            InlineKeyboardButton("Skip",
                                 callback_data=f"scan_reject:{r['sym']}"),
        ])

    kb = InlineKeyboardMarkup(buttons) if buttons else None

    if card_sent:
        # Card image already sent — delete the "Scanning..." message
        # and send buttons as a compact text follow-up
        try:
            await msg.delete()
        except Exception:
            pass
        btn_text = "\u2694\ufe0f <b>Actions</b> — tap to execute"
        if ai and results:
            summary = await _ai_summary(results[:15])
            btn_text += f"\n\n\U0001f916 <b>AI:</b> {summary}"
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id and kb:
            await context.bot.send_message(
                chat_id=chat_id, text=btn_text,
                parse_mode="HTML", reply_markup=kb)
    else:
        # Fallback: plain text with buttons (no Pillow or card failed)
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
        await query.message.reply_text(f"\u274c <b>{sym}</b> skipped.", parse_mode="HTML")
        return

    # ── scan_limit: prompt user to set custom limit price ──
    if data.startswith("scan_limit:"):
        parts = data.split(":")
        if len(parts) < 4:
            await query.message.reply_text("Invalid callback data."); return
        _, symbol, dir_str, price_str = parts[:4]
        price = float(price_str)
        direction = Direction.LONG if dir_str == "LONG" else Direction.SHORT
        engine = context.bot_data.get("engine")
        if engine is None:
            await query.message.reply_text("Engine not available."); return

        # Create idea and register as pending
        atr_val = price * 0.03  # default 3% ATR estimate
        try:
            exchange = await engine.scanner._get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "4h", limit=30)
            h = np.array([c[2] for c in ohlcv], dtype=float)
            l_arr = np.array([c[3] for c in ohlcv], dtype=float)
            c_arr = np.array([c[4] for c in ohlcv], dtype=float)
            atr_val = _compute_atr(h, l_arr, c_arr)
        except Exception:
            pass
        sl = round(price * (0.97 if direction == Direction.LONG else 1.03), 6)
        tp = round(price * (1.06 if direction == Direction.LONG else 0.94), 6)
        try:
            idea = TradeIdea(asset=symbol, direction=direction, entry_price=price,
                             stop_loss=sl, take_profit=tp, confidence=0.6,
                             reasoning=f"Scan signal for {symbol} with custom limit",
                             source="scan_skill", order_type="limit")
        except ValueError as e:
            await query.message.reply_text(f"\u26a0\ufe0f Invalid trade: {e}", parse_mode="HTML")
            return

        engine._pending_ideas[idea.id] = idea
        engine._pending_atr[idea.id] = atr_val

        # Store limit input state in the telegram handler
        handler = context.bot_data.get("telegram_handler")
        caller_uid = str(update.effective_user.id) if update.effective_user else ""
        if handler and hasattr(handler, '_pending_limit_input'):
            import time as _time
            handler._pending_limit_input[caller_uid] = {
                "trade_id": idea.id,
                "asset": symbol.replace("/USDT", ""),
                "pair": symbol,
                "direction": direction.value,
                "current_entry": price,
                "timestamp": _time.time(),
            }

        sym_short = symbol.replace("/USDT", "")
        await query.message.reply_text(
            f"\U0001f4b0 <b>Set limit price for {sym_short} {direction.value}</b>\n\n"
            f"Current entry: <code>${price:,.6g}</code>\n"
            f"SL: <code>${sl:,.6g}</code> | TP: <code>${tp:,.6g}</code>\n\n"
            f"Type your limit price (e.g. <code>{price * 0.99:,.4g}</code> or <code>{price * 0.98:,.4g}</code>):",
            parse_mode="HTML")
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

        if rc.verdict != RiskVerdict.APPROVED:
            await query.message.reply_text(
                f"{ve} <b>{symbol} {direction.value}</b> -- Risk: <b>{rc.verdict.value}</b>\n"
                f"  Entry <code>${price:,.6g}</code>  SL <code>${sl:,.6g}</code>  TP <code>${tp:,.6g}</code>\n"
                f"  R:R <code>{idea.risk_reward_ratio}</code>\n  <i>{rc.reason}</i>",
                parse_mode="HTML")
            return

        # Risk passed — register as pending idea and execute via confirm_trade
        trade_id = idea.id
        engine._pending_ideas[trade_id] = idea
        engine._pending_atr[trade_id] = atr

        caller_uid = str(update.effective_user.id) if update.effective_user else ""
        result = await engine.confirm_trade(trade_id, user_id=caller_uid)

        # ── Auto re-analyze on price drift rejection ──
        # If price moved since scan, rebuild the idea at the current price
        # and retry once (max 1 retry to avoid loops).
        if "price drifted" in result.lower() and "re-analyze" in result.lower():
            await query.message.reply_text(
                f"\u26a0\ufe0f <b>Price moved — auto re-analyzing {symbol}...</b>",
                parse_mode="HTML")
            try:
                ticker = await exchange.fetch_ticker(symbol)
                new_price = float(ticker.get("last", 0))
                if new_price > 0:
                    new_sl = round(new_price * (0.97 if direction == Direction.LONG else 1.03), 6)
                    new_tp = round(new_price * (1.06 if direction == Direction.LONG else 0.94), 6)
                    new_idea = TradeIdea(
                        asset=symbol, direction=direction, entry_price=new_price,
                        stop_loss=new_sl, take_profit=new_tp, confidence=0.6,
                        reasoning=f"Auto re-analyzed after price drift for {symbol}",
                        source="scan_skill_retry")
                    # Re-fetch ATR with fresh data
                    ohlcv2 = await exchange.fetch_ohlcv(symbol, "4h", limit=30)
                    h2 = np.array([c2[2] for c2 in ohlcv2], dtype=float)
                    l2 = np.array([c2[3] for c2 in ohlcv2], dtype=float)
                    c2 = np.array([c2[4] for c2 in ohlcv2], dtype=float)
                    atr2 = _compute_atr(h2, l2, c2)
                    rc2 = engine.risk.evaluate(new_idea, atr=atr2)
                    if rc2.verdict != RiskVerdict.APPROVED:
                        await query.message.reply_text(
                            f"\u274c <b>{symbol} {direction.value}</b> re-analysis rejected\n"
                            f"  New entry: <code>${new_price:,.6g}</code>\n"
                            f"  <i>{rc2.reason}</i>",
                            parse_mode="HTML")
                        return
                    retry_id = new_idea.id
                    engine._pending_ideas[retry_id] = new_idea
                    engine._pending_atr[retry_id] = atr2
                    result = await engine.confirm_trade(retry_id, user_id=caller_uid)
            except Exception as retry_exc:
                log.error("Auto re-analyze failed for %s: %s", symbol, retry_exc)
                result = f"Auto re-analyze failed: {retry_exc}"

        # Check if execution succeeded. Use the canonical classifier (same one
        # engine.confirm_trade uses) rather than a local prefix list — a stale
        # local copy here previously missed "EXECUTION BLOCKED:" (degraded-mode
        # / reduce-only blocks), so a blocked trade with NO order placed was
        # displayed to the user as "✅ EXECUTED".
        from bot.core.live_executor import execution_indicates_failure
        _local_fail_markers = (
            "Trade not found", "not found", "expired", "No pending",
            "Trade REJECTED", "Trade HALTED", "Execution denied",
            "Auto re-analyze failed",
        )
        is_failure = (execution_indicates_failure(result)
                      or any(result.startswith(p) for p in _local_fail_markers))
        if is_failure:
            await query.message.reply_text(
                f"\u274c <b>{symbol} {direction.value}</b> -- Execution failed\n\n{result}",
                parse_mode="HTML")
        else:
            await query.message.reply_text(
                f"\u2705 <b>{symbol} {direction.value} EXECUTED</b>\n\n{result}",
                parse_mode="HTML")
    except Exception as exc:
        # Audit F-15: log the real exception server-side, but never send raw
        # exception text to the user -- str(exc) on a ccxt/auth error can
        # contain the raw API key (same class of leak fixed across
        # telegram_handler.py's command handlers).
        log.error("Confirm callback failed for %s: %s", symbol, exc, exc_info=True)
        await query.message.reply_text(
            "\u26a0\ufe0f Something went wrong confirming this trade. Try again in a moment.",
            parse_mode="HTML")
