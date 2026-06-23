"""
Signal card image renderer for RUNECLAW.

Generates a styled PNG trading signal card using Pillow.
Matches the dark-themed, grid-layout design with colored accents.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, Optional

log = logging.getLogger("runeclaw.signal_card")

# ── Color palette ───────────────────────────────────────────────
_BG = (18, 22, 30)           # Dark navy background
_CARD_BG = (25, 30, 42)      # Slightly lighter card panels
_BORDER = (40, 50, 70)       # Subtle border
_ACCENT_GOLD = (212, 175, 55)  # Gold accent line
_GREEN = (0, 200, 100)       # Profit / LONG
_RED = (230, 60, 60)         # Loss / SHORT
_WHITE = (230, 230, 240)     # Primary text
_GRAY = (130, 140, 160)      # Secondary text / labels
_CYAN = (80, 200, 220)       # Accent highlights
_YELLOW = (230, 190, 50)     # Warning / pattern badge
_DIM = (60, 70, 90)          # Dimmed elements


def render_signal_card(data: Dict[str, Any]) -> bytes:
    """Render a trading signal as a PNG image.

    Args:
        data: Dict with keys:
            - rank: int (e.g. 1)
            - symbol: str (e.g. "BIO")
            - direction: str ("LONG" or "SHORT")
            - entry: float
            - stop_loss: float
            - tp1: float
            - tp2: float (optional)
            - margin_usd: float (optional)
            - rr: float (risk:reward ratio)
            - pattern: str (optional, e.g. "Double Bottom")
            - rsi: float (optional)
            - confidence: float (0-1 or 0-100)
            - volume_x: float (optional, e.g. 0.3)
            - summary: str (optional, one-line summary)

    Returns:
        PNG bytes
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render signal card")
        return b""

    # ── Dimensions ──
    W, H = 520, 420
    PAD = 20
    CELL_W = (W - PAD * 3) // 2  # Two columns
    CELL_H = 62
    CELL_GAP = 10

    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    # ── Fonts (use default if no TTF available) ──
    def _font(size: int, bold: bool = False):
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    font_title = _font(22, bold=True)
    font_label = _font(11)
    font_value = _font(16, bold=True)
    font_badge = _font(13, bold=True)
    font_small = _font(11)
    font_summary = _font(11)

    # ── Extract data ──
    rank = data.get("rank", 1)
    symbol = data.get("symbol", "???").replace(":USDT", "").replace("/USDT", "")
    direction = data.get("direction", "LONG").upper()
    entry = data.get("entry", 0)
    sl = data.get("stop_loss", 0)
    tp1 = data.get("tp1", data.get("take_profit", 0))
    tp2 = data.get("tp2", 0)
    margin = data.get("margin_usd", 0)
    rr = data.get("rr", 0)
    pattern = data.get("pattern", "")
    rsi = data.get("rsi", 0)
    confidence = data.get("confidence", 0)
    if confidence <= 1:
        confidence = confidence * 100
    vol_x = data.get("volume_x", 0)
    summary = data.get("summary", "")
    strategy_type = data.get("strategy_type", "").upper()

    is_long = direction == "LONG"
    dir_color = _GREEN if is_long else _RED

    # ── Price formatter ──
    def _fmt(price: float) -> str:
        if price == 0:
            return "—"
        if price >= 100:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.5f}"
        else:
            return f"${price:.6f}"

    y = PAD

    # ── Gold accent line at top ──
    draw.rectangle([0, 0, W, 3], fill=_ACCENT_GOLD)

    # ── Header row: #1 BIO  [LONG]   0.3x BID ──
    rank_text = f"#{rank}"
    draw.text((PAD, y + 2), rank_text, fill=_GRAY, font=font_badge)
    rank_w = draw.textlength(rank_text, font=font_badge)

    draw.text((PAD + rank_w + 6, y - 2), symbol, fill=_WHITE, font=font_title)
    sym_w = draw.textlength(symbol, font=font_title)

    # Direction badge
    badge_x = PAD + rank_w + 6 + sym_w + 12
    badge_text = f" {direction} "
    badge_tw = draw.textlength(badge_text, font=font_badge)
    badge_h = 22
    draw.rounded_rectangle(
        [badge_x, y + 1, badge_x + badge_tw + 4, y + 1 + badge_h],
        radius=4, fill=dir_color)
    draw.text((badge_x + 2, y + 4), badge_text, fill=(0, 0, 0), font=font_badge)

    # Strategy type badge (after direction)
    if strategy_type:
        st_x = badge_x + badge_tw + 10
        st_text = f" {strategy_type} "
        st_tw = draw.textlength(st_text, font=font_badge)
        st_color = {
            "SCALP": (180, 80, 220),    # purple
            "INTRADAY": (60, 140, 220),  # blue
            "SWING": (220, 160, 40),     # amber
            "POSITION": (40, 180, 120),  # teal
        }.get(strategy_type, _GRAY)
        draw.rounded_rectangle(
            [st_x, y + 1, st_x + st_tw + 4, y + 1 + badge_h],
            radius=4, fill=st_color)
        draw.text((st_x + 2, y + 4), st_text, fill=(0, 0, 0), font=font_badge)

    # Volume indicator (right side)
    if vol_x > 0:
        vol_text = f"{vol_x:.1f}x BID"
        vol_tw = draw.textlength(vol_text, font=font_badge)
        draw.text((W - PAD - vol_tw, y + 4), vol_text, fill=_CYAN, font=font_badge)

    y += 40

    # ── Separator ──
    draw.line([(PAD, y), (W - PAD, y)], fill=_BORDER, width=1)
    y += 12

    # ── Grid cells ──
    def _draw_cell(x: int, y: int, label: str, value: str,
                   value_color=_WHITE, w: int = CELL_W) -> None:
        # Cell background
        draw.rounded_rectangle(
            [x, y, x + w, y + CELL_H],
            radius=6, fill=_CARD_BG, outline=_BORDER)
        # Label
        draw.text((x + 12, y + 8), label, fill=_GRAY, font=font_label)
        # Value
        draw.text((x + 12, y + 28), value, fill=value_color, font=font_value)

    # Row 1: Entry | Stop Loss
    col1_x = PAD
    col2_x = PAD + CELL_W + CELL_GAP
    _draw_cell(col1_x, y, "ENTRY", _fmt(entry), _WHITE)
    _draw_cell(col2_x, y, "STOP LOSS", _fmt(sl), _RED)
    y += CELL_H + CELL_GAP

    # Row 2: TP1 | TP2 (or R:R if no TP2)
    _draw_cell(col1_x, y, "TP1", _fmt(tp1), _GREEN)
    if tp2 and tp2 > 0:
        _draw_cell(col2_x, y, "TP2", _fmt(tp2), _GREEN)
    else:
        rr_text = f"1:{rr:.1f}" if rr else "—"
        _draw_cell(col2_x, y, "R:R", rr_text, _CYAN)
    y += CELL_H + CELL_GAP

    # Row 3: Margin | R:R (or Margin | Confidence)
    if margin and margin > 0:
        margin_text = f"${margin:,.1f}" if margin >= 1 else f"${margin:.2f}"
        _draw_cell(col1_x, y, "MARGIN", margin_text, _WHITE)
    else:
        conf_text = f"{confidence:.0f}%"
        _draw_cell(col1_x, y, "CONFIDENCE", conf_text, _CYAN)

    if tp2 and tp2 > 0:
        rr_text = f"1:{rr:.1f}" if rr else "—"
        _draw_cell(col2_x, y, "R:R", rr_text, _CYAN)
    else:
        if margin and margin > 0:
            conf_text = f"{confidence:.0f}%"
            _draw_cell(col2_x, y, "CONFIDENCE", conf_text, _CYAN)
        else:
            if rsi:
                _draw_cell(col2_x, y, "RSI", f"{rsi:.1f}", _YELLOW if rsi > 70 or rsi < 30 else _WHITE)
            else:
                _draw_cell(col2_x, y, "SCORE", f"{confidence:.0f}%", _CYAN)
    y += CELL_H + CELL_GAP + 4

    # ── Pattern badge ──
    if pattern:
        badge_y = y
        icon = "\u26a0"  # ⚠
        pat_text = f"  {icon} {pattern}  "
        pat_tw = draw.textlength(pat_text, font=font_badge)
        draw.rounded_rectangle(
            [PAD, badge_y, PAD + pat_tw + 8, badge_y + 26],
            radius=5, fill=(40, 35, 15), outline=_YELLOW)
        draw.text((PAD + 4, badge_y + 5), pat_text, fill=_YELLOW, font=font_badge)
        y += 34

    # ── Summary line ──
    if summary:
        draw.text((PAD, y + 2), summary, fill=_GRAY, font=font_summary)
        y += 22
    elif rsi:
        bias = "LONG bias" if is_long else "SHORT bias"
        vol_part = f" | Vol {vol_x:.1f}x avg" if vol_x else ""
        auto_summary = f"{bias} | RSI {rsi:.1f} | Score {confidence:.0f}%{vol_part}"
        draw.text((PAD, y + 2), auto_summary, fill=_GRAY, font=font_summary)
        y += 22

    # ── Gold accent line at bottom ──
    draw.rectangle([0, H - 3, W, H], fill=_ACCENT_GOLD)

    # ── RUNECLAW watermark ──
    wm_text = "RUNECLAW"
    wm_w = draw.textlength(wm_text, font=font_small)
    draw.text((W - PAD - wm_w, H - 22), wm_text, fill=_DIM, font=font_small)

    # ── Export ──
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def signal_card_from_idea(idea, rank: int = 1, scan_data: Optional[Dict] = None) -> bytes:
    """Build signal card data from a TradeIdea and render it.

    Args:
        idea: TradeIdea object
        rank: Position in scan results (1-based)
        scan_data: Optional dict with extra scan fields (rsi, volume, pattern, etc.)

    Returns:
        PNG bytes
    """
    import re as _re
    sd = scan_data or {}

    # Extract RSI from idea reasoning if not in scan_data
    rsi = sd.get("rsi", 0)
    if not rsi and hasattr(idea, "reasoning") and idea.reasoning:
        m = _re.search(r'RSI[=:\s]+(\d+\.?\d*)', idea.reasoning, _re.IGNORECASE)
        if m:
            rsi = float(m.group(1))

    # Extract pattern from reasoning
    pattern = sd.get("pattern", "")
    if not pattern and hasattr(idea, "reasoning") and idea.reasoning:
        # Look for common patterns mentioned
        _patterns = [
            "Double Bottom", "Double Top", "Head & Shoulders", "Inverse H&S",
            "Bull Flag", "Bear Flag", "Ascending Triangle", "Descending Triangle",
            "Cup & Handle", "Falling Wedge", "Rising Wedge", "Breakout",
            "Pullback", "Liquidity Sweep", "Elliott Wave",
        ]
        reasoning_lower = idea.reasoning.lower()
        for p in _patterns:
            if p.lower() in reasoning_lower:
                pattern = p
                break

    # Extract volume info
    vol_x = sd.get("volume_x", sd.get("vol_spike", 0))
    if not vol_x and hasattr(idea, "reasoning") and idea.reasoning:
        m = _re.search(r'vol[_\s]*spike[=:\s]*([\d.]+)', idea.reasoning, _re.IGNORECASE)
        if m:
            vol_x = float(m.group(1))
        else:
            m = _re.search(r'Vol\s+([\d.]+)x', idea.reasoning)
            if m:
                vol_x = float(m.group(1))

    # Extract regime/mode for summary
    regime = ""
    if hasattr(idea, "reasoning") and idea.reasoning:
        m = _re.search(r'Regime[=:\s]*(\w+)', idea.reasoning, _re.IGNORECASE)
        if m:
            regime = m.group(1)

    # Build summary line
    summary = sd.get("summary", "")
    if not summary:
        dir_str = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
        parts = [f"{dir_str} bias"]
        if rsi:
            parts.append(f"RSI {rsi:.1f}")
        conf = idea.confidence
        if conf <= 1:
            conf *= 100
        parts.append(f"Score {conf:.0f}%")
        if vol_x:
            parts.append(f"Vol {vol_x:.1f}x avg")
        if regime:
            parts.append(regime)
        summary = " | ".join(parts)

    data = {
        "rank": rank,
        "symbol": idea.asset,
        "direction": idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction),
        "entry": idea.entry_price,
        "stop_loss": idea.stop_loss,
        "tp1": idea.take_profit,
        "tp2": sd.get("tp2", 0),
        "margin_usd": sd.get("margin_usd", sd.get("position_size_usd", 0)),
        "rr": idea.risk_reward_ratio if hasattr(idea, "risk_reward_ratio") else 0,
        "pattern": pattern,
        "rsi": rsi,
        "confidence": idea.confidence,
        "volume_x": vol_x,
        "summary": summary,
        "strategy_type": getattr(idea, "strategy_type", ""),
    }
    return render_signal_card(data)


# ═══════════════════════════════════════════════════════════════════
# SCAN RESULTS CARD — multi-setup overview for /fullscan & /deepscan
# ═══════════════════════════════════════════════════════════════════

def render_scan_results_card(
    setups: list[Dict[str, Any]],
    btc_gate: Optional[Dict[str, Any]] = None,
    scan_label: str = "LIVE SCAN",
    timestamp: str = "",
) -> bytes:
    """Render a styled PNG card showing scan results overview."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render scan card")
        return b""

    W = 520
    PAD = 18
    ROW_H = 130
    GATE_H = 70
    HEADER_H = 50
    FOOTER_H = 30
    MAX_SETUPS = 6

    n = min(len(setups), MAX_SETUPS)
    H = HEADER_H + (GATE_H if btc_gate else 0) + n * ROW_H + FOOTER_H + PAD

    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    def _font(size: int, bold: bool = False):
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    f_header = _font(18, bold=True)
    f_rank = _font(14, bold=True)
    f_label = _font(10)
    f_value = _font(13, bold=True)
    f_badge = _font(11, bold=True)
    f_small = _font(10)
    f_gate_sub = _font(11)

    def _fmt(price: float) -> str:
        if price == 0:
            return "\u2014"
        if price >= 1000:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.5f}"
        else:
            return f"${price:.6f}"

    y = 0
    draw.rectangle([0, 0, W, 3], fill=_ACCENT_GOLD)
    y = 8

    title = f"RUNECLAW {scan_label}"
    draw.text((PAD, y + 4), title, fill=_WHITE, font=f_header)
    if timestamp:
        ts_w = draw.textlength(timestamp, font=f_small)
        draw.text((W - PAD - ts_w, y + 8), timestamp, fill=_GRAY, font=f_small)
    y += HEADER_H - 10

    if btc_gate:
        gate_y = y
        draw.rounded_rectangle(
            [PAD, gate_y, W - PAD, gate_y + GATE_H - 8],
            radius=8, fill=_CARD_BG, outline=_BORDER)
        g_price = btc_gate.get("price", 0)
        g_sma = btc_gate.get("sma20", 0)
        g_rsi = btc_gate.get("rsi", 0)
        g_vwap = btc_gate.get("vs_vwap", 0)
        g_label = btc_gate.get("label", "OPEN")
        gate_color = _GREEN if g_label == "OPEN" else _YELLOW if g_label == "CAUTION" else _RED
        draw.text((PAD + 12, gate_y + 8), "BTC GATE", fill=_GRAY, font=f_label)
        draw.text((PAD + 80, gate_y + 8), g_label, fill=gate_color, font=f_badge)
        gate_detail = (
            f"${g_price:,.0f}  |  SMA20 ${g_sma:,.0f}  "
            f"({'+' if g_vwap >= 0 else ''}{g_vwap:.1f}%)  RSI {g_rsi:.0f}"
        )
        draw.text((PAD + 12, gate_y + 30), gate_detail, fill=_WHITE, font=f_gate_sub)
        y += GATE_H

    for i, s in enumerate(setups[:MAX_SETUPS]):
        row_y = y + i * ROW_H
        sym = s.get("sym", "???").replace("/USDT", "").replace(":USDT", "")
        direction = s.get("dir", "LONG").upper()
        entry = s.get("entry", s.get("price", 0))
        sl = s.get("sl", 0)
        tp = s.get("tp", 0)
        rr = s.get("rr", 0)
        rsi = s.get("rsi", 0)
        vol_ratio = s.get("vol_ratio", 0)
        score = s.get("score", 0)
        score_pct = int(score * 100) if score <= 1 else int(score)
        dir_color = _GREEN if direction == "LONG" else _RED
        score_color = _GREEN if score_pct >= 75 else _YELLOW if score_pct >= 60 else _RED

        draw.rounded_rectangle(
            [PAD, row_y + 4, W - PAD, row_y + ROW_H - 4],
            radius=8, fill=_CARD_BG, outline=_BORDER)

        rx = PAD + 12
        ry = row_y + 12
        rank_text = f"#{i + 1}"
        draw.text((rx, ry + 2), rank_text, fill=_GRAY, font=f_badge)
        rx += draw.textlength(rank_text, font=f_badge) + 8
        draw.text((rx, ry - 2), sym, fill=_WHITE, font=f_rank)
        rx += draw.textlength(sym, font=f_rank) + 10
        badge_text = f" {direction} "
        badge_tw = draw.textlength(badge_text, font=f_badge)
        draw.rounded_rectangle(
            [rx, ry, rx + badge_tw + 4, ry + 20], radius=4, fill=dir_color)
        draw.text((rx + 2, ry + 3), badge_text, fill=(0, 0, 0), font=f_badge)

        score_text = f"{score_pct}%"
        score_tw = draw.textlength(score_text, font=f_rank)
        score_x = W - PAD - 12 - score_tw
        draw.text((score_x, ry - 1), score_text, fill=score_color, font=f_rank)
        sc_lbl_w = draw.textlength("SCORE", font=f_label)
        draw.text((score_x + (score_tw - sc_lbl_w) / 2, ry - 14),
                  "SCORE", fill=_GRAY, font=f_label)

        dy = ry + 26
        col_w = (W - PAD * 2 - 24) // 3
        c1 = PAD + 12
        c2 = c1 + col_w
        c3 = c2 + col_w

        draw.text((c1, dy), "ENTRY", fill=_GRAY, font=f_label)
        draw.text((c1, dy + 14), _fmt(entry), fill=_WHITE, font=f_value)

        sl_dist = abs(entry - sl) / entry * 100 if entry > 0 and sl > 0 else 0
        draw.text((c2, dy), "STOP LOSS", fill=_GRAY, font=f_label)
        sl_text = _fmt(sl)
        if sl_dist > 0:
            sl_text += f" ({sl_dist:.1f}%)"
        draw.text((c2, dy + 14), sl_text, fill=_RED, font=f_value)

        tp_dist = abs(tp - entry) / entry * 100 if entry > 0 and tp > 0 else 0
        draw.text((c3, dy), "TAKE PROFIT", fill=_GRAY, font=f_label)
        tp_text = _fmt(tp)
        if tp_dist > 0:
            tp_text += f" ({tp_dist:.1f}%)"
        draw.text((c3, dy + 14), tp_text, fill=_GREEN, font=f_value)

        dy2 = dy + 36
        stats_parts = []
        if rr > 0:
            stats_parts.append(f"R:R {rr:.1f}:1")
        if rsi > 0:
            stats_parts.append(f"RSI {rsi:.0f}")
        if vol_ratio > 0:
            stats_parts.append(f"Vol {vol_ratio:.1f}x")
        draw.text((c1, dy2), "  |  ".join(stats_parts), fill=_DIM, font=f_small)

    footer_y = y + n * ROW_H + 4
    remaining = len(setups) - n
    if remaining > 0:
        draw.text((PAD, footer_y), f"+{remaining} more below threshold",
                  fill=_DIM, font=f_small)

    draw.rectangle([0, H - 3, W, H], fill=_ACCENT_GOLD)
    wm_w = draw.textlength("RUNECLAW", font=f_small)
    draw.text((W - PAD - wm_w, H - 18), "RUNECLAW", fill=_DIM, font=f_small)

    _buf = io.BytesIO()
    img.save(_buf, format="PNG", optimize=True)
    return _buf.getvalue()


# ═══════════════════════════════════════════════════════════════════
# POSITION CARD — styled PNG for open/closed position monitoring
# ═══════════════════════════════════════════════════════════════════

def render_position_card(data: Dict[str, Any]) -> bytes:
    """Render a live position status as a styled PNG card.

    Args:
        data: Dict with keys:
            - symbol: str (e.g. "GRASS/USDT")
            - direction: str ("LONG" or "SHORT")
            - is_live: bool
            - entry: float
            - now: float (current price)
            - pnl_pct: float (e.g. -0.17)
            - pnl_usd: float (e.g. -0.87)
            - net_pnl: float (after fees)
            - fees: float
            - size_usd: float
            - leverage: float
            - hold_time: str (e.g. "2m", "1.4h")
            - rr: float (R:R ratio)
            - sl: float
            - tp: float
            - sl_pct: float (distance %)
            - tp_pct: float (distance %)
            - sl_status: str ("on exchange" or "bot-managed")
            - tp_status: str
            - rsi: float (optional)
            - rsi_label: str (optional, "oversold"/"neutral"/"overbought")
            - structure: str (optional, trend narrative)

    Returns:
        PNG bytes
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render position card")
        return b""

    W, H = 520, 480
    PAD = 20

    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    def _font(size: int, bold: bool = False):
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    f_title = _font(20, bold=True)
    f_label = _font(10)
    f_value = _font(14, bold=True)
    f_big = _font(18, bold=True)
    f_small = _font(10)
    f_badge = _font(12, bold=True)

    # ── Extract data ──
    symbol = data.get("symbol", "???").replace("/USDT", "").replace(":USDT", "")
    direction = data.get("direction", "LONG").upper()
    is_live = data.get("is_live", True)
    entry = data.get("entry", 0)
    now_px = data.get("now", 0)
    pnl_pct = data.get("pnl_pct", 0)
    pnl_usd = data.get("pnl_usd", 0)
    net_pnl = data.get("net_pnl", 0)
    fees = data.get("fees", 0)
    size_usd = data.get("size_usd", 0)
    leverage = data.get("leverage", 1)
    hold_time = data.get("hold_time", "")
    rr = data.get("rr", 0)
    sl = data.get("sl", 0)
    tp = data.get("tp", 0)
    sl_pct = data.get("sl_pct", 0)
    tp_pct = data.get("tp_pct", 0)
    sl_status = data.get("sl_status", "")
    tp_status = data.get("tp_status", "")
    rsi = data.get("rsi", 0)
    rsi_label = data.get("rsi_label", "")
    structure = data.get("structure", "")

    is_long = direction == "LONG"
    dir_color = _GREEN if is_long else _RED
    pnl_positive = net_pnl >= 0
    pnl_color = _GREEN if pnl_positive else _RED

    def _fmt(price: float) -> str:
        if price == 0:
            return "—"
        if price >= 100:
            return f"{price:,.2f}"
        elif price >= 1:
            return f"{price:.4f}"
        elif price >= 0.01:
            return f"{price:.5f}"
        else:
            return f"{price:.6f}"

    y = PAD

    # ── Accent stripe ──
    stripe_color = _GREEN if pnl_positive else _RED
    draw.rectangle([0, 0, W, 4], fill=stripe_color)

    # ── Header: SYMBOL  [LONG]  LIVE ──
    draw.text((PAD, y), symbol, fill=_WHITE, font=f_title)
    sym_w = draw.textlength(symbol, font=f_title)

    badge_x = PAD + sym_w + 12
    badge_text = f" {direction} "
    badge_tw = draw.textlength(badge_text, font=f_badge)
    draw.rounded_rectangle(
        [badge_x, y + 2, badge_x + badge_tw + 4, y + 22],
        radius=4, fill=dir_color)
    draw.text((badge_x + 2, y + 5), badge_text, fill=(0, 0, 0), font=f_badge)

    if is_live:
        live_x = badge_x + badge_tw + 16
        draw.text((live_x, y + 5), "LIVE", fill=_GREEN, font=f_badge)

    y += 34

    # ── PnL Hero Row ──
    pnl_sign = "+" if pnl_pct >= 0 else ""
    pnl_text = f"{pnl_sign}{pnl_pct:.2f}%"
    draw.text((PAD, y), pnl_text, fill=pnl_color, font=_font(28, bold=True))
    pnl_w = draw.textlength(pnl_text, font=_font(28, bold=True))

    usd_text = f"  (${net_pnl:+,.2f})"
    draw.text((PAD + pnl_w, y + 8), usd_text, fill=pnl_color, font=f_value)

    y += 42

    # ── Separator ──
    draw.line([(PAD, y), (W - PAD, y)], fill=_BORDER, width=1)
    y += 12

    # ── Entry / Now row ──
    CELL_W = (W - PAD * 3) // 2
    CELL_H = 52
    GAP = 10

    def _cell(x, cy, label, value, color=_WHITE, w=CELL_W):
        draw.rounded_rectangle([x, cy, x + w, cy + CELL_H],
                               radius=6, fill=_CARD_BG, outline=_BORDER)
        draw.text((x + 10, cy + 6), label, fill=_GRAY, font=f_label)
        draw.text((x + 10, cy + 24), value, fill=color, font=f_value)

    c1 = PAD
    c2 = PAD + CELL_W + GAP

    _cell(c1, y, "ENTRY", _fmt(entry))
    _cell(c2, y, "NOW", _fmt(now_px), pnl_color)
    y += CELL_H + GAP

    # ── SL / TP row ──
    sl_text = f"{_fmt(sl)}  ({sl_pct:.1f}%)" if sl else "—"
    tp_text = f"{_fmt(tp)}  ({tp_pct:.1f}%)" if tp else "—"
    _cell(c1, y, f"STOP LOSS  {sl_status}", sl_text, _RED)
    _cell(c2, y, f"TAKE PROFIT  {tp_status}", tp_text, _GREEN)
    y += CELL_H + GAP

    # ── Size / Hold / R:R / Fees row ──
    lev_str = f" | {leverage:.0f}x" if leverage > 1 else ""
    _cell(c1, y, "SIZE", f"${size_usd:,.2f}{lev_str}")
    rr_str = f"{rr:.1f}x" if rr else "—"
    _cell(c2, y, "R:R | HOLD", f"{rr_str} | {hold_time}")
    y += CELL_H + GAP

    # ── Net PnL + Fees row ──
    net_text = f"${net_pnl:+,.2f}"
    fees_text = f"fees ${fees:.2f}"
    full_w = W - PAD * 2
    _cell(c1, y, "NET PnL", f"{net_text}  ({fees_text})", pnl_color, w=full_w)
    y += CELL_H + GAP + 4

    # ── Market context line ──
    if rsi:
        ctx_parts = [f"RSI {rsi:.0f} ({rsi_label})"]
        if structure:
            ctx_parts.append(structure)
        ctx_text = " | ".join(ctx_parts)
        draw.text((PAD, y), ctx_text, fill=_GRAY, font=f_small)
        y += 18

    # ── Bottom stripe ──
    draw.rectangle([0, H - 4, W, H], fill=stripe_color)

    # ── Watermark ──
    wm = "RUNECLAW"
    wm_w = draw.textlength(wm, font=f_small)
    draw.text((W - PAD - wm_w, H - 20), wm, fill=_DIM, font=f_small)

    _buf = io.BytesIO()
    img.save(_buf, format="PNG", optimize=True)
    return _buf.getvalue()


def render_close_card(data: Dict[str, Any]) -> bytes:
    """Render a trade close confirmation as a styled PNG card.

    Args:
        data: Dict with keys:
            symbol, direction, reason, entry, exit, pnl_pct,
            pnl_usd (net), fees, size_usd, leverage, hold_time

    Returns:
        PNG bytes
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render close card")
        return b""

    W, H = 520, 400
    PAD = 20

    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    def _font(size: int, bold: bool = False):
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    f_title = _font(20, bold=True)
    f_label = _font(10)
    f_value = _font(14, bold=True)
    f_badge = _font(12, bold=True)
    f_small = _font(10)

    symbol = data.get("symbol", "???").replace("/USDT", "").replace(":USDT", "").replace("/", "")
    direction = data.get("direction", "LONG").upper()
    reason = data.get("reason", "closed").upper()
    entry = data.get("entry", 0)
    exit_px = data.get("exit", 0)
    pnl_pct = data.get("pnl_pct", 0)
    pnl_usd = data.get("pnl_usd", 0)
    fees = data.get("fees", 0)
    size_usd = data.get("size_usd", 0)
    leverage = data.get("leverage", 1)
    hold_time = data.get("hold_time", "")

    is_win = pnl_usd >= 0
    pnl_color = _GREEN if is_win else _RED

    def _fmt(price: float) -> str:
        if price == 0:
            return "\u2014"
        if price >= 100:
            return f"{price:,.2f}"
        elif price >= 1:
            return f"{price:.4f}"
        elif price >= 0.01:
            return f"{price:.5f}"
        else:
            return f"{price:.6f}"

    y = PAD
    stripe_color = _GREEN if is_win else _RED
    draw.rectangle([0, 0, W, 4], fill=stripe_color)

    # ── Header: SYMBOL  [CLOSED]  reason ──
    draw.text((PAD, y), symbol, fill=_WHITE, font=f_title)
    sym_w = draw.textlength(symbol, font=f_title)

    badge_x = PAD + sym_w + 12
    badge_text = " CLOSED "
    badge_tw = draw.textlength(badge_text, font=f_badge)
    draw.rounded_rectangle(
        [badge_x, y + 2, badge_x + badge_tw + 4, y + 22],
        radius=4, fill=stripe_color)
    draw.text((badge_x + 2, y + 5), badge_text, fill=(0, 0, 0), font=f_badge)

    reason_x = badge_x + badge_tw + 16
    draw.text((reason_x, y + 5), reason, fill=_GRAY, font=f_badge)
    y += 34

    # ── PnL Hero Row ──
    pnl_sign = "+" if pnl_pct >= 0 else ""
    pnl_text = f"{pnl_sign}{pnl_pct:.2f}%"
    big_font = _font(28, bold=True)
    draw.text((PAD, y), pnl_text, fill=pnl_color, font=big_font)
    pnl_tw = draw.textlength(pnl_text, font=big_font)
    usd_text = f"  (${pnl_usd:+,.2f})"
    draw.text((PAD + pnl_tw, y + 8), usd_text, fill=pnl_color, font=f_value)
    y += 42

    draw.line([(PAD, y), (W - PAD, y)], fill=_BORDER, width=1)
    y += 12

    CELL_W = (W - PAD * 3) // 2
    CELL_H = 52
    GAP = 10
    c1 = PAD
    c2 = PAD + CELL_W + GAP

    def _cell(x, cy, label, value, color=_WHITE, w=CELL_W):
        draw.rounded_rectangle([x, cy, x + w, cy + CELL_H],
                               radius=6, fill=_CARD_BG, outline=_BORDER)
        draw.text((x + 10, cy + 6), label, fill=_GRAY, font=f_label)
        draw.text((x + 10, cy + 24), value, fill=color, font=f_value)

    _cell(c1, y, "ENTRY", _fmt(entry))
    _cell(c2, y, "EXIT", _fmt(exit_px), pnl_color)
    y += CELL_H + GAP

    lev_str = f" | {leverage:.0f}x" if leverage > 1 else ""
    _cell(c1, y, "SIZE", f"${size_usd:,.2f}{lev_str}")
    _cell(c2, y, f"{direction} | HOLD", hold_time)
    y += CELL_H + GAP

    net_text = f"${pnl_usd:+,.2f}"
    fees_text = f"fees ${fees:.2f}"
    full_w = W - PAD * 2
    _cell(c1, y, "NET PnL", f"{net_text}  ({fees_text})", pnl_color, w=full_w)
    y += CELL_H + GAP + 4

    # ── Verification status row ──
    confirmed = data.get("confirmed", None)
    if confirmed is not None:
        verify_color = _GREEN if confirmed else (200, 150, 50)
        verify_text = "CONFIRMED" if confirmed else "UNCONFIRMED"
        verify_icon = "\u2705 " if confirmed else "\u26a0\ufe0f "
        draw.text((PAD, y), f"Verified: {verify_text}", fill=verify_color, font=f_small)
        y += 18

    draw.rectangle([0, H - 4, W, H], fill=stripe_color)
    wm = "RUNECLAW"
    wm_w = draw.textlength(wm, font=f_small)
    draw.text((W - PAD - wm_w, H - 20), wm, fill=_DIM, font=f_small)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
