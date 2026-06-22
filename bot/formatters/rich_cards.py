"""Rich Telegram card formatters for RUNECLAW.

Produces the detailed, war-room-grade analysis cards with VWAP, orderbook
depth, support/resistance levels, comparison tables, PNL reports, and
pending order views.

All functions return plain HTML strings (Telegram parse_mode="HTML").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("runeclaw.formatters")

# ── Visual constants ─────────────────────────────────────────────
SEP = "\u2500" * 16  # ────────────────


def display_symbol(symbol: str) -> str:
    """Consistent short display name for any symbol format.

    MEGA/USDT:USDT  → MEGA
    NATGAS/USDT:USDT → NATGAS
    XLK/USDT        → XLK
    MEGAUSDT         → MEGA
    BTC/USDT:USDT   → BTC
    """
    s = symbol.upper()
    # Strip settle suffix :USDT
    if ":USDT" in s:
        s = s.split(":")[0]
    # Strip quote /USDT
    if "/USDT" in s:
        s = s.split("/")[0]
    # Handle raw concatenated form (BTCUSDT → BTC)
    if s.endswith("USDT") and "/" not in s and ":" not in s:
        base = s[:-4]
        if base:
            s = base
    return s


# ── Market-data helpers ──────────────────────────────────────────

def compute_vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 volumes: np.ndarray) -> float:
    """Volume-weighted average price from OHLCV arrays."""
    typical = (highs + lows + closes) / 3.0
    total_vol = np.sum(volumes)
    if total_vol == 0:
        return float(closes[-1])
    return float(np.sum(typical * volumes) / total_vol)


def compute_support_resistance(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    current_price: float, n_levels: int = 2,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Derive support/resistance zones from recent swing points.

    Returns (supports, resistances) as lists of (low, high) tuples.
    """
    # Find local minima/maxima using a simple rolling window
    window = 5
    supports: list[float] = []
    resistances: list[float] = []

    for i in range(window, len(closes) - window):
        if lows[i] == np.min(lows[i - window:i + window + 1]):
            supports.append(float(lows[i]))
        if highs[i] == np.max(highs[i - window:i + window + 1]):
            resistances.append(float(highs[i]))

    # Cluster nearby levels and pick the closest below/above current price
    def _cluster(levels: list[float], price: float, side: str) -> List[Tuple[float, float]]:
        if not levels:
            return []
        filtered = [l for l in levels if (l < price if side == "support" else l > price)]
        if not filtered:
            return []
        # Sort by distance from current price
        filtered.sort(key=lambda x: abs(x - price))
        zones: List[Tuple[float, float]] = []
        used = set()
        for lvl in filtered:
            if any(abs(lvl - u) / price < 0.005 for u in used):
                continue
            used.add(lvl)
            spread = price * 0.005  # 0.5% zone width
            zones.append((round(lvl - spread / 2, 6), round(lvl + spread / 2, 6)))
            if len(zones) >= n_levels:
                break
        # Sort supports descending (closest first), resistances ascending
        zones.sort(key=lambda z: z[0], reverse=(side == "support"))
        return zones

    return (_cluster(supports, current_price, "support"),
            _cluster(resistances, current_price, "resistance"))


def compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    g = np.mean(np.maximum(deltas[-period:], 0))
    l = np.mean(np.maximum(-deltas[-period:], 0))
    return 100.0 if l == 0 else float(100.0 - 100.0 / (1.0 + g / l))


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    h, l, c = highs[1:], lows[1:], closes[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - c), np.abs(l - c)))
    return float(np.mean(tr[-period:]))


def _fmt_price(p: float) -> str:
    """Smart price formatter — fewer decimals for larger prices."""
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:,.5f}"
    return f"${p:,.6f}"


def _fmt_vol(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1e9:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1e6:.1f}M"
    if v >= 1_000:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.0f}"


def _pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _verdict_header(status_icon: str, status_label: str, bias: str = "",
                     risk_state: str = "", action: str = "") -> str:
    """Build a one-glance verdict header block."""
    lines = []
    lines.append(f"⚔️ <b>RUNECLAW VERDICT</b>")
    lines.append(SEP)
    lines.append(f"  Status: {status_icon} <b>{status_label}</b>")
    if bias:
        lines.append(f"  Bias: <i>{bias}</i>")
    if risk_state:
        lines.append(f"  Risk State: <i>{risk_state}</i>")
    if action:
        lines.append(f"  Action: <i>{action}</i>")
    lines.append("")
    return "\n".join(lines)


# ── Data-fetching helper (async) ─────────────────────────────────

async def fetch_analysis_data(exchange, symbol: str, timeframe: str = "1h",
                              limit: int = 100) -> Optional[Dict[str, Any]]:
    """Fetch OHLCV + orderbook + ticker for a symbol. Returns None on failure."""
    # Normalize symbol: try original, then variants
    candidates = [symbol]
    if ":USDT" in symbol:
        candidates.append(symbol.split(":")[0])  # SUSHI/USDT:USDT -> SUSHI/USDT
    elif "/" in symbol and ":USDT" not in symbol:
        candidates.append(f"{symbol}:USDT")  # SUSHI/USDT -> SUSHI/USDT:USDT
    if "/" not in symbol and symbol.endswith("USDT"):
        base = symbol[:-4]  # Strip trailing "USDT" only (not mid-string)
        if base:
            candidates = [f"{base}/USDT", f"{base}/USDT:USDT", symbol]
    try:
        ohlcv, ticker, orderbook = None, None, None
        for sym in candidates:
            try:
                ohlcv = await exchange.fetch_ohlcv(sym, timeframe, limit=limit)
                if ohlcv and len(ohlcv) >= 20:
                    symbol = sym  # use the working symbol
                    break
                ohlcv = None
            except Exception as e:
                log.debug("OHLCV fetch failed for %s: %s", sym, e)
                continue
        if not ohlcv or len(ohlcv) < 20:
            log.warning("OHLCV unavailable for %s (tried: %s)", symbol, candidates)
            return None

        try:
            ticker = await exchange.fetch_ticker(symbol)
        except Exception as e:
            log.warning("Ticker fetch failed for %s: %s", symbol, e)
            ticker = {}

        try:
            orderbook = await exchange.fetch_order_book(symbol, limit=20)
        except Exception as e:
            log.debug("Orderbook fetch failed for %s: %s", symbol, e)
            orderbook = {"bids": [], "asks": []}

        if not ohlcv:
            return None

        o = np.array([c[1] for c in ohlcv], dtype=float)
        h = np.array([c[2] for c in ohlcv], dtype=float)
        l = np.array([c[3] for c in ohlcv], dtype=float)
        c = np.array([c[4] for c in ohlcv], dtype=float)
        v = np.array([c[5] for c in ohlcv], dtype=float)

        price = float(c[-1])
        high_24h = float(np.max(h[-24:])) if len(h) >= 24 else float(np.max(h))
        low_24h = float(np.min(l[-24:])) if len(l) >= 24 else float(np.min(l))
        vwap = compute_vwap(h, l, c, v)
        vwap_pct = ((price - vwap) / vwap * 100) if vwap > 0 else 0
        rsi = compute_rsi(c)
        atr = compute_atr(h, l, c)

        # Volume
        vol_24h = float(np.sum(v[-24:])) if len(v) >= 24 else float(np.sum(v))
        vol_avg = float(np.mean(v[-48:-24])) if len(v) >= 48 else float(np.mean(v[:-24])) if len(v) > 24 else vol_24h
        vol_spike = vol_24h / vol_avg if vol_avg > 0 else 1.0

        # Price change
        price_24h_ago = float(c[-25]) if len(c) >= 25 else float(c[0])
        change_pct = ((price - price_24h_ago) / price_24h_ago * 100) if price_24h_ago > 0 else 0

        # Orderbook depth
        bid_depth = sum(b[1] for b in orderbook.get("bids", [])[:10])
        ask_depth = sum(a[1] for a in orderbook.get("asks", [])[:10])
        # Convert to USD-equivalent
        bid_depth_usd = bid_depth * price
        ask_depth_usd = ask_depth * price

        # Support/Resistance
        supports, resistances = compute_support_resistance(h, l, c, price)

        # SMA
        sma9 = float(np.mean(c[-9:])) if len(c) >= 9 else price
        sma20 = float(np.mean(c[-20:])) if len(c) >= 20 else price
        sma50 = float(np.mean(c[-50:])) if len(c) >= 50 else sma20

        # 1H structure description
        recent_low = float(np.min(l[-24:])) if len(l) >= 24 else float(np.min(l))
        recent_high = float(np.max(h[-24:])) if len(h) >= 24 else float(np.max(h))
        if price > sma20 and price > recent_low * 1.03:
            structure = f"Breakout from {_fmt_price(recent_low)} base \u2192 {_fmt_price(recent_high)} high"
            if abs(price - recent_high) / price < 0.01:
                structure += ", consolidating at top"
            else:
                structure += f", pulling back from high"
        elif price < sma20:
            structure = f"Downtrend from {_fmt_price(recent_high)} \u2192 testing {_fmt_price(recent_low)}"
        else:
            structure = f"Range-bound {_fmt_price(recent_low)} \u2013 {_fmt_price(recent_high)}"

        return {
            "symbol": symbol,
            "pair": display_symbol(symbol),
            "price": price,
            "high_24h": high_24h,
            "low_24h": low_24h,
            "change_pct": change_pct,
            "volume_24h_usd": vol_24h * price,
            "vol_spike": vol_spike,
            "vwap": vwap,
            "vwap_pct": vwap_pct,
            "rsi": rsi,
            "atr": atr,
            "sma9": sma9,
            "sma20": sma20,
            "sma50": sma50,
            "bid_depth": bid_depth_usd,
            "ask_depth": ask_depth_usd,
            "supports": supports,
            "resistances": resistances,
            "structure": structure,
            "ohlcv": {"o": o, "h": h, "l": l, "c": c, "v": v},
            "ohlcv_raw": ohlcv,  # raw CCXT [ts,o,h,l,c,v] — used by chart renderer
        }
    except Exception as e:
        log.error("fetch_analysis_data failed for %s: %s", symbol, e)
        return None


# ── Card renderers ───────────────────────────────────────────────

def render_analysis_card(data: Dict[str, Any], idea: Optional[Any] = None) -> str:
    """Render a single-asset rich analysis card.

    `data` is the dict from fetch_analysis_data().
    `idea` is an optional TradeIdea with entry/SL/TP.
    """
    pair = data["pair"]
    price = data["price"]
    change = data["change_pct"]
    vol = data["volume_24h_usd"]

    # Header
    lines = [
        f"\u2694\ufe0f <b>{pair}</b> \u2014 {_fmt_price(price)} | {_pct(change)} | Vol {_fmt_vol(vol)}",
        "",
        "<b>Current Snapshot:</b>",
        f"- Last: {_fmt_price(price)} | High: {_fmt_price(data['high_24h'])} | Low: {_fmt_price(data['low_24h'])}",
        f"- VWAP: {_fmt_price(data['vwap'])} \u2014 price is {_pct(data['vwap_pct'])} {'above' if data['vwap_pct'] >= 0 else 'below'} VWAP"
        + (" (very extended)" if abs(data["vwap_pct"]) > 10 else " (moderate)" if abs(data["vwap_pct"]) > 5 else ""),
        f"- Bid/Ask: {_fmt_vol(data['bid_depth'])} bid vs {_fmt_vol(data['ask_depth'])} ask \u2014 "
        + (_bid_ask_read(data["bid_depth"], data["ask_depth"])),
        f"- {data.get('timeframe', '1H')} structure: {data['structure']}",
        f"- RSI: {data['rsi']:.1f} | ATR: {_fmt_price(data['atr'])} | Vol spike: {data['vol_spike']:.1f}x",
    ]

    # Key Levels
    lines.append("")
    lines.append("<b>Key Levels (from kline analysis):</b>")
    for i, s in enumerate(data.get("supports", []), 1):
        desc = ""
        if i == 1:
            desc = " (breakout retest zone)"
        elif i == 2:
            desc = " (VWAP area)" if abs(s[0] - data["vwap"]) / data["vwap"] < 0.02 else " (deeper support)"
        lines.append(f"- Support {i}: {_fmt_price(s[0])}-{_fmt_price(s[1])}{desc}")
    for i, r in enumerate(data.get("resistances", []), 1):
        desc = " (current high)" if i == 1 else ""
        lines.append(f"- Resistance{'' if i == 1 else f' {i}'}: {_fmt_price(r[0])}{desc}")

    # Setup (from TradeIdea)
    if idea:
        entry, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
        sl_pct = abs(entry - sl) / entry * 100
        tp_pct = abs(tp - entry) / entry * 100
        rr = idea.risk_reward_ratio
        direction = idea.direction.value

        lines.append("")
        lines.append(f"<b>Setup \u2014 {direction} {'on Pullback' if direction == 'LONG' and entry < price else ''}:</b>")
        lines.append(f"- Entry: {_fmt_price(entry)}")
        lines.append(f"- SL: {_fmt_price(sl)} (-{sl_pct:.1f}%)")
        lines.append(f"- TP: {_fmt_price(tp)} (+{tp_pct:.1f}%)")
        lines.append(f"- Risk/Reward: 1:{rr:.1f}")
        lines.append(f"- Confidence: {idea.confidence:.0%}")

    # Velocity gate warning
    if abs(data["change_pct"]) > 15:
        lines.append("")
        gate_dir = "counter-trend short is blocked" if data["change_pct"] > 0 else "counter-trend long is blocked"
        lines.append(f"\u26a0\ufe0f Velocity Gate: {data.get('timeframe', '1H')} change {_pct(data['change_pct'])} \u2014 {gate_dir}")

    # Orderbook concern
    if data["ask_depth"] > data["bid_depth"] * 2:
        lines.append("")
        lines.append(f"\u26a0\ufe0f Concern: Ask-side dominance ({_fmt_vol(data['ask_depth'])} vs {_fmt_vol(data['bid_depth'])}) suggests distribution.")

    return "\n".join(lines)


def _bid_ask_read(bid: float, ask: float) -> str:
    """Human-readable orderbook bias."""
    if bid > ask * 3:
        return "buyers stacking hard"
    if bid > ask * 1.5:
        return "bid-side dominant (bullish)"
    if ask > bid * 3:
        return "heavy sell wall (distribution)"
    if ask > bid * 1.5:
        return "ask-side dominant (bearish)"
    return "balanced"


def render_comparison_table(assets: List[Dict[str, Any]],
                            ideas: Optional[List[Any]] = None) -> str:
    """Render a side-by-side comparison table for multiple assets."""
    if len(assets) < 2:
        return ""

    ideas_map = {}
    if ideas:
        for idea in ideas:
            ideas_map[idea.asset] = idea

    lines = ["\u2694\ufe0f <b>COMPARISON</b>", ""]

    # Build rows
    rows = [
        ("Current Price", [_fmt_price(a["price"]) for a in assets]),
        ("24h Change", [_pct(a["change_pct"]) for a in assets]),
        ("Above VWAP", [f"{_pct(a['vwap_pct'])} ({'extended' if abs(a['vwap_pct']) > 10 else 'moderate' if abs(a['vwap_pct']) > 5 else 'tight'})" for a in assets]),
        ("Bid/Ask", [f"{_fmt_vol(a['bid_depth'])} vs {_fmt_vol(a['ask_depth'])} ({'bullish' if a['bid_depth'] > a['ask_depth'] else 'bearish'})" for a in assets]),
        ("RSI", [f"{a['rsi']:.1f}" for a in assets]),
        ("Volume", [_fmt_vol(a["volume_24h_usd"]) for a in assets]),
    ]

    # Add idea-specific rows
    for a in assets:
        sym = a["symbol"]
        if sym in ideas_map:
            idea = ideas_map[sym]
            idx = assets.index(a)
            if len(rows) <= 6:
                entry_row = [""] * len(assets)
                sl_row = [""] * len(assets)
                rr_row = [""] * len(assets)
                for j, aa in enumerate(assets):
                    if aa["symbol"] in ideas_map:
                        ii = ideas_map[aa["symbol"]]
                        entry_row[j] = _fmt_price(ii.entry_price)
                        sl_pct = abs(ii.entry_price - ii.stop_loss) / ii.entry_price * 100
                        sl_row[j] = f"-{sl_pct:.1f}%"
                        rr_row[j] = f"1:{ii.risk_reward_ratio:.1f}"
                rows.append(("Entry", entry_row))
                rows.append(("SL Distance", sl_row))
                rows.append(("R:R", rr_row))
                break

    # Determine verdict
    scores = []
    for a in assets:
        s = 0
        s += (1 if a["bid_depth"] > a["ask_depth"] else -1)
        s += (1 if abs(a["vwap_pct"]) < 10 else -1)
        s += (1 if a["vol_spike"] > 1.2 else 0)
        if a["symbol"] in ideas_map:
            s += ideas_map[a["symbol"]].risk_reward_ratio
        scores.append(s)

    verdict_row = []
    best_idx = scores.index(max(scores))
    for i in range(len(assets)):
        verdict_row.append("<b>Preferred</b>" if i == best_idx else "Secondary")
    rows.append(("Verdict", verdict_row))

    # Format as bullet list
    names = [a["pair"] for a in assets]
    for label, vals in rows:
        parts = "; ".join(f"{names[i]}: {vals[i]}" for i in range(len(assets)))
        lines.append(f"\u2022 {label}: {parts}")

    return "\n".join(lines)


def render_recommended_orders(assets: List[Dict[str, Any]],
                              ideas: List[Any]) -> str:
    """Render RECOMMENDED ORDERS section."""
    lines = ["", SEP, "", "<b>RECOMMENDED ORDERS</b>", ""]

    ideas_map = {idea.asset: idea for idea in ideas}
    scores = []
    for a in assets:
        s = 0
        s += (1 if a["bid_depth"] > a["ask_depth"] else -1)
        if a["symbol"] in ideas_map:
            s += ideas_map[a["symbol"]].risk_reward_ratio
        scores.append(s)

    best_idx = scores.index(max(scores)) if scores else 0

    for i, a in enumerate(assets):
        if a["symbol"] not in ideas_map:
            continue
        idea = ideas_map[a["symbol"]]
        rank = "Primary" if i == best_idx else "Secondary"
        sl_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
        tp_pct = abs(idea.take_profit - idea.entry_price) / idea.entry_price * 100

        lines.append(f"<b>{a['pair']}</b> ({rank}):")
        lines.append(f"- Entry: {_fmt_price(idea.entry_price)}")
        lines.append(f"- SL: {_fmt_price(idea.stop_loss)} (-{sl_pct:.1f}%) | TP: {_fmt_price(idea.take_profit)} (+{tp_pct:.1f}%)")
        lines.append(f"- R:R 1:{idea.risk_reward_ratio:.1f} | Conf: {idea.confidence:.0%}")
        if i == best_idx and a["bid_depth"] > a["ask_depth"]:
            lines.append(f"- \u2705 Bid dominance \u2014 cleaner setup")
        elif a["ask_depth"] > a["bid_depth"] * 1.5:
            lines.append(f"- \u26a0\ufe0f Ask wall present \u2014 caution")
        lines.append("")

    is_pullback = any(
        ideas_map.get(a["symbol"]) and ideas_map[a["symbol"]].entry_price < a["price"]
        for a in assets
    )
    if is_pullback:
        lines.append("<i>Pullback entries \u2014 not chases.</i>")

    # Next Best Action
    if lines:
        lines.append("")
        lines.append(f"🎯 <b>Next Action:</b> <i>Entry is conditional. Wait for confirmation trigger before execution.</i>")

    return "\n".join(lines)


# ── Pending orders card ──────────────────────────────────────────

def render_pending_orders(orders: List[Dict[str, Any]],
                          current_prices: Dict[str, float]) -> str:
    """Render pending/open orders with distances from current price."""
    if not orders:
        return ("\u2694\ufe0f <b>PENDING ORDERS</b>\n"
                f"{SEP}\n\n"
                "No pending orders.")

    lines = [
        "\u2694\ufe0f <b>PENDING ORDERS \u2014 Live</b>",
        "",
    ]

    for i, order in enumerate(orders, 1):
        pair = order.get("symbol", "").replace("/", "")
        direction = order.get("side", "buy").upper()
        d_icon = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
        entry = order.get("price", 0)
        qty = order.get("amount", 0)
        order_id = order.get("id", "N/A")
        sl = order.get("stopLoss", order.get("sl", 0))
        tp = order.get("takeProfit", order.get("tp", 0))
        status = order.get("status", "open").capitalize()
        leverage = order.get("leverage", "")

        # Distance from current price
        current = current_prices.get(order.get("symbol", ""), entry)
        distance_pct = ((current - entry) / entry * 100) if entry > 0 else 0

        lines.append(f"{i}. {d_icon} <b>{pair} {direction.replace('BUY', 'Long').replace('SELL', 'Short')}</b>")
        lines.append(f"- Order ID: <code>{order_id}</code>")
        lines.append(f"- Limit: {_fmt_price(entry)} | Qty: {qty:,.3f}")
        if leverage:
            lines.append(f"- Leverage: {leverage}x")
        if sl:
            lines.append(f"- SL: {_fmt_price(sl)} | TP: {_fmt_price(tp)}")
        lines.append(f"- Status: {status}")
        lines.append(f"- Distance: {pair} at ~{_fmt_price(current)} \u2192 entry is {_pct(-distance_pct)} {'below' if distance_pct > 0 else 'above'} current")
        lines.append("")

    return "\n".join(lines)


# ── PNL report card ──────────────────────────────────────────────

def render_pnl_report(
    equity: float,
    available: float,
    locked: float,
    open_positions: int,
    closed_trades: List[Dict[str, Any]],
    pending_orders: List[Dict[str, Any]] = None,
) -> str:
    """Render a detailed PNL report with session tally."""
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        f"\u2694\ufe0f <b>PNL REPORT</b> \u2014 {now}",
        "",
        f"Account Equity: <b>{_fmt_vol(equity)}</b>"
        + (f" | {open_positions} open position{'s' if open_positions != 1 else ''}" if open_positions else " | No open positions"),
        "",
        SEP,
    ]

    # Closed trades detail
    session_pnl = 0.0
    if closed_trades:
        for trade in closed_trades:
            pair = trade.get("pair", trade.get("symbol", "N/A")).replace("/", "")
            entry_price = trade.get("entry_price", 0)
            exit_price = trade.get("exit_price", 0)
            size = trade.get("size", 0)
            pnl = trade.get("pnl", 0)
            fees = trade.get("fees", 0)
            net_pnl = pnl - fees
            session_pnl += net_pnl
            win = net_pnl > 0

            lines.extend([
                "",
                f"<b>{pair} Trade</b> {'(closed)' if trade.get('closed') else ''}",
                "",
                f"\u2022 Entry: {_fmt_price(entry_price)}",
                f"\u2022 Exit: {_fmt_price(exit_price)}",
                f"\u2022 Size: {size:,.4f}",
                f"\u2022 Gross PnL: {_pct(pnl) if isinstance(pnl, float) and abs(pnl) < 100 else _fmt_price(pnl)}",
            ])
            if fees:
                lines.append(f"\u2022 Fees: ~{_fmt_price(fees)}")
            icon = "\u2705" if win else "\u274c"
            lines.append(f"\u2022 <b>Net PnL: {_fmt_price(net_pnl)}</b> {icon}")
            lines.append("")
            lines.append(SEP)

    # Current state
    lines.extend([
        "",
        "<b>Current State</b>",
        "",
        f"- Equity: <b>{_fmt_price(equity)}</b>",
        f"- Available: {_fmt_price(available)}",
    ])
    if locked > 0:
        lines.append(f"- Locked: {_fmt_price(locked)}")
    lines.append(f"- Open positions: {open_positions}")

    if pending_orders:
        for po in pending_orders:
            pair = po.get("symbol", "").replace("/", "")
            lines.append(f"- Pending: {pair} limit @ {_fmt_price(po.get('price', 0))}")

    # Session tally
    lines.extend([
        "",
        SEP,
        "",
        "<b>Session Tally</b>",
        "",
    ])

    if closed_trades:
        wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        losses = len(closed_trades) - wins
        lines.append(
            f"{'Today was green.' if session_pnl > 0 else 'Today was red.' if session_pnl < 0 else 'Flat session.'} "
            f"{wins}W/{losses}L, net {_fmt_price(session_pnl)}."
        )
    else:
        lines.append("No closed trades this session.")

    return "\n".join(lines)


# ── Multi-asset analysis card ────────────────────────────────────

def render_multi_analysis(
    assets: List[Dict[str, Any]],
    ideas: Optional[List[Any]] = None,
) -> str:
    """Full multi-asset analysis: individual cards + comparison + orders."""
    if not assets:
        return "No data available."

    # Dedup: keep only unique symbols (last occurrence wins)
    seen = {}
    for a in assets:
        seen[a.get("symbol") or a.get("pair", "")] = a
    assets = list(seen.values())

    # Dedup ideas by asset
    if ideas:
        seen_ideas = {}
        for i in ideas:
            seen_ideas[i.asset] = i
        ideas = list(seen_ideas.values())

    names = " & ".join(a["pair"] for a in assets)
    parts = [
        f"\u2694\ufe0f <b>{names}</b> \u2014 LIVE ANALYSIS",
        "",
        SEP,
        "",
    ]

    ideas_map = {}
    if ideas:
        ideas_map = {i.asset: i for i in ideas}

    for a in assets:
        parts.append("")
        idea = ideas_map.get(a["symbol"])
        parts.append(render_analysis_card(a, idea))
        parts.append("")
        parts.append(SEP)

    # Comparison (if multiple)
    if len(assets) >= 2:
        parts.append("")
        parts.append(render_comparison_table(assets, ideas))
        parts.append("")
        parts.append(SEP)

    # Recommended orders
    if ideas:
        parts.append(render_recommended_orders(assets, ideas))

    return "\n".join(parts)


# ── Open positions card ──────────────────────────────────────────

def render_open_positions(positions: List[Dict[str, Any]]) -> str:
    """Render open positions — compact card format."""
    if not positions:
        return ("No open positions right now.\n"
                "Say \"scan\" or \"analyze BTC\" to find setups.")

    total_pnl = sum(p.get("pnl_pct", 0) for p in positions)
    pnl_icon = "\U0001f7e2" if total_pnl > 0 else "\U0001f534" if total_pnl < 0 else ""

    lines = [
        f"<b>Open Positions ({len(positions)})</b> {pnl_icon} {_pct(total_pnl)} total",
        "",
    ]

    for p in positions:
        pair = p.get("pair", "N/A").replace("/", "")
        direction = p.get("direction", "LONG")
        d_icon = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
        entry = p.get("entry", 0)
        current = p.get("current", entry)
        pnl = p.get("pnl_pct", 0)
        pnl_icon = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < 0 else ""
        size_usd = p.get("size_usd", 0)
        pnl_usd_val = p.get("pnl_usd", size_usd * pnl / 100 if pnl else 0)
        leverage = p.get("leverage")
        rr_live = p.get("rr_live")
        sl = p.get("sl", 0)
        tp = p.get("tp", 0)
        sl_dist = p.get("sl_dist_pct")
        tp_dist = p.get("tp_dist_pct")
        sl_order = p.get("sl_order")
        hold_h = p.get("hold_hours", 0)

        # Hold time
        if hold_h < 1:
            hold_str = f"{hold_h * 60:.0f}m"
        elif hold_h < 24:
            hold_str = f"{hold_h:.1f}h"
        else:
            hold_str = f"{hold_h / 24:.1f}d"

        lev_str = f" | {leverage:.0f}x" if leverage and leverage > 1 else ""
        rr_str = f" | R:R {rr_live:.1f}" if rr_live else ""
        sl_tag = " on exchange" if sl_order == "exchange" else ""
        # Show "None" for missing SL/TP (untracked exchange positions)
        sl_str = _fmt_price(sl) if sl and sl > 0 else "<i>None</i>"
        tp_str = _fmt_price(tp) if tp and tp > 0 else "<i>None</i>"
        untracked = p.get("untracked", False)
        strategy_type = p.get("strategy_type", "").upper()
        st_tag = f" [{strategy_type}]" if strategy_type else ""

        lines.extend([
            f"{d_icon} <b>{pair}</b> {direction}{st_tag} | {pnl_icon} {_pct(pnl)} (${pnl_usd_val:+,.2f})",
            f"  {_fmt_price(entry)} -> {_fmt_price(current)} | ${size_usd:.0f}{lev_str}{rr_str} | {hold_str}",
            f"  SL {sl_str} / TP {tp_str}{sl_tag}",
        ])
        if untracked:
            lines.append("  \u26a0\ufe0f <i>Untracked — opened outside bot</i>")
        lines.append("")

    return "\n".join(lines)


# ── Status card ──────────────────────────────────────────────────

def render_status_card(
    mode: str,
    active: bool,
    equity: float,
    open_positions: int,
    daily_pnl: float,
    drawdown: float,
    max_drawdown: float,
    market_bias: str,
    pending_ideas: int = 0,
) -> str:
    """Render a compact status dashboard."""
    status = "\U0001f7e2 ACTIVE" if active else "\U0001f534 HALTED"
    mode_label = "\U0001f534 LIVE" if mode == "LIVE" else "\U0001f7e1 PAPER"
    pnl_icon = "\U0001f7e2\u25b2" if daily_pnl > 0 else "\U0001f534\u25bc" if daily_pnl < 0 else "\u26aa"

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        f"\U0001f43e <b>RUNECLAW STATUS</b> \u2014 {now}",
        "",
        f"{status} | {mode_label} | Bitget",
        "",
        SEP,
        "",
        "<b>Engine</b>",
        f"- State: {'Active' if active else 'Halted (circuit breaker)'}",
        f"- Mode: {mode}",
        f"- Market Bias: {market_bias}",
        f"- Pending Ideas: {pending_ideas}",
        "",
        "<b>Capital</b>",
        f"- Equity: {_fmt_price(equity)}",
        f"- Open Positions: {open_positions}",
        f"- Daily PnL: {pnl_icon} {_pct(daily_pnl)}",
        "",
        "<b>Risk</b>",
        f"- Drawdown: {_pct(drawdown)} / {_pct(max_drawdown)} limit",
    ]

    # Drawdown gauge
    ratio = drawdown / max_drawdown if max_drawdown > 0 else 0
    bar_len = 12
    filled = int(ratio * bar_len)
    bar = "\u2501" * filled + "\u254c" * (bar_len - filled)
    tip = "\U0001f7e2" if ratio < 0.5 else "\U0001f7e1" if ratio < 0.8 else "\U0001f534"
    lines.append(f"  {tip} \u2502{bar}\u2502")

    return "\n".join(lines)
