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

# CJK-capable fonts (cover Latin too) for cards whose labels are translated to
# Chinese. Tried in order; if none are present the renderer falls back to the
# Latin font (Chinese would show as tofu, but English cards are never affected).
_CJK_FONT_PATHS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def humanize_close_reason(raw_reason: str, pnl_usd: float = 0.0) -> tuple[str, str]:
    """Map a raw internal close-reason string to (emoji, friendly_label).

    LiveExecutor's close-reason inference is deliberately honest: when the
    exit price doesn't clearly match either SL or TP, it reports the literal
    string "CLOSED (unknown)" rather than falsely asserting a manual close
    (see LiveExecutor._infer_close_reason). That honesty is the right call
    internally/in audit logs, but three separate user-facing renderers used
    to display that raw string verbatim (as "CLOSED (unknown)" or, upper-
    cased, "CLOSED (UNKNOWN)") -- reading as broken/placeholder text rather
    than a deliberate "we can't prove which trigger fired" signal. When the
    mechanism isn't confidently identified, this drops the technical
    qualifier entirely and just reports the plain outcome (win/loss is
    already conveyed by the PnL figure next to it).
    """
    r = (raw_reason or "").upper()
    if "LIQUID" in r:
        return "\U0001f4a5", "Liquidated"
    if "TP" in r:
        return "\U0001f3af", "Take-Profit Hit"
    if "TRAILING" in r:
        return "\U0001f6d1", "Trailing Stop Hit"
    # Checked before the generic "SL"/"STOP" match below: a time-based exit
    # reason containing the word "stop" (e.g. a hypothetical "TIME_STOP")
    # would otherwise be misclassified as a stop-loss hit.
    if "TIME" in r:
        return "⏰", "Time Stop"
    if "SL" in r or "STOP" in r:
        return "\U0001f6d1", "Stop-Loss Hit"
    return ("✅", "Closed") if pnl_usd >= 0 else ("❌", "Closed")


def _has_cjk(s: str) -> bool:
    """True if the string contains any CJK ideograph (needs a CJK font)."""
    return any("㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in s)


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
# PATTERNS CARD — styled PNG mirroring the deep-scan patterns readout
# ═══════════════════════════════════════════════════════════════════

def render_patterns_card(
    hits: list[Dict[str, Any]],
    scan_label: str = "DEEP SCAN",
    timestamp: str = "",
    subtitle: str = "",
    max_symbols: int = 8,
) -> bytes:
    """Render the deep-scan pattern observations as a PNG card.

    Mirrors the text readout: one block per symbol with a header
    (direction arrow, symbol, price, change %, RSI), the top chart patterns
    each with a confidence bar, and a row of candlestick badges.

    Args:
        hits: list of dicts with keys ``symbol``, ``price``, ``chg``, ``rsi``,
            ``vol_spike``, ``chart_patterns`` (list of {name, signal,
            confidence}), ``candle_patterns`` (dict name->signal).
        scan_label: e.g. "DEEP SCAN 4H".
        timestamp: right-aligned header timestamp.
        subtitle: small line under the title (e.g. "100 symbols · 4h").
        max_symbols: cap the number of symbol blocks drawn.

    Returns:
        PNG bytes (empty bytes if Pillow is unavailable).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render patterns card")
        return b""

    W = 560
    PAD = 18
    HEADER_H = 64
    FOOTER_H = 26
    HDR_ROW = 26       # per-symbol header line
    PAT_ROW = 22       # per chart-pattern row
    CANDLE_ROW = 22    # candle badge line
    BLOCK_PAD = 22     # top+bottom padding inside a block panel
    GAP = 10           # gap between blocks

    shown = [h for h in (hits or [])][:max_symbols]

    def _block_h(h: Dict[str, Any]) -> int:
        n_pat = min(len(h.get("chart_patterns") or []), 3)
        has_candle = bool(h.get("candle_patterns"))
        return BLOCK_PAD + HDR_ROW + n_pat * PAT_ROW + (CANDLE_ROW if has_candle else 0)

    body_h = sum(_block_h(h) + GAP for h in shown) if shown else 60
    H = HEADER_H + body_h + FOOTER_H

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
    f_sub = _font(10)
    f_sym = _font(14, bold=True)
    f_price = _font(12, bold=True)
    f_label = _font(10)
    f_pat = _font(11)
    f_small = _font(10)

    def _fmt(price: float) -> str:
        if price >= 1000:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.5f}"
        return f"${price:.6f}"

    def _sig_color(sig: str):
        return _GREEN if sig == "bullish" else _RED if sig == "bearish" else _GRAY

    # ── Header ──
    draw.rectangle([0, 0, W, 3], fill=_ACCENT_GOLD)
    title = f"RUNECLAW {scan_label}"
    draw.text((PAD, 12), title, fill=_WHITE, font=f_header)
    if timestamp:
        ts_w = draw.textlength(timestamp, font=f_small)
        draw.text((W - PAD - ts_w, 16), timestamp, fill=_GRAY, font=f_small)
    if subtitle:
        draw.text((PAD, 38), subtitle, fill=_GRAY, font=f_sub)

    y = HEADER_H

    if not shown:
        draw.text((PAD, y + 8), "No actionable patterns detected.",
                  fill=_GRAY, font=f_pat)
    for h in shown:
        bh = _block_h(h)
        draw.rounded_rectangle(
            [PAD, y, W - PAD, y + bh], radius=8, fill=_CARD_BG, outline=_BORDER)

        sym = str(h.get("symbol", "?")).replace("/USDT", "").replace(":USDT", "")
        price = float(h.get("price", 0) or 0)
        chg = float(h.get("chg", 0) or 0)
        rsi = float(h.get("rsi", 0) or 0)

        # Direction arrow from change.
        if chg > 0:
            arrow, arrow_c = "▲", _GREEN     # ▲
        elif chg < 0:
            arrow, arrow_c = "▼", _RED       # ▼
        else:
            arrow, arrow_c = "●", _GRAY      # ●

        hx = PAD + 14
        hy = y + 12
        draw.text((hx, hy + 1), arrow, fill=arrow_c, font=f_price)
        hx += draw.textlength(arrow, font=f_price) + 8
        draw.text((hx, hy - 1), sym, fill=_WHITE, font=f_sym)
        hx += draw.textlength(sym, font=f_sym) + 12
        draw.text((hx, hy + 1), _fmt(price), fill=_CYAN, font=f_price)
        hx += draw.textlength(_fmt(price), font=f_price) + 12
        chg_c = _GREEN if chg > 0 else _RED if chg < 0 else _GRAY
        chg_txt = f"{chg:+.1f}%"
        draw.text((hx, hy + 1), chg_txt, fill=chg_c, font=f_price)

        # RSI badge, right aligned.
        rsi_tag = " OB" if rsi > 70 else " OS" if rsi < 30 else ""
        rsi_c = _RED if rsi > 70 else _GREEN if rsi < 30 else _GRAY
        rsi_txt = f"RSI {rsi:.0f}{rsi_tag}"
        if h.get("vol_spike"):
            rsi_txt += "  VOL"
        rsi_w = draw.textlength(rsi_txt, font=f_price)
        draw.text((W - PAD - 14 - rsi_w, hy + 1), rsi_txt, fill=rsi_c, font=f_price)

        ry = hy + HDR_ROW
        # ── Chart patterns with confidence bars ──
        bar_w = 96
        bar_x = W - PAD - 14 - 44 - bar_w  # leave room for the "NN%" after the bar
        for cp in (h.get("chart_patterns") or [])[:3]:
            sig = str(cp.get("signal", "neutral"))
            sc = _sig_color(sig)
            conf = float(cp.get("confidence", 0) or 0)
            # signal dot
            draw.ellipse([PAD + 16, ry + 4, PAD + 24, ry + 12], fill=sc)
            name = str(cp.get("name", ""))
            # truncate long names so they don't collide with the bar
            while name and draw.textlength(name, font=f_pat) > (bar_x - (PAD + 30) - 8):
                name = name[:-1]
            draw.text((PAD + 30, ry + 1), name, fill=_WHITE, font=f_pat)
            # bar track + fill
            draw.rounded_rectangle([bar_x, ry + 3, bar_x + bar_w, ry + 12],
                                   radius=4, fill=_DIM)
            fill_w = int(max(0.0, min(1.0, conf)) * bar_w)
            if fill_w > 0:
                draw.rounded_rectangle([bar_x, ry + 3, bar_x + fill_w, ry + 12],
                                       radius=4, fill=sc)
            draw.text((bar_x + bar_w + 8, ry), f"{conf:.0%}", fill=_GRAY, font=f_small)
            ry += PAT_ROW

        # ── Candlestick badges ──
        candles = h.get("candle_patterns") or {}
        if candles:
            cx = PAD + 16
            # Small drawn candlestick icon (DejaVu has no emoji glyph): a wick
            # line through a filled body.
            draw.line([cx + 4, ry + 1, cx + 4, ry + 15], fill=_YELLOW, width=1)
            draw.rectangle([cx + 1, ry + 4, cx + 7, ry + 12], fill=_YELLOW)
            cx += 14
            for k, v in list(candles.items())[:4]:
                cc = _sig_color(str(v))
                # colored dot + name
                draw.ellipse([cx, ry + 5, cx + 7, ry + 12], fill=cc)
                cx += 11
                label = str(k)
                draw.text((cx, ry + 1), label, fill=_GRAY, font=f_small)
                cx += draw.textlength(label, font=f_small) + 12

        y += bh + GAP

    # ── Footer ──
    draw.rectangle([0, H - 3, W, H], fill=_ACCENT_GOLD)
    foot = "Patterns are observations, not signals"
    draw.text((PAD, H - 20), foot, fill=_DIM, font=f_small)
    wm_w = draw.textlength("RUNECLAW", font=f_small)
    draw.text((W - PAD - wm_w, H - 20), "RUNECLAW", fill=_DIM, font=f_small)

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
    # GROSS sign (matches pnl_pct, the hero %, the current-price cell, and the
    # card's overall accent) -- kept separate from the NET-of-fees sign below.
    # A small favorable price move that fees eat into (net negative on a gross
    # positive move, or vice versa) previously showed the gross "+0.60%" in
    # red/loss color right next to a negative net dollar figure in the SAME
    # line, reading as self-contradictory even though both numbers were
    # individually correct.
    pnl_positive = pnl_pct >= 0
    pnl_color = _GREEN if pnl_positive else _RED
    # NET (after-fee) sign -- used ONLY for the dedicated "NET PnL" cell below,
    # which can legitimately disagree with the gross sign once fees are
    # subtracted.
    net_positive = net_pnl >= 0
    net_color = _GREEN if net_positive else _RED

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

    usd_text = f"  (${pnl_usd:+,.2f})"
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
    _cell(c1, y, "NET PnL", f"{net_text}  ({fees_text})", net_color, w=full_w)
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
    pnl_usd = data.get("pnl_usd", 0)
    _, reason = humanize_close_reason(data.get("reason", "closed"), pnl_usd)
    reason = reason.upper()
    entry = data.get("entry", 0)
    exit_px = data.get("exit", 0)
    pnl_pct = data.get("pnl_pct", 0)
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


# ═══════════════════════════════════════════════════════════════════
# ORDERS CARD — styled PNG for open/pending orders display
# ═══════════════════════════════════════════════════════════════════

def render_orders_card(orders: list[Dict[str, Any]], timestamp: str = "") -> bytes:
    """Render open orders as a styled PNG card.

    Args:
        orders: List of dicts with keys:
            sym, side, price, current_price, amount, ttl_str, oid, created,
            type ("limit"|"stop"|"take_profit"), dist_pct
        timestamp: UTC time string

    Returns:
        PNG bytes
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render orders card")
        return b""

    W = 520
    PAD = 18
    HEADER_H = 50
    ROW_H = 110
    FOOTER_H = 30
    MAX_ORDERS = 6

    n = min(len(orders), MAX_ORDERS)
    if n == 0:
        return b""

    H = HEADER_H + n * ROW_H + FOOTER_H + PAD

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

    def _fmt(price: float) -> str:
        if price == 0:
            return "\u2014"
        if price >= 1000:
            return f"${price:,.4f}"
        elif price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.5f}"
        else:
            return f"${price:.6f}"

    y = 0
    # Purple accent for orders (different from gold for scans)
    _PURPLE = (140, 80, 220)
    draw.rectangle([0, 0, W, 3], fill=_PURPLE)
    y = 8

    title = f"OPEN ORDERS ({n})"
    draw.text((PAD, y + 4), title, fill=_WHITE, font=f_header)
    if timestamp:
        ts_w = draw.textlength(timestamp, font=f_small)
        draw.text((W - PAD - ts_w, y + 8), timestamp, fill=_GRAY, font=f_small)
    y += HEADER_H - 10

    for i, o in enumerate(orders[:MAX_ORDERS]):
        row_y = y + i * ROW_H
        sym = o.get("sym", "???").replace("/USDT", "").replace(":USDT", "")
        side = o.get("side", "BUY").upper()
        price = o.get("price", 0)
        cur_price = o.get("current_price", 0)
        amount = o.get("amount", 0)
        ttl = o.get("ttl_str", "")
        oid = o.get("oid", "")
        created = o.get("created", "")
        otype = o.get("type", "limit")
        dist_pct = o.get("dist_pct", 0)

        is_buy = side == "BUY"
        dir_label = "LONG" if is_buy else "SHORT"
        dir_color = _GREEN if is_buy else _RED

        # Type label
        if "stop" in otype or "loss" in otype:
            type_label = "STOP LOSS"
            type_color = _RED
        elif "take" in otype or "profit" in otype:
            type_label = "TAKE PROFIT"
            type_color = _GREEN
        else:
            type_label = "LIMIT"
            type_color = _CYAN

        # Row background
        draw.rounded_rectangle(
            [PAD, row_y + 4, W - PAD, row_y + ROW_H - 4],
            radius=8, fill=_CARD_BG, outline=_BORDER)

        # Header: SYM  [SHORT]  LIMIT
        rx = PAD + 12
        ry = row_y + 12
        draw.text((rx, ry - 2), sym, fill=_WHITE, font=f_rank)
        rx += draw.textlength(sym, font=f_rank) + 10

        badge_text = f" {dir_label} "
        badge_tw = draw.textlength(badge_text, font=f_badge)
        draw.rounded_rectangle(
            [rx, ry, rx + badge_tw + 4, ry + 20], radius=4, fill=dir_color)
        draw.text((rx + 2, ry + 3), badge_text, fill=(0, 0, 0), font=f_badge)
        rx += badge_tw + 12

        type_text = f" {type_label} "
        type_tw = draw.textlength(type_text, font=f_badge)
        draw.rounded_rectangle(
            [rx, ry, rx + type_tw + 4, ry + 20], radius=4,
            fill=(30, 35, 50), outline=type_color)
        draw.text((rx + 2, ry + 3), type_text, fill=type_color, font=f_badge)

        # Data row: Limit | Current | Distance
        dy = ry + 26
        col_w = (W - PAD * 2 - 24) // 3
        c1 = PAD + 12
        c2 = c1 + col_w
        c3 = c2 + col_w

        draw.text((c1, dy), "LIMIT PRICE", fill=_GRAY, font=f_label)
        draw.text((c1, dy + 14), _fmt(price), fill=_WHITE, font=f_value)

        if cur_price > 0:
            draw.text((c2, dy), "CURRENT", fill=_GRAY, font=f_label)
            draw.text((c2, dy + 14), _fmt(cur_price), fill=_CYAN, font=f_value)

            draw.text((c3, dy), "TO FILL", fill=_GRAY, font=f_label)
            dist_color = _GREEN if abs(dist_pct) < 0.5 else _YELLOW if abs(dist_pct) < 2 else _WHITE
            draw.text((c3, dy + 14), f"{dist_pct:+.2f}%", fill=dist_color, font=f_value)

        # Bottom: Qty | TTL | ID
        dy2 = dy + 36
        info_parts = [f"Qty: {amount:.4f}"]
        if ttl:
            # Strip emoji from ttl_str
            clean_ttl = ttl.replace(" | ", "").replace("\u23f0 ", "").strip()
            if clean_ttl:
                info_parts.append(clean_ttl)
        if oid:
            info_parts.append(f"ID: {oid}")
        draw.text((c1, dy2), "  |  ".join(info_parts), fill=_DIM, font=f_small)

        # Separator
        if i < n - 1:
            sep_y = row_y + ROW_H - 2
            draw.line([(PAD + 10, sep_y), (W - PAD - 10, sep_y)],
                      fill=_BORDER, width=1)

    # Footer
    footer_y = y + n * ROW_H + 4
    draw.text((PAD, footer_y), "Bitget USDT-M Futures", fill=_DIM, font=f_small)

    draw.rectangle([0, H - 3, W, H], fill=_PURPLE)
    wm_w = draw.textlength("RUNECLAW", font=f_small)
    draw.text((W - PAD - wm_w, H - 18), "RUNECLAW", fill=_DIM, font=f_small)

    _buf = io.BytesIO()
    img.save(_buf, format="PNG", optimize=True)
    return _buf.getvalue()


# ═══════════════════════════════════════════════════════════════════
# SCAN GRID CARD — breadth grid (all symbols + sparklines) + top setups
# ═══════════════════════════════════════════════════════════════════

def render_scan_grid_card(data: Dict[str, Any]) -> bytes:
    """Render a market scan as a PNG card: a compact breadth GRID (every scanned
    symbol with a price sparkline) followed by an optional detailed SETUPS section.

    Args:
        data: dict with keys
            - title: str          (e.g. "US STOCK SCAN")
            - timestamp: str      (e.g. "08:20 UTC")
            - banner: str         (optional warning line, e.g. weekend liquidity)
            - grid: list of dicts, each:
                sym: str, price: float, change_pct: float,
                rsi: float | None, score: float | None (0-1),
                spark: list[float] | None   (recent closes for the sparkline)
            - setups: list of dicts (optional), each:
                sym, direction ("LONG"/"SHORT"), entry, stop_loss,
                take_profit, rr (optional), score (optional 0-1)
            - summary: dict (optional): up:int, down:int, vol_usd:float

    Returns:
        PNG bytes (b"" if Pillow unavailable or grid empty).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render scan grid card")
        return b""

    grid = data.get("grid") or []
    if not grid:
        return b""

    setups = data.get("setups") or []
    title = str(data.get("title", "MARKET SCAN"))
    timestamp = str(data.get("timestamp", ""))
    banner = str(data.get("banner", "") or "")
    summary = data.get("summary") or {}

    W = 560
    PAD = 18
    HEADER_H = 46
    BANNER_H = 22 if banner else 0
    GRID_ROW_H = 30
    SETUP_ROW_H = 64
    SECTION_H = 24
    FOOTER_H = 30

    MAX_GRID = 20
    MAX_SETUPS = 6
    g_rows = grid[:MAX_GRID]
    s_rows = setups[:MAX_SETUPS]

    setups_block = (SECTION_H + len(s_rows) * SETUP_ROW_H) if s_rows else 0
    H = (HEADER_H + BANNER_H + SECTION_H + len(g_rows) * GRID_ROW_H
         + setups_block + FOOTER_H + PAD)

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
    f_section = _font(11, bold=True)
    f_sym = _font(13, bold=True)
    f_value = _font(12, bold=True)
    f_label = _font(9)
    f_small = _font(10)

    def _fmt(price: float) -> str:
        if not price:
            return "—"
        if price >= 1000:
            return f"${price:,.2f}"
        if price >= 1:
            return f"${price:.2f}"
        if price >= 0.01:
            return f"${price:.4f}"
        return f"${price:.6f}"

    def _spark(vals, x, y, w, h, color):
        """Draw a tiny price polyline scaled into the (x,y,w,h) box."""
        pts = [float(v) for v in (vals or []) if isinstance(v, (int, float))]
        if len(pts) < 2:
            return
        lo, hi = min(pts), max(pts)
        rng = (hi - lo) or 1.0
        n = len(pts)
        coords = [
            (x + (i / (n - 1)) * w, y + h - ((v - lo) / rng) * h)
            for i, v in enumerate(pts)
        ]
        draw.line(coords, fill=color, width=1)

    _GOLD = _ACCENT_GOLD
    # ── Header ──
    draw.rectangle([0, 0, W, 3], fill=_GOLD)
    draw.text((PAD, 12), title, fill=_WHITE, font=f_header)
    if timestamp:
        tw = draw.textlength(timestamp, font=f_small)
        draw.text((W - PAD - tw, 16), timestamp, fill=_GRAY, font=f_small)
    y = HEADER_H

    # ── Optional banner ──
    if banner:
        draw.text((PAD, y), banner, fill=_YELLOW, font=f_small)
        y += BANNER_H

    # ── GRID section ──
    draw.text((PAD, y + 6), f"BREADTH — {len(grid)} symbols", fill=_GRAY, font=f_section)
    y += SECTION_H

    # Columns: dot | SYM | price | %chg | sparkline | RSI | score bar
    c_dot = PAD + 2
    c_sym = PAD + 16
    c_price = PAD + 92
    c_chg = PAD + 168
    c_spark = PAD + 238
    spark_w = 60
    c_rsi = c_spark + spark_w + 14
    c_score = c_rsi + 56

    for i, g in enumerate(g_rows):
        ry = y + i * GRID_ROW_H
        if i % 2 == 0:
            draw.rectangle([PAD, ry, W - PAD, ry + GRID_ROW_H], fill=_CARD_BG)
        sym = str(g.get("sym", "?")).replace("/USDT", "").replace(":USDT", "")
        price = float(g.get("price", 0) or 0)
        chg = float(g.get("change_pct", 0) or 0)
        up = chg >= 0
        col = _GREEN if up else _RED
        mid = ry + GRID_ROW_H // 2

        draw.ellipse([c_dot, mid - 4, c_dot + 8, mid + 4], fill=col)
        draw.text((c_sym, mid - 7), sym[:7], fill=_WHITE, font=f_sym)
        draw.text((c_price, mid - 6), _fmt(price), fill=_WHITE, font=f_value)
        draw.text((c_chg, mid - 6), f"{chg:+.1f}%", fill=col, font=f_value)

        _spark(g.get("spark"), c_spark, mid - 8, spark_w, 16, col)

        rsi = g.get("rsi")
        if isinstance(rsi, (int, float)) and rsi > 0:
            rsi_col = _RED if rsi >= 70 else _GREEN if rsi <= 30 else _GRAY
            draw.text((c_rsi, mid - 6), f"{rsi:.0f}", fill=rsi_col, font=f_value)

        score = g.get("score")
        if isinstance(score, (int, float)) and score > 0:
            bar_w = 44
            fill_w = int(max(0.0, min(1.0, score)) * bar_w)
            by = mid - 4
            draw.rectangle([c_score, by, c_score + bar_w, by + 8], fill=_DIM)
            sc_col = _GREEN if score >= 0.75 else _YELLOW if score >= 0.6 else _GRAY
            if fill_w > 0:
                draw.rectangle([c_score, by, c_score + fill_w, by + 8], fill=sc_col)

    y += len(g_rows) * GRID_ROW_H

    # ── SETUPS section ──
    if s_rows:
        draw.text((PAD, y + 6), f"TOP SETUPS ({len(s_rows)})", fill=_GRAY, font=f_section)
        y += SECTION_H
        for i, s in enumerate(s_rows):
            ry = y + i * SETUP_ROW_H
            draw.rounded_rectangle(
                [PAD, ry + 3, W - PAD, ry + SETUP_ROW_H - 3],
                radius=8, fill=_CARD_BG, outline=_BORDER)
            sym = str(s.get("sym", "?")).replace("/USDT", "").replace(":USDT", "")
            direction = str(s.get("direction", "LONG")).upper()
            is_long = direction == "LONG"
            col = _GREEN if is_long else _RED
            rx = PAD + 12
            ty = ry + 10
            draw.text((rx, ty), sym, fill=_WHITE, font=f_sym)
            rx += draw.textlength(sym, font=f_sym) + 10
            bt = f" {direction} "
            btw = draw.textlength(bt, font=f_section)
            draw.rounded_rectangle([rx, ty, rx + btw + 4, ty + 18], radius=4, fill=col)
            draw.text((rx + 2, ty + 2), bt, fill=(0, 0, 0), font=f_section)
            rr = s.get("rr")
            if isinstance(rr, (int, float)) and rr > 0:
                rr_txt = f"R:R {rr:.1f}"
                rrw = draw.textlength(rr_txt, font=f_small)
                draw.text((W - PAD - 12 - rrw, ty + 2), rr_txt, fill=_CYAN, font=f_small)

            dy = ty + 26
            col_w = (W - PAD * 2 - 24) // 3
            for j, (lbl, val, vc) in enumerate([
                ("ENTRY", _fmt(float(s.get("entry", 0) or 0)), _WHITE),
                ("STOP", _fmt(float(s.get("stop_loss", 0) or 0)), _RED),
                ("TARGET", _fmt(float(s.get("take_profit", 0) or 0)), _GREEN),
            ]):
                cx = PAD + 12 + j * col_w
                draw.text((cx, dy), lbl, fill=_GRAY, font=f_label)
                draw.text((cx, dy + 12), val, fill=vc, font=f_value)
        y += len(s_rows) * SETUP_ROW_H

    # ── Footer ──
    fy = y + 6
    if summary:
        up_n = int(summary.get("up", 0) or 0)
        dn_n = int(summary.get("down", 0) or 0)
        vol = float(summary.get("vol_usd", 0) or 0)
        draw.text((PAD, fy), f"▲ {up_n} up", fill=_GREEN, font=f_small)
        ux = PAD + draw.textlength(f"▲ {up_n} up", font=f_small) + 12
        draw.text((ux, fy), f"▼ {dn_n} down", fill=_RED, font=f_small)
        if vol > 0:
            vtxt = f"Vol ${vol / 1e6:.1f}M"
            vw = draw.textlength(vtxt, font=f_small)
            draw.text((W - PAD - vw, fy), vtxt, fill=_GRAY, font=f_small)

    draw.rectangle([0, H - 3, W, H], fill=_GOLD)
    wm_w = draw.textlength("RUNECLAW", font=f_small)
    draw.text((W - PAD - wm_w, H - 18), "RUNECLAW", fill=_DIM, font=f_small)

    _buf = io.BytesIO()
    img.save(_buf, format="PNG", optimize=True)
    return _buf.getvalue()


# ═══════════════════════════════════════════════════════════════════
# STATS CARD — hero number + tile grid for /portfolio /performance /risk
# ═══════════════════════════════════════════════════════════════════

_STAT_COLORS = {
    "green": _GREEN, "red": _RED, "white": _WHITE, "gray": _GRAY,
    "cyan": _CYAN, "yellow": _YELLOW, "gold": _ACCENT_GOLD,
}


def render_stats_card(data: Dict[str, Any]) -> bytes:
    """Render a status readout as a PNG card: an optional hero number plus a
    2-column grid of labelled stat tiles. Used by /portfolio, /performance, /risk.

    Args:
        data: dict with keys
            - title: str
            - subtitle: str (optional, e.g. "LIVE · 08:20 UTC")
            - hero: {label, value, color} (optional big number)
            - tiles: list of {label, value, color} (color is a key in _STAT_COLORS)
            - footer: str (optional)

    Returns:
        PNG bytes (b"" if Pillow unavailable).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render stats card")
        return b""

    title = str(data.get("title", "STATS"))
    subtitle = str(data.get("subtitle", "") or "")
    hero = data.get("hero") or None
    tiles = data.get("tiles") or []
    footer = str(data.get("footer", "") or "")

    # Does any label/value need CJK glyphs? (e.g. zh-localized labels.) If so,
    # render the whole card with a CJK-capable font so Chinese doesn't tofu.
    # English cards never trip this, so their rendering is unchanged.
    _texts = [title, subtitle, footer]
    if hero:
        _texts += [str(hero.get("label", "")), str(hero.get("value", ""))]
    for _t in tiles:
        _texts += [str(_t.get("label", "")), str(_t.get("value", ""))]
    use_cjk = any(_has_cjk(s) for s in _texts)

    W = 520
    PAD = 18
    HEADER_H = 56
    HERO_H = 60 if hero else 0
    TILE_W = (W - PAD * 3) // 2
    TILE_H = 58
    GAP = 10
    rows = (len(tiles) + 1) // 2
    FOOTER_H = 28 if footer else 12
    H = HEADER_H + HERO_H + rows * (TILE_H + GAP) + FOOTER_H + PAD

    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    def _font(size: int, bold: bool = False):
        latin = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]
        # When the card has CJK text, try a CJK-capable font first (it also
        # covers Latin, so numbers/$ still render); fall back to DejaVu.
        paths = (_CJK_FONT_PATHS + latin) if use_cjk else latin
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    f_title = _font(19, bold=True)
    f_sub = _font(10)
    f_hero = _font(30, bold=True)
    f_hlabel = _font(10)
    f_label = _font(10)
    f_value = _font(17, bold=True)

    def _col(key):
        return _STAT_COLORS.get(str(key or "white"), _WHITE)

    # ── Header ──
    draw.rectangle([0, 0, W, 3], fill=_ACCENT_GOLD)
    draw.text((PAD, 12), title, fill=_WHITE, font=f_title)
    if subtitle:
        sw = draw.textlength(subtitle, font=f_sub)
        draw.text((W - PAD - sw, 18), subtitle, fill=_GRAY, font=f_sub)
    y = HEADER_H

    # ── Hero number ──
    if hero:
        draw.text((PAD, y), str(hero.get("label", "")).upper(), fill=_GRAY, font=f_hlabel)
        draw.text((PAD, y + 14), str(hero.get("value", "")),
                  fill=_col(hero.get("color")), font=f_hero)
        y += HERO_H

    # ── Tile grid ──
    for i, tile in enumerate(tiles):
        r, c = divmod(i, 2)
        tx = PAD + c * (TILE_W + PAD)
        ty = y + r * (TILE_H + GAP)
        draw.rounded_rectangle([tx, ty, tx + TILE_W, ty + TILE_H],
                               radius=8, fill=_CARD_BG, outline=_BORDER)
        draw.text((tx + 12, ty + 10), str(tile.get("label", "")).upper(),
                  fill=_GRAY, font=f_label)
        draw.text((tx + 12, ty + 26), str(tile.get("value", "")),
                  fill=_col(tile.get("color")), font=f_value)
    y += rows * (TILE_H + GAP)

    # ── Footer ──
    if footer:
        draw.text((PAD, y + 2), footer, fill=_DIM, font=f_sub)

    draw.rectangle([0, H - 3, W, H], fill=_ACCENT_GOLD)
    wm_w = draw.textlength("RUNECLAW", font=f_sub)
    draw.text((W - PAD - wm_w, H - 16), "RUNECLAW", fill=_DIM, font=f_sub)

    _buf = io.BytesIO()
    img.save(_buf, format="PNG", optimize=True)
    return _buf.getvalue()


def render_alpha_card(data: Dict[str, Any]) -> bytes:
    """Render the Daily Alpha insight (bot/core/alpha_card.build_alpha_insight
    output) as a RUNECLAW-styled PNG: gold accent, trend badge with per-TF
    dots, support/resistance cells, MACD/RSI/ADX strength tags, a long/short
    ratio bar (the crypto-native "general rating"), and sentiment footer.
    Sections whose data is missing are skipped, and the canvas is cropped to
    the drawn height. Returns b"" if Pillow is unavailable or on error data.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render alpha card")
        return b""
    if data.get("error"):
        return b""

    W = 520
    PAD = 20
    CANVAS_H = 900  # tall working canvas; cropped to the final y at the end

    img = Image.new("RGB", (W, CANVAS_H), _BG)
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
    f_badge = _font(12, bold=True)
    f_label = _font(10)
    f_value = _font(14, bold=True)
    f_small = _font(10)
    f_hero = _font(26, bold=True)

    def _fmt(price: float) -> str:
        if price == 0:
            return "—"
        if price >= 1000:
            return f"{price:,.2f}"
        if price >= 1:
            return f"{price:,.4f}"
        return f"{price:.6f}"

    symbol = str(data.get("symbol", "???")).replace("/USDT", "").replace(":USDT", "")
    price = float(data.get("price") or 0)
    chg = float(data.get("change_24h_pct") or 0)
    chg_color = _GREEN if chg >= 0 else _RED

    # ── Top stripe (gold = insight, not P&L) ──
    draw.rectangle([0, 0, W, 4], fill=_ACCENT_GOLD)
    y = PAD

    # ── Header: SYMBOL [DAILY ALPHA] ──
    draw.text((PAD, y), symbol, fill=_WHITE, font=f_title)
    sym_w = draw.textlength(symbol, font=f_title)
    badge_x = PAD + sym_w + 12
    badge_text = " DAILY ALPHA "
    badge_tw = draw.textlength(badge_text, font=f_badge)
    draw.rounded_rectangle([badge_x, y + 2, badge_x + badge_tw + 4, y + 22],
                           radius=4, fill=_ACCENT_GOLD)
    draw.text((badge_x + 2, y + 5), badge_text, fill=(0, 0, 0), font=f_badge)
    gen = data.get("generated")
    if gen is not None:
        try:
            ts = gen.strftime("%Y-%m-%d %H:%M UTC")
            ts_w = draw.textlength(ts, font=f_small)
            draw.text((W - PAD - ts_w, y + 7), ts, fill=_GRAY, font=f_small)
        except Exception:
            pass
    y += 34

    # ── Price hero ──
    draw.text((PAD, y), f"${_fmt(price)}", fill=_WHITE, font=f_hero)
    p_w = draw.textlength(f"${_fmt(price)}", font=f_hero)
    draw.text((PAD + p_w + 10, y + 10), f"{chg:+.2f}% 24h", fill=chg_color, font=f_value)
    y += 40
    draw.line([(PAD, y), (W - PAD, y)], fill=_BORDER, width=1)
    y += 12

    # ── Trend badge + per-TF dots ──
    label = str(data.get("trend_label") or "")
    if not label:
        # Derive lazily so callers can pass either the label or the raw parts.
        try:
            from bot.core.alpha_card import overall_trend_label
            label = overall_trend_label(
                data.get("htf_trend", ""), int(data.get("bos_dir", 0)),
                int(data.get("choch_dir", 0)))
        except Exception:
            label = "Range / Mixed"
    draw.text((PAD, y), "OVERALL TREND", fill=_GRAY, font=f_label)
    y += 16
    draw.text((PAD, y), label, fill=_CYAN, font=f_value)
    # per-TF dots to the right of the label
    per_tf = data.get("per_tf") or {}
    dot_x = PAD + draw.textlength(label, font=f_value) + 24
    _dot_color = {"up": _GREEN, "down": _RED, "flat": _GRAY}
    for tf in ("1d", "4h", "1h"):
        if tf not in per_tf:
            continue
        c = _dot_color.get(per_tf[tf], _GRAY)
        draw.ellipse([dot_x, y + 4, dot_x + 9, y + 13], fill=c)
        draw.text((dot_x + 13, y + 2), tf.upper(), fill=_GRAY, font=f_label)
        dot_x += 13 + draw.textlength(tf.upper(), font=f_label) + 14
    y += 28

    # ── Support / Resistance cells ──
    lv = data.get("levels") or {}
    sups = lv.get("supports") or []
    ress = lv.get("resistances") or []
    if sups or ress:
        CELL_W = (W - PAD * 3) // 2
        CELL_H = 30 + 16 * max(len(sups), len(ress), 1)
        c1, c2 = PAD, PAD + CELL_W + 10
        for x, title, vals, color in ((c1, "SUPPORT", sups, _GREEN),
                                      (c2, "RESISTANCE", ress, _RED)):
            draw.rounded_rectangle([x, y, x + CELL_W, y + CELL_H],
                                   radius=6, fill=_CARD_BG, outline=_BORDER)
            draw.text((x + 10, y + 6), title, fill=_GRAY, font=f_label)
            yy = y + 22
            for v in vals[:3]:
                draw.text((x + 10, yy), _fmt(float(v)), fill=color, font=_font(12, bold=True))
                yy += 16
            if not vals:
                draw.text((x + 10, yy), "—", fill=_DIM, font=f_value)
        y += CELL_H + 10

    # ── Strength row ──
    st = data.get("strength") or {}
    if st:
        draw.text((PAD, y), "STRENGTH", fill=_GRAY, font=f_label)
        y += 16
        xx = PAD
        def _tag(text: str, good: bool):
            nonlocal xx
            color = _GREEN if good else _RED
            tw = draw.textlength(text, font=f_badge)
            draw.rounded_rectangle([xx, y, xx + tw + 10, y + 20],
                                   radius=4, outline=color)
            draw.text((xx + 5, y + 3), text, fill=color, font=f_badge)
            xx += tw + 20
        if "macd_1d" in st:
            _tag(f"MACD 1D {'Buy' if st['macd_1d'] > 0 else 'Sell'}", st["macd_1d"] > 0)
        if "macd_4h" in st:
            _tag(f"MACD 4H {'Buy' if st['macd_4h'] > 0 else 'Sell'}", st["macd_4h"] > 0)
        y += 26
        parts = []
        if "rsi_1h" in st:
            r = st["rsi_1h"]
            zone = "oversold" if r < 30 else ("overbought" if r > 70 else "neutral")
            parts.append(f"RSI(1H) {r:.1f} ({zone})")
        if "adx_1h" in st:
            a = st["adx_1h"]
            parts.append(f"ADX(1H) {a:.1f} ({'trending' if a >= 20 else 'weak'})")
        if parts:
            draw.text((PAD, y), "  |  ".join(parts), fill=_WHITE, font=f_small)
            y += 20
        y += 4

    # ── Positioning ──
    has_pos = any(k in data for k in
                  ("funding_rate", "open_interest_usd", "long_short_ratio"))
    if has_pos:
        draw.text((PAD, y), "POSITIONING", fill=_GRAY, font=f_label)
        y += 16
        row = []
        if "funding_rate" in data:
            f = data["funding_rate"] * 100
            payer = "longs pay" if f > 0 else ("shorts pay" if f < 0 else "flat")
            row.append(f"Funding {f:+.4f}% ({payer})")
        if data.get("open_interest_usd"):
            oi = data["open_interest_usd"]
            oi_s = (f"${oi / 1e9:.2f}B" if oi >= 1e9 else
                    f"${oi / 1e6:.1f}M" if oi >= 1e6 else f"${oi:,.0f}")
            row.append(f"OI {oi_s}")
        if row:
            draw.text((PAD, y), "   ".join(row), fill=_WHITE, font=f_value)
            y += 24
        # Long/short ratio bar — the crypto-native "general rating" bar.
        if data.get("long_short_ratio"):
            r = float(data["long_short_ratio"])
            long_frac = r / (1 + r)
            bar_w = W - PAD * 2
            bar_y = y + 4
            split = int(bar_w * (1 - long_frac))  # shorts (red) on the left
            draw.rounded_rectangle([PAD, bar_y, PAD + bar_w, bar_y + 8],
                                   radius=4, fill=_DIM)
            if split > 6:
                draw.rounded_rectangle([PAD, bar_y, PAD + split, bar_y + 8],
                                       radius=4, fill=_RED)
            if bar_w - split > 6:
                draw.rounded_rectangle([PAD + split, bar_y, PAD + bar_w, bar_y + 8],
                                       radius=4, fill=_GREEN)
            y = bar_y + 14
            draw.text((PAD, y), f"Short {100 - long_frac * 100:.0f}%",
                      fill=_RED, font=f_small)
            lbl = f"Long {long_frac * 100:.0f}%"
            draw.text((W - PAD - draw.textlength(lbl, font=f_small), y),
                      lbl, fill=_GREEN, font=f_small)
            y += 20
        y += 2

    # ── Sentiment footer line ──
    if data.get("fear_greed"):
        regime = str(data.get("sentiment_regime", "") or "")
        draw.text((PAD, y),
                  f"Fear&Greed {data['fear_greed']:.0f}"
                  + (f" ({regime})" if regime else ""),
                  fill=_YELLOW, font=f_small)
        y += 18

    draw.text((PAD, y), "Same data the bot trades on — not investment advice.",
              fill=_DIM, font=f_small)
    y += 22

    # ── Bottom stripe + watermark, then crop ──
    H = y + 10
    draw.rectangle([0, H - 4, W, H], fill=_ACCENT_GOLD)
    wm = "RUNECLAW"
    wm_w = draw.textlength(wm, font=f_small)
    draw.text((W - PAD - wm_w, H - 22), wm, fill=_DIM, font=f_small)
    img = img.crop((0, 0, W, H))

    _buf = io.BytesIO()
    img.save(_buf, format="PNG", optimize=True)
    return _buf.getvalue()
