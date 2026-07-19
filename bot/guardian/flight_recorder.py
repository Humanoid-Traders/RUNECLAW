"""Agent Flight Recorder — provenance-complete decision records over the
engine's tamper-evident audit chain.

The engine already seals every decision into a SHA-256 hash-chained,
Ed25519-attested append-only log (``bot.utils.audit_chain.AuditChain``). What it
sealed was *thin* — a couple of fields — and it never linked a decision to the
outcome it produced. The Flight Recorder widens the sealed payload to the full
provenance behind a decision (inputs → voter reasoning → model/prompt version →
risk-gate verdict → approval → transaction), and adds an ``OUTCOME`` event keyed
to the same ``decision_id`` so a decision joins to its fill / PnL / close.

Every function here is **pure and fail-open**: it reads whatever optional
attributes are present via ``getattr``/``.get`` and never raises. A recorder
failure must never touch a trade — these helpers run strictly downstream of the
already-computed ``TradeIdea`` / ``RiskCheck`` / closed position, exactly where
the existing ``seal_decision`` calls sit.

Nothing in this module imports the engine, the analyzer, or any network client,
so it stays trivially unit-testable and dependency-light.
"""

from __future__ import annotations

from typing import Any, Optional

# Keep payloads bounded — the chain is a JSONL file and rides the website sync.
_REASONING_MAX = 320
_MAX_VOTES = 12
_MAX_FACTORS = 8
_MAX_CHECKS = 40


# ── small, defensive coercers ────────────────────────────────────────

def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-key access that tolerates dicts, models, and None."""
    if obj is None:
        return default
    try:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        # A hostile/broken __getattr__ must never break the recorder.
        return default


def _num(x: Any) -> Optional[float]:
    """Coerce to a finite float, else None (garbage is dropped, never raised)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return v


def _round(x: Any, nd: int = 6) -> Optional[float]:
    v = _num(x)
    return round(v, nd) if v is not None else None


def _trim(s: Any, n: int = _REASONING_MAX) -> str:
    if s is None:
        return ""
    try:
        s = str(s)
    except Exception:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _enum_str(x: Any) -> str:
    """Stringify a value that might be an Enum (``.value``) or a plain str."""
    if x is None:
        return ""
    v = getattr(x, "value", x)
    try:
        return str(v)
    except Exception:
        return ""


def _compact_votes(votes: Any, limit: int = _MAX_VOTES) -> list[dict]:
    """Normalise ``(name, vote, weight)`` tuples into ranked contribution rows.

    The analyzer attaches ``idea._confluence_votes`` as a list of 3-tuples. We
    keep the ones that actually moved the decision (|vote·weight| largest),
    labelled bullish/bearish, so the record explains *why* — not just *that*.
    """
    rows: list[dict] = []
    if not votes:
        return rows
    try:
        for item in votes:
            try:
                name, vote, weight = item[0], item[1], item[2]
            except (TypeError, IndexError, KeyError):
                # Also accept dict-shaped votes.
                if isinstance(item, dict):
                    name = item.get("name") or item.get("voter") or ""
                    vote = item.get("vote")
                    weight = item.get("weight")
                else:
                    continue
            v = _num(vote)
            w = _num(weight)
            if v is None or w is None:
                continue
            contrib = v * w
            if abs(contrib) <= 1e-9:
                continue  # a vote that didn't move the decision explains nothing
            rows.append({
                "name": _trim(name, 48),
                "vote": round(v, 4),
                "weight": round(w, 4),
                "contribution": round(contrib, 4),
                "direction": "bullish" if contrib > 1e-9 else ("bearish" if contrib < -1e-9 else "neutral"),
            })
    except Exception:
        return rows[:limit]
    rows.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    return rows[:limit]


# ── decision-record builders (widen the sealed payload) ──────────────

def decision_idea_payload(idea: Any) -> dict:
    """Provenance-complete ``idea`` dict for a DecisionRecord.

    Captures the thesis, the trade geometry, the full provenance fields already
    carried on ``TradeIdea`` (model, prompt hash, analysis version, data
    sufficiency), the ranked voter breakdown, and — when the analyzer attached
    it — a compact slice of the deterministic explainability report.
    """
    try:
        entry = _round(_get(idea, "entry_price"))
        sl = _round(_get(idea, "stop_loss"))
        tp = _round(_get(idea, "take_profit"))
        rr = _get(idea, "risk_reward_ratio")
        out: dict = {
            "direction": _enum_str(_get(idea, "direction")),
            "confidence": _round(_get(idea, "confidence"), 4),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": _round(rr, 2),
            "strategy_type": _trim(_get(idea, "strategy_type", ""), 32),
            "signal_type": _trim(_get(idea, "signal_type", ""), 40),
            "timeframe": _trim(_get(idea, "timeframe", ""), 16),
            "htf_trend": _trim(_get(idea, "htf_trend", ""), 16),
            "source": _trim(_get(idea, "source", ""), 32),
            "reasoning": _trim(_get(idea, "reasoning", "")),
            "signals_used": [_trim(s, 40) for s in (_get(idea, "signals_used", []) or [])][:16],
            # Provenance / evidence (audit fix #18 fields on TradeIdea).
            "provenance": {
                "model_provider": _trim(_get(idea, "model_provider", ""), 64) or None,
                "prompt_hash": _trim(_get(idea, "prompt_hash", ""), 32) or None,
                "analysis_version": _trim(_get(idea, "analysis_version", ""), 32) or None,
                "llm_confidence": _round(_get(idea, "llm_confidence"), 4),
                "confluence_score": _round(_get(idea, "confluence_score"), 4),
                "blended_confidence_raw": _round(_get(idea, "blended_confidence_raw"), 4),
                "data_bars": _get(idea, "data_bars"),
                "data_thin": _get(idea, "data_thin"),
            },
            "votes": _compact_votes(_get(idea, "_confluence_votes")),
        }
        explain = _explain_slice(_get(idea, "_explain_report"))
        if explain:
            out["explain"] = explain
        return out
    except Exception:
        # Absolute fail-open: fall back to the historic thin shape.
        return {
            "direction": _enum_str(_get(idea, "direction")),
            "confidence": _round(_get(idea, "confidence"), 4),
        }


def _explain_slice(report: Any) -> Optional[dict]:
    """Compact slice of an ExplainabilityReport (model_dump) for the record."""
    if not report:
        return None
    try:
        if not isinstance(report, dict):
            # Pydantic model — serialise defensively.
            report = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
        compliance = report.get("compliance") or {}
        factors = report.get("factors") or []
        slim_factors = []
        for f in factors[:_MAX_FACTORS]:
            if not isinstance(f, dict):
                continue
            slim_factors.append({
                "name": _trim(f.get("name", ""), 40),
                "contribution_pct": _round(f.get("contribution_pct"), 2),
                "direction": _trim(f.get("direction", ""), 12),
            })
        return {
            "top_bullish": [_trim(x, 60) for x in (report.get("top_bullish") or [])][:3],
            "top_bearish": [_trim(x, 60) for x in (report.get("top_bearish") or [])][:3],
            "factors": slim_factors,
            "compliance": _round(compliance.get("overall") if isinstance(compliance, dict) else None, 3),
            "summary": _trim(report.get("summary", ""), 240),
        }
    except Exception:
        return None


def decision_risk_payload(risk: Any, size_usd: Optional[float] = None) -> dict:
    """Provenance-complete ``risk`` dict from a RiskCheck verdict."""
    try:
        passed = _get(risk, "checks_passed", []) or []
        failed = _get(risk, "checks_failed", []) or []
        size = _num(size_usd)
        if size is None:
            size = _num(_get(risk, "position_size_usd"))
        return {
            "verdict": _enum_str(_get(risk, "verdict")) or "UNKNOWN",
            "passed": len(passed) if hasattr(passed, "__len__") else 0,
            "failed": len(failed) if hasattr(failed, "__len__") else 0,
            "size_usd": _round(size, 2),
            "position_pct": _round(_get(risk, "position_pct"), 4),
            "daily_loss_pct": _round(_get(risk, "daily_loss_pct"), 4),
            "drawdown_pct": _round(_get(risk, "drawdown_pct"), 4),
            "reason": _trim(_get(risk, "reason", ""), 200),
            "checks_failed": [_trim(c, 48) for c in (failed or [])][:_MAX_CHECKS],
        }
    except Exception:
        return {"verdict": _enum_str(_get(risk, "verdict")) or "UNKNOWN"}


def outcome_event_payload(pos: Any) -> dict:
    """OUTCOME event payload from a closed position, keyed to its decision.

    Appended to the same chain as the DECISION so a record links to the fill /
    PnL / close it produced. ``decision_id`` == the position's ``trade_id`` (the
    id the DecisionRecord was sealed under).
    """
    return {
        "decision_id": _trim(_get(pos, "trade_id", ""), 64),
        "symbol": _trim(_get(pos, "symbol", ""), 32),
        "direction": _enum_str(_get(pos, "direction")),
        "pnl_usd": _round(_get(pos, "pnl_usd"), 4),
        "exit_price": _round(_get(pos, "exit_price")),
        "entry_price": _round(_get(pos, "entry_price")),
        "close_reason": _trim(_get(pos, "close_reason", ""), 48),
    }


# ── reading the chain back as joined flight records ──────────────────

def _entry_dict(e: Any) -> dict:
    """Normalise an AuditEntry (or its dict form) to a plain dict."""
    if isinstance(e, dict):
        return e
    to_dict = getattr(e, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    return {
        "sequence": _get(e, "sequence"),
        "event_type": _get(e, "event_type"),
        "payload": _get(e, "payload", {}) or {},
        "actor": _get(e, "actor", ""),
        "timestamp": _get(e, "timestamp", ""),
        "prev_hash": _get(e, "prev_hash", ""),
        "entry_hash": _get(e, "entry_hash", ""),
    }


def assemble_flight_records(entries: Any, limit: int = 50) -> list[dict]:
    """Join DECISION entries with their OUTCOME into flight records, newest first.

    ``entries`` is an ordered iterable of AuditEntry objects or their dict form
    (as produced by ``AuditChain.get_entries``). DECISION events carry the
    sealed DecisionRecord; OUTCOME events carry the realised close keyed by
    ``decision_id``. Chain fields (sequence, entry_hash, prev_hash) ride along so
    the website can display and re-verify integrity.
    """
    if not entries:
        return []
    outcomes: dict[str, dict] = {}
    decisions: list[dict] = []
    try:
        norm = [_entry_dict(e) for e in entries]
    except Exception:
        return []

    for e in norm:
        etype = e.get("event_type")
        payload = e.get("payload") or {}
        if etype == "OUTCOME":
            did = payload.get("decision_id")
            if did:
                outcomes[str(did)] = payload

    for e in norm:
        if e.get("event_type") != "DECISION":
            continue
        payload = e.get("payload") or {}
        did = payload.get("decision_id") or ""
        decisions.append({
            "decision_id": did,
            "symbol": payload.get("symbol", ""),
            "timestamp": payload.get("timestamp") or e.get("timestamp", ""),
            "outcome": payload.get("outcome", ""),
            "is_paper": bool(payload.get("is_paper", True)),
            "idea": payload.get("idea"),
            "risk": payload.get("risk"),
            "macro": payload.get("macro"),
            "compliance": payload.get("compliance"),
            "result": outcomes.get(str(did)),
            "chain": {
                "sequence": e.get("sequence"),
                "entry_hash": e.get("entry_hash", ""),
                "prev_hash": e.get("prev_hash", ""),
            },
        })

    decisions.reverse()  # newest first
    out = decisions[:limit]
    # Attach a plain-English "why" to each record (deterministic, drawn strictly
    # from the sealed record). Additive + fail-open — a narration hiccup never
    # drops the record itself.
    try:
        from bot.guardian.explain_fill import explain as _explain
        for rec in out:
            try:
                rec["explanation"] = _explain(rec)
            except Exception:
                pass
    except Exception:
        pass
    return out


def verify_entries(entries: Any) -> tuple[bool, list[str]]:
    """Re-derive the hash chain over an in-memory list of entries.

    A pure counterpart to ``AuditChain.verify`` (which reads the file): recompute
    every ``entry_hash`` and confirm sequence continuity + prev_hash linkage.
    Returns ``(True, [])`` when intact, else ``(False, [problem, ...])``. Used by
    tests and available to any caller holding entries rather than the file.
    """
    from bot.utils.audit_chain import GENESIS_HASH, _compute_hash

    problems: list[str] = []
    prev_hash = GENESIS_HASH
    expected_seq = 0
    try:
        norm = [_entry_dict(e) for e in (entries or [])]
    except Exception:
        return False, ["entries not iterable"]

    for i, d in enumerate(norm):
        seq = d.get("sequence")
        # Only enforce continuity when the window starts at genesis; a tail
        # slice legitimately starts mid-chain, so anchor on the first entry.
        if i == 0:
            expected_seq = seq if isinstance(seq, int) else 0
            prev_hash = d.get("prev_hash", GENESIS_HASH)
        if seq != expected_seq:
            problems.append(f"entry {i}: expected sequence {expected_seq}, got {seq}")
        recorded_prev = d.get("prev_hash", "")
        if recorded_prev != prev_hash:
            problems.append(f"entry {i}: prev_hash linkage broken")
        recomputed = _compute_hash(
            d.get("sequence", 0), d.get("event_type", ""), d.get("payload", {}),
            d.get("actor", ""), d.get("timestamp", ""), d.get("prev_hash", ""),
        )
        if recomputed != d.get("entry_hash", ""):
            problems.append(f"entry {i}: entry_hash mismatch (tampered payload)")
        prev_hash = d.get("entry_hash", "")
        expected_seq = (expected_seq or 0) + 1

    return (len(problems) == 0, problems)
