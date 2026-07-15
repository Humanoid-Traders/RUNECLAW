"""
Phase 2 — multi-venue per-user credentials (store + venue adapter + web pull).

Proves the credential store, the Hyperliquid venue adapter, and the web→bot
credential pull all became venue-aware WITHOUT regressing Bitget:

  * store: Hyperliquid round-trip via set_venue/get/get_venue; a legacy flat
    record still reads back as Bitget; venue-specific fingerprints; format check.
  * venue: HyperliquidVenue.create_exchange prefers per-user {wallet_address,
    agent_private_key} over the operator env wallet.
  * pull: a decrypted payload carrying "venue":"hyperliquid" imports through
    store.set_venue with the HL field set; a venue-less payload stays Bitget.

Deterministic — no network. ccxt is only constructed locally (no calls).
"""

import base64
import json

import pytest
from cryptography.fernet import Fernet

from bot.core.exchange_credentials import (
    ExchangeCredentialStore,
    basic_hl_format_ok,
    valid_venue_ids,
)

BG_KEY, BG_SEC, BG_PP = "bg_publickey_abcdef123456", "supersecret_secret_val", "pp!1"
HL_WALLET = "0x" + "a" * 40
HL_AGENT = "0x" + "b" * 64


def _store(tmp_path):
    creds = tmp_path / "exchange_creds.enc"
    kf = tmp_path / ".exchange_secret.key"
    kf.write_bytes(Fernet.generate_key())
    return ExchangeCredentialStore(creds_file=str(creds), key_file=str(kf)), creds


# ── store: venue-aware round trips ───────────────────────────────────────────

def test_hyperliquid_round_trip(tmp_path):
    s, creds_file = _store(tmp_path)
    s.set_venue("77", "hyperliquid", {
        "wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})
    assert s.get_venue("77") == "hyperliquid"
    assert s.get("77") == {"wallet_address": HL_WALLET, "agent_private_key": HL_AGENT}
    # Encrypted at rest — no plaintext key on disk.
    raw = creds_file.read_text()
    assert HL_AGENT not in raw and HL_WALLET not in raw
    # Distinct venue fingerprint, never the key material.
    fp = s.fingerprint("77")
    assert fp.startswith("HL-") and HL_AGENT not in fp and HL_WALLET not in fp


def test_bitget_still_default_and_byte_identical(tmp_path):
    s, _ = _store(tmp_path)
    s.set("11", BG_KEY, BG_SEC, BG_PP)
    assert s.get_venue("11") == "bitget"
    assert s.get("11") == {"api_key": BG_KEY, "api_secret": BG_SEC, "passphrase": BG_PP}
    assert s.fingerprint("11").startswith("BG-")


def test_legacy_flat_record_reads_as_bitget():
    # A pre-multi-venue on-disk record is a flat {field: ciphertext} with no
    # venue wrapper — it must normalize to Bitget.
    venue, fields = ExchangeCredentialStore._read_record(
        {"api_key": "ct1", "api_secret": "ct2", "passphrase": "ct3"})
    assert venue == "bitget"
    assert set(fields) == {"api_key", "api_secret", "passphrase"}
    # A wrapped record is used as-is.
    v2, f2 = ExchangeCredentialStore._read_record(
        {"venue": "hyperliquid", "fields": {"wallet_address": "x", "agent_private_key": "y"}})
    assert v2 == "hyperliquid" and set(f2) == {"wallet_address", "agent_private_key"}


def test_set_venue_rejects_unknown_venue_and_missing_fields(tmp_path):
    s, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        s.set_venue("1", "ftx", {"api_key": "x"})
    with pytest.raises(ValueError):
        s.set_venue("1", "hyperliquid", {"wallet_address": HL_WALLET})  # missing agent key


def test_format_check_and_venue_ids():
    assert basic_hl_format_ok(HL_WALLET, HL_AGENT) is True
    assert basic_hl_format_ok(HL_WALLET, "0xshort") is False
    assert basic_hl_format_ok("not-an-address", HL_AGENT) is False
    assert basic_hl_format_ok(HL_WALLET.replace("0x", ""), HL_AGENT.replace("0x", "")) is True
    ids = valid_venue_ids()
    assert "bitget" in ids and "hyperliquid" in ids


# ── venue adapter: per-user credentials win over operator env ────────────────

def test_hyperliquid_create_exchange_uses_per_user_credentials():
    import asyncio
    from bot.core.venues import get_venue

    class Cfg:  # operator env wallet is DIFFERENT — must be ignored when creds given
        hyperliquid_wallet_address = "0x" + "c" * 40
        hyperliquid_private_key = "0x" + "d" * 64
        hyperliquid_testnet = False

    hl = get_venue("hyperliquid")
    ex = hl.create_exchange(Cfg(), credentials={
        "wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})
    try:
        assert ex.walletAddress == HL_WALLET  # per-user wallet, not the operator's
    finally:
        asyncio.get_event_loop().run_until_complete(ex.close())


def test_hyperliquid_create_exchange_missing_per_user_creds_raises():
    from bot.core.venues import get_venue

    class Empty:
        hyperliquid_wallet_address = ""
        hyperliquid_private_key = ""
        hyperliquid_testnet = False

    hl = get_venue("hyperliquid")
    with pytest.raises(RuntimeError):
        hl.create_exchange(Empty(), credentials={"wallet_address": HL_WALLET})  # no agent key


# ── web pull: venue flows through to set_venue ───────────────────────────────

class _FakeStore:
    def __init__(self):
        self.bitget = {}
        self.venue_sets = []

    def set(self, tg, api_key, api_secret, passphrase):
        self.bitget[str(tg)] = (api_key, api_secret, passphrase)

    def set_venue(self, tg, venue, fields):
        self.venue_sets.append((str(tg), venue, dict(fields)))

    def delete(self, tg):
        return False


def _run_pull(monkeypatch, payload):
    import bot.utils.credential_pull as cp
    monkeypatch.setattr(cp, "decrypt_payload", lambda _e: payload)
    store = _FakeStore()
    rows = [{"user_id": 5, "telegram_id": "500", "action": "connect",
             "encrypted_payload": "ignored"}]
    acks = cp.process_pending(rows, store)
    return store, acks


def test_pull_hyperliquid_payload_imports_via_set_venue(monkeypatch):
    store, acks = _run_pull(monkeypatch, {
        "venue": "hyperliquid", "wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})
    assert acks == [{"user_id": 5, "action": "connect", "ok": True}]
    assert store.venue_sets == [("500", "hyperliquid",
                                 {"wallet_address": HL_WALLET, "agent_private_key": HL_AGENT})]
    assert store.bitget == {}  # bitget path untouched


def test_pull_venueless_payload_stays_bitget(monkeypatch):
    store, acks = _run_pull(monkeypatch, {
        "api_key": BG_KEY, "api_secret": BG_SEC, "passphrase": BG_PP})
    assert acks == [{"user_id": 5, "action": "connect", "ok": True}]
    assert store.bitget == {"500": (BG_KEY, BG_SEC, BG_PP)}  # legacy .set path
    assert store.venue_sets == []


def test_pull_incomplete_hyperliquid_payload_rejected(monkeypatch):
    store, acks = _run_pull(monkeypatch, {"venue": "hyperliquid", "wallet_address": HL_WALLET})
    assert acks[0]["ok"] is False and "incomplete" in acks[0]["error"]
    assert store.venue_sets == [] and store.bitget == {}
