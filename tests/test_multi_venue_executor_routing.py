"""
Phase 4 — end-to-end per-user venue routing.

Proves the whole chain a user experiences after connecting a non-Bitget venue:
their stored credential record (real ExchangeCredentialStore) → the engine's
per-user executor resolver → a LiveExecutor bound to the RIGHT venue → a venue
exchange built from THEIR credentials.

  * a Hyperliquid-connected user's balance-view + trading executors use the
    Hyperliquid venue and carry the wallet creds;
  * that venue actually builds a ccxt.hyperliquid client with the user's wallet;
  * a Bitget-connected user still routes to Bitget (byte-identical default).

ccxt is only constructed locally (no network).
"""

from unittest.mock import patch

from cryptography.fernet import Fernet

from bot.core.engine import RuneClawEngine
from bot.core.exchange_credentials import ExchangeCredentialStore

HL_WALLET = "0x" + "a" * 40
HL_AGENT = "0x" + "b" * 64
BG = {"api_key": "K" * 16, "api_secret": "S" * 16, "passphrase": "pp"}


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()  # sentinel shared operator executor
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    return eng


def _real_store(tmp_path):
    return ExchangeCredentialStore(
        creds_file=str(tmp_path / "creds.enc"),
        key_file=str(tmp_path / ".key"),
    )


def _patch(store):
    return patch("bot.core.exchange_credentials.get_credential_store",
                 return_value=store)


def test_hyperliquid_user_balance_view_executor_routes_to_hyperliquid(tmp_path):
    store = _real_store(tmp_path)
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = _real_store(tmp_path)
    store.set_venue("carol", "hyperliquid",
                    {"wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})

    eng = _engine()
    with _patch(store):
        ex = eng.balance_view_executor("carol")

    assert ex is not eng.live_executor
    assert ex._venue.id == "hyperliquid"
    assert ex._credentials["wallet_address"] == HL_WALLET
    assert ex._credentials["agent_private_key"] == HL_AGENT


def test_hyperliquid_venue_builds_exchange_from_user_wallet(tmp_path):
    import asyncio
    from bot.config import CONFIG

    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = _real_store(tmp_path)
    store.set_venue("carol", "hyperliquid",
                    {"wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})

    eng = _engine()
    with _patch(store):
        ex = eng.balance_view_executor("carol")

    exch = ex._venue.create_exchange(CONFIG.exchange, ex._credentials)
    try:
        assert exch.walletAddress == HL_WALLET  # the USER's wallet, not the operator's
    finally:
        asyncio.get_event_loop().run_until_complete(exch.close())


def test_hyperliquid_user_trading_executor_routes_to_hyperliquid(tmp_path):
    # _executor_for is the ORDER-placement resolver (gated on per_user_live).
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = _real_store(tmp_path)
    store.set_venue("carol", "hyperliquid",
                    {"wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})

    eng = _engine()
    eng.risk = object()
    eng.slippage = None
    with _patch(store), patch("bot.core.engine.CONFIG") as cfg:
        cfg.per_user_live_enabled = True
        ex = eng._executor_for("carol")

    assert ex is not eng.live_executor
    assert ex._venue.id == "hyperliquid"


def test_bybit_user_routes_to_bybit_with_keysecret(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = _real_store(tmp_path)
    store.set_venue("frank", "bybit", {"api_key": "BY" + "K" * 14, "api_secret": "BY" + "S" * 14})

    eng = _engine()
    with _patch(store):
        ex = eng.balance_view_executor("frank")
    assert ex._venue.id == "bybit"
    assert ex._credentials["api_key"] == "BY" + "K" * 14
    # The venue builds a ccxt.bybit client from the user's key.
    from bot.config import CONFIG
    exch = ex._venue.create_exchange(CONFIG.exchange, ex._credentials)
    import asyncio
    try:
        assert exch.apiKey == "BY" + "K" * 14
    finally:
        asyncio.get_event_loop().run_until_complete(exch.close())


def test_bitget_user_still_routes_to_bitget(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = _real_store(tmp_path)
    store.set("dave", BG["api_key"], BG["api_secret"], BG["passphrase"])

    eng = _engine()
    with _patch(store):
        ex = eng.balance_view_executor("dave")

    assert ex._venue.id == "bitget"
    assert ex._credentials["api_key"] == BG["api_key"]


def test_reconnect_switching_venue_rebuilds_executor(tmp_path):
    # A user who disconnects Bitget and connects Hyperliquid must get a fresh
    # executor on the new venue — the rebuild check is venue-agnostic.
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = _real_store(tmp_path)
    store.set("erin", BG["api_key"], BG["api_secret"], BG["passphrase"])
    eng = _engine()
    with _patch(store):
        a = eng.balance_view_executor("erin")
        assert a._venue.id == "bitget"
        store.set_venue("erin", "hyperliquid",
                        {"wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})
        b = eng.balance_view_executor("erin")
    assert b is not a
    assert b._venue.id == "hyperliquid"
