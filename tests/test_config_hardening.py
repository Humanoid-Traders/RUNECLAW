"""
Regression tests for config hardening (CFG-1, CFG-2) from
docs/AUDIT_REPORT_V6.1.md.

CFG-1 — `_env_float` rejects non-finite values (inf/nan parse without error and a
        non-finite risk limit silently disables guards: x > nan is always False).
CFG-2 — risk-gate limits are clamped, so a negative value (which inverts the
        comparison) or an absurd typo cannot load.
"""
import subprocess
import sys

import pytest

from bot.config import _env_float, _env_float_bounded


# ── CFG-1: non-finite env floats fall back to the safe default ──────

@pytest.mark.parametrize("bad", ["inf", "-inf", "nan", "Infinity", "NaN"])
def test_env_float_rejects_non_finite(monkeypatch, bad):
    monkeypatch.setenv("RC_TEST_FLOAT", bad)
    assert _env_float("RC_TEST_FLOAT", 7.0) == 7.0


def test_env_float_accepts_normal(monkeypatch):
    monkeypatch.setenv("RC_TEST_FLOAT", "12.5")
    assert _env_float("RC_TEST_FLOAT", 7.0) == 12.5


# ── CFG-2: bounded reader clamps out-of-range / negative ────────────

def test_env_float_bounded_clamps(monkeypatch):
    monkeypatch.setenv("RC_TEST_B", "-5")
    assert _env_float_bounded("RC_TEST_B", 10.0, 0.1, 100.0) == 0.1
    monkeypatch.setenv("RC_TEST_B", "99999")
    assert _env_float_bounded("RC_TEST_B", 10.0, 0.1, 100.0) == 100.0
    monkeypatch.setenv("RC_TEST_B", "nan")  # CFG-1 also covers the bounded path
    assert _env_float_bounded("RC_TEST_B", 10.0, 0.1, 100.0) == 10.0


def test_risk_limits_clamp_negative_and_absurd():
    """Integration: a negative drawdown limit and an absurd exposure must clamp
    when RiskLimits is built (run in a subprocess so the dataclass defaults are
    evaluated with the env in place)."""
    code = (
        "import bot.config as c; rl = c.RiskLimits(); "
        "print(rl.max_drawdown_pct, rl.max_portfolio_exposure_pct, rl.max_margin_risk_pct)"
    )
    env = {
        "MAX_DRAWDOWN_PCT": "-5",
        "MAX_PORTFOLIO_EXPOSURE_PCT": "99999",
        "MAX_MARGIN_RISK_PCT": "-1",
        "PATH": "/usr/bin:/bin",
    }
    out = subprocess.check_output([sys.executable, "-c", code], env=env,
                                  stderr=subprocess.DEVNULL, cwd="/home/user/RUNECLAW")
    dd, expo, margin = (float(x) for x in out.decode().split())
    assert dd >= 0.1            # not negative
    assert expo == 1000.0       # clamped to ceiling
    assert margin >= 0.1        # not negative
