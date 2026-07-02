"""
RUNECLAW smart-money-concept primitives — fair value gaps, equal-level
liquidity pools, premium/discount positioning.

Pure functions over CLOSED bars; each returns confluence-vote material the
analyzer wires in (flags default ON). These fill the audit's "missing
high-value structure" list:

- Fair value gaps (FVG): a 3-candle displacement leaves an untraded window
  (bullish: low[i] > high[i-2]). Unfilled gaps act as magnets/support.
- Equal highs/lows: >=2 swing extremes within an ATR-scaled tolerance form
  a liquidity pool (clustered stops) — price is drawn to sweep it. The
  strict > / < fractal in the MTF module actively ERASES equal extremes,
  so they are detected here with a tolerance instead.
- Premium/discount: position of price within the recent dealing range.
  Buying deep premium / selling deep discount is chasing; the vote leans
  toward value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class FVG:
    kind: str        # "bullish" | "bearish"
    top: float
    bottom: float
    bar_index: int   # index of the middle (displacement) candle
    filled: bool


def find_fvgs(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
              max_age: int = 30) -> list[FVG]:
    """Detect 3-candle fair value gaps in the last ``max_age`` bars and mark
    whether later price action has filled them (traded fully through)."""
    n = len(highs)
    out: list[FVG] = []
    start = max(2, n - max_age)
    for i in range(start, n):
        # Bullish FVG: candle i's low gaps above candle i-2's high.
        if lows[i] > highs[i - 2]:
            top, bottom = float(lows[i]), float(highs[i - 2])
            filled = bool(np.min(lows[i + 1:]) <= bottom) if i + 1 < n else False
            out.append(FVG("bullish", top, bottom, i - 1, filled))
        # Bearish FVG: candle i's high gaps below candle i-2's low.
        if highs[i] < lows[i - 2]:
            top, bottom = float(lows[i - 2]), float(highs[i])
            filled = bool(np.max(highs[i + 1:]) >= top) if i + 1 < n else False
            out.append(FVG("bearish", top, bottom, i - 1, filled))
    return out


def fvg_vote(fvgs: list[FVG], price: float, atr: float) -> tuple[float, float]:
    """(vote, weight): an UNFILLED bullish FVG within 1 ATR below price is
    support (+), an unfilled bearish FVG within 1 ATR above is resistance (−).
    Nearest unfilled gap wins; nothing near → (0, 0)."""
    if atr <= 0 or price <= 0:
        return 0.0, 0.0
    best: Optional[tuple[float, float]] = None  # (distance, vote)
    for g in fvgs:
        if g.filled:
            continue
        if g.kind == "bullish" and g.top <= price:
            dist = price - g.top
            if dist <= atr and (best is None or dist < best[0]):
                best = (dist, 1.0)
        elif g.kind == "bearish" and g.bottom >= price:
            dist = g.bottom - price
            if dist <= atr and (best is None or dist < best[0]):
                best = (dist, -1.0)
    if best is None:
        return 0.0, 0.0
    return best[1], 0.6


def equal_level_pools(highs: np.ndarray, lows: np.ndarray, atr: float,
                      fractal: int = 3, tol_atr: float = 0.15) -> dict:
    """Detect equal-highs / equal-lows liquidity pools: >=2 swing extremes
    within ``tol_atr``×ATR of each other. Returns {"eqh": [...], "eql": [...]}
    with clustered pool prices."""
    n = len(highs)
    if atr <= 0 or n < 2 * fractal + 3:
        return {"eqh": [], "eql": []}
    sh, sl = [], []
    for i in range(fractal, n - fractal):
        if all(highs[i] >= highs[i - j] for j in range(1, fractal + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, fractal + 1)):
            sh.append(float(highs[i]))
        if all(lows[i] <= lows[i - j] for j in range(1, fractal + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, fractal + 1)):
            sl.append(float(lows[i]))
    tol = tol_atr * atr

    def _pools(vals: list[float]) -> list[float]:
        vals = sorted(vals)
        pools, run = [], [vals[0]] if vals else []
        for v in vals[1:]:
            if v - run[-1] <= tol:
                run.append(v)
            else:
                if len(run) >= 2:
                    pools.append(float(np.mean(run)))
                run = [v]
        if len(run) >= 2:
            pools.append(float(np.mean(run)))
        return pools

    return {"eqh": _pools(sh), "eql": _pools(sl)}


def premium_discount(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                     window: int = 100) -> Optional[float]:
    """Position of the last close within the recent dealing range: 0 = range
    low (deep discount), 1 = range high (deep premium). None if degenerate."""
    h = float(np.max(highs[-window:]))
    l = float(np.min(lows[-window:]))  # noqa: E741
    if h <= l:
        return None
    return float((closes[-1] - l) / (h - l))
