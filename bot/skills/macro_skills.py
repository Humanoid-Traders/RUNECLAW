"""
RUNECLAW v2 macro-aware skills.

Integrates macro calendar, compliance, approval tokens, and kill-switch
into the Telegram skill system.
"""
from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import TYPE_CHECKING, List

# Lazy import — skill_registry lives in the same package tree.
try:
    from bot.skills.skill_registry import BaseSkill
except ImportError:
    # Fallback for standalone testing / linting.
    class BaseSkill:  # type: ignore[no-redef]
        name: str = ""
        description: str = ""
        command: str = ""
        async def execute(self, engine, **kwargs) -> str:
            raise NotImplementedError

if TYPE_CHECKING:
    pass  # forward refs only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _html_bold(text: str) -> str:
    return f"<b>{text}</b>"


def _safe_getattr(obj, attr, fallback=None):
    """getattr that also catches AttributeError inside properties."""
    try:
        return getattr(obj, attr, fallback)
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# 1. MacroBriefSkill
# ---------------------------------------------------------------------------

class MacroBriefSkill(BaseSkill):
    name = "macro_brief"
    description = "Current macro window status, next events, and risk state."
    command = "/macro"

    async def execute(self, engine, **kwargs) -> str:
        provider = _safe_getattr(engine, "macro_provider")
        calendar = _safe_getattr(engine, "macro_calendar")

        if provider is None and calendar is None:
            return (
                f"{_html_bold('Macro Brief')}\n"
                "v2 macro provider not wired — no macro data available."
            )

        source = provider or calendar
        lines: list[str] = [_html_bold("Macro Brief")]

        # -- risk state --
        risk_state = _safe_getattr(source, "risk_state", "UNKNOWN")
        lines.append(f"Risk state: <code>{risk_state}</code>")

        # -- current window --
        window = _safe_getattr(source, "current_window")
        if window:
            lines.append(f"Active window: {window}")
        else:
            lines.append("No active macro window.")

        # -- next events --
        upcoming = _safe_getattr(source, "upcoming_events")
        if callable(upcoming):
            try:
                upcoming = upcoming(limit=5)
            except TypeError:
                upcoming = upcoming()
        if upcoming:
            lines.append("")
            lines.append(_html_bold("Next events:"))
            for ev in upcoming[:5]:
                label = getattr(ev, "label", str(ev))
                scheduled = getattr(ev, "scheduled_utc", "")
                severity = getattr(ev, "severity", "")
                lines.append(f"  - {label}  ({scheduled})  [{severity}]")
        else:
            lines.append("No upcoming events loaded.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. CheckEventRiskSkill
# ---------------------------------------------------------------------------

class CheckEventRiskSkill(BaseSkill):
    name = "check_event_risk"
    description = "Check macro-event risk for a given symbol."
    command = "/eventrisk"

    async def execute(self, engine, **kwargs) -> str:
        symbol = kwargs.get("symbol", "").upper()
        if not symbol:
            return "Usage: /eventrisk &lt;SYMBOL&gt;"

        provider = _safe_getattr(engine, "macro_provider")
        calendar = _safe_getattr(engine, "macro_calendar")

        if provider is None and calendar is None:
            return (
                f"{_html_bold('Event Risk')} — {symbol}\n"
                "v2 macro provider not wired — cannot evaluate event risk."
            )

        source = provider or calendar

        # Try dedicated risk-check method first.
        check_fn = _safe_getattr(source, "check_risk")
        if check_fn and callable(check_fn):
            try:
                result = check_fn(symbol)
            except Exception as exc:
                return f"Error checking risk for {symbol}: {exc}"

            severity = getattr(result, "severity", _safe_getattr(result, "get", lambda k, d=None: d)("severity", "N/A"))
            window = getattr(result, "window", _safe_getattr(result, "get", lambda k, d=None: d)("window", "none"))
            multiplier = getattr(result, "size_multiplier", _safe_getattr(result, "get", lambda k, d=None: d)("size_multiplier", 1.0))
            explanation = getattr(result, "explanation", _safe_getattr(result, "get", lambda k, d=None: d)("explanation", ""))

            lines = [
                f"{_html_bold('Event Risk')} — {symbol}",
                f"Severity:        <code>{severity}</code>",
                f"Window:          <code>{window}</code>",
                f"Size multiplier: <code>{multiplier}</code>",
                f"Explanation:     {explanation}",
            ]
            return "\n".join(lines)

        # Fallback: no check_risk method.
        return (
            f"{_html_bold('Event Risk')} — {symbol}\n"
            "Macro source has no <code>check_risk()</code> method. "
            "Ensure the v2 macro provider is wired."
        )


# ---------------------------------------------------------------------------
# 3. ComplianceStatusSkill
# ---------------------------------------------------------------------------

class ComplianceStatusSkill(BaseSkill):
    name = "compliance_status"
    description = "Current compliance profile and consent ledger summary."
    command = "/compliance"

    async def execute(self, engine, **kwargs) -> str:
        compliance = _safe_getattr(engine, "compliance")
        if compliance is None:
            return (
                f"{_html_bold('Compliance Status')}\n"
                "v2 compliance module not wired."
            )

        lines = [_html_bold("Compliance Status")]

        # -- profile permissions --
        profile = _safe_getattr(compliance, "profile")
        if profile:
            permissions = _safe_getattr(profile, "permissions", {})
            lines.append("")
            lines.append(_html_bold("Permissions:"))
            if isinstance(permissions, dict):
                for perm, allowed in permissions.items():
                    flag = "YES" if allowed else "NO"
                    lines.append(f"  {perm}: <code>{flag}</code>")
            else:
                lines.append(f"  {permissions}")
        else:
            lines.append("No compliance profile loaded.")

        # -- consent ledger --
        ledger = _safe_getattr(compliance, "consent_ledger")
        if ledger:
            entries_fn = _safe_getattr(ledger, "recent")
            entries = []
            if callable(entries_fn):
                try:
                    entries = entries_fn(limit=5)
                except TypeError:
                    entries = entries_fn()
            elif isinstance(ledger, (list, tuple)):
                entries = ledger[-5:]

            if entries:
                lines.append("")
                lines.append(_html_bold("Consent Ledger (last 5):"))
                for entry in entries:
                    ts = getattr(entry, "timestamp", "")
                    action = getattr(entry, "action", str(entry))
                    lines.append(f"  [{ts}] {action}")
            else:
                lines.append("Consent ledger is empty.")
        else:
            lines.append("No consent ledger available.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. RequestLiveApprovalSkill
# ---------------------------------------------------------------------------

class RequestLiveApprovalSkill(BaseSkill):
    name = "request_live_approval"
    description = "Issue a one-time approval token for a pending trade."
    command = "/approve"

    async def execute(self, engine, **kwargs) -> str:
        trade_id = kwargs.get("trade_id", "")
        if not trade_id:
            return "Usage: /approve &lt;trade_id&gt;"

        approvals = _safe_getattr(engine, "approval_manager")
        if approvals is None:
            return (
                f"{_html_bold('Live Approval')}\n"
                "v2 approval manager not wired."
            )

        issue_fn = _safe_getattr(approvals, "issue_token")
        if issue_fn is None or not callable(issue_fn):
            return (
                f"{_html_bold('Live Approval')}\n"
                "Approval manager has no <code>issue_token()</code> method."
            )

        try:
            token = issue_fn(trade_id=trade_id)
        except Exception as exc:
            return f"Failed to issue approval token: {exc}"

        token_id = getattr(token, "token_id", _safe_getattr(token, "get", lambda k, d=None: d)("token_id", str(_uuid.uuid4())[:8]))
        expiry = getattr(token, "expires_utc", _safe_getattr(token, "get", lambda k, d=None: d)("expires_utc", "N/A"))
        one_time = getattr(token, "one_time", True)

        lines = [
            f"{_html_bold('Approval Token Issued')}",
            f"Trade ID:  <code>{trade_id}</code>",
            f"Token:     <code>{token_id}</code>",
            f"Expires:   <code>{expiry}</code>",
            f"One-time:  <code>{'yes' if one_time else 'no'}</code>",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. KillSwitchSkill
# ---------------------------------------------------------------------------

class KillSwitchSkill(BaseSkill):
    name = "kill_switch"
    description = "Trip the circuit breaker immediately and seal to audit chain."
    command = "/kill"

    async def execute(self, engine, **kwargs) -> str:
        breaker = _safe_getattr(engine, "circuit_breaker")
        if breaker is None:
            return (
                f"{_html_bold('KILL SWITCH')}\n"
                "v2 circuit breaker not wired."
            )

        trip_fn = _safe_getattr(breaker, "trip")
        if trip_fn is None or not callable(trip_fn):
            return (
                f"{_html_bold('KILL SWITCH')}\n"
                "Circuit breaker has no <code>trip()</code> method."
            )

        reason = kwargs.get("reason", "Manual kill via /kill command")
        try:
            trip_fn(reason=reason)
        except Exception as exc:
            return f"KILL SWITCH FAILED: {exc}"

        # Seal event to audit chain if available.
        audit = _safe_getattr(engine, "audit_chain")
        seal_ts = _now_utc().isoformat()
        if audit:
            seal_fn = _safe_getattr(audit, "seal")
            if seal_fn and callable(seal_fn):
                try:
                    seal_fn(event="KILL_SWITCH", reason=reason, timestamp=seal_ts)
                except Exception:
                    pass  # best-effort audit

        lines = [
            f"{_html_bold('KILL SWITCH ACTIVATED')}",
            f"Time:   <code>{seal_ts}</code>",
            f"Reason: {reason}",
            "All positions frozen. Circuit breaker is OPEN.",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_v2_skills() -> List[BaseSkill]:
    """Return all v2 macro-aware skill instances."""
    return [
        MacroBriefSkill(),
        CheckEventRiskSkill(),
        ComplianceStatusSkill(),
        RequestLiveApprovalSkill(),
        KillSwitchSkill(),
    ]
