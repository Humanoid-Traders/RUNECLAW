"""Guardian least-privilege preflight + custody red-team.

Pins the honest reconciliation statuses (CONFIRMED / VIOLATION / UNVERIFIED) and
proves the custody red team denies every envelope-crossing attack while allowing
the in-bounds control action.
"""
from bot.guardian import authority as auth
from bot.guardian import authority_preflight as pf
from bot.guardian import authority_redteam as rt


def _env(**over):
    spec = {"allowed_venues": ["bitget"], "allowed_market_types": ["swap"],
            "max_notional_per_trade_usd": 1000}
    spec.update(over)
    return auth.compile_envelope(spec)


# ── preflight reconciliation ──────────────────────────────────────────

def test_read_confirmed_and_missing_read_is_violation():
    ok = pf.reconcile_posture(_env(), {"read": True})
    read = next(d for d in ok["dimensions"] if d["dimension"] == "read")
    assert read["status"] == pf.CONFIRMED

    bad = pf.reconcile_posture(_env(), {"read": False})
    assert bad["ok"] is False
    read2 = next(d for d in bad["dimensions"] if d["dimension"] == "read")
    assert read2["status"] == pf.VIOLATION


def test_withdraw_unknown_is_unverified_not_pass():
    # No withdraw evidence → UNVERIFIED (honest), and it does NOT fail the preflight.
    rep = pf.reconcile_posture(_env(), {"read": True, "withdraw": "unknown"})
    wd = next(d for d in rep["dimensions"] if d["dimension"] == "withdraw")
    assert wd["status"] == pf.UNVERIFIED
    assert rep["ok"] is True   # unverified is surfaced, not blocking
    assert "unverified" in rep["summary"].lower()


def test_over_privileged_key_is_violation():
    # Envelope forbids withdrawal, but the key CAN withdraw → over-privileged → VIOLATION.
    rep = pf.reconcile_posture(_env(), {"read": True, "withdraw": "on"})
    wd = next(d for d in rep["dimensions"] if d["dimension"] == "withdraw")
    assert wd["status"] == pf.VIOLATION
    assert rep["ok"] is False
    assert "OVER-PRIVILEGED" in wd["detail"]


def test_withdraw_off_matches_noncustodial_intent():
    rep = pf.reconcile_posture(_env(), {"read": True, "withdraw": "off"})
    wd = next(d for d in rep["dimensions"] if d["dimension"] == "withdraw")
    assert wd["status"] == pf.CONFIRMED


def test_environment_mismatch_is_violation():
    rep = pf.reconcile_posture(_env(), {
        "read": True, "environment": "live", "expected_environment": "demo"})
    env_dim = next(d for d in rep["dimensions"] if d["dimension"] == "environment")
    assert env_dim["status"] == pf.VIOLATION
    assert rep["ok"] is False


# ── custody red team ──────────────────────────────────────────────────

def test_authority_redteam_denies_every_attack():
    report = rt.run_authority_redteam()
    # every scenario must be handled correctly (attacks denied, control allowed)
    assert report["failed"] == 0, [s for s in report["scenarios"] if not s["passed"]]
    assert report["pass_rate"] == 100.0
    # the control action proves the gate is not just deny-everything
    control = next(s for s in report["scenarios"] if s["name"] == "control_in_bounds_trade")
    assert control["actual"] == "allow"
    # the injection attack is denied because compile clamped the forged cap
    inj = next(s for s in report["scenarios"] if s["name"] == "injection_raise_the_limit")
    assert inj["actual"] == "deny"
    # every non-control scenario is an attack expected to be denied
    attacks = [s for s in report["scenarios"] if s["category"] != "control"]
    assert all(s["expected"] == "deny" for s in attacks)
    assert all(s["actual"] == "deny" for s in attacks)
