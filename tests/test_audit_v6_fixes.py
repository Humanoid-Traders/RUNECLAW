"""
Regression tests for the V6 deep-audit fixes (docs/AUDIT_REPORT_V6.md).

Covers:
  RC-AUD-V6-1 — BacktestValidationGate.get_all_validations() no longer
                self-deadlocks on its non-reentrant lock.
  RC-AUD-V6-2 — PatternRecord.may_override_risk safety invariant is enforced
                on post-construction assignment, not just at construction.
  RC-AUD-V6-3 — Restricted-jurisdiction hard block is casing/whitespace
                insensitive on both sides.
  RC-AUD-V6-4 — MultiUserPortfolio uses one canonical (sanitized) key across
                get()/has_user()/_load_existing(), so a user portfolio is never
                silently recreated (wiping balance/positions) on re-access.
  RC-AUD-V6-5 — A liquidation close arms the per-symbol re-entry cooldown.
"""
import threading

import pytest

from bot.compliance.compliance_engine import (
    ComplianceEngine,
    Permission,
    SubjectProfile,
)
from bot.core.validation_gate import BacktestValidationGate
from bot.learning.models import PatternRecord
from bot.risk.multi_portfolio import MultiUserPortfolio

# ── V6-1: validation gate must not deadlock ─────────────────────────

def test_get_all_validations_does_not_deadlock():
    """get_all_validations() previously re-acquired its own non-reentrant lock
    via get_validation_status() and hung forever.  Guard with a watchdog
    thread so a regression fails fast instead of hanging the suite."""
    gate = BacktestValidationGate()
    gate.record_validation("alpha", sharpe=1.2, max_drawdown=5.0,
                           win_rate=0.6, total_trades=50, walk_forward_score=0.8)
    gate.record_validation("beta", sharpe=0.3, max_drawdown=12.0,
                           win_rate=0.4, total_trades=8, walk_forward_score=0.2)

    result: dict = {}

    def _run():
        result["all"] = gate.get_all_validations()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "get_all_validations() deadlocked"
    assert set(result["all"]) == {"alpha", "beta"}
    assert result["all"]["alpha"]["validated"] is True
    assert result["all"]["beta"]["validated"] is False


# ── V6-2: may_override_risk invariant enforced at runtime ───────────

def test_pattern_may_override_risk_rejected_at_construction():
    with pytest.raises(Exception):
        PatternRecord(pattern_type="x", may_override_risk=True)


def test_pattern_may_override_risk_rejected_on_assignment():
    """The documented 'cannot be bypassed at runtime' guarantee must hold for
    post-construction assignment too."""
    p = PatternRecord(pattern_type="x")
    assert p.may_override_risk is False
    with pytest.raises(Exception):
        p.may_override_risk = True
    assert p.may_override_risk is False


# ── V6-3: restricted-jurisdiction casing/whitespace ─────────────────

@pytest.mark.parametrize("juris", ["RU", "ru", "Ru", " RU", "RU ", " ru "])
def test_restricted_jurisdiction_blocked_regardless_of_casing(juris):
    eng = ComplianceEngine()
    profile = SubjectProfile(
        subject_id="s1",
        permissions={Permission.LIVE_TRADE},
        jurisdiction=juris,
    )
    decision = eng.authorize(
        action=Permission.LIVE_TRADE,
        profile=profile,
        live_mode=True,
        risk_passed=True,
        macro_ok=True,
        notional_usd=100.0,
    )
    assert decision.granted is False
    assert "jurisdiction" in decision.locks_failed


def test_custom_restricted_set_is_normalized():
    eng = ComplianceEngine(restricted_jurisdictions={"gb"})
    profile = SubjectProfile(
        subject_id="s2",
        permissions={Permission.LIVE_TRADE},
        jurisdiction="GB",
    )
    decision = eng.authorize(
        action=Permission.LIVE_TRADE, profile=profile, live_mode=True,
        risk_passed=True, macro_ok=True, notional_usd=100.0,
    )
    assert decision.granted is False


# ── V6-4: multi-portfolio canonical key ─────────────────────────────

def test_multi_portfolio_key_is_stable_across_access():
    """A user_id that changes under sanitization must resolve to the SAME
    tracker on every access, not a freshly-wiped one."""
    multi = MultiUserPortfolio(default_balance=10_000.0)
    p1 = multi.get("user.123")           # sanitizes to "user123"
    p1.balance = 4242.0                  # mutate to detect recreation
    assert multi.has_user("user.123") is True
    p2 = multi.get("user.123")
    assert p1 is p2, "portfolio was recreated (state wiped) on re-access"
    assert p2.balance == 4242.0


def test_multi_portfolio_empty_id_rejected():
    multi = MultiUserPortfolio(default_balance=10_000.0)
    with pytest.raises(ValueError):
        multi.get("!!!")  # empty after sanitization
    assert multi.has_user("!!!") is False


# ── V6-5: liquidation arms per-symbol re-entry cooldown ─────────────

@pytest.mark.parametrize("reason,expected", [
    ("SL hit", True),
    ("STOP_LOSS", True),
    ("LIQUIDATED", True),     # most adverse close — previously missed
    ("TP hit", False),
    ("manual", False),
])
def test_liquidation_and_sl_arm_symbol_cooldown(reason, expected):
    """A liquidation must arm the per-symbol cooldown just like an SL hit; a
    profitable/manual close must not.  Tested against the real handler via a
    duck-typed self to avoid constructing the full engine."""
    from types import SimpleNamespace

    from bot.core.engine import RuneClawEngine, normalize_symbol

    fake = SimpleNamespace(
        _symbol_cooldowns={},
        _symbol_cooldown_seconds=1800.0,
        _invalidate_live_balance_cache=lambda: None,
    )
    pos = SimpleNamespace(close_reason=reason, symbol="BTC/USDT")
    RuneClawEngine._on_live_position_closed(fake, pos)
    armed = normalize_symbol("BTC/USDT") in fake._symbol_cooldowns
    assert armed is expected
