"""Trade co-pilot — a deterministic second opinion on a proposed order.

Before a user confirms a manual trade, the co-pilot reviews it against the
objective facts that matter — reward:risk, stop distance, geometry, size vs
equity, and (when supplied) the engine's current bias and the user's existing
exposure for that symbol. It returns a structured verdict + flags the UI shows
right in the ticket.

Deliberately DETERMINISTIC and pure: no LLM dependency, no network, same input →
same review every time. It ADVISES — it never blocks or places anything (the
risk gate and, for live, the Authority Envelope are the authorities). An LLM
one-liner can be layered on top by the caller, but the substance here is real
arithmetic the user can verify.
"""

from __future__ import annotations

from typing import Any, Optional

# Thresholds (percentage points / ratios). Tuned to flag, not to nag.
_MIN_RR = 1.5
_STOP_TIGHT_PCT = 0.3
_STOP_WIDE_PCT = 15.0
_SIZE_HEAVY_PCT = 20.0        # margin > 20% of equity → concentration flag


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def review(trade: dict, *, equity_usd: Optional[float] = None,
           engine_bias: Optional[str] = None,
           existing_exposure: Optional[str] = None) -> dict:
    """Review a proposed trade. Returns
    ``{verdict, score, rr, stop_pct, target_pct, flags:[{level,msg}], notes:[...]}``.

    ``trade`` = ``{direction, symbol, entry, sl, tp, margin?}``.
    ``engine_bias`` ∈ {"long","short",None}: the engine's current lean on the
    symbol. ``existing_exposure`` ∈ {"long","short",None}: the user's current
    net side on the symbol (for stack/hedge notes). Both optional; absent = no
    opinion (never fabricated).
    """
    direction = str(trade.get("direction") or "").upper().strip()
    is_long = direction in ("LONG", "BUY")
    e, sl, tp = _f(trade.get("entry")), _f(trade.get("sl")), _f(trade.get("tp"))
    flags: list[dict] = []
    notes: list[str] = []

    # Geometry must be valid before anything else means anything.
    geom_ok = (e is not None and sl is not None and tp is not None and e > 0
               and ((is_long and sl < e < tp) or (not is_long and tp < e < sl)))
    if not geom_ok:
        return {"verdict": "invalid", "score": 0, "rr": None,
                "stop_pct": None, "target_pct": None,
                "flags": [{"level": "block", "msg":
                           "Stop/target are on the wrong side of entry for a "
                           f"{'long' if is_long else 'short'}."}],
                "notes": []}

    assert e is not None and sl is not None and tp is not None  # narrowed by geom_ok
    risk = abs(e - sl)
    reward = abs(tp - e)
    rr = round(reward / risk, 2) if risk > 0 else None
    stop_pct = round(risk / e * 100, 2)
    target_pct = round(reward / e * 100, 2)
    score = 100

    # Reward:risk.
    if rr is not None and rr < _MIN_RR:
        flags.append({"level": "warn",
                      "msg": f"Reward:risk is {rr:g} — below {_MIN_RR:g}. The target "
                             "doesn't pay enough for the risk."})
        score -= 25
    elif rr is not None and rr >= 2.5:
        notes.append(f"Strong reward:risk ({rr:g}).")

    # Stop distance.
    if stop_pct < _STOP_TIGHT_PCT:
        flags.append({"level": "warn",
                      "msg": f"Stop is only {stop_pct:g}% away — likely to be wicked "
                             "out by noise."})
        score -= 20
    elif stop_pct > _STOP_WIDE_PCT:
        flags.append({"level": "warn",
                      "msg": f"Stop is {stop_pct:g}% away — a wide stop means a large "
                             "loss if hit; size accordingly."})
        score -= 15

    # Size vs equity.
    margin = _f(trade.get("margin"))
    eq = _f(equity_usd)
    if margin is not None and eq and eq > 0:
        share = margin / eq * 100
        if share > _SIZE_HEAVY_PCT:
            flags.append({"level": "warn",
                          "msg": f"Margin is {share:.0f}% of your equity — heavy "
                                 "concentration on one trade."})
            score -= 20
        else:
            notes.append(f"Margin is {share:.0f}% of equity.")

    # Alignment with the engine's current bias.
    side = "long" if is_long else "short"
    if engine_bias in ("long", "short"):
        if engine_bias != side:
            flags.append({"level": "caution",
                          "msg": f"This {side} runs counter to the engine's current "
                                 f"{engine_bias} bias on {trade.get('symbol', 'the symbol')}."})
            score -= 15
        else:
            notes.append(f"Aligned with the engine's {engine_bias} bias.")

    # Existing exposure (stacking / hedging).
    if existing_exposure in ("long", "short"):
        if existing_exposure == side:
            notes.append(f"You're already {existing_exposure} this symbol — this "
                         "stacks the position (correlated risk).")
        else:
            notes.append(f"You're currently {existing_exposure} this symbol — this "
                         "would hedge/reduce it.")

    score = max(0, min(100, score))
    has_warn = any(f["level"] == "warn" for f in flags)
    verdict = "caution" if (has_warn or any(f["level"] == "caution" for f in flags)) else "clear"
    return {"verdict": verdict, "score": score, "rr": rr,
            "stop_pct": stop_pct, "target_pct": target_pct,
            "flags": flags, "notes": notes}


def human_readable(rev: dict) -> str:
    """Plain-text render of a review (no markup)."""
    if not rev or rev.get("verdict") == "invalid":
        msg = (rev.get("flags") or [{}])[0].get("msg", "Invalid trade geometry.")
        return f"⛔ {msg}"
    head = {"clear": "✅ Looks disciplined", "caution": "⚠️ Worth a second look"}.get(
        rev["verdict"], rev["verdict"])
    bits = [f"{head} (score {rev['score']}/100)",
            f"R:R {rev['rr']:g} · stop {rev['stop_pct']:g}% · target {rev['target_pct']:g}%"]
    for f in rev.get("flags", []):
        bits.append(f"• {f['msg']}")
    for n in rev.get("notes", []):
        bits.append(f"· {n}")
    return "\n".join(bits)
