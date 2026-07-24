"""
RUNECLAW level-aware SL/TP — snap stops and targets to real S/R structure.

The audit's highest-expected-value upgrade: SL/TP were pure ATR multiples,
blind to every level the bot itself detects. A LONG stop could sit one tick
above a triple-tested wick low (the exact liquidity-sweep target the bot's
own sweep module models) and a TP routinely parked just past a resistance
and missed by inches.

Pure functions, no I/O:

- ``gather_levels``    — collect candidate S/R from swing wicks, the volume
                         profile (POC/VAH/VAL), prior-day high/low and round
                         numbers; cluster near-duplicates (ATR-scaled) and
                         score by touch count.
- ``snap_sl_tp``       — SL: tighten (never widen) to just beyond the
                         nearest scored support/resistance between the ATR
                         stop and the entry. TP: clip to just inside a
                         scored opposing level that sits at 50–105% of the
                         ATR target distance (a target just PAST a wall is
                         a target that never fills).

Gated by LEVEL_AWARE_SLTP_ENABLED (default ON). The leverage margin-risk
cap runs AFTER this and only ever tightens further, so the snap can never
raise dollar risk beyond the ATR baseline it started from.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Level:
    price: float
    kind: str        # "swing" | "poc" | "vah" | "val" | "pdh" | "pdl" | "round"
    touches: int = 1
    score: float = 1.0


_KIND_BASE_SCORE = {
    "swing": 1.0, "poc": 1.6, "vah": 1.2, "val": 1.2,
    "pdh": 1.4, "pdl": 1.4, "round": 0.8,
    # The bot's own derived objectives (audit upgrade): fib retracements of
    # the dominant leg and Elliott wave-projected targets — the levels the
    # strategy itself reasons about now also shape where its stops hide and
    # its targets land.
    "fib": 1.1, "ew_target": 1.2,
    # Pattern price objectives (fib extensions, harmonic D PRZ, Wyckoff phase
    # extremes, necklines) — fed only under PATTERN_TARGET_LEVELS_ENABLED.
    "pattern_target": 1.1,
}


def _round_levels_near(price: float) -> list[float]:
    """The two psychologically 'round' prices bracketing price at a
    magnitude-appropriate step (e.g. 500s on a 5-figure price)."""
    if price <= 0 or not math.isfinite(price):
        return []
    step = 10 ** (math.floor(math.log10(price)) - 1)
    if price / step > 50:
        step *= 5
    below = math.floor(price / step) * step
    return [float(below), float(below + step)]


def gather_levels(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    atr: float,
    times: Optional[np.ndarray] = None,
    vp: Optional[dict] = None,
    fractal: int = 3,
    extra_levels: Optional[list] = None,
) -> list[Level]:
    """Collect + cluster + score candidate S/R levels from CLOSED bars.

    ``extra_levels`` — optional ``[(price, kind), ...]`` of externally derived
    levels (fib retracements, Elliott targets); unknown kinds score as swing.
    """
    if len(closes) < 2 * fractal + 3 or atr <= 0:
        return []
    price = float(closes[-1])
    raw: list[Level] = []

    # Swing WICK highs/lows (the levels traders defend), 3-bar fractal.
    n = len(highs)
    for i in range(fractal, n - fractal):
        if all(highs[i] >= highs[i - j] for j in range(1, fractal + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, fractal + 1)):
            raw.append(Level(float(highs[i]), "swing"))
        if all(lows[i] <= lows[i - j] for j in range(1, fractal + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, fractal + 1)):
            raw.append(Level(float(lows[i]), "swing"))

    # Volume profile value-area edges + POC.
    if vp:
        for key in ("poc", "vah", "val"):
            v = vp.get(key)
            if v and v > 0:
                raw.append(Level(float(v), key))

    # Prior-day high/low (UTC day boundaries from bar open times, ms).
    if times is not None and len(times) == len(highs):
        try:
            day_ms = 86_400_000
            cur_day = int(times[-1] // day_ms)
            mask = (times // day_ms) == (cur_day - 1)
            if mask.any():
                raw.append(Level(float(np.max(highs[mask])), "pdh"))
                raw.append(Level(float(np.min(lows[mask])), "pdl"))
        except Exception:
            pass

    # Round numbers bracketing the current price.
    for rp in _round_levels_near(price):
        raw.append(Level(rp, "round"))

    # Externally derived levels (fib / Elliott targets from the analyzer).
    for item in (extra_levels or []):
        try:
            lp, kind = float(item[0]), str(item[1])
            if lp > 0 and math.isfinite(lp):
                raw.append(Level(lp, kind if kind in _KIND_BASE_SCORE else "swing"))
        except Exception:
            continue

    if not raw:
        return []

    # Cluster within 0.25 ATR: merge into touch-weighted means.
    raw.sort(key=lambda level: level.price)
    tol = 0.25 * atr
    clustered: list[Level] = []
    for lv in raw:
        if clustered and abs(lv.price - clustered[-1].price) <= tol:
            c = clustered[-1]
            total = c.touches + 1
            c.price = (c.price * c.touches + lv.price) / total
            c.touches = total
            c.score += _KIND_BASE_SCORE.get(lv.kind, 1.0)
            if _KIND_BASE_SCORE.get(lv.kind, 1.0) > _KIND_BASE_SCORE.get(c.kind, 1.0):
                c.kind = lv.kind
        else:
            clustered.append(Level(lv.price, lv.kind, 1,
                                   _KIND_BASE_SCORE.get(lv.kind, 1.0)))

    # Count additional touches: bars whose wick came within 0.15 ATR.
    wick_tol = 0.15 * atr
    for c in clustered:
        c.touches = int(np.sum((np.abs(highs - c.price) <= wick_tol)
                               | (np.abs(lows - c.price) <= wick_tol)))
        c.score += 0.25 * max(0, c.touches - 1)
    return clustered


def snap_sl_tp(
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    levels: list[Level],
    atr: float,
    min_score: float = 1.5,
) -> tuple[float, float, str]:
    """Snap ATR-based SL/TP to scored structure. Returns (sl, tp, note).

    SL: tighten-only. The strongest level strictly between the ATR stop and
    the entry (with room for a 0.25-ATR buffer) becomes the new stop anchor:
    stop just BEYOND the level, so a sweep of the level doesn't take the
    position out. Never widens — sizing and the margin cap assume at most
    the ATR distance.

    TP: if a scored opposing level sits at 50–105% of the target distance,
    clip the target just INSIDE it (0.1 ATR) — a fill beats a near-miss.
    """
    if atr <= 0 or not levels or entry <= 0:
        return stop_loss, take_profit, ""
    is_long = direction.upper() == "LONG"
    buf = 0.25 * atr
    note = []

    if is_long:
        candidates = [c for c in levels
                      if c.score >= min_score
                      and stop_loss + buf < c.price - buf
                      and c.price < entry - 0.1 * atr]
        if candidates:
            anchor = max(candidates, key=lambda c: c.price)  # nearest below entry
            new_sl = anchor.price - buf
            if new_sl > stop_loss:
                stop_loss = new_sl
                note.append(f"SL snapped under {anchor.kind} {anchor.price:.6g}")
        tp_dist = take_profit - entry
        walls = [c for c in levels
                 if c.score >= min_score and c.price > entry
                 and 0.5 * tp_dist <= (c.price - entry) <= 1.05 * tp_dist]
        if walls:
            wall = min(walls, key=lambda c: c.price)
            new_tp = wall.price - 0.1 * atr
            if entry < new_tp < take_profit:
                take_profit = new_tp
                note.append(f"TP clipped inside {wall.kind} {wall.price:.6g}")
    else:
        candidates = [c for c in levels
                      if c.score >= min_score
                      and c.price + buf < stop_loss - buf
                      and c.price > entry + 0.1 * atr]
        if candidates:
            anchor = min(candidates, key=lambda c: c.price)  # nearest above entry
            new_sl = anchor.price + buf
            if new_sl < stop_loss:
                stop_loss = new_sl
                note.append(f"SL snapped over {anchor.kind} {anchor.price:.6g}")
        tp_dist = entry - take_profit
        walls = [c for c in levels
                 if c.score >= min_score and c.price < entry
                 and 0.5 * tp_dist <= (entry - c.price) <= 1.05 * tp_dist]
        if walls:
            wall = max(walls, key=lambda c: c.price)
            new_tp = wall.price + 0.1 * atr
            if take_profit < new_tp < entry:
                take_profit = new_tp
                note.append(f"TP clipped inside {wall.kind} {wall.price:.6g}")

    return stop_loss, take_profit, "; ".join(note)
