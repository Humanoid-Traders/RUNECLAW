"""RUNECLAW AI Learning Safety Policy.

Hard-coded safety rules that AI learning may NEVER bypass.
Safety beats performance. Compliance beats speed.
Auditability beats black-box automation. If uncertain, fail closed.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import ChangeClassification, ImprovementProposal

logger = logging.getLogger("runeclaw.learning.safety")


# ── Immutable Block-List ───────────────────────────────────────────────
# Actions that AI learning may NEVER perform automatically.

BLOCKED_ACTIONS: frozenset[str] = frozenset({
    "increase_leverage",
    "remove_stop_loss",
    "disable_risk_checks",
    "reduce_audit_logging",
    "enable_live_trading",
    "add_risky_symbol",
    "bypass_macro_lockdown",
    "modify_api_permissions",
    "delete_audit_logs",
    "rewrite_historical_results",
    "use_future_data",
    "claim_guaranteed_profit",
    "remove_loss_limits",
    "remove_max_drawdown_limits",
    "remove_human_approval",
    "hide_losses",
    "overfit_to_small_samples",
})

# Actions AI learning MAY perform (still audited)
ALLOWED_AUTO_ACTIONS: frozenset[str] = frozenset({
    "improve_explanation",
    "improve_documentation",
    "suggest_safer_filter",
    "suggest_better_test",
    "improve_paper_analysis",
    "detect_recurring_mistake",
    "rank_strategies_for_review",
    "produce_improvement_proposal",
})

# Keywords that indicate risk-increasing proposals
RISK_INCREASE_KEYWORDS: frozenset[str] = frozenset({
    "leverage", "margin", "remove stop", "disable check",
    "skip risk", "bypass", "force trade", "override",
    "increase size", "remove limit", "live trading",
    "real money", "production", "no confirmation",
})

# Keywords that indicate safety-weakening prompt changes
UNSAFE_PROMPT_KEYWORDS: frozenset[str] = frozenset({
    "guaranteed profit", "always profitable", "risk-free",
    "no stop loss needed", "ignore risk", "skip safety",
    "bypass confirmation", "auto-execute", "force buy",
    "force sell", "no human", "disable audit",
})


def classify_proposal(proposal: ImprovementProposal) -> str:
    """Classify an improvement proposal by safety level.

    Returns the ChangeClassification string. This is the ONLY path
    through which AI learning changes are classified.

    Rules (in order of precedence):
    1. BLOCKED_RISK_INCREASE — if proposal touches blocked actions
    2. BLOCKED_COMPLIANCE_RISK — if proposal weakens safety wording
    3. SAFE_AUTO_DOCS — documentation-only changes
    4. SAFE_AUTO_TEST — test-only changes
    5. HUMAN_REVIEW_REQUIRED — everything else
    """
    text = (
        f"{proposal.proposed_change} {proposal.expected_benefit} "
        f"{proposal.risk_impact} {proposal.problem}"
    ).lower()

    # Rule 1: Check for blocked actions
    for keyword in RISK_INCREASE_KEYWORDS:
        if keyword in text:
            proposal.classification = ChangeClassification.BLOCKED_RISK_INCREASE.value
            proposal.human_approval_required = True
            logger.warning(
                "BLOCKED proposal %s: risk-increase keyword '%s'",
                proposal.audit_id, keyword,
            )
            return proposal.classification

    # Rule 2: Check for compliance risk
    for keyword in UNSAFE_PROMPT_KEYWORDS:
        if keyword in text:
            proposal.classification = ChangeClassification.BLOCKED_COMPLIANCE_RISK.value
            proposal.human_approval_required = True
            logger.warning(
                "BLOCKED proposal %s: compliance-risk keyword '%s'",
                proposal.audit_id, keyword,
            )
            return proposal.classification

    # Rule 3: Documentation-only
    doc_indicators = {"documentation", "docstring", "readme", "comment", "gitbook", "docs"}
    if any(ind in text for ind in doc_indicators):
        code_indicators = {"def ", "class ", "import ", "return ", "raise "}
        if not any(ci in text for ci in code_indicators):
            proposal.classification = ChangeClassification.SAFE_AUTO_DOCS.value
            proposal.human_approval_required = False
            return proposal.classification

    # Rule 4: Test-only (must not also contain code-modifying language)
    test_indicators = {"test_", "assert", "pytest", "unittest", "test case", "test plan"}
    code_indicators_test = {"def ", "class ", "import ", "return ", "raise ", "modify ", "change ", "update "}
    if any(ind in text for ind in test_indicators):
        if not any(ci in text for ci in code_indicators_test):
            proposal.classification = ChangeClassification.SAFE_AUTO_TEST.value
            proposal.human_approval_required = False
            return proposal.classification

    # Rule 5: Default — human review
    proposal.classification = ChangeClassification.HUMAN_REVIEW_REQUIRED.value
    proposal.human_approval_required = True
    return proposal.classification


def validate_prompt_safety(prompt_text: str) -> tuple[bool, list[str]]:
    """Check that a prompt does not contain unsafe wording.

    Returns (is_safe, list_of_violations).
    """
    violations: list[str] = []
    lower = prompt_text.lower()

    for keyword in UNSAFE_PROMPT_KEYWORDS:
        if keyword in lower:
            violations.append(f"Unsafe keyword found: '{keyword}'")

    # Must contain fail-closed language
    fail_closed_terms = ["fail-closed", "fail closed", "reject", "block"]
    if not any(t in lower for t in fail_closed_terms):
        violations.append("Missing fail-closed safety language")

    return (len(violations) == 0, violations)


def validate_learning_action(action: str) -> bool:
    """Check whether a learning action is allowed.

    Returns True if action is in the allowed list.
    Returns False and logs if action is blocked.
    """
    if action in BLOCKED_ACTIONS:
        logger.error("BLOCKED learning action: %s", action)
        return False
    if action in ALLOWED_AUTO_ACTIONS:
        return True
    # Unknown action → fail closed
    logger.warning("Unknown learning action '%s' — defaulting to BLOCKED", action)
    return False


def audit_proposal(proposal: ImprovementProposal) -> dict[str, Any]:
    """Generate audit record for a proposal. Always called before apply."""
    return {
        "audit_id": proposal.audit_id,
        "timestamp_utc": proposal.timestamp_utc.isoformat(),
        "classification": proposal.classification,
        "human_approval_required": proposal.human_approval_required,
        "problem": proposal.problem[:200],
        "proposed_change": proposal.proposed_change[:200],
        "risk_impact": proposal.risk_impact[:200],
        "status": proposal.status,
    }
