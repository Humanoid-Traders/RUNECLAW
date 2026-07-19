"""Authority spend ledger — rolling-window notional accounting + engine wiring.

Pins: pure window math, idempotent-by-ref recording, atomic persistence round-trip,
corrupt-file fail-safe, and the risk-engine daily-cap enforcement (a bound ledger
makes cumulative daily notional bite; approved trades accrue against the window).
"""
import os
import tempfile
from contextlib import contextmanager

from bot.config import CONFIG
from bot.guardian import authority as auth
from bot.guardian.authority_ledger import AuthoritySpendLedger, prune, window_sum
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

_ATR = 2600.0
_DAY = 86_400


@contextmanager
def _flag(val):
    old = CONFIG.risk.authority_envelope_enabled
    object.__setattr__(CONFIG.risk, "authority_envelope_enabled", val)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.risk, "authority_envelope_enabled", old)


# ── pure window math ──────────────────────────────────────────────────

def test_prune_and_window_sum():
    now = 1_000_000
    entries = [
        {"ts": now - 10, "amount": 100},        # in window
        {"ts": now - _DAY - 5, "amount": 999},   # aged out
        {"ts": now, "amount": 50},               # in window
    ]
    assert window_sum(entries, now) == 150.0
    assert len(prune(entries, now)) == 2


# ── ledger behaviour ──────────────────────────────────────────────────

def test_record_and_spent():
    led = AuthoritySpendLedger()
    now = 1_000_000
    led.record("env_abc", 300, now, ref="t1")
    led.record("env_abc", 200, now, ref="t2")
    assert led.spent("env_abc", now) == 500.0
    assert led.remaining("env_abc", 1000, now) == 500.0


def test_record_is_idempotent_by_ref():
    led = AuthoritySpendLedger()
    now = 1_000_000
    assert led.record("k", 100, now, ref="dup") is True
    assert led.record("k", 100, now, ref="dup") is False   # same ref ignored
    assert led.spent("k", now) == 100.0


def test_old_entries_age_out():
    led = AuthoritySpendLedger()
    t0 = 1_000_000
    led.record("k", 400, t0, ref="old")
    later = t0 + _DAY + 100
    led.record("k", 100, later, ref="new")
    assert led.spent("k", later) == 100.0     # the 400 aged out of the window


def test_persistence_round_trip_and_corrupt_failsafe():
    d = tempfile.mkdtemp(prefix="rc-led-")
    path = os.path.join(d, "ledger.json")
    now = 1_000_000
    led = AuthoritySpendLedger(state_file=path)
    led.record("k", 250, now, ref="t1")
    # a fresh ledger on the same file sees the persisted spend
    led2 = AuthoritySpendLedger(state_file=path)
    assert led2.spent("k", now) == 250.0
    # a corrupt file fails safe to empty (never raises)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not json")
    led3 = AuthoritySpendLedger(state_file=path)
    assert led3.spent("k", now) == 0.0


# ── engine integration: the daily cap now bites ───────────────────────

def _idea(idea_id="TI-led"):
    return TradeIdea(
        id=idea_id, asset="BTC/USDT", direction=Direction.LONG,
        entry_price=65000.0, stop_loss=63700.0, take_profit=66560.0,
        confidence=0.72, reasoning="t", signals_used=["rsi"], strategy_type="scalp")


def _risk():
    port = PortfolioTracker(initial_balance=10000.0)
    return RiskEngine(port, state_file=os.path.join(tempfile.mkdtemp(prefix="rc-le-"), "s.json"))


def _env(daily):
    # per-trade cap generous, daily cap tight so the LEDGER is what bites.
    return auth.compile_envelope({
        "mode": "enforce", "allowed_venues": ["bitget"],
        "allowed_market_types": ["swap"],
        "max_notional_per_trade_usd": 1_000_000,
        "max_notional_daily_usd": daily,
    })


def test_daily_cap_enforced_via_ledger():
    with _flag(True):
        risk = _risk()
        env = _env(daily=100)   # ~$275 notional/trade (55 pos x 5x) > $100 daily
        risk.set_authority_envelope(env, venue="bitget")
        led = AuthoritySpendLedger()
        risk.set_authority_ledger(led)
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
    # a single ~$275 notional trade (55 pos x 5x) already exceeds the $100 daily
    # cap (spent starts at 0) → the daily-cap branch denies
    assert r.verdict.value == "REJECTED"
    assert any("AUTHORITY" in f and "daily" in f.lower() for f in r.checks_failed)


def test_approved_trade_accrues_to_ledger():
    with _flag(True):
        risk = _risk()
        env = _env(daily=100_000)   # roomy daily cap → trade approves
        risk.set_authority_envelope(env, venue="bitget")
        led = AuthoritySpendLedger()
        risk.set_authority_ledger(led)
        r = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
        env_id = env["envelope_id"]
        import datetime as _dt
        now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        spent = led.spent(env_id, now)
    assert r.verdict.value == "APPROVED"
    assert spent > 0   # the approved trade's notional was recorded
