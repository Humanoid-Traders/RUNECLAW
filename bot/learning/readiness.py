"""Learning-loop readiness: is the learned stack validated enough to apply?

The learners fit continuously (auto-refit every N closes) but their
APPLICATION stays behind default-OFF flags until a human flips them. This
module answers, from evidence, the question the operator has to answer
before flipping: "do we have enough resolved outcomes, and does the learned
adjustment hold out-of-sample?" — and turns state changes into a proactive
alert so nobody has to remember to check.

Components assessed:
  - confidence calibration  -> AUTO_CONFIRM_USE_CALIBRATED
  - voter-weight learning   -> VOTER_WEIGHT_LEARNING_ENABLED
  - per-setup expectancy    -> (applies via confidence nudge when ready)

States per component:
  ACCUMULATING  — not enough resolved samples yet (n/needed shown)
  VALIDATING    — enough samples but the OOS check does not clear the bar
  READY         — fitted, enough samples, OOS evidence clears the bar

The walk-forward result on production data (0/6 profitable folds) makes the
learning loop the deciding evidence source for where the edge is — which is
exactly why readiness is assessed from resolved LIVE/paper outcomes, not
from backtest curves.
"""

from __future__ import annotations

import logging

log = logging.getLogger("runeclaw.readiness")

# The OOS bar for voter weights: the fraction of learned voters whose
# adjustment direction must hold on unseen trades. 0.5 = coin flip; demand
# clearly better before recommending live application.
_VW_HOLD_RATE_BAR = 0.6
# Calibration readiness rides on the fitter's own min_samples (30), but for
# a RECOMMENDATION we want a fuller curve than the bare minimum.
_CAL_RECOMMEND_SAMPLES = 50


def assess_readiness(store=None) -> dict:
    """Assess every learner. Never raises — a component that errors reports
    state 'ERROR' with the message, and the others still assess."""
    out: dict = {"components": {}, "resolved_samples": 0, "recommendations": []}

    # Resolved-outcome sample base (shared denominator).
    decisions = []
    try:
        from bot.learning.store import LearningStore
        decisions = (store or LearningStore()).get_decisions(limit=5000)
    except Exception as exc:
        log.debug("readiness: store unavailable: %s", exc)

    # -- confidence calibration ------------------------------------------------
    comp: dict = {"flag": "AUTO_CONFIRM_USE_CALIBRATED"}
    try:
        from bot.config import CONFIG
        from bot.learning.confidence_calibration import ConfidenceCalibrator
        samples = ConfidenceCalibrator.samples_from_decisions(decisions)
        out["resolved_samples"] = len(samples)
        cal = ConfidenceCalibrator.load()
        n = getattr(cal, "_n_samples", 0) if cal else 0
        need = getattr(cal, "min_samples", 30) if cal else 30
        comp.update(samples=max(n, len(samples)), needed=need,
                    applied=CONFIG.auto_confirm_use_calibrated)
        if cal is None or not cal.is_ready():
            comp["state"] = "ACCUMULATING"
        elif max(n, len(samples)) < _CAL_RECOMMEND_SAMPLES:
            comp["state"] = "VALIDATING"
            comp["note"] = (f"fitted, but curve rests on {n} samples — "
                            f"recommend >= {_CAL_RECOMMEND_SAMPLES} before applying")
        else:
            comp["state"] = "READY"
            comp["note"] = cal.summary()
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["calibration"] = comp

    # -- voter weights -----------------------------------------------------------
    comp = {"flag": "VOTER_WEIGHT_LEARNING_ENABLED"}
    try:
        from bot.config import CONFIG
        from bot.learning.voter_weights import VoterWeightLearner
        learner = VoterWeightLearner()
        samples = learner.load_samples(store)
        comp.update(samples=len(samples), needed=learner.min_samples,
                    applied=CONFIG.analyzer.voter_weight_learning_enabled)
        if len(samples) < learner.min_samples:
            comp["state"] = "ACCUMULATING"
        else:
            oos = learner.validate_oos(samples)
            comp["oos_hold_rate"] = oos.get("hold_rate", 0.0)
            comp["oos_n_test"] = oos.get("n_test", 0)
            if oos.get("n_test", 0) and oos["hold_rate"] >= _VW_HOLD_RATE_BAR:
                comp["state"] = "READY"
                comp["note"] = (f"OOS hold rate {oos['hold_rate']:.0%} on "
                                f"{oos['n_test']} unseen trades (bar {_VW_HOLD_RATE_BAR:.0%})")
            else:
                comp["state"] = "VALIDATING"
                comp["note"] = (f"OOS hold rate {oos.get('hold_rate', 0.0):.0%} "
                                f"< bar {_VW_HOLD_RATE_BAR:.0%} — learned directions "
                                "do not generalize yet")
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["voter_weights"] = comp

    # -- setup expectancy ----------------------------------------------------------
    comp = {"flag": "(auto-applies when ready)"}
    try:
        from bot.learning.setup_expectancy import get_setup_expectancy
        se = get_setup_expectancy(reload=True)
        comp.update(setups=len(getattr(se, "_table", {}) or {}),
                    applied=se.is_ready())
        comp["state"] = "READY" if se.is_ready() else "ACCUMULATING"
        comp["note"] = se.summary()
    except Exception as exc:
        comp.update(state="ERROR", note=str(exc)[:160])
    out["components"]["setup_expectancy"] = comp

    # -- recommendations -------------------------------------------------------
    for name, c in out["components"].items():
        if c.get("state") == "READY" and c.get("applied") is False:
            out["recommendations"].append(
                f"{name} is validated but not applied — consider {c['flag']}=true")
        if c.get("state") == "READY" and c.get("applied") is True:
            out["recommendations"].append(f"{name}: applied and validated ✓")
    return out


def render_report(assessment: dict) -> str:
    """Telegram-HTML readiness report."""
    icon = {"READY": "✅", "VALIDATING": "\U0001f7e0",
            "ACCUMULATING": "⏳", "ERROR": "⚠️"}
    lines = ["\U0001f9e0 <b>Learning Readiness</b>", "─" * 28,
             f"Resolved outcomes: <code>{assessment.get('resolved_samples', 0)}</code>", ""]
    for name, c in assessment.get("components", {}).items():
        state = c.get("state", "?")
        head = f"{icon.get(state, '')} <b>{name}</b>: {state}"
        if "samples" in c and "needed" in c and state == "ACCUMULATING":
            head += f" ({c['samples']}/{c['needed']})"
        lines.append(head)
        if c.get("note"):
            lines.append(f"   {c['note']}")
        lines.append(f"   apply flag: <code>{c.get('flag')}</code>"
                     + (" — ON" if c.get("applied") is True else ""))
    recs = assessment.get("recommendations", [])
    if recs:
        lines += ["", "<b>Recommended:</b>"]
        lines += [f"• {r}" for r in recs]
    else:
        lines += ["", "No action yet — keep accumulating closes."]
    return "\n".join(lines)
