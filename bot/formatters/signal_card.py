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
    symbol = data.get("symbol", "???").replace("/USDT", "").replace("USDT", "")
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
    }
    return render_signal_card(data)
