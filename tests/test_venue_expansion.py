"""Venue expansion — OKX, Gate.io, KuCoin Futures (USDT perps, ccxt-native).

Validates registration, symbol/param shape, credential wiring, and the missing-
credential guards WITHOUT any live keys or network. Live order placement still
gates behind the existing /venue preflight before auto-trade — same bar Bybit/
BingX cleared.
"""
import pytest

from bot.core.venues import get_venue, valid_venue_ids
from bot.core import exchange_credentials as ec

_NEW = ("okx", "gate", "kucoin")


def test_new_venues_registered_and_resolvable():
    ids = valid_venue_ids()
    for vid in _NEW:
        assert vid in ids
        assert get_venue(vid).id == vid
    # unknown still falls back to bitget (fail-safe, never crash)
    assert get_venue("definitely-not-a-venue").id == "bitget"


def test_symbol_and_trigger_shapes():
    for vid in _NEW:
        v = get_venue(vid)
        # one-way perp symbol mapping
        assert v.order_symbol("BTC/USDT") == "BTC/USDT:USDT"
        assert v.order_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
        # ccxt-unified reduce-only triggers
        assert v.trigger_params("sl", 100.0) == {"stopLossPrice": 100.0, "reduceOnly": True}
        assert v.trigger_params("tp", 120.0) == {"takeProfitPrice": 120.0, "reduceOnly": True}
        assert v.quote == "USDT" and v.balance_coin == "USDT"


def test_credential_fields_match_store_and_web():
    # bot credential store knows each venue's required fields
    assert ec._VENUE_FIELDS["okx"] == ("api_key", "api_secret", "passphrase")
    assert ec._VENUE_FIELDS["gate"] == ("api_key", "api_secret")
    assert ec._VENUE_FIELDS["kucoin"] == ("api_key", "api_secret", "passphrase")
    for vid in _NEW:
        assert vid in ec.valid_venue_ids()
    # kucoin's perp product maps to the dedicated ccxt id
    assert ec._CCXT_ID["kucoin"] == "kucoinfutures"


def test_missing_passphrase_and_keys_raise():
    # OKX/KuCoin require a passphrase; omitting it fails loud (never a silent bad client)
    for vid in ("okx", "kucoin"):
        with pytest.raises(RuntimeError):
            get_venue(vid).create_exchange(None, {"api_key": "k" * 12, "api_secret": "s" * 12})
    # missing key/secret fails for all three
    for vid in _NEW:
        with pytest.raises(RuntimeError):
            get_venue(vid).create_exchange(None, {})


def test_basic_format_check():
    # gate: key+secret; okx/kucoin: key+secret+passphrase
    assert ec.basic_venue_format_ok("gate", {"api_key": "k" * 12, "api_secret": "s" * 12})
    assert not ec.basic_venue_format_ok("gate", {"api_key": "short"})
    assert ec.basic_venue_format_ok(
        "okx", {"api_key": "k" * 12, "api_secret": "s" * 12, "passphrase": "pw"})
    assert not ec.basic_venue_format_ok(
        "okx", {"api_key": "k" * 12, "api_secret": "s" * 12})   # no passphrase
    # a space in any field is a paste mistake → rejected
    assert not ec.basic_venue_format_ok(
        "kucoin", {"api_key": "k k", "api_secret": "s" * 12, "passphrase": "pw"})


def test_gate_builds_offline_client_with_key_secret():
    # constructing a ccxt client is offline (no network until a call); proves the
    # key+secret path builds without a passphrase.
    client = get_venue("gate").create_exchange(None, {"api_key": "k" * 12, "api_secret": "s" * 12})
    assert client is not None


# ── Paradex — wallet-authenticated on-chain perps DEX ─────────────────

def test_paradex_registered_and_wallet_authed():
    assert "paradex" in valid_venue_ids()
    v = get_venue("paradex")
    assert v.id == "paradex"
    assert v.quote == "USDC" and v.balance_coin == "USDC"    # on-chain, USDC-margined
    assert v.order_symbol("BTC/USDT") == "BTC/USDC:USDC"     # internal USDT → venue USDC
    # wallet auth (walletAddress + privateKey), like Hyperliquid
    assert ec._VENUE_FIELDS["paradex"] == ("wallet_address", "agent_private_key")


def test_paradex_missing_wallet_raises_and_format_check():
    with pytest.raises(RuntimeError):
        get_venue("paradex").create_exchange(None, {})
    # 0x-address + 64-hex-key format check (shared with Hyperliquid)
    ok = ec.basic_venue_format_ok("paradex", {
        "wallet_address": "0x" + "a" * 40, "agent_private_key": "b" * 64})
    assert ok
    assert not ec.basic_venue_format_ok("paradex", {
        "wallet_address": "not-an-address", "agent_private_key": "b" * 64})
