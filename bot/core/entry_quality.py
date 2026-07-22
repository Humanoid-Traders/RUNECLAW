"""QC-2b — order-book entry-quality checks.

Pure, side-effect-free helpers the live executor calls just before it places an
entry. The star is ``book_wall_verdict``: it looks at the L2 order book and
flags entries where a dominant opposing wall sits in the path between the entry
and the take-profit — a level so much larger than its neighbours that price is
likely to stall or reject there, giving away the setup's edge.

Design notes:
  * PURE. No I/O, no config reads, no clock. The executor fetches the book and
    passes it in; every threshold is an explicit argument. This is what makes
    it unit-testable and safe to reason about on a money path.
  * FAIL-OPEN. A missing / malformed / one-sided book returns "no flag" — the
    executor must never block a trade because the book read was degraded. The
    caller layers its own try/except on top so a fetch error is a no-op too.
  * OBSERVE-FIRST. The executor defaults this OFF, then WARN (log only), and
    only enforces (block) when explicitly switched on — the house rule for any
    new gate on the live path.
"""

from __future__ import annotations

from typing import Optional


def _norm_levels(levels) -> list[tuple[float, float]]:
    """Coerce a ccxt-style [[price, size], ...] ladder into clean float tuples,
    dropping anything non-numeric or non-positive. Never raises."""
    out: list[tuple[float, float]] = []
    if not levels:
        return out
    for lvl in levels:
        try:
            price = float(lvl[0])
            size = float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return out


def book_wall_verdict(
    direction: str,
    entry: float,
    take_profit: Optional[float],
    bids,
    asks,
    *,
    band_pct: float = 1.5,
    wall_ratio: float = 4.0,
    imbalance_ratio: float = 3.0,
) -> dict:
    """Judge whether the book has an opposing wall in the entry→TP path.

    Args:
        direction: "LONG" or "SHORT" (case-insensitive).
        entry: entry/reference price.
        take_profit: TP price — bounds the path we care about. None/invalid
            just widens the band to ``band_pct`` alone.
        bids / asks: ccxt-style ladders [[price, size], ...].
        band_pct: how far above/below entry (percent) to inspect the path.
        wall_ratio: a single level counts as a "wall" when its size is at least
            this multiple of the average level size in the path band.
        imbalance_ratio: the path-side resting liquidity counts as an adverse
            "shelf" when it is at least this multiple of the entry-side
            liquidity in the same band.

    Returns:
        {"flag": bool, "reason": str, "metrics": {...}} — flag True means an
        obstruction was found. Always returns a dict; never raises.
    """
    d = str(direction or "").strip().upper()
    metrics: dict = {}
    try:
        entry = float(entry)
    except (TypeError, ValueError):
        return {"flag": False, "reason": "no-entry", "metrics": metrics}
    if entry <= 0 or d not in ("LONG", "SHORT"):
        return {"flag": False, "reason": "bad-input", "metrics": metrics}

    bids_n = _norm_levels(bids)
    asks_n = _norm_levels(asks)
    if not bids_n or not asks_n:
        return {"flag": False, "reason": "book-unavailable", "metrics": metrics}

    band = entry * (max(0.0, band_pct) / 100.0)
    if band <= 0:
        return {"flag": False, "reason": "no-band", "metrics": metrics}

    # The path side is the direction price must travel to reach TP:
    #   LONG  → up through the asks;  SHORT → down through the bids.
    if d == "LONG":
        path_levels = asks_n
        supp_levels = bids_n
        lo, hi = entry, entry + band
    else:
        path_levels = bids_n
        supp_levels = asks_n
        lo, hi = entry - band, entry

    # A valid TP inside the band tightens the path window to entry→TP.
    try:
        tp = float(take_profit) if take_profit is not None else None
    except (TypeError, ValueError):
        tp = None
    if tp is not None and tp > 0:
        if d == "LONG" and tp < hi:
            hi = max(entry, tp)
        elif d == "SHORT" and tp > lo:
            lo = min(entry, tp)

    path_band = [(p, s) for (p, s) in path_levels if lo <= p <= hi]
    supp_band = [(p, s) for (p, s) in supp_levels
                 if (entry - band) <= p <= (entry + band)]

    path_liq = sum(s for _, s in path_band)
    supp_liq = sum(s for _, s in supp_band)
    metrics["path_liq"] = round(path_liq, 6)
    metrics["supp_liq"] = round(supp_liq, 6)
    metrics["path_levels"] = len(path_band)

    if not path_band:
        return {"flag": False, "reason": "clear-path", "metrics": metrics}

    # 1) Concentrated wall: one level dwarfs the band's average level size.
    avg = path_liq / len(path_band)
    biggest_p, biggest_s = max(path_band, key=lambda x: x[1])
    wall_mult = biggest_s / avg if avg > 0 else 0.0
    metrics["wall_mult"] = round(wall_mult, 3)
    metrics["wall_price"] = biggest_p
    if len(path_band) >= 3 and wall_mult >= wall_ratio:
        return {"flag": True,
                "reason": f"wall {wall_mult:.1f}x avg at {biggest_p:g}",
                "metrics": metrics}

    # 2) Adverse shelf: the path side is far heavier than the entry side.
    imb = path_liq / supp_liq if supp_liq > 0 else float("inf")
    metrics["imbalance"] = round(imb, 3) if imb != float("inf") else None
    if imb >= imbalance_ratio:
        return {"flag": True,
                "reason": f"opposing shelf {imb:.1f}x support",
                "metrics": metrics}

    return {"flag": False, "reason": "ok", "metrics": metrics}
