"""Systemic Risk Sentinel — the concentration/crowding layer of Guardian.

    The AI proposes. Deterministic controls authorize. The sentinel warns.

The Digital Twin asks "what if the market moves against the book?"; the Sentinel
asks the complementary, *static* question — **is the book structurally crowded
right now**, such that a single move would hit everything at once?

A truthful scope note: RUNECLAW observes its *own* positions and market data, not
the wider agent population, so this is not a cross-agent systemic monitor — it
cannot see what other agents hold. What it *can* see, and what actually causes a
one-move wipeout of a single book, is **intra-book crowding**:

* **Correlated concentration** — too much gross exposure in one correlation group
  (the risk engine's BTC / ETH / MEME / L2 … buckets), so a sector move dominates.
* **Directional crowding** — the book is heavily net-long or net-short, so a
  single broad move has nothing to offset it.
* **Same-group, same-direction clusters** — many positions that rise and fall
  together (the in-book analogue of "shared liquidation levels").
* **Shared liquidation zone** — same-direction positions whose isolated-margin
  liquidation prices sit within a tight adverse-move band, so one shock cascades
  them together.

``analyze(positions)`` is a **pure, deterministic** function of the book snapshot
(no engine, no exchange, no clock, no network): it returns the concentration
metrics, the triggered concerns, and a rolled-up risk level. Read-only telemetry
— it warns, it never blocks. Pairs with the Digital Twin's ``liquidation_move_frac``
for the shared-liquidation-zone math (one canonical formula, no drift).
"""

from __future__ import annotations

from typing import Any, Optional

from bot.guardian.digital_twin import liquidation_move_frac

SENTINEL_VERSION = 1

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# ── Thresholds (percent of gross notional unless noted) ───────────────
_CONCENTRATION_HIGH = 50.0     # one correlation group ≥ this share of the book
_CONCENTRATION_MEDIUM = 35.0
_CROWDING_HIGH = 0.85          # net directional bias |long−short|/gross
_CROWDING_MEDIUM = 0.60
_CLUSTER_HIGH = 4              # positions in one group AND one direction
_CLUSTER_MEDIUM = 3
_LIQ_ZONE_BAND = 5.0           # adverse-move % band width for "same zone"
_LIQ_ZONE_HIGH = 3            # same-direction positions clustered in one zone


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _notional(pos: dict) -> float:
    """Position notional in USD: entry × qty, falling back to margin × leverage."""
    entry, qty = _num(pos.get("entry")), _num(pos.get("qty"))
    if entry is not None and qty is not None:
        return abs(entry * qty)
    cost, lev = _num(pos.get("cost_usd")), _num(pos.get("leverage"))
    if cost is not None:
        return abs(cost * (lev or 1.0))
    return 0.0


def _direction(pos: dict) -> str:
    return "SHORT" if str(pos.get("direction", "LONG")).upper().startswith("S") else "LONG"


def _rollup(*severities: str) -> str:
    worst = "none"
    for s in severities:
        if RISK_ORDER.get(s, 0) > RISK_ORDER[worst]:
            worst = s
    return worst


def analyze(positions: list[dict]) -> dict:
    """Assess intra-book crowding/concentration. Pure; never raises.

    Returns::

        {
          "version": int,
          "position_count": int,
          "gross_notional_usd": float,
          "net_direction": "long" | "short" | "balanced",
          "net_bias": float,                 # 0..1, |long−short| / gross
          "top_group": {"group","share_pct"},
          "concerns": [ {"kind","severity","detail"}, ... ],
          "risk": "none" | "low" | "medium" | "high",
        }
    """
    base = {"version": SENTINEL_VERSION, "position_count": 0,
            "gross_notional_usd": 0.0, "net_direction": "balanced",
            "net_bias": 0.0, "top_group": {"group": "", "share_pct": 0.0},
            "concerns": [], "risk": "none"}
    try:
        rows = [p for p in (positions or []) if _notional(p) > 0]
        if not rows:
            return base
        gross = sum(_notional(p) for p in rows)
        if gross <= 0:
            return base
        long_notional = sum(_notional(p) for p in rows if _direction(p) == "LONG")
        short_notional = gross - long_notional

        concerns: list[dict] = []

        # 1) Correlated concentration — largest group's share of gross.
        by_group: dict[str, float] = {}
        for p in rows:
            by_group[str(p.get("group") or "*")] = by_group.get(str(p.get("group") or "*"), 0.0) + _notional(p)
        top_group, top_notional = max(by_group.items(), key=lambda kv: kv[1])
        top_share = round(top_notional / gross * 100, 1)
        conc_sev = "high" if top_share >= _CONCENTRATION_HIGH else \
            "medium" if top_share >= _CONCENTRATION_MEDIUM else "none"
        if conc_sev != "none" and top_group not in ("", "*"):
            concerns.append({"kind": "correlated_concentration", "severity": conc_sev,
                             "detail": f"{top_share}% of the book is in {top_group}"})

        # 2) Directional crowding — net one-sidedness.
        net_bias = round(abs(long_notional - short_notional) / gross, 3)
        net_dir = "long" if long_notional > short_notional else \
            "short" if short_notional > long_notional else "balanced"
        crowd_sev = "high" if net_bias >= _CROWDING_HIGH else \
            "medium" if net_bias >= _CROWDING_MEDIUM else "none"
        if crowd_sev != "none" and len(rows) >= 2:
            concerns.append({"kind": "directional_crowding", "severity": crowd_sev,
                             "detail": f"book is {int(net_bias * 100)}% net {net_dir}"})

        # 3) Same-group, same-direction clusters (positions that move together).
        cluster_counts: dict[tuple, int] = {}
        for p in rows:
            key = (str(p.get("group") or "*"), _direction(p))
            cluster_counts[key] = cluster_counts.get(key, 0) + 1
        (cg, cd), cn = max(cluster_counts.items(), key=lambda kv: kv[1])
        clus_sev = "high" if cn >= _CLUSTER_HIGH else "medium" if cn >= _CLUSTER_MEDIUM else "none"
        if clus_sev != "none" and cg not in ("", "*"):
            concerns.append({"kind": "correlated_cluster", "severity": clus_sev,
                             "detail": f"{cn} {cd.lower()} positions all in {cg}"})

        # 4) Shared liquidation zone — same-direction positions whose isolated-margin
        #    liquidation sits within one tight adverse-move band.
        zone_hits = _shared_liquidation_zone(rows)
        if zone_hits >= _LIQ_ZONE_HIGH:
            concerns.append({"kind": "shared_liquidation_zone", "severity": "high",
                             "detail": f"{zone_hits} positions liquidate within a "
                                       f"{int(_LIQ_ZONE_BAND)}% move of each other"})

        risk = _rollup(*[c["severity"] for c in concerns]) if concerns else "none"
        return {
            "version": SENTINEL_VERSION,
            "position_count": len(rows),
            "gross_notional_usd": round(gross, 2),
            "net_direction": net_dir,
            "net_bias": net_bias,
            "top_group": {"group": top_group, "share_pct": top_share},
            "concerns": concerns,
            "risk": risk,
        }
    except Exception:
        return base


def _shared_liquidation_zone(rows: list[dict]) -> int:
    """The size of the largest cluster of same-direction positions whose
    liquidation adverse-move % falls within one ``_LIQ_ZONE_BAND``-wide window.
    A single shock through that window cascades all of them together."""
    best = 0
    for direction in ("LONG", "SHORT"):
        moves = sorted(
            m for m in (liquidation_move_frac(p.get("leverage")) for p in rows
                        if _direction(p) == direction)
            if m is not None)
        moves = [m * 100 for m in moves]
        # Sliding window: largest count within a band of width _LIQ_ZONE_BAND.
        i = 0
        for j in range(len(moves)):
            while moves[j] - moves[i] > _LIQ_ZONE_BAND:
                i += 1
            best = max(best, j - i + 1)
    return best


def sentinel_payload(positions: list[dict]) -> dict:
    """A compact, JSON-serialisable record for the Flight Recorder / telemetry:
    the risk level, the crowding metrics, and the triggered concerns."""
    a = analyze(positions)
    return {
        "version": SENTINEL_VERSION,
        "risk": a["risk"],
        "position_count": a["position_count"],
        "gross_notional_usd": a["gross_notional_usd"],
        "net_direction": a["net_direction"],
        "net_bias": a["net_bias"],
        "top_group": a["top_group"],
        "concerns": a["concerns"][:8],
    }
