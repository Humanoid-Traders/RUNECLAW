"""Guardian Authority Envelope — the five pre-registered predictions.

Pins the discipline invariants of the custody layer (docs/guardian_authority.md):
A1 withdraw-denied-by-default, A2 tighten-only compile, A3 fail-closed,
A4 daily-ceiling, A5 determinism + identity. Pure module — no engine, network,
or clock.
"""
from bot.guardian import authority as auth


# ── helpers ───────────────────────────────────────────────────────────

def _env(**over):
    spec = {
        "label": "test",
        "mode": "enforce",
        "allowed_venues": ["bitget"],
        "allowed_market_types": ["swap"],
        "max_notional_per_trade_usd": 1000,
        "max_notional_daily_usd": 5000,
    }
    spec.update(over)
    return auth.compile_envelope(spec, engine_caps={
        "max_notional_per_trade_usd": 2000,
        "max_notional_daily_usd": 10000,
    }, venue_universe=["bitget", "bybit", "hyperliquid"])


def _trade(**over):
    a = {"kind": "trade", "venue": "bitget", "market_type": "swap",
         "asset": "BTC/USDT", "notional_usd": 500}
    a.update(over)
    return a


# ── A1 — withdraw denied by default ───────────────────────────────────

def test_a1_withdraw_denied_by_default():
    env = _env()   # no withdraw_allowed
    assert env["withdraw_allowed"] is False
    # any withdraw is denied regardless of size/destination
    d = auth.authorize(env, {"kind": "withdraw", "dest": "0xabc", "notional_usd": 1},
                       now_ts=1000)
    assert d["decision"] == "deny"
    assert any("not permitted" in r for r in d["reasons"])
    # transfer is treated the same
    d2 = auth.authorize(env, {"kind": "transfer", "dest": "0xabc"}, now_ts=1000)
    assert d2["decision"] == "deny"


def test_a1_withdraw_needs_double_optin():
    # flag without an allowlist → still denied (compile refuses to grant it)
    env = _env(withdraw_allowed=True)
    assert env["withdraw_allowed"] is False
    assert any("stays DENIED" in w for w in env["warnings"])
    # flag WITH an allowlisted destination → the one allowed dest passes,
    # a non-allowlisted dest is denied
    env2 = _env(withdraw_allowed=True, withdraw_allowlist=["0xCOLDWALLET"])
    assert env2["withdraw_allowed"] is True
    ok = auth.authorize(env2, {"kind": "withdraw", "dest": "0xcoldwallet"}, now_ts=1000)
    assert ok["decision"] == "allow"
    bad = auth.authorize(env2, {"kind": "withdraw", "dest": "0xATTACKER"}, now_ts=1000)
    assert bad["decision"] == "deny"
    assert any("allowlist" in r for r in bad["reasons"])


# ── A2 — tighten-only compile ─────────────────────────────────────────

def test_a2_ceiling_clamped_to_engine_cap():
    env = auth.compile_envelope(
        {"allowed_venues": ["bitget"], "max_notional_per_trade_usd": 999999},
        engine_caps={"max_notional_per_trade_usd": 2000})
    assert env["max_notional_per_trade_usd"] == 2000
    assert any("clamped" in w for w in env["warnings"])


def test_a2_unknown_venue_dropped():
    env = auth.compile_envelope(
        {"allowed_venues": ["bitget", "ftx"]},   # ftx not in universe
        venue_universe=["bitget", "bybit"])
    assert env["allowed_venues"] == ["bitget"]
    assert any("ftx" in w for w in env["warnings"])


# ── A3 — fail-closed ──────────────────────────────────────────────────

def test_a3_none_envelope_denies():
    d = auth.authorize(None, _trade(), now_ts=1000)
    assert d["decision"] == "deny"
    assert any("no authority" in r for r in d["reasons"])


def test_a3_expired_and_revoked_deny():
    env = _env(expiry_ts=500)
    d = auth.authorize(env, _trade(), now_ts=1000)   # now > expiry
    assert d["decision"] == "deny"
    assert any("expired" in r for r in d["reasons"])

    rev = auth.revoke(_env())
    assert rev["revoked"] is True
    assert rev["compiled_hash"] != _env()["compiled_hash"]   # revocation changes identity
    d2 = auth.authorize(rev, _trade(), now_ts=1000)
    assert d2["decision"] == "deny"
    assert any("revoked" in r for r in d2["reasons"])


def test_a3_unknown_kind_and_bad_venue_deny():
    env = _env()
    assert auth.authorize(env, {"kind": "sudo_drain"}, now_ts=1000)["decision"] == "deny"
    off_venue = auth.authorize(env, _trade(venue="kraken"), now_ts=1000)
    assert off_venue["decision"] == "deny"
    assert any("kraken" in r for r in off_venue["reasons"])


def test_a3_happy_path_allows():
    env = _env()
    d = auth.authorize(env, _trade(), now_ts=1000, spent_today_usd=0)
    assert d["decision"] == "allow", d["reasons"]
    assert d["reasons"] == []


# ── A4 — daily ceiling ────────────────────────────────────────────────

def test_a4_daily_ceiling_denies_even_when_per_trade_ok():
    env = _env(max_notional_per_trade_usd=1000, max_notional_daily_usd=5000)
    # a single $800 trade is under the per-trade cap...
    a = _trade(notional_usd=800)
    # ...but with $4500 already spent, it would push the day to $5300 > $5000
    d = auth.authorize(env, a, now_ts=1000, spent_today_usd=4500)
    assert d["decision"] == "deny"
    assert any("daily" in r.lower() for r in d["reasons"])
    # the same trade with $0 spent is fine
    ok = auth.authorize(env, a, now_ts=1000, spent_today_usd=0)
    assert ok["decision"] == "allow", ok["reasons"]


def test_a4_over_per_trade_denies():
    env = _env(max_notional_per_trade_usd=1000)
    d = auth.authorize(env, _trade(notional_usd=1500), now_ts=1000)
    assert d["decision"] == "deny"
    assert any("per-trade cap" in r for r in d["reasons"])


# ── A5 — determinism + identity ───────────────────────────────────────

def test_a5_determinism_and_hash_sensitivity():
    a = _env()
    b = _env()
    assert a["compiled_hash"] == b["compiled_hash"]
    assert a["envelope_id"] == b["envelope_id"]
    # mode/label are cosmetic → do NOT change identity
    c = auth.compile_envelope({
        "label": "totally different label", "mode": "shadow",
        "allowed_venues": ["bitget"], "allowed_market_types": ["swap"],
        "max_notional_per_trade_usd": 1000, "max_notional_daily_usd": 5000,
    }, engine_caps={"max_notional_per_trade_usd": 2000,
                    "max_notional_daily_usd": 10000},
       venue_universe=["bitget", "bybit", "hyperliquid"])
    assert c["compiled_hash"] == a["compiled_hash"]
    # changing ONE ceiling changes identity
    d = _env(max_notional_per_trade_usd=900)
    assert d["compiled_hash"] != a["compiled_hash"]


def test_a5_human_readable_states_noncustodial():
    txt = auth.human_readable(_env())
    assert "Withdraw: DENIED" in txt
    assert "non-custodial" in txt
