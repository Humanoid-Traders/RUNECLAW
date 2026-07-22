"""Web-live per-trade authorization at confirm — predictions Z1–Z6.

The enforce-mode Authority Envelope must authorize the SPECIFIC order (venue,
symbol, notional, 24h spend) before a web-live trade executes. Z1 within-caps
allows + records spend; Z2 symbol not allowed denies; Z3 over per-trade cap
denies; Z4 daily cap across trades denies; Z5 no envelope denies; Z6 unknown
notional (auto-sized) denies against a cap. Fail-closed throughout.
"""
import types

import pytest

from bot.web import user_gateway as ug
from bot.guardian import user_authority_store as uas
from bot.guardian.authority_ledger import AuthoritySpendLedger
from bot.guardian.authority import compile_envelope


class _Idea:
    def __init__(self, asset):
        self.asset = asset


class _Engine:
    def __init__(self, ideas, margins):
        self._pending_ideas = ideas
        self._manual_margin_override = margins


def _bind_env(store, uid, *, symbols=("BTC", "ETH", "SOL"), per_trade=500,
              daily=2000, venues=("bitget",)):
    env = compile_envelope({
        "mode": "enforce", "label": "test",
        "allowed_venues": list(venues), "symbol_allowlist": list(symbols),
        "max_notional_per_trade_usd": per_trade,
        "max_notional_daily_usd": daily,
    })
    store.bind(uid, env)
    return env


@pytest.fixture
def wired(monkeypatch, tmp_path):
    store = uas.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(uas, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    # credential store → active venue "bitget"
    fake_cred = types.SimpleNamespace(get_venue=lambda uid: "bitget")
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: fake_cred)
    # leverage defaults to 5 in CONFIG.risk (margin 50 → notional 250).
    return store, ledger


def _engine(asset="SOL/USDT", margin=50, tid="T1"):
    return _Engine({tid: _Idea(asset)}, {tid: margin})


# ── Z1 — within caps allows and records ───────────────────────────────

def test_z1_within_caps_allows_and_records(wired):
    store, ledger = wired
    _bind_env(store, "web:5")
    eng = _engine()
    ok, reasons = ug._authorize_web_live_trade({}, eng, "web:5", "T1")
    assert ok is True and reasons == []
    # 50 margin × 5x = 250 recorded
    assert abs(ledger.spent("web:5", __import__("time").time()) - 250.0) < 1e-6


# ── Z2 — symbol not in allowlist denies ───────────────────────────────

def test_z2_symbol_not_allowed_denies(wired):
    store, _ = wired
    _bind_env(store, "web:5", symbols=("BTC", "ETH"))     # no SOL
    ok, reasons = ug._authorize_web_live_trade({}, _engine("SOL/USDT"), "web:5", "T1")
    assert ok is False
    assert any("SOL" in r for r in reasons)


# ── Z3 — over per-trade cap denies ────────────────────────────────────

def test_z3_over_per_trade_cap_denies(wired):
    store, _ = wired
    _bind_env(store, "web:5", per_trade=100)              # 250 > 100
    ok, reasons = ug._authorize_web_live_trade({}, _engine(margin=50), "web:5", "T1")
    assert ok is False
    assert any("per-trade cap" in r for r in reasons)


# ── Z4 — daily cap across trades denies the second ────────────────────

def test_z4_daily_cap_blocks_second_trade(wired):
    store, _ = wired
    _bind_env(store, "web:5", per_trade=2000, daily=300)  # 250 ok once, not twice
    eng = _Engine({"T1": _Idea("SOL/USDT"), "T2": _Idea("BTC/USDT")},
                  {"T1": 50, "T2": 50})
    ok1, _ = ug._authorize_web_live_trade({}, eng, "web:5", "T1")
    assert ok1 is True
    ok2, reasons = ug._authorize_web_live_trade({}, eng, "web:5", "T2")
    assert ok2 is False
    assert any("daily" in r for r in reasons)


# ── Z5 — no envelope denies ───────────────────────────────────────────

def test_z5_no_envelope_denies(wired):
    ok, reasons = ug._authorize_web_live_trade({}, _engine(), "web:9", "T1")
    assert ok is False
    assert any("Envelope" in r or "envelope" in r for r in reasons)


# ── Z6 — auto-sized (unknown notional) denies against a cap ───────────

def test_z6_unknown_notional_denies_against_cap(wired):
    store, _ = wired
    _bind_env(store, "web:5", per_trade=500)
    eng = _Engine({"T1": _Idea("SOL/USDT")}, {})          # no margin override
    ok, reasons = ug._authorize_web_live_trade({}, eng, "web:5", "T1")
    assert ok is False
    assert any("notional is unknown" in r for r in reasons)
