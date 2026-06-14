"""Premium multi-panel chart rendering for Telegram delivery.

Renders the SAME OHLCV the analyzer trades on (CCXT ``[ts, o, h, l, c, v]``)
into a 3-panel TradingView-style PNG: price + EMA(9/21), volume, RSI(14).

Design notes (why this differs from a naive script):
  * matplotlib/mplfinance are **blocking** and use pyplot global state. All
    rendering is pure-sync and isolated here so callers MUST offload it with
    ``asyncio.to_thread`` — never run it on the event loop (it would freeze the
    Telegram bot, dashboard and websocket feeds, like any blocking call).
  * Charting libs are **optional**. If they're not installed, every entry point
    degrades to ``None`` / a text message instead of crashing the pipeline.
  * Indicators are computed from the caller's candles so the chart always
    matches the data the bot actually decided on — no second, divergent math
    path (and no fragile ``pandas-ta``/``numpy`` version coupling).
  * Sending uses the project's existing python-telegram-bot session (async
    ``send_photo``), not a raw blocking ``requests.post`` to a hand-built URL.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# CCXT OHLCV column indices.
_TS, _OPEN, _HIGH, _LOW, _CLOSE, _VOL = 0, 1, 2, 3, 4, 5

# Visual themes. "dark" is the default — a TradingView-terminal look.
_THEMES = {
    "dark": {
        "figcolor": "#0b0e14",
        "facecolor": "#131722",
        "gridcolor": "#222631",
        "text": "#d1d4dc",
        "muted": "#787b86",
        "up": "#26a69a",
        "down": "#ef5350",
        "ema_fast": "#2962ff",
        "ema_slow": "#ff9800",
        "rsi": "#ab47bc",
        "entry": "#42a5f5",
        "stop": "#ef5350",
        "target": "#26a69a",
        "vwap": "#ffd54f",
        "choch": "#ff7043",
    },
    "light": {
        "figcolor": "#ffffff",
        "facecolor": "#ffffff",
        "gridcolor": "#e0e3eb",
        "text": "#131722",
        "muted": "#787b86",
        "up": "#089981",
        "down": "#f23645",
        "ema_fast": "#2962ff",
        "ema_slow": "#ff6d00",
        "rsi": "#7e57c2",
        "entry": "#2962ff",
        "stop": "#f23645",
        "target": "#089981",
        "vwap": "#f9a825",
        "choch": "#e64a19",
    },
}
_DEFAULT_THEME = "dark"

# Telegram limits.
_CAPTION_LIMIT = 1024
_MESSAGE_LIMIT = 4096

# Optional dependencies — resolved once at import.
try:
    import matplotlib
    matplotlib.use("Agg")  # headless backend; must be set before pyplot import
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D as _Line2D
    import mplfinance as mpf
    import pandas as pd
    _CHARTS_AVAILABLE = True
    _IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # noqa: BLE001 — any import failure ⇒ graceful text fallback
    _CHARTS_AVAILABLE = False
    _IMPORT_ERROR = exc

# mplfinance renders through pyplot's global state; serialize across worker
# threads so concurrent scans can't corrupt a shared figure.
_RENDER_LOCK = threading.Lock()


def charts_available() -> bool:
    """True if matplotlib + mplfinance + pandas are importable."""
    return _CHARTS_AVAILABLE


def _wilder_rsi(close, length: int = 14):
    """Standard Wilder RSI (the definition trading terminals use)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)  # neutral during warm-up so the panel renders cleanly


def compute_chart_indicators(candles, rsi_length: int = 14,
                             ema_fast: int = 9, ema_slow: int = 21):
    """Build an mplfinance-ready DataFrame from CCXT candles.

    candles: ``list[[ts_ms, open, high, low, close, volume]]``.
    Returns a DataFrame indexed by datetime with OHLCV + EMA_9/EMA_21/RSI.
    """
    if not _CHARTS_AVAILABLE:
        raise RuntimeError(f"charting libraries unavailable: {_IMPORT_ERROR}")
    rows = []
    for c in candles:
        c = list(c)
        if len(c) <= _VOL:                       # tolerate missing volume column
            c = c + [0.0] * (_VOL + 1 - len(c))
        rows.append(c[:6])
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.set_index("Date").drop(columns=["ts"])
    df["EMA_9"] = df["Close"].ewm(span=ema_fast, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=ema_slow, adjust=False).mean()
    df["RSI"] = _wilder_rsi(df["Close"], rsi_length)
    df["RSI_70"] = 70.0
    df["RSI_30"] = 30.0
    # VWAP (cumulative, anchored at window start) — same formula as the analyzer.
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].fillna(0.0)
    cum_v = vol.cumsum()
    df["VWAP"] = ((tp * vol).cumsum() / cum_v.replace(0.0, float("nan"))).fillna(df["Close"])
    return df


def render_chart_png(df, title: str = "RUNECLAW Setup", dpi: int = 160,
                     levels: Optional[dict] = None, theme: str = _DEFAULT_THEME,
                     subtitle: str = "", smc: bool = True) -> bytes:
    """Render a polished 3-panel chart to PNG bytes. BLOCKING — call via to_thread.

    Upgrades over a plain mpf chart:
      * dark "terminal" theme (or "light"), styled title + subtitle
      * EMA legend, gradient-free clean candles
      * RSI panel fixed to a true 0-100 scale with shaded OB/OS zones
      * entry/stop/target drawn AND labelled at the right edge
      * subtle RUNECLAW watermark

    levels (optional): {"entry","stop_loss","take_profit"} in price units.
    """
    if not _CHARTS_AVAILABLE:
        raise RuntimeError(f"charting libraries unavailable: {_IMPORT_ERROR}")
    t = _THEMES.get(theme, _THEMES[_DEFAULT_THEME])

    mc = mpf.make_marketcolors(
        up=t["up"], down=t["down"], edge="inherit",
        wick={"up": t["up"], "down": t["down"]},
        volume={"up": t["up"], "down": t["down"]},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mc,
        facecolor=t["facecolor"],
        figcolor=t["figcolor"],
        edgecolor=t["gridcolor"],
        gridcolor=t["gridcolor"],
        gridstyle="-",
        rc={
            "axes.labelcolor": t["muted"],
            "axes.edgecolor": t["gridcolor"],
            "xtick.color": t["muted"],
            "ytick.color": t["muted"],
            "text.color": t["text"],
            "axes.titlecolor": t["text"],
            "font.size": 10,
            "axes.linewidth": 0.8,
        },
    )

    add = [
        mpf.make_addplot(df["EMA_9"], color=t["ema_fast"], width=1.3, panel=0,
                         secondary_y=False, label="EMA 9"),
        mpf.make_addplot(df["EMA_21"], color=t["ema_slow"], width=1.3, panel=0,
                         secondary_y=False, label="EMA 21"),
        mpf.make_addplot(df["VWAP"], color=t["vwap"], width=1.3, panel=0,
                         secondary_y=False, linestyle=":", label="VWAP"),
        # RSI panel — all on the primary axis with a fixed 0-100 scale so the
        # 70/30 guides land in the right place (the old chart mis-scaled this).
        mpf.make_addplot(df["RSI"], color=t["rsi"], width=1.4, panel=2,
                         secondary_y=False, ylim=(0, 100), ylabel="RSI 14"),
        mpf.make_addplot(df["RSI_70"], color=t["down"], linestyle="--", width=0.8,
                         panel=2, secondary_y=False, ylim=(0, 100)),
        mpf.make_addplot(df["RSI_30"], color=t["up"], linestyle="--", width=0.8,
                         panel=2, secondary_y=False, ylim=(0, 100)),
    ]

    plot_kwargs = dict(
        type="candle", style=style, addplot=add,
        volume=True, panel_ratios=(6, 1.6, 2),
        ylabel="Price", returnfig=True, figratio=(16, 10), figscale=1.0,
        datetime_format="%m/%d %Hh", xrotation=15, tight_layout=False,
        scale_padding={"left": 0.4, "right": 2.2, "top": 1.5, "bottom": 0.7},
    )

    # Trade-level overlay lines (entry / stop / target) on the price panel.
    lvl_specs = []
    if levels:
        for key, color, tag in (("entry", t["entry"], "Entry"),
                                ("stop_loss", t["stop"], "SL"),
                                ("take_profit", t["target"], "TP")):
            val = levels.get(key)
            try:
                if val is not None and float(val) > 0:
                    lvl_specs.append((float(val), color, tag))
            except (TypeError, ValueError):
                continue
    if lvl_specs:
        plot_kwargs["hlines"] = dict(
            hlines=[v for v, _, _ in lvl_specs],
            colors=[c for _, c, _ in lvl_specs],
            linestyle="--", linewidths=1.1, alpha=0.95,
        )

    buf = io.BytesIO()
    with _RENDER_LOCK:
        fig, axlist = mpf.plot(df, **plot_kwargs)
        try:
            price_ax = axlist[0]
            rsi_ax = axlist[4] if len(axlist) > 4 else None

            # Title + subtitle anchored just above the price panel (robust under
            # bbox_inches="tight"; the old figure-space placement overlapped).
            price_ax.set_title(title, loc="left", color=t["text"],
                               fontsize=15, fontweight="bold", pad=30)
            if subtitle:
                price_ax.text(0.0, 1.022, subtitle, transform=price_ax.transAxes,
                              color=t["muted"], fontsize=9.5, ha="left", va="bottom")

            # Thin out crowded x-axis date labels (keep ~8, smaller font).
            for ax in axlist:
                ax.tick_params(axis="x", labelsize=8)
            bottom_ax = axlist[-2] if len(axlist) >= 2 else price_ax
            ticks = bottom_ax.get_xticks()
            if len(ticks) > 9:
                step = max(1, len(ticks) // 8)
                bottom_ax.set_xticks(ticks[::step])

            # EMA legend.
            handles = [
                _Line2D([0], [0], color=t["ema_fast"], lw=1.6, label="EMA 9"),
                _Line2D([0], [0], color=t["ema_slow"], lw=1.6, label="EMA 21"),
                _Line2D([0], [0], color=t["vwap"], lw=1.6, ls=":", label="VWAP"),
            ]
            leg = price_ax.legend(handles=handles, loc="upper left", fontsize=8.5,
                                  facecolor=t["facecolor"], edgecolor=t["gridcolor"],
                                  labelcolor=t["text"], framealpha=0.85, ncol=3,
                                  columnspacing=1.0, handlelength=1.4, borderpad=0.4)
            leg.get_frame().set_linewidth(0.6)

            # Right-edge price tags (levels + last price), de-cluttered so they
            # never overprint or run off the axis. A tag nudged off its true
            # level gets a thin leader line back to it.
            last_close = float(df["Close"].iloc[-1])
            last_up = len(df) < 2 or df["Close"].iloc[-1] >= df["Close"].iloc[-2]
            y0, y1 = price_ax.get_ylim()
            gap = (y1 - y0) * 0.052
            margin = (y1 - y0) * 0.02

            tags = [[val, f"{tag} {_fmt(val)}", color, 0.95, val] for val, color, tag in lvl_specs]
            # Skip the last-price pill if it would just duplicate a nearby level.
            if not any(abs(last_close - v) < gap for v, _, _ in lvl_specs):
                tags.append([last_close, _fmt(last_close),
                             t["up"] if last_up else t["down"], 0.62, last_close])

            tags.sort(key=lambda x: x[0])
            draw_ys = [tg[0] for tg in tags]
            for i in range(1, len(draw_ys)):                  # push up to keep gap
                if draw_ys[i] < draw_ys[i - 1] + gap:
                    draw_ys[i] = draw_ys[i - 1] + gap
            if draw_ys:                                       # shift down if overflow
                over = draw_ys[-1] - (y1 - margin)
                if over > 0:
                    draw_ys = [y - over for y in draw_ys]
                for i in range(len(draw_ys) - 1, 0, -1):      # then re-clamp bottom
                    if draw_ys[i] - draw_ys[i - 1] < gap:
                        draw_ys[i - 1] = draw_ys[i] - gap

            ytx = price_ax.get_yaxis_transform()
            for (true_y, text, fc, alpha, _), dy in zip(tags, draw_ys):
                if abs(dy - true_y) > gap * 0.3:              # leader line when nudged
                    price_ax.plot([1.0, 1.022], [true_y, dy], transform=ytx,
                                  color=fc, lw=0.7, alpha=0.7, clip_on=False, zorder=4)
                price_ax.text(
                    1.026, dy, f" {text} ", transform=ytx,
                    color="#ffffff", fontsize=8, fontweight="bold",
                    va="center", ha="left", clip_on=False,
                    bbox=dict(boxstyle="round,pad=0.22", fc=fc, ec="none", alpha=alpha),
                )

            # Risk/reward shaded zones: entry→target (reward) and entry→stop
            # (risk). Direction-agnostic — axhspan sorts its bounds.
            lvl_map = {tag: val for val, _, tag in lvl_specs}
            entry_v = lvl_map.get("Entry")
            if entry_v is not None and "TP" in lvl_map:
                price_ax.axhspan(entry_v, lvl_map["TP"], color=t["target"],
                                 alpha=0.07, zorder=0)
            if entry_v is not None and "SL" in lvl_map:
                price_ax.axhspan(entry_v, lvl_map["SL"], color=t["stop"],
                                 alpha=0.07, zorder=0)

            # EMA ribbon: faint fill between EMA9/EMA21, green when fast>slow.
            xs = list(range(len(df)))
            e9, e21 = df["EMA_9"].to_numpy(), df["EMA_21"].to_numpy()
            price_ax.fill_between(xs, e9, e21, where=(e9 >= e21), interpolate=True,
                                  color=t["up"], alpha=0.10, zorder=0)
            price_ax.fill_between(xs, e9, e21, where=(e9 < e21), interpolate=True,
                                  color=t["down"], alpha=0.10, zorder=0)

            # Swing high/low markers (local extrema over a ±k window).
            k = 3
            hi = df["High"]; lo = df["Low"]
            hi_mask = (hi == hi.rolling(2 * k + 1, center=True).max())
            lo_mask = (lo == lo.rolling(2 * k + 1, center=True).min())
            span = (hi.max() - lo.min()) or 1.0
            for i in [j for j, m in enumerate(hi_mask) if m]:
                price_ax.scatter(i, hi.iloc[i] + span * 0.02, marker="v",
                                 s=18, color=t["down"], alpha=0.55, zorder=3)
            for i in [j for j, m in enumerate(lo_mask) if m]:
                price_ax.scatter(i, lo.iloc[i] - span * 0.02, marker="^",
                                 s=18, color=t["up"], alpha=0.55, zorder=3)

            # BOS / CHoCH structure lines (reuse the engine's structure logic).
            n = len(df)
            for ln in _market_structure_lines(df):
                color = t.get(ln["color_key"], t["muted"])
                x0 = max(0, min(ln["start"], n - 1))
                price_ax.plot([x0, n - 1], [ln["level"], ln["level"]],
                              color=color, lw=1.3, ls=(0, (2, 1.5)),
                              alpha=0.9, zorder=2)
                price_ax.text(
                    x0 + (n - 1 - x0) * 0.5, ln["level"], ln["label"],
                    color="#ffffff", fontsize=7.5, fontweight="bold",
                    ha="center", va="bottom",
                    bbox=dict(boxstyle="round,pad=0.18", fc=color, ec="none", alpha=0.9),
                    zorder=4,
                )

            # ── Smart-money concepts: FVG, order blocks, sweeps, swing tags ──
            if smc:
                # Fair value gaps (shaded imbalance bands, label at left edge).
                for z in _fair_value_gaps(df):
                    col = t["up"] if z["bull"] else t["down"]
                    price_ax.fill_between([z["start"], n - 1], z["bottom"], z["top"],
                                          color=col, alpha=0.12, zorder=1)
                    if z["start"] < n * 0.85:  # skip label near the right pills
                        price_ax.text(z["start"], (z["top"] + z["bottom"]) / 2, "FVG",
                                      color=col, fontsize=6.5, fontweight="bold",
                                      va="center", ha="left", alpha=0.9, zorder=4)
                # Order blocks (bordered zone + OB tag).
                for ob in _order_blocks(df):
                    col = t["up"] if ob["bull"] else t["down"]
                    price_ax.fill_between([ob["start"], n - 1], ob["bottom"], ob["top"],
                                          color=col, alpha=0.16, edgecolor=col,
                                          linewidth=0.8, zorder=1)
                    price_ax.text(ob["start"], ob["bottom"], "OB", color="#ffffff",
                                  fontsize=6.5, fontweight="bold", va="top", ha="left",
                                  bbox=dict(boxstyle="round,pad=0.12", fc=col, ec="none",
                                            alpha=0.85), zorder=4)
                # Liquidity sweep marker on the swept level.
                sweep = _liquidity_sweep(df)
                if sweep and sweep.get("level"):
                    scol = t["up"] if sweep["bull"] else t["down"]
                    price_ax.axhline(sweep["level"], color=scol, lw=0.9, ls=(0, (1, 1)),
                                     alpha=0.7, zorder=2)
                    price_ax.text(n * 0.02, sweep["level"], "⇋ sweep", color="#ffffff",
                                  fontsize=6.5, fontweight="bold", va="center", ha="left",
                                  bbox=dict(boxstyle="round,pad=0.12", fc=scol, ec="none",
                                            alpha=0.85), zorder=4)
                # HH/HL/LH/LL swing structure tags.
                for sx, sy, lab, kind in _swing_labels(df):
                    off = span * 0.045 if kind == "high" else -span * 0.045
                    price_ax.text(sx, sy + off, lab, color=t["muted"], fontsize=6.5,
                                  fontweight="bold", ha="center",
                                  va="bottom" if kind == "high" else "top",
                                  alpha=0.85, zorder=4)

            # RSI overbought/oversold shaded zones + clean ticks.
            if rsi_ax is not None:
                rsi_ax.axhspan(70, 100, color=t["down"], alpha=0.08, zorder=0)
                rsi_ax.axhspan(0, 30, color=t["up"], alpha=0.08, zorder=0)
                rsi_ax.set_yticks([30, 50, 70])

            # Faint centered brand mark, behind all content (never conflicts).
            price_ax.text(0.5, 0.5, "RUNECLAW", transform=price_ax.transAxes,
                          color=t["text"], alpha=0.035, fontsize=40,
                          fontweight="bold", ha="center", va="center", zorder=0)

            # ── Cosmetic smoothing pass (premium finish) ──
            for ax in axlist:
                for sp_name in ("top", "right"):
                    if sp_name in ax.spines:
                        ax.spines[sp_name].set_visible(False)
                for sp_name in ("left", "bottom"):
                    if sp_name in ax.spines:
                        ax.spines[sp_name].set_color(t["gridcolor"])
                        ax.spines[sp_name].set_linewidth(0.8)
                # Soft, horizontal-only gridlines read cleaner than a full mesh.
                ax.grid(True, axis="y", color=t["gridcolor"], linewidth=0.5, alpha=0.45)
                ax.grid(False, axis="x")
                ax.tick_params(length=0)  # remove tick marks, keep labels
            # Dim the volume bars so the price action dominates the hierarchy.
            if len(axlist) > 2:
                for patch in axlist[2].patches:
                    patch.set_alpha(0.45)
            # RSI 50 midline.
            if rsi_ax is not None:
                rsi_ax.axhline(50, color=t["muted"], lw=0.6, alpha=0.35, zorder=0)

            fig.savefig(buf, format="png", dpi=dpi, facecolor=t["figcolor"],
                        bbox_inches="tight", pad_inches=0.25)
        finally:
            plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _market_structure_lines(df):
    """Compute BOS / CHoCH structure lines for the price panel.

    Reuses the bot's own swing + structure detection (multi_timeframe) so the
    chart shows the SAME structure the engine reasons about — not a parallel
    re-implementation. Returns a list of dicts:
        {"start": int, "level": float, "label": "BOS"|"CHoCH", "color_key": str}
    Empty list if structure is indeterminate or the module is unavailable.
    """
    try:
        from bot.core.multi_timeframe import _analyze_structure, _find_swings
    except Exception:  # noqa: BLE001 — structure overlay is optional
        return []
    try:
        highs = df["High"].to_numpy()
        lows = df["Low"].to_numpy()
        closes = df["Close"].to_numpy()
        if len(closes) < 12:
            return []
        swings = _find_swings(highs, lows, lookback=3)
        struct = _analyze_structure(highs, lows, closes, lookback=3)
        sh = swings.get("swing_highs", [])
        sl = swings.get("swing_lows", [])
        lines = []
        bias = struct.get("bias", 0.0)
        if struct.get("bos"):
            if bias >= 0 and sh:
                lines.append({"start": sh[-1][0], "level": sh[-1][1],
                              "label": "BOS", "color_key": "up"})
            elif sl:
                lines.append({"start": sl[-1][0], "level": sl[-1][1],
                              "label": "BOS", "color_key": "down"})
        if struct.get("choch"):
            if bias < 0 and sl:
                lines.append({"start": sl[-1][0], "level": sl[-1][1],
                              "label": "CHoCH", "color_key": "choch"})
            elif sh:
                lines.append({"start": sh[-1][0], "level": sh[-1][1],
                              "label": "CHoCH", "color_key": "choch"})
        return lines
    except Exception:  # noqa: BLE001
        return []


def _fair_value_gaps(df, max_zones: int = 2):
    """Unfilled 3-candle fair value gaps (price imbalances).

    Bullish FVG: low[i+1] > high[i-1] (gap left below). Bearish: high[i+1] <
    low[i-1]. Only gaps not yet traded back through are returned (most recent).
    """
    h = df["High"].to_numpy(); l = df["Low"].to_numpy()
    n = len(df)
    zones = []
    for i in range(1, n - 1):
        if l[i + 1] > h[i - 1]:                       # bullish imbalance
            top, bot = float(l[i + 1]), float(h[i - 1])
            if i + 2 >= n or float(l[i + 2:].min()) > bot:
                zones.append({"start": i, "top": top, "bottom": bot, "bull": True})
        elif h[i + 1] < l[i - 1]:                     # bearish imbalance
            top, bot = float(l[i - 1]), float(h[i + 1])
            if i + 2 >= n or float(h[i + 2:].max()) < top:
                zones.append({"start": i, "top": top, "bottom": bot, "bull": False})
    return zones[-max_zones:]


def _order_blocks(df, max_blocks: int = 2):
    """Order blocks: the last opposite candle before a displacement move.

    Bullish OB = last down candle before a strong up candle; bearish OB = last
    up candle before a strong down candle. Zone is that candle's full range.
    """
    o = df["Open"].to_numpy(); h = df["High"].to_numpy()
    l = df["Low"].to_numpy(); c = df["Close"].to_numpy()
    n = len(df)
    if n < 6:
        return []
    rng = (h - l)
    avg = float(rng[-50:].mean()) or float(rng.mean()) or 1.0
    obs = []
    for j in range(1, n):
        body = c[j] - o[j]
        if body > 1.5 * avg:                          # bullish displacement
            for k in range(j - 1, max(-1, j - 6), -1):
                if c[k] < o[k]:
                    obs.append({"start": k, "top": float(h[k]),
                                "bottom": float(l[k]), "bull": True}); break
        elif -body > 1.5 * avg:                        # bearish displacement
            for k in range(j - 1, max(-1, j - 6), -1):
                if c[k] > o[k]:
                    obs.append({"start": k, "top": float(h[k]),
                                "bottom": float(l[k]), "bull": False}); break
    # de-dup by start index, keep the most recent
    seen, uniq = set(), []
    for ob in reversed(obs):
        if ob["start"] in seen:
            continue
        seen.add(ob["start"]); uniq.append(ob)
    return list(reversed(uniq))[-max_blocks:]


def _liquidity_sweep(df):
    """Reuse the engine's liquidity-sweep detector. Returns the swept level +
    direction, or None."""
    try:
        from bot.core.chart_patterns import detect_liquidity_sweep
    except Exception:  # noqa: BLE001
        return None
    try:
        res = detect_liquidity_sweep(df["High"].to_numpy(), df["Low"].to_numpy(),
                                     df["Close"].to_numpy(), lookback=3)
        if not res:
            return None
        kl = res.get("key_levels", {})
        return {"level": kl.get("swept_level"), "bull": res.get("signal") == "bullish"}
    except Exception:  # noqa: BLE001
        return None


def _swing_labels(df, max_each: int = 2):
    """HH/HL/LH/LL tags for the most recent swing highs/lows (reuses the engine
    swing detector)."""
    try:
        from bot.core.multi_timeframe import _find_swings
    except Exception:  # noqa: BLE001
        return []
    try:
        sw = _find_swings(df["High"].to_numpy(), df["Low"].to_numpy(), lookback=3)
        sh, sl = sw.get("swing_highs", []), sw.get("swing_lows", [])
        out = []
        for idx in range(1, len(sh)):
            out.append((sh[idx][0], sh[idx][1],
                        "HH" if sh[idx][1] > sh[idx - 1][1] else "LH", "high"))
        for idx in range(1, len(sl)):
            out.append((sl[idx][0], sl[idx][1],
                        "HL" if sl[idx][1] > sl[idx - 1][1] else "LL", "low"))
        return out[-(max_each * 2):]
    except Exception:  # noqa: BLE001
        return []


def _fmt(price: float) -> str:
    """Compact price label: 64,250 / 3.85 / 0.00231."""
    p = abs(price)
    if p >= 1000:
        return f"{price:,.0f}"
    if p >= 1:
        return f"{price:,.2f}"
    if p >= 0.01:
        return f"{price:.4f}"
    return f"{price:.6f}"


def build_chart_png(candles, title: str = "RUNECLAW Setup",
                    min_bars: int = 25, dpi: int = 160,
                    levels: Optional[dict] = None,
                    theme: str = _DEFAULT_THEME, subtitle: str = "",
                    smc: bool = True) -> Optional[bytes]:
    """Compute indicators + render. Returns PNG bytes, or None on any problem.

    BLOCKING — invoke with ``await asyncio.to_thread(build_chart_png, ...)``.
    Never raises: missing libs, too few bars, or a render error all return None
    so the caller can fall back to a plain text signal.
    """
    if not _CHARTS_AVAILABLE:
        logger.info("charts unavailable (%s) — falling back to text", _IMPORT_ERROR)
        return None
    try:
        if not candles or len(candles) < min_bars:
            logger.debug("not enough candles for chart: %d (< %d)",
                         len(candles or []), min_bars)
            return None
        df = compute_chart_indicators(candles)
        return render_chart_png(df, title=title, dpi=dpi, levels=levels,
                                theme=theme, subtitle=subtitle, smc=smc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart render failed: %s", exc)
        return None


def _levels_from_idea(idea) -> Optional[dict]:
    """Extract entry/stop/target from a TradeIdea-like object, if present."""
    if idea is None:
        return None
    try:
        return {
            "entry": float(getattr(idea, "entry_price", 0) or 0),
            "stop_loss": float(getattr(idea, "stop_loss", 0) or 0),
            "take_profit": float(getattr(idea, "take_profit", 0) or 0),
        }
    except (TypeError, ValueError):
        return None


async def send_chart(bot, chat_id, candles, caption: str,
                     title: str = "RUNECLAW Setup", dpi: int = 160,
                     levels: Optional[dict] = None,
                     theme: str = _DEFAULT_THEME, subtitle: str = "") -> bool:
    """Render off-thread and deliver via the PTB bot. Returns True iff a photo
    was sent. Falls back to a text message when charting is unavailable.

    The render is offloaded with asyncio.to_thread so the event loop is never
    blocked. Both photo and text paths retry with parse_mode=None on an HTML
    parse error (mirrors TelegramHandler._send).
    """
    png = await asyncio.to_thread(
        build_chart_png, candles, title, 25, dpi, levels, theme, subtitle)
    caption = caption or ""

    if png is None:
        if caption:
            await _send_text_fallback(bot, chat_id, caption)
        return False

    cap = caption[:_CAPTION_LIMIT]
    photo = io.BytesIO(png)
    photo.name = "chart.png"
    try:
        await bot.send_photo(chat_id=int(chat_id), photo=photo,
                             caption=cap, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001 — likely HTML parse error; retry plain
        logger.debug("send_photo HTML failed (%s) — retrying plain caption", exc)
        photo.seek(0)
        try:
            await bot.send_photo(chat_id=int(chat_id), photo=photo,
                                 caption=_strip_html(cap), parse_mode=None)
        except Exception as exc2:  # noqa: BLE001
            logger.error("send_photo failed entirely: %s", exc2)
            await _send_text_fallback(bot, chat_id, caption)
            return False
    return True


async def _send_text_fallback(bot, chat_id, text: str) -> None:
    try:
        await bot.send_message(chat_id=int(chat_id),
                               text=text[:_MESSAGE_LIMIT], parse_mode="HTML")
    except Exception:  # noqa: BLE001
        await bot.send_message(chat_id=int(chat_id),
                               text=_strip_html(text)[:_MESSAGE_LIMIT], parse_mode=None)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


async def send_idea_chart(bot, chat_id, candles, idea,
                          extra_caption: str = "", dpi: int = 160,
                          theme: str = _DEFAULT_THEME) -> bool:
    """Send a setup chart for a TradeIdea with entry/SL/TP lines drawn on it.

    Convenience wrapper over send_chart: derives the title, caption, and the
    trade-level overlays from the idea. Used by both the on-demand analysis
    cards and the proactive new-signal alerts.
    """
    try:
        asset = getattr(idea, "asset", "") or ""
        pair = asset.replace("/", "")
        direction = getattr(getattr(idea, "direction", None), "value", "") or ""
        import html as _html
        caption = f"<b>{_html.escape(pair)}</b> {_html.escape(direction)} — price · EMA9/21 · RSI(14)"
        if extra_caption:
            caption += f"\n{extra_caption}"
        # Subtitle line baked into the image: direction · confidence · R:R.
        bits = []
        if direction:
            bits.append(direction)
        conf = getattr(idea, "confidence", None)
        if isinstance(conf, (int, float)):
            bits.append(f"conf {conf:.0%}")
        rr = getattr(idea, "risk_reward_ratio", None)
        if isinstance(rr, (int, float)) and rr > 0:
            bits.append(f"R:R 1:{rr:.1f}")
        subtitle = "   ".join(bits)
        return await send_chart(
            bot, chat_id, candles, caption=caption,
            title=f"{pair} {direction}".strip(),
            dpi=dpi, levels=_levels_from_idea(idea), subtitle=subtitle, theme=theme,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("send_idea_chart skipped: %s", exc)
        return False


def _idea_meta(idea):
    """Shared (pair, direction, subtitle, levels) for an idea's chart(s)."""
    asset = getattr(idea, "asset", "") or ""
    pair = asset.replace("/", "")
    direction = getattr(getattr(idea, "direction", None), "value", "") or ""
    bits = []
    if direction:
        bits.append(direction)
    conf = getattr(idea, "confidence", None)
    if isinstance(conf, (int, float)):
        bits.append(f"conf {conf:.0%}")
    rr = getattr(idea, "risk_reward_ratio", None)
    if isinstance(rr, (int, float)) and rr > 0:
        bits.append(f"R:R 1:{rr:.1f}")
    return pair, direction, "   ".join(bits), _levels_from_idea(idea)


async def send_idea_charts_multi(bot, chat_id, candles_by_tf: dict, idea,
                                 dpi: int = 160, theme: str = _DEFAULT_THEME) -> bool:
    """Render the setup across several timeframes and deliver them as one album.

    candles_by_tf: ordered {timeframe_label: candles}, e.g. {"4h": [...], "1h": [...]}.
    Higher timeframes give context, lower ones the entry — sending them as a
    Telegram media group keeps it one tidy message. Falls back to a single
    photo (or text) when only one timeframe renders or charts are unavailable.

    Each render is offloaded to a thread so the event loop is never blocked.
    """
    import html as _html
    try:
        pair, direction, subtitle, levels = _idea_meta(idea)

        rendered = []  # (tf, png)
        for tf, candles in (candles_by_tf or {}).items():
            png = await asyncio.to_thread(
                build_chart_png, candles, f"{pair} {direction} · {tf}".strip(),
                25, dpi, levels, theme, f"{subtitle}   ·   {tf}".strip(" ·"))
            if png:
                rendered.append((tf, png))

        if not rendered:
            await _send_text_fallback(
                bot, chat_id,
                f"<b>{_html.escape(pair)}</b> {_html.escape(direction)} — "
                f"price · EMA9/21 · RSI(14)")
            return False

        # Single timeframe → a normal photo (albums need 2+).
        if len(rendered) == 1:
            tf, png = rendered[0]
            return await _send_single_photo(
                bot, chat_id, png,
                f"<b>{_html.escape(pair)}</b> {_html.escape(direction)} · {tf}")

        # 2+ timeframes → a media group (album), caption on the first item.
        try:
            from telegram import InputMediaPhoto
        except Exception:  # noqa: BLE001 — no telegram lib: send the first photo
            tf, png = rendered[0]
            return await _send_single_photo(
                bot, chat_id, png,
                f"<b>{_html.escape(pair)}</b> {_html.escape(direction)} · {tf}")

        caption = f"<b>{_html.escape(pair)}</b> {_html.escape(direction)} — {_html.escape(subtitle)}"
        media = []
        for i, (tf, png) in enumerate(rendered):
            buf = io.BytesIO(png); buf.name = f"chart_{tf}.png"
            if i == 0:
                media.append(InputMediaPhoto(buf, caption=caption[:_CAPTION_LIMIT],
                                             parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(buf))
        try:
            await bot.send_media_group(chat_id=int(chat_id), media=media)
        except Exception as exc:  # noqa: BLE001 — retry once with plain caption
            logger.debug("send_media_group HTML failed (%s) — retrying plain", exc)
            media2 = []
            for i, (tf, png) in enumerate(rendered):
                buf = io.BytesIO(png); buf.name = f"chart_{tf}.png"
                media2.append(InputMediaPhoto(buf, caption=_strip_html(caption)[:_CAPTION_LIMIT])
                              if i == 0 else InputMediaPhoto(buf))
            await bot.send_media_group(chat_id=int(chat_id), media=media2)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("send_idea_charts_multi skipped: %s", exc)
        return False


async def _send_single_photo(bot, chat_id, png: bytes, caption: str) -> bool:
    """Send one PNG with HTML caption + plain-caption fallback."""
    buf = io.BytesIO(png); buf.name = "chart.png"
    try:
        await bot.send_photo(chat_id=int(chat_id), photo=buf,
                             caption=caption[:_CAPTION_LIMIT], parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        logger.debug("send_photo HTML failed (%s) — retrying plain", exc)
        buf.seek(0)
        await bot.send_photo(chat_id=int(chat_id), photo=buf,
                             caption=_strip_html(caption)[:_CAPTION_LIMIT], parse_mode=None)
    return True
