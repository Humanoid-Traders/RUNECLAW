"""Explain-my-fill — a plain-English "why" for a recorded trading decision.

The Flight Recorder seals every decision's full provenance (thesis, voters,
model/version, risk, outcome) into a hash-chained record. This turns ONE such
record into a sentence a human reads — *what* was decided, *why* (the strongest
factors), *by whom* (model + analysis version), how it *turned out*, and how it
can be *verified* (chain sequence + entry hash).

Deliberately DETERMINISTIC — it narrates the record it's given, inventing
nothing. Same record → same narrative; no LLM, no network. A caller may layer an
LLM rephrase on top, but the substance is drawn strictly from the sealed record,
so it stays faithful to what actually happened. Pairs the story with the
verifiable Proof-of-PnL: the narrative is only as trustworthy as the chain it
cites, and the chain is re-derivable.
"""

from __future__ import annotations

from typing import Any, Optional


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _short(h: Any, n: int = 10) -> str:
    s = str(h or "")
    return s[:n] if s else ""


def explain(record: dict) -> dict:
    """Turn a flight record (from ``assemble_flight_records``) into
    ``{headline, why:[...], provenance:{...}, outcome:{...}, verification:{...},
    narrative}``. Faithful to the record; fabricates nothing."""
    if not isinstance(record, dict):
        return {"headline": "", "why": [], "provenance": {}, "outcome": {},
                "verification": {}, "narrative": "No decision record."}
    idea = record.get("idea") or {}
    symbol = str(record.get("symbol") or "").split("/")[0].upper() or "?"
    direction = str(idea.get("direction") or "").upper()
    outcome_kind = str(record.get("outcome") or "").lower()   # taken / rejected / ...
    is_paper = bool(record.get("is_paper", True))
    conf = _f(idea.get("confidence"))
    rr = _f(idea.get("rr"))

    # ── headline: what was decided ──
    verb = {"taken": "took", "confirmed": "took", "rejected": "rejected",
            "skipped": "passed on"}.get(outcome_kind, "evaluated")
    mode = "paper" if is_paper else "live"
    conf_txt = f" at {conf*100:.0f}% confidence" if conf is not None else ""
    headline = (f"The agent {verb} a {mode} {direction or 'trade'} on {symbol}"
                f"{conf_txt}.").replace("  ", " ")

    # ── why: the strongest evidence, drawn from the record ──
    why: list[str] = []
    strat = str(idea.get("strategy_type") or "").strip()
    sig = str(idea.get("signal_type") or "").strip()
    if sig or strat:
        why.append("Setup: " + " · ".join([x for x in (sig, strat) if x]))
    explain_slice = idea.get("explain") or {}
    bull = [str(x) for x in (explain_slice.get("top_bullish") or [])][:3]
    bear = [str(x) for x in (explain_slice.get("top_bearish") or [])][:3]
    if direction == "LONG" and bull:
        why.append("For it: " + "; ".join(bull))
    elif direction == "SHORT" and bear:
        why.append("For it: " + "; ".join(bear))
    elif bull or bear:
        why.append("Factors: " + "; ".join((bull + bear)[:3]))
    # Top voters (ranked contribution).
    votes = [v for v in (idea.get("votes") or []) if isinstance(v, dict)][:3]
    if votes:
        vtxt = ", ".join(f"{str(v.get('name', '?'))}" for v in votes)
        why.append("Top voters: " + vtxt)
    if rr is not None:
        why.append(f"Reward:risk {rr:g} (entry {idea.get('entry')}, "
                   f"stop {idea.get('sl')}, target {idea.get('tp')}).")
    reasoning = str(idea.get("reasoning") or "").strip()
    if reasoning:
        why.append("Thesis: " + reasoning[:280])

    # ── provenance: by whom ──
    prov = idea.get("provenance") or {}
    provenance = {
        "model": prov.get("model_provider"),
        "analysis_version": prov.get("analysis_version"),
        "prompt_hash": _short(prov.get("prompt_hash"), 12) or None,
        "data_thin": prov.get("data_thin"),
    }

    # ── outcome: how it turned out (only if closed) ──
    result = record.get("result") or {}
    outcome: dict = {}
    if result:
        pnl = _f(result.get("realized_pnl") if "realized_pnl" in result else result.get("pnl"))
        outcome = {
            "closed": True,
            "pnl_usd": round(pnl, 2) if pnl is not None else None,
            "exit_reason": str(result.get("exit_reason") or result.get("reason") or "").strip() or None,
        }

    # ── verification: re-derivable proof ──
    chain = record.get("chain") or {}
    verification = {
        "sequence": chain.get("sequence"),
        "entry_hash": _short(chain.get("entry_hash"), 12),
        "is_paper": is_paper,
        "note": "Sealed in the SHA-256 hash chain — re-derivable from the record.",
    }

    # ── one-paragraph narrative ──
    parts = [headline]
    if why:
        parts.append(" ".join(why))
    if provenance.get("model") or provenance.get("analysis_version"):
        parts.append("Decided by "
                     + " / ".join([str(x) for x in (provenance.get("model"),
                                   provenance.get("analysis_version")) if x]) + ".")
    if outcome.get("closed"):
        p = _f(outcome.get("pnl_usd"))
        if p is None or p == 0:
            verdict, amt = "closed flat", ""
        else:
            verdict = "won" if p > 0 else "lost"
            amt = f" ${abs(p):,.2f}"
        parts.append(f"It {verdict}{amt}"
                     + (f" ({outcome['exit_reason']})" if outcome.get("exit_reason") else "") + ".")
    narrative = " ".join(parts)

    return {"headline": headline, "why": why, "provenance": provenance,
            "outcome": outcome, "verification": verification, "narrative": narrative}
