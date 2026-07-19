"""Universal Escape Agent — the recovery layer of Guardian.

    The AI proposes. Deterministic controls authorize. The escape agent recovers.

When something goes wrong — a crash, a compromised key, an operator who just wants
*out* — the question is not only "close everything" but **in what order**, so the
unwind itself doesn't make things worse. The Escape Agent produces a **safe,
ordered emergency-exit plan**: which position to close first, why, and how much
margin each close frees for the positions still open.

``plan(positions)`` is a **pure, deterministic** planner (no engine, no exchange,
no clock, no network). It ranks the book by *escape urgency* — a risk-weighted
blend of how close each position sits to its own liquidation (fragility) and how
large it is (exposure) — so the most dangerous positions are unwound first, and
each close frees isolated margin that widens the liquidation buffer on everything
still open.

Scope + safety stance (this is the plan-only module):

* **Plan, don't pull the trigger.** This module *describes* the exit; it never
  closes anything. Execution stays with the existing, battle-tested primitives
  (``engine.flatten_all_positions`` / ``executor.close_all_positions`` /
  ``close_position`` / reduce-only ``_partial_close``). The plan names the
  recommended path; a human (or a later, explicitly-gated executor) acts on it.
* **Pure + deterministic + fail-open.** Ranking is a pure function of the book
  snapshot, so it is trivially testable and can never touch the trade path; a bad
  position is skipped, a fault degrades to an empty plan.
* **Ordered for safety, with the reason attached.** Every step carries *why* it
  is where it is (fragility, exposure, margin freed), so the plan is auditable,
  not a black box.

Reuses the Digital Twin's canonical ``liquidation_move_frac`` (one liquidation
formula across Guardian, no drift).
"""

from __future__ import annotations

from typing import Any, Optional

from bot.guardian.digital_twin import liquidation_move_frac

ESCAPE_VERSION = 1

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# Escape-urgency (book danger) thresholds, keyed on the MOST fragile position's
# adverse-move-to-liquidation %. Closer to liquidation → more urgent to unwind.
_URGENT_HIGH = 8.0      # a position within an 8% adverse move of liquidation
_URGENT_MEDIUM = 15.0
_URGENT_LOW = 30.0

# When leverage is unknown we can't estimate fragility; treat as a wide, low-
# urgency move so unknown-leverage positions sort last, never first.
_UNKNOWN_MOVE = 1.0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _notional(pos: dict) -> float:
    entry, qty = _num(pos.get("entry")), _num(pos.get("qty"))
    if entry is not None and qty is not None:
        return abs(entry * qty)
    cost, lev = _num(pos.get("cost_usd")), _num(pos.get("leverage"))
    if cost is not None:
        return abs(cost * (lev or 1.0))
    return 0.0


def _margin(pos: dict) -> float:
    cost = _num(pos.get("cost_usd"))
    if cost is not None:
        return abs(cost)
    # Fall back to notional / leverage.
    lev = _num(pos.get("leverage")) or 1.0
    return _notional(pos) / lev if lev > 0 else 0.0


def _direction(pos: dict) -> str:
    return "SHORT" if str(pos.get("direction", "LONG")).upper().startswith("S") else "LONG"


def _urgency(notional: float, liq_move_frac: Optional[float]) -> float:
    """Risk-weighted escape priority: exposure amplified by fragility. A big,
    near-liquidation position outranks a small, well-collateralised one. Scaled
    so a 10× position (≈0.10 move) weights roughly at its full notional."""
    move = liq_move_frac if (liq_move_frac is not None and liq_move_frac > 0) else _UNKNOWN_MOVE
    return notional * (0.10 / max(move, 0.01))


def _book_risk(min_move_pct: Optional[float]) -> str:
    if min_move_pct is None:
        return "none"
    if min_move_pct < _URGENT_HIGH:
        return "high"
    if min_move_pct < _URGENT_MEDIUM:
        return "medium"
    if min_move_pct < _URGENT_LOW:
        return "low"
    return "none"


def _reason(rank: int, group: str, liq_move_pct: Optional[float], share_pct: float) -> str:
    bits = []
    if liq_move_pct is not None and liq_move_pct < _URGENT_MEDIUM:
        bits.append(f"~{liq_move_pct}% from liquidation")
    if share_pct >= 25.0:
        bits.append(f"{share_pct}% of the book")
    if group not in ("", "*"):
        bits.append(f"{group} exposure")
    if not bits:
        return "reduce remaining exposure"
    if rank == 1:
        return "close first — " + ", ".join(bits)
    return ", ".join(bits)


def plan(positions: list[dict]) -> dict:
    """Build a safe, ordered emergency-exit plan. Pure; never raises.

    Returns::

        {
          "version": int,
          "position_count": int,
          "gross_notional_usd": float,
          "total_margin_usd": float,
          "risk": "none"|"low"|"medium"|"high",   # how urgent unwinding is
          "steps": [ {"order","symbol","direction","group","notional_usd",
                      "leverage","liq_move_pct","urgency","margin_freed_cum_usd",
                      "reason"}, ... ],            # most-dangerous first
          "recommended": str,                      # the execution primitive to use
        }
    """
    base = {"version": ESCAPE_VERSION, "position_count": 0,
            "gross_notional_usd": 0.0, "total_margin_usd": 0.0,
            "risk": "none", "steps": [],
            "recommended": "no open positions — nothing to unwind"}
    try:
        rows = [p for p in (positions or []) if _notional(p) > 0]
        if not rows:
            return base
        gross = sum(_notional(p) for p in rows)
        if gross <= 0:
            return base

        ranked: list[dict[str, Any]] = []
        min_move_pct: Optional[float] = None
        for p in rows:
            notional = _notional(p)
            frac = liquidation_move_frac(p.get("leverage"))
            liq_move_pct = round(frac * 100, 2) if frac is not None else None
            if liq_move_pct is not None:
                min_move_pct = liq_move_pct if min_move_pct is None else min(min_move_pct, liq_move_pct)
            ranked.append({
                "symbol": str(p.get("symbol", "?")),
                "direction": _direction(p),
                "group": str(p.get("group") or "*"),
                "notional_usd": round(notional, 2),
                "leverage": _num(p.get("leverage")) or 1.0,
                "liq_move_pct": liq_move_pct,
                "margin": _margin(p),
                "_urgency": _urgency(notional, frac),
                "_share": notional / gross * 100,
            })
        # Most dangerous first; stable tie-break by notional then symbol.
        ranked.sort(key=lambda r: (-r["_urgency"], -r["notional_usd"], r["symbol"]))

        steps = []
        margin_cum = 0.0
        for i, r in enumerate(ranked, start=1):
            margin_cum += r["margin"]
            steps.append({
                "order": i,
                "symbol": r["symbol"],
                "direction": r["direction"],
                "group": r["group"],
                "notional_usd": r["notional_usd"],
                "leverage": r["leverage"],
                "liq_move_pct": r["liq_move_pct"],
                "urgency": round(r["_urgency"], 2),
                "margin_freed_cum_usd": round(margin_cum, 2),
                "reason": _reason(i, r["group"], r["liq_move_pct"], round(r["_share"], 1)),
            })

        return {
            "version": ESCAPE_VERSION,
            "position_count": len(rows),
            "gross_notional_usd": round(gross, 2),
            "total_margin_usd": round(sum(r["margin"] for r in ranked), 2),
            "risk": _book_risk(min_move_pct),
            "steps": steps,
            "recommended": ("flatten via reduce-only market closes, most-fragile "
                            "first (engine.flatten_all_positions / "
                            "executor.close_all_positions)"),
        }
    except Exception:
        return base


def escape_payload(positions: list[dict]) -> dict:
    """A compact, JSON-serialisable record for the Flight Recorder / telemetry:
    the escape urgency, the book totals, and the ordered exit symbols with their
    reasons — enough to prove what the plan was, without the full per-step detail."""
    p = plan(positions)
    return {
        "version": ESCAPE_VERSION,
        "risk": p["risk"],
        "position_count": p["position_count"],
        "gross_notional_usd": p["gross_notional_usd"],
        "total_margin_usd": p["total_margin_usd"],
        "order": [
            {"order": s["order"], "symbol": s["symbol"], "direction": s["direction"],
             "liq_move_pct": s["liq_move_pct"], "reason": s["reason"]}
            for s in p["steps"][:12]
        ],
        "recommended": p["recommended"],
    }
