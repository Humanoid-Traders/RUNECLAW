"""
RUNECLAW Compliance Engine — Five-Lock Authorization Layer.

Fail-closed design: a live trade requires ALL five locks to pass.
Missing any lock results in DENIED with the failing lock named.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Permission(Enum):
    """Granular permission levels for trading subjects."""
    READ_ONLY = "READ_ONLY"
    ANALYSIS = "ANALYSIS"
    PAPER_TRADE = "PAPER_TRADE"
    LIVE_TRADE = "LIVE_TRADE"
    ADMIN = "ADMIN"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SubjectProfile:
    """Identity and capability envelope for a trading subject."""
    subject_id: str
    permissions: Set[Permission]
    jurisdiction: str = "US"
    max_notional_usd: float = 10_000.0
    kyc_verified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ApprovalToken:
    """One-time, expiring, trade-bound human approval token."""
    token_id: str
    trade_id: str
    subject_id: str
    issued_at: datetime
    expires_at: datetime = field(init=False)
    used: bool = False

    _TTL_MINUTES: int = field(default=5, init=False, repr=False)

    def __post_init__(self) -> None:
        self.expires_at = self.issued_at + timedelta(minutes=self._TTL_MINUTES)


@dataclass
class AuthorizationDecision:
    """Immutable record of an authorization outcome."""
    granted: bool
    reasons: List[str]
    locks_passed: List[str]
    locks_failed: List[str]
    timestamp: datetime
    trade_id: Optional[str] = None


# ---------------------------------------------------------------------------
# ComplianceEngine
# ---------------------------------------------------------------------------

class ComplianceEngine:
    """Five-lock, fail-closed authorization gate for RUNECLAW trades."""

    _DEFAULT_RESTRICTED: Set[str] = {"KP", "IR", "SY", "CU", "RU"}

    def __init__(self, restricted_jurisdictions: Optional[Set[str]] = None) -> None:
        self._restricted: Set[str] = (
            restricted_jurisdictions
            if restricted_jurisdictions is not None
            else set(self._DEFAULT_RESTRICTED)
        )
        self._consent_ledger: List[AuthorizationDecision] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def authorize(
        self,
        action: Permission,
        profile: SubjectProfile,
        live_mode: bool,
        risk_passed: bool,
        macro_ok: bool,
        notional_usd: float,
        trade_id: Optional[str] = None,
        approval_token: Optional[ApprovalToken] = None,
    ) -> AuthorizationDecision:
        """Evaluate all applicable locks and return a decision."""

        # Jurisdiction gate — hard block regardless of mode
        if profile.jurisdiction in self._restricted:
            decision = AuthorizationDecision(
                granted=False,
                reasons=[
                    f"Jurisdiction {profile.jurisdiction!r} is restricted"
                ],
                locks_passed=[],
                locks_failed=["jurisdiction"],
                timestamp=datetime.now(timezone.utc),
                trade_id=trade_id,
            )
            self._consent_ledger.append(decision)
            return decision

        if live_mode:
            return self._authorize_live(
                action, profile, risk_passed, macro_ok,
                notional_usd, trade_id, approval_token,
            )
        return self._authorize_paper(action, profile, risk_passed, trade_id)

    def issue_approval_token(
        self, trade_id: str, subject_id: str
    ) -> ApprovalToken:
        """Create a one-time human approval token valid for 5 minutes."""
        return ApprovalToken(
            token_id=str(uuid.uuid4()),
            trade_id=trade_id,
            subject_id=subject_id,
            issued_at=datetime.now(timezone.utc),
        )

    def validate_token(
        self,
        token: ApprovalToken,
        trade_id: str,
        subject_id: str,
    ) -> Tuple[bool, str]:
        """Check expiry, binding, and single-use constraints."""
        now = datetime.now(timezone.utc)

        if token.used:
            return False, "Token already used"
        if token.trade_id != trade_id:
            return False, (
                f"Token bound to trade {token.trade_id!r}, "
                f"not {trade_id!r}"
            )
        if token.subject_id != subject_id:
            return False, (
                f"Token bound to subject {token.subject_id!r}, "
                f"not {subject_id!r}"
            )
        if now >= token.expires_at:
            return False, "Token expired"

        return True, "Token valid"

    def get_consent_ledger(self) -> List[AuthorizationDecision]:
        """Return the append-only consent ledger."""
        return list(self._consent_ledger)

    def format_for_telegram(self) -> str:
        """Return an HTML status summary suitable for Telegram."""
        total = len(self._consent_ledger)
        granted = sum(1 for d in self._consent_ledger if d.granted)
        denied = total - granted

        lines = [
            "<b>RUNECLAW Compliance Status</b>",
            "",
            f"Total decisions: <code>{total}</code>",
            f"Granted: <code>{granted}</code>",
            f"Denied: <code>{denied}</code>",
            f"Restricted jurisdictions: <code>{', '.join(sorted(self._restricted))}</code>",
        ]

        if self._consent_ledger:
            last = self._consent_ledger[-1]
            outcome = "GRANTED" if last.granted else "DENIED"
            lines.append("")
            lines.append(f"<b>Last decision:</b> {outcome}")
            if last.trade_id:
                lines.append(f"Trade: <code>{last.trade_id}</code>")
            if last.locks_failed:
                lines.append(
                    f"Failed locks: <code>{', '.join(last.locks_failed)}</code>"
                )
            if last.reasons:
                lines.append(
                    f"Reasons: {'; '.join(last.reasons)}"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _authorize_paper(
        self,
        action: Permission,
        profile: SubjectProfile,
        risk_passed: bool,
        trade_id: Optional[str],
    ) -> AuthorizationDecision:
        """Paper trades require PAPER_TRADE permission + risk pass."""
        passed: List[str] = []
        failed: List[str] = []
        reasons: List[str] = []

        if Permission.PAPER_TRADE in profile.permissions:
            passed.append("permission")
        else:
            failed.append("permission")
            reasons.append("Subject lacks PAPER_TRADE permission")

        if risk_passed:
            passed.append("risk")
        else:
            failed.append("risk")
            reasons.append("Risk engine did not approve")

        granted = len(failed) == 0
        decision = AuthorizationDecision(
            granted=granted,
            reasons=reasons,
            locks_passed=passed,
            locks_failed=failed,
            timestamp=datetime.now(timezone.utc),
            trade_id=trade_id,
        )
        self._consent_ledger.append(decision)
        return decision

    def _authorize_live(
        self,
        action: Permission,
        profile: SubjectProfile,
        risk_passed: bool,
        macro_ok: bool,
        notional_usd: float,
        trade_id: Optional[str],
        approval_token: Optional[ApprovalToken],
    ) -> AuthorizationDecision:
        """Live trades require all five locks."""
        passed: List[str] = []
        failed: List[str] = []
        reasons: List[str] = []

        # Lock 1 — Permission
        if Permission.LIVE_TRADE in profile.permissions:
            passed.append("permission")
        else:
            failed.append("permission")
            reasons.append("Subject lacks LIVE_TRADE permission")

        # Lock 2 — Risk
        if risk_passed:
            passed.append("risk")
        else:
            failed.append("risk")
            reasons.append("Risk engine did not approve")

        # Lock 3 — Macro
        if macro_ok:
            passed.append("macro")
        else:
            failed.append("macro")
            reasons.append("Macro window is in BLOCK state")

        # Lock 4 — Notional cap
        if notional_usd <= profile.max_notional_usd:
            passed.append("notional_cap")
        else:
            failed.append("notional_cap")
            reasons.append(
                f"Notional ${notional_usd:,.2f} exceeds cap "
                f"${profile.max_notional_usd:,.2f}"
            )

        # Lock 5 — Human approval token
        if approval_token is not None and trade_id is not None:
            tok_ok, tok_msg = self.validate_token(
                approval_token, trade_id, profile.subject_id,
            )
            if tok_ok:
                passed.append("human_approval")
                approval_token.used = True
            else:
                failed.append("human_approval")
                reasons.append(f"Approval token invalid: {tok_msg}")
        else:
            failed.append("human_approval")
            if approval_token is None:
                reasons.append("No human approval token provided")
            else:
                reasons.append("trade_id required for token validation")

        granted = len(failed) == 0
        decision = AuthorizationDecision(
            granted=granted,
            reasons=reasons,
            locks_passed=passed,
            locks_failed=failed,
            timestamp=datetime.now(timezone.utc),
            trade_id=trade_id,
        )
        self._consent_ledger.append(decision)
        return decision


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def default_demo_profile() -> SubjectProfile:
    """Return a paper-only demo profile — live trading structurally impossible."""
    return SubjectProfile(
        subject_id="demo-user",
        permissions={Permission.READ_ONLY, Permission.ANALYSIS, Permission.PAPER_TRADE},
        jurisdiction="US",
        max_notional_usd=10_000.0,
        kyc_verified=False,
    )
