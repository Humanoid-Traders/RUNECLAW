"""Market-Integrity & Manipulation Veto — Guardian's defensive intelligence (veto-only).

    The AI proposes. Deterministic controls authorize. This layer can only say NO.

Scores external red-flag features (social + market + on-chain shapes seen around
manipulated / pump-and-dump tokens) into one deterministic verdict — ``clear`` /
``caution`` / ``veto`` — that the risk gate can consume as an ADDITIONAL rejection
input. Two hard properties, by construction:

1. **Veto-only.** The verdict can reject or warn, never approve, up-vote, size, or
   originate a trade. It only ever tightens.
2. **Detection, never generation.** It flags manipulation *shapes* (sybil-looking
   mention networks, wash-trade-shaped volume, coordinated-uniform sentiment) as
   reasons to stand down — it never produces any of them.

Pure, dependency-light (no engine/network/clock). A missing feature is skipped
(fail-open per feature) — never fabricated, never counted.
"""

from __future__ import annotations

from typing import Any, Optional

VALID_MODES = ("off", "shadow", "enforce")

# Verdicts, in increasing severity. This module NEVER emits anything else — there
# is deliberately no "approve"/positive verdict (veto-only).
CLEAR = "clear"
CAUTION = "caution"
VETO = "veto"


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


# Each feature: (direction, soft_threshold, hard_threshold, weight, label).
# direction "high" → a HIGH reading is risky; "low" → a LOW reading is risky.
# soft crossing contributes ``weight`` to the score + a flag; hard crossing forces
# an immediate veto. hard=None → no single-feature veto for that feature.
_FEATURES: dict[str, dict] = {
    "social_spike_ratio":         {"dir": "high", "soft": 5.0,  "hard": 25.0, "w": 1.0,
                                    "label": "social mention spike"},
    "new_account_ratio":          {"dir": "high", "soft": 0.4,  "hard": 0.75, "w": 1.5,
                                    "label": "fresh-account (sybil-shaped) mentions"},
    "sentiment_uniformity":       {"dir": "high", "soft": 0.85, "hard": 0.97, "w": 1.0,
                                    "label": "near-identical (bot-network) sentiment"},
    "price_liquidity_divergence": {"dir": "high", "soft": 3.0,  "hard": 10.0, "w": 1.5,
                                    "label": "price move large vs on-chain liquidity"},
    "wash_volume_ratio":          {"dir": "high", "soft": 0.4,  "hard": 0.8,  "w": 1.5,
                                    "label": "wash-trade-shaped volume"},
    "holder_concentration":       {"dir": "high", "soft": 0.5,  "hard": 0.7,  "w": 2.0,
                                    "label": "top-holder concentration (rug risk)"},
    "listing_age_hours":          {"dir": "low",  "soft": 48.0, "hard": 2.0,  "w": 1.0,
                                    "label": "brand-new listing (no track record)"},
}

# Soft-score thresholds → verdict. A hard flag overrides these to VETO outright.
_CAUTION_SCORE = 1.5
_VETO_SCORE = 3.0


def assess(features: Optional[dict], *, mode: str = "off") -> dict:
    """Score a feature bundle into a veto-only verdict.

    ``features`` maps any subset of the keys in ``_FEATURES`` to numeric readings
    (a missing key is skipped — fail-open per feature, never fabricated). Returns::

        {verdict, score, flags:[{feature,label,severity,value,threshold}],
         reasons:[str], checked, skipped, mode}

    ``verdict`` ∈ {clear, caution, veto} — never anything positive. ``mode`` is
    carried through for the caller's staged wiring (off/shadow/enforce); it does
    not change the verdict, only how the caller acts on it.
    """
    result: dict[str, Any] = {
        "verdict": CLEAR, "score": 0.0, "flags": [], "reasons": [],
        "checked": 0, "skipped": 0,
        "mode": mode if mode in VALID_MODES else "off",
    }
    if not features or not isinstance(features, dict):
        return result

    flags: list[dict] = result["flags"]
    score = 0.0
    hard_veto = False

    for key, spec in _FEATURES.items():
        v = _num((features or {}).get(key))
        if v is None:
            result["skipped"] += 1
            continue
        result["checked"] += 1
        high = spec["dir"] == "high"
        soft, hard = spec["soft"], spec["hard"]

        # hard flag first — a single disqualifying reading forces veto.
        hard_hit = (v >= hard) if high else (v <= hard)
        soft_hit = (v >= soft) if high else (v <= soft)
        if hard_hit:
            hard_veto = True
            flags.append({"feature": key, "label": spec["label"], "severity": "hard",
                          "value": v, "threshold": hard})
        elif soft_hit:
            score += spec["w"]
            flags.append({"feature": key, "label": spec["label"], "severity": "soft",
                          "value": v, "threshold": soft})

    result["score"] = round(score, 3)
    if hard_veto:
        result["verdict"] = VETO
    elif score >= _VETO_SCORE:
        result["verdict"] = VETO
    elif score >= _CAUTION_SCORE:
        result["verdict"] = CAUTION
    else:
        result["verdict"] = CLEAR

    result["reasons"] = [
        f"{f['label']} {f['value']:g} "
        f"({'hard' if f['severity'] == 'hard' else 'soft'} flag, threshold {f['threshold']:g})"
        for f in flags
    ]
    return result


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render of a veto report (no markup)."""
    if not report or not isinstance(report, dict):
        return "No integrity report."
    v = report.get("verdict", CLEAR)
    icon = {CLEAR: "✓", CAUTION: "⚠", VETO: "⛔"}.get(v, "·")
    lines = [f"{icon} MARKET INTEGRITY: {v.upper()} "
             f"(score {report.get('score')}, {report.get('checked')} checked, "
             f"{report.get('skipped')} skipped)"]
    for r in report.get("reasons", []):
        lines.append(f"   – {r}")
    if v == CLEAR and not report.get("reasons"):
        lines.append("   no manipulation/integrity red flags")
    return "\n".join(lines)
