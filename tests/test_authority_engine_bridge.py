"""Guardian Authority Envelope — risk-engine bridge (gated, shadow/enforce, fail-open).

Verifies the money-path wiring mirrors the Intent Compiler precedent:
* default OFF → byte-identical to before (no envelope effect, no AUTHORITY line);
* bound but flag OFF → still inert;
* enforce mode → an over-cap or revoked action flips APPROVED→REJECTED with an
  AUTHORITY reason;
* shadow mode → records "would deny" in checks_passed but NEVER blocks;
* an in-bounds action → AUTHORITY: OK, trade still approved;
* the bridge is fail-open: a malformed envelope never crashes evaluate().

Uses tiny per-trade caps against the default sizing so the notional gate bites
deterministically.
"""
import os
import tempfile
from contextlib import contextmanager

from bot.config import CONFIG
from bot.guardian import authority as auth
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

_ATR = 2600.0


@contextmanager
def _flag(val: bool):
    """Flip the frozen CONFIG.risk.authority_envelope_enabled for a test."""
    old = CONFIG.risk.authority_envelope_enabled
    object.__setattr__(CONFIG.risk, "authority_envelope_enabled", val)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.risk, "authority_envelope_enabled", old)


def _idea():
    return TradeIdea(
        id="TI-auth", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=65000.0, stop_loss=63700.0, take_profit=66560.0,
        confidence=0.72, reasoning="test", signals_used=["rsi"],
        strategy_type="scalp")


def _risk():
    port = PortfolioTracker(initial_balance=10000.0)
    state = os.path.join(tempfile.mkdtemp(prefix="rc-auth-"), "s.json")
    return RiskEngine(port, state_file=state)


def _env(mode="enforce", **over):
    spec = {"label": "bridge", "mode": mode, "allowed_venues": ["bitget"],
            "allowed_market_types": ["swap"], "max_notional_per_trade_usd": 1_000_000}
    spec.update(over)
    return auth.compile_envelope(spec)


def _bind(risk, env):
    """Bind the envelope with its venue (bitget), as an executor would."""
    risk.set_authority_envelope(env, venue="bitget")


# ── default OFF ───────────────────────────────────────────────────────

def test_flag_off_is_inert():
    with _flag(False):
        risk = _risk()
        _bind(risk, _env(max_notional_per_trade_usd=1))  # would deny if consulted
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "APPROVED"
    assert r.authority is None
    assert not any("AUTHORITY" in c for c in r.checks_passed + r.checks_failed)


def test_no_envelope_is_inert():
    with _flag(True):
        risk = _risk()   # no envelope bound
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "APPROVED"
    assert r.authority is None


# ── enforce ───────────────────────────────────────────────────────────

def test_enforce_over_cap_rejects():
    with _flag(True):
        risk = _risk()
        # $1 per-trade cap against a ~$100 position → denied.
        _bind(risk, _env(mode="enforce", max_notional_per_trade_usd=1))
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "REJECTED"
    assert any("AUTHORITY" in f and "per-trade cap" in f for f in r.checks_failed)
    assert r.authority is not None and r.authority["decision"] == "deny"


def test_enforce_revoked_rejects():
    with _flag(True):
        risk = _risk()
        env = auth.revoke(_env(mode="enforce"))  # revoke() copies the dict, mode preserved
        _bind(risk, env)
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "REJECTED"
    assert any("AUTHORITY" in f and "revoked" in f for f in r.checks_failed)


def test_enforce_in_bounds_approves():
    with _flag(True):
        risk = _risk()
        _bind(risk, _env(mode="enforce", max_notional_per_trade_usd=1_000_000))
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "APPROVED"
    assert any(c == "AUTHORITY: OK" for c in r.checks_passed)
    assert r.authority["decision"] == "allow"


# ── shadow never blocks ───────────────────────────────────────────────

def test_shadow_records_but_never_blocks():
    with _flag(True):
        risk = _risk()
        # Same $1 cap that rejects in enforce — in shadow it must only RECORD.
        _bind(risk, _env(mode="shadow", max_notional_per_trade_usd=1))
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "APPROVED"                      # NOT blocked
    assert any("AUTHORITY: shadow" in c and "would deny" in c for c in r.checks_passed)
    assert r.authority is not None and r.authority["decision"] == "deny"


# ── fail-open ─────────────────────────────────────────────────────────

def test_bridge_is_fail_open_when_authorize_raises(monkeypatch):
    # Force the pure core to raise — the BRIDGE must swallow it and leave the
    # clean trade APPROVED (an envelope bug can never halt the engine).
    from bot.guardian import authority as _auth

    def _boom(*a, **k):
        raise RuntimeError("simulated envelope fault")
    monkeypatch.setattr(_auth, "authorize", _boom)

    with _flag(True):
        risk = _risk()
        _bind(risk, _env(mode="enforce", max_notional_per_trade_usd=1))
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    assert r.verdict.value == "APPROVED"   # fault did NOT block the trade
    assert any("AUTHORITY: skipped (error" in c for c in r.checks_passed)
