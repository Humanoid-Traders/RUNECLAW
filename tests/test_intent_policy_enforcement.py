"""Intent Compiler enforcement seam in the live risk gate.

The pure policy core is tested in test_intent_policy. Here we prove the *wiring*
into RiskEngine.evaluate() behaves on the live-money invariants:

* Flag OFF (default) → the hook is inert; the RiskCheck is byte-identical to
  before (no INTENT_POLICY entries, intent_policy is None).
* Flag ON + enforce policy that a trade violates → the trade is REJECTED and the
  violation is namespaced under INTENT_POLICY in checks_failed.
* Flag ON + shadow policy → NEVER blocks; would-reject violations are recorded
  (checks_passed + the intent_policy result) but do not touch the verdict.
* A policy can only ADD rejections — it can never approve a trade the engine
  itself rejects (tighten-only is a property of the append-only control flow).
* A broken policy is skipped, never crashes the gate.
"""
import os
import tempfile
from datetime import datetime

from bot.compat import UTC
from bot.config import CONFIG
from bot.guardian import intent_policy as ip
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, RiskVerdict, TradeIdea


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-intent-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea(asset="BTC/USDT", direction=Direction.LONG, conf=0.9):
    return TradeIdea(
        asset=asset, direction=direction, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, confidence=conf,
        reasoning="intent-enforce", source="scan", timestamp=datetime.now(UTC))


class _flag:
    """Context manager: flip the frozen CONFIG.risk.intent_policy_enabled."""
    def __init__(self, val):
        self.val = val

    def __enter__(self):
        self.old = CONFIG.risk.intent_policy_enabled
        object.__setattr__(CONFIG.risk, "intent_policy_enabled", self.val)

    def __exit__(self, *a):
        object.__setattr__(CONFIG.risk, "intent_policy_enabled", self.old)


def _has_intent(entries):
    return [e for e in entries if e.startswith("INTENT_POLICY")]


def test_flag_off_is_a_noop_even_with_policy_set():
    eng = _engine()
    eng.set_intent_policy(ip.compile_policy(
        {"mode": "enforce", "rules": [{"type": "allowed_symbols", "value": ["ETH"]}]}))
    with _flag(False):
        check = eng.evaluate(_idea(), atr=2.0)
    # Policy set but flag off → hook skipped entirely.
    assert check.intent_policy is None
    assert _has_intent(check.checks_failed) == []
    assert _has_intent(check.checks_passed) == []


def test_enforce_policy_rejects_disallowed_symbol():
    eng = _engine()
    eng.set_intent_policy(ip.compile_policy(
        {"mode": "enforce", "rules": [{"type": "allowed_symbols", "value": ["ETH"]}]}))
    with _flag(True):
        check = eng.evaluate(_idea(asset="BTC/USDT"), atr=2.0)
    assert check.verdict == RiskVerdict.REJECTED
    assert _has_intent(check.checks_failed), check.checks_failed
    assert check.intent_policy["verdict"] == "reject"
    assert check.intent_policy["mode"] == "enforce"


def test_enforce_policy_allows_permitted_trade():
    eng = _engine()
    eng.set_intent_policy(ip.compile_policy(
        {"mode": "enforce", "rules": [{"type": "allowed_symbols", "value": ["BTC"]}]}))
    with _flag(True):
        check = eng.evaluate(_idea(asset="BTC/USDT"), atr=2.0)
    # The policy adds no failure for BTC; it records an OK pass.
    assert _has_intent(check.checks_failed) == []
    assert check.intent_policy["verdict"] == "pass"


def test_shadow_policy_never_blocks_but_records():
    eng = _engine()
    eng.set_intent_policy(ip.compile_policy(
        {"mode": "shadow", "rules": [{"type": "allowed_symbols", "value": ["ETH"]}]}))
    with _flag(True):
        check = eng.evaluate(_idea(asset="BTC/USDT"), atr=2.0)
    # Shadow must NOT contribute a rejection...
    assert _has_intent(check.checks_failed) == []
    # ...but it DOES record the would-reject as an observation.
    shadow = [e for e in check.checks_passed if "shadow" in e.lower()]
    assert shadow, check.checks_passed
    assert check.intent_policy["mode"] == "shadow"
    assert check.intent_policy["verdict"] == "reject"   # would have rejected


def test_policy_only_adds_never_approves_engine_rejection():
    # An engine that is already halted (open circuit breaker) rejects everything;
    # an enforce policy that "passes" the trade cannot flip that to APPROVED.
    eng = _engine()
    eng._circuit_open = True   # halt the engine → it rejects everything
    eng.set_intent_policy(ip.compile_policy(
        {"mode": "enforce", "rules": [{"type": "allowed_symbols", "value": ["BTC"]}]}))
    with _flag(True):
        check = eng.evaluate(_idea(asset="BTC/USDT"), atr=2.0)
    assert check.verdict == RiskVerdict.REJECTED   # engine halt stands


def test_broken_policy_is_skipped_not_fatal():
    eng = _engine()
    # A structurally broken policy dict (rules not a list of dicts).
    eng.set_intent_policy({"policy_id": "x", "mode": "enforce", "rules": "boom"})
    with _flag(True):
        check = eng.evaluate(_idea(), atr=2.0)
    # evaluate() returned a verdict at all → no crash. No spurious policy reject.
    assert check.verdict in (RiskVerdict.APPROVED, RiskVerdict.REJECTED)
    assert _has_intent(check.checks_failed) == []
