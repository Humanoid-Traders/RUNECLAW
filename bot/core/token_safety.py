"""Token Safety Scanner — detection-only rug/honeypot shape detection.

    Detects rug/honeypot/manipulation shapes so the agent can stand down.
    It never proposes a buy, and it never treats "no data" as "safe".

Pure, deterministic scorer: a token's on-chain + market safety features → a verdict
(``safe`` / ``caution`` / ``danger``) + a per-check report. Two roles:

* user-facing (research dossiers / meme radar): shows WHY a token is dangerous;
* feeder: ``to_veto_features`` maps readings onto the Guardian Integrity Veto's
  keys, so this scanner is what unblocks the veto's engine wiring.

Discipline (matches veto-only + honest-UNVERIFIED):
* Detection, never generation — outputs are only stand-down signals; there is no
  buy / positive / up-vote output.
* No data ≠ safe — a missing input is ``unknown``, never a pass. ``safe`` requires
  positive evidence; a mostly-unknown token is at best ``caution``.
* A single disqualifying reading (hard flag) forces ``danger``.

No engine/network/clock import — the caller fetches the features (from
``bot.core.onchain`` etc.) and passes them in.
"""

from __future__ import annotations

from typing import Any, Optional

SAFE = "safe"
CAUTION = "caution"
DANGER = "danger"

OK = "ok"
FLAG = "flag"
HARD = "hard"
UNKNOWN = "unknown"


def _num(x: Any) -> Optional[float]:
    # bools are handled by the boolean checks, never the numeric ones
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _b(x: Any) -> Optional[bool]:
    return x if isinstance(x, bool) else None


# Weighted soft-flag score → verdict (a hard flag overrides to DANGER outright).
_CAUTION_SCORE = 1.5
_DANGER_SCORE = 3.0
# If most checks are unknown we cannot certify safety → at best caution.
_MIN_EVIDENCE_FRAC = 0.5


def assess_token(features: Optional[dict]) -> dict:
    """Score a token's safety features. Returns::

        {verdict, score, checks:[{name,status,detail}], flags:[str],
         evidence, unknowns, veto_features}

    ``verdict`` ∈ {safe, caution, danger} — never a positive/buy signal.
    """
    f = features or {}
    checks: list[dict] = []
    score = 0.0
    hard = False

    def boolean(name: str, danger_when: bool, weight: float, danger_hard: bool,
                on_bad: str) -> None:
        nonlocal score, hard
        v = _b(f.get(name))
        if v is None:
            checks.append({"name": name, "status": UNKNOWN, "detail": "not provided"})
            return
        bad = (v is danger_when)
        if not bad:
            checks.append({"name": name, "status": OK, "detail": "ok"})
            return
        if danger_hard:
            hard = True
            checks.append({"name": name, "status": HARD, "detail": on_bad})
        else:
            score += weight
            checks.append({"name": name, "status": FLAG, "detail": on_bad})

    def numeric(name: str, direction: str, soft: float, hard_th: Optional[float],
                weight: float, on_soft: str, on_hard: str) -> None:
        nonlocal score, hard
        v = _num(f.get(name))
        if v is None:
            checks.append({"name": name, "status": UNKNOWN, "detail": "not provided"})
            return
        high = direction == "high"
        hard_hit = (hard_th is not None) and ((v >= hard_th) if high else (v <= hard_th))
        soft_hit = (v >= soft) if high else (v <= soft)
        if hard_hit:
            hard = True
            checks.append({"name": name, "status": HARD, "detail": on_hard})
        elif soft_hit:
            score += weight
            checks.append({"name": name, "status": FLAG, "detail": on_soft})
        else:
            checks.append({"name": name, "status": OK, "detail": "ok"})

    # -- hard-capable checks --
    boolean("honeypot_cannot_sell", True, 0, True, "HONEYPOT: token cannot be sold")
    boolean("mint_authority_active", True, 0, True, "live mint authority — supply can be inflated")
    boolean("freeze_authority_active", True, 1.0, True, "live freeze authority — balances can be frozen")
    numeric("sell_tax_pct", "high", 10.0, 30.0, 1.0,
            "elevated sell tax", "sell tax ≥30% — exit trap")
    numeric("top_holder_pct", "high", 0.3, 0.5, 2.0,
            "concentrated top holder", "one wallet holds ≥50% — can dump everything")

    # -- soft-only checks --
    boolean("ownership_renounced", False, 1.0, False, "ownership NOT renounced")
    boolean("lp_locked", False, 1.5, False, "liquidity NOT locked")
    numeric("buy_tax_pct", "high", 10.0, None, 0.5, "elevated buy tax", "")
    numeric("liquidity_usd", "low", 10_000.0, None, 1.0, "thin liquidity (<$10k)", "")
    numeric("holder_count", "low", 50.0, None, 1.0, "few holders (<50)", "")
    numeric("listing_age_hours", "low", 24.0, None, 0.5, "brand-new listing (<24h)", "")

    evidence = sum(1 for c in checks if c["status"] in (OK, FLAG, HARD))
    unknowns = sum(1 for c in checks if c["status"] == UNKNOWN)
    total = len(checks)

    # Verdict.
    if hard:
        verdict = DANGER
    elif score >= _DANGER_SCORE:
        verdict = DANGER
    elif score >= _CAUTION_SCORE:
        verdict = CAUTION
    elif total and (evidence / total) < _MIN_EVIDENCE_FRAC:
        # Not enough positive evidence to certify safety → cannot say safe.
        verdict = CAUTION
    else:
        verdict = SAFE

    flags = [c["detail"] for c in checks if c["status"] in (FLAG, HARD)]
    return {
        "verdict": verdict,
        "score": round(score, 3),
        "checks": checks,
        "flags": flags,
        "evidence": evidence,
        "unknowns": unknowns,
        "veto_features": to_veto_features(f),
    }


def to_veto_features(features: Optional[dict]) -> dict:
    """Map token features onto the Guardian Integrity Veto's feature keys, so the
    scanner can feed the veto. Only maps keys that are present (missing → skipped,
    so the veto's own fail-open-per-feature rule applies)."""
    f = features or {}
    out: dict[str, Any] = {}
    if f.get("top_holder_pct") is not None:
        out["holder_concentration"] = f["top_holder_pct"]
    if f.get("listing_age_hours") is not None:
        out["listing_age_hours"] = f["listing_age_hours"]
    if f.get("wash_volume_ratio") is not None:
        out["wash_volume_ratio"] = f["wash_volume_ratio"]
    if f.get("price_liquidity_divergence") is not None:
        out["price_liquidity_divergence"] = f["price_liquidity_divergence"]
    return out


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render of a safety report (no markup)."""
    if not report or not isinstance(report, dict):
        return "No token-safety report."
    v = report.get("verdict", CAUTION)
    icon = {SAFE: "✓", CAUTION: "⚠", DANGER: "⛔"}.get(v, "·")
    lines = [f"{icon} TOKEN SAFETY: {v.upper()} "
             f"(score {report.get('score')}, {report.get('evidence')} checks with data, "
             f"{report.get('unknowns')} unknown)"]
    for fl in report.get("flags", []):
        lines.append(f"   – {fl}")
    if v == SAFE and not report.get("flags"):
        lines.append("   renounced, LP-locked, distributed, low tax, deep liquidity")
    if report.get("unknowns", 0) and v != DANGER:
        lines.append(f"   note: {report['unknowns']} check(s) had no data — safety not fully verified")
    return "\n".join(lines)
