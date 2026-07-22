"""NL → Authority Envelope compiler + per-user store — predictions A1–A6.

A1 phrase mapping; A2 percent needs equity (no fabrication); A3 compiles into a
real clamped envelope; A4 store bind/mode/revoke; A5 the store satisfies the web
live gate's enforce precondition; A6 unmatched is honest.
"""
import tempfile
import os


from bot.guardian.authority_nl import compile_nl_envelope
from bot.guardian.authority import compile_envelope
from bot.guardian import user_authority_store as uas


# ── A1 — phrase mapping ───────────────────────────────────────────────

def test_a1_full_sentence_maps_every_clause():
    r = compile_nl_envelope("only majors, max $500 per trade, $2000 a day, "
                            "only on bitget, never DOGE")
    s = r["spec"]
    assert s["max_notional_per_trade_usd"] == 500.0
    assert s["max_notional_daily_usd"] == 2000.0
    assert s["symbol_allowlist"] == ["BTC", "ETH", "SOL", "BNB", "XRP"]
    assert "DOGE" in s["symbol_blocklist"]
    assert s["allowed_venues"] == ["bitget"]
    assert s["mode"] == "shadow"                 # authoring never auto-enforces
    assert not r["unmatched"]


def test_a1_explicit_symbol_list_and_k_suffix():
    r = compile_nl_envelope("only trade BTC and ETH, max 1k per trade")
    assert r["spec"]["symbol_allowlist"] == ["BTC", "ETH"]
    assert r["spec"]["max_notional_per_trade_usd"] == 1000.0


def test_a1_perps_only_market_type():
    r = compile_nl_envelope("perps only, max $250 a trade")
    assert r["spec"]["allowed_market_types"] == ["swap"]


# ── A2 — percent needs equity (no fabrication) ────────────────────────

def test_a2_percent_without_equity_is_pending_not_invented():
    r = compile_nl_envelope("max 2% per trade")
    assert "max_notional_per_trade_usd" not in r["spec"]
    assert any("2% per trade" in p for p in r["pending"])


def test_a2_percent_with_equity_becomes_dollar_cap():
    r = compile_nl_envelope("max 2% per trade", equity_usd=10000)
    assert r["spec"]["max_notional_per_trade_usd"] == 200.0


# ── A3 — compiles into a real clamped envelope ────────────────────────

def test_a3_compiles_and_clamps():
    r = compile_nl_envelope("only majors, max $500 per trade")
    env = compile_envelope({**r["spec"], "mode": "enforce"},
                           engine_caps={"max_notional_per_trade_usd": 300},
                           venue_universe=["bitget", "bybit"])
    # engine cap 300 tightens the typed 500 (tighten-only).
    assert env["max_notional_per_trade_usd"] == 300
    assert env["mode"] == "enforce"
    assert env["withdraw_allowed"] is False       # never opened by NL
    assert env["envelope_id"].startswith("env_")


# ── A4/A5 — per-user store + web-live enforce precondition ────────────

def test_a4_store_bind_mode_revoke_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        store = uas.UserAuthorityStore(path)
        r = compile_nl_envelope("only majors, max $500 per trade")
        env = compile_envelope({**r["spec"], "mode": "shadow"})
        assert store.bind("web:5", env) is True
        assert store.mode("web:5") == "shadow"
        assert store.is_enforcing("web:5") is False   # A5: shadow ≠ enforce
        assert store.set_mode("web:5", "enforce") is True
        assert store.is_enforcing("web:5") is True    # A5: now the gate precond passes
        # persistence across a reload
        store2 = uas.UserAuthorityStore(path)
        assert store2.is_enforcing("web:5") is True
        # revoke kills it
        assert store.revoke("web:5") is True
        assert store.is_enforcing("web:5") is False
        assert store.mode("web:5") == "off"
    finally:
        os.unlink(path)


def test_a5_no_envelope_is_never_enforcing():
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        store = uas.UserAuthorityStore(path)
        assert store.is_enforcing("web:9") is False
        assert store.set_mode("web:9", "enforce") is False   # nothing to flip
    finally:
        os.unlink(path)


# ── A6 — unmatched is honest ──────────────────────────────────────────

def test_a6_gibberish_is_unmatched():
    r = compile_nl_envelope("please make me lots of money fast")
    assert r["unmatched"] is True
    assert r["matched"] == []
