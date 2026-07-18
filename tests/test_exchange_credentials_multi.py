"""Multi-venue credential store: connecting a second venue must MERGE, never
overwrite; the active pointer follows the latest connect / set_active /
deletes; all legacy record shapes keep decrypting."""
import json

from bot.core.exchange_credentials import ExchangeCredentialStore


def _store(tmp_path):
    return ExchangeCredentialStore(
        creds_file=str(tmp_path / "c.enc"), key_file=str(tmp_path / "k.key"))


def test_second_venue_merges_and_active_pointer_moves(tmp_path):
    s = _store(tmp_path)
    s.set_venue("42", "bitget", {"api_key": "a", "api_secret": "b", "passphrase": "c"})
    s.set_venue("42", "bybit", {"api_key": "x", "api_secret": "y"})
    assert s.list_venues("42") == ["bitget", "bybit"]
    assert s.get_venue("42") == "bybit"           # latest connect becomes active
    assert s.get("42")["api_key"] == "x"          # active venue's fields
    assert s.get_for_venue("42", "bitget")["api_key"] == "a"  # bitget kept
    assert s.set_active("42", "bitget")
    assert s.get("42")["api_key"] == "a"
    assert s.set_active("42", "bybit")
    assert s.delete_venue("42", "bybit")
    assert s.get_venue("42") == "bitget"          # pointer falls back
    assert s.delete_venue("42", "bitget")
    assert not s.has("42")                        # empty record removed


def test_legacy_single_venue_record_reads_and_upgrades(tmp_path):
    s = _store(tmp_path)
    s.set_venue("7", "hyperliquid", {"wallet_address": "0xabc", "agent_private_key": "k"})
    # v2 single-venue shape alongside — read-through as that venue.
    raw = json.loads((tmp_path / "c.enc").read_text())
    raw["9"] = {"venue": "bitget", "fields": {}}
    (tmp_path / "c.enc").write_text(json.dumps(raw))
    s2 = _store(tmp_path)
    assert s2.list_venues("9") == ["bitget"]
    assert s2.get_venue("9") == "bitget"
    assert s2.get_for_venue("7", "hyperliquid")["wallet_address"] == "0xabc"


def test_set_active_requires_stored_credentials(tmp_path):
    s = _store(tmp_path)
    assert not s.set_active("1", "bybit")         # nothing stored
    s.set_venue("1", "bitget", {"api_key": "a", "api_secret": "b", "passphrase": "c"})
    assert not s.set_active("1", "bybit")         # not connected to bybit
    assert s.get_venue("1") == "bitget"
