"""
NEWS-2 — Bring-Your-Own-News (BYON): per-user paid news API keys.

Covers the pure adapter (provider/key validation, §4-safe parsing, fail-soft
fetch) and the encrypted-at-rest key store. §4 spine: the adapter maps ONLY the
provider's public fields (headline + source + link), never an article body, and
never raises or surfaces the key (F-15).
"""

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from bot.core import news_byon as nb


class TestValidation:
    def test_known_provider(self):
        assert nb.validate_provider("cryptopanic")
        assert nb.validate_provider("CryptoPanic")   # case-insensitive
        assert not nb.validate_provider("nope")
        assert not nb.validate_provider("")

    def test_providers_catalogue_is_id_label_only(self):
        provs = nb.providers()
        assert provs and all(set(p) == {"id", "label"} for p in provs)
        assert any(p["id"] == "cryptopanic" for p in provs)

    def test_key_charset_length(self):
        assert nb.validate_key("cryptopanic", "a" * 20)
        assert nb.validate_key("cryptopanic", "A1b2C3d4E5f6G7h8")
        # copy-paste damage is rejected before we ever store it
        assert not nb.validate_key("cryptopanic", "ab cd ef")   # spaces
        assert not nb.validate_key("cryptopanic", "abc\ndef")   # newline
        assert not nb.validate_key("cryptopanic", "abc")        # too short
        assert not nb.validate_key("cryptopanic", "a" * 200)    # too long
        assert not nb.validate_key("cryptopanic", "")
        assert not nb.validate_key("bogus", "a" * 20)           # bad provider

    def test_fingerprint_never_echoes_the_key(self):
        fp = nb.key_fingerprint("supersecretkey1234")
        assert fp == "…1234"
        assert "supersecret" not in fp
        assert nb.key_fingerprint("ab") == "set"    # too short to mask → generic


class TestParse:
    def test_maps_public_fields_only(self):
        data = {"results": [{
            "title": "BTC breaks out",
            "url": "https://cp/x",
            "source": {"title": "CoinDesk"},
            "currencies": [{"code": "btc"}, {"code": "eth"}],
            "published_at": "2026-01-01T00:00:00Z",
            # a body/description field, if present, must be IGNORED (§4)
            "body": "full paywalled article text that must never appear",
        }]}
        out = nb._parse_cryptopanic(data, now=0.0, limit=20)
        assert len(out) == 1
        it = out[0]
        assert it["title"] == "BTC breaks out"
        assert it["source"] == "CoinDesk"
        assert it["symbols"] == ["BTC", "ETH"]
        assert it["byon"] is True
        # the article body is never carried through
        assert "body" not in it
        assert all("paywalled" not in str(v) for v in it.values())

    def test_drops_items_without_a_title(self):
        data = {"results": [{"url": "https://x"}, {"title": "keep me"}]}
        out = nb._parse_cryptopanic(data, now=0.0, limit=20)
        assert [i["title"] for i in out] == ["keep me"]

    def test_respects_limit(self):
        data = {"results": [{"title": f"h{i}"} for i in range(50)]}
        assert len(nb._parse_cryptopanic(data, now=0.0, limit=5)) == 5

    def test_malformed_never_raises(self):
        for bad in (None, {}, {"results": None}, {"results": "x"}, [], 42):
            assert nb._parse_cryptopanic(bad, now=0.0, limit=20) == []

    def test_source_defaults_when_missing(self):
        out = nb._parse_cryptopanic({"results": [{"title": "h"}]}, now=0.0, limit=20)
        assert out[0]["source"] == "CryptoPanic"


class TestFetchFailSoft:
    def test_bad_provider_or_key_returns_empty(self):
        run = asyncio.new_event_loop().run_until_complete
        assert run(nb.fetch_byon_news("bogus", "a" * 20, ["BTC/USDT"], 0.0)) == []
        assert run(nb.fetch_byon_news("cryptopanic", "", ["BTC/USDT"], 0.0)) == []
        assert run(nb.fetch_byon_news("cryptopanic", "bad key", ["BTC/USDT"], 0.0)) == []

    def test_base_asset_strips_quote_and_suffix(self):
        assert nb._base_asset("BTC/USDT") == "BTC"
        assert nb._base_asset("ETH-PERP") == "ETH"
        assert nb._base_asset("SOLUSDC") == "SOL"
        assert nb._base_asset("") == ""


@pytest.fixture()
def models(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
    import bot.db.models as m
    monkeypatch.setattr(m, "DB_PATH", Path(tmp_path / "t.db"))
    monkeypatch.setattr(m, "_LLM_CIPHER", None)
    m.init_db()
    return m


class TestKeyStore:
    def test_round_trip_encrypted_at_rest(self, models):
        uid = models.create_user("a@x.io", "password12")
        models.save_user_news_key(uid, "cryptopanic", "abcdef1234567890")
        prov, key = models.get_user_news_key(uid)
        assert prov == "cryptopanic"
        assert key == "abcdef1234567890"          # caller sees plaintext
        with models.get_db() as db:
            raw = db.execute(
                "SELECT api_key FROM user_news_keys WHERE user_id=?", (uid,)
            ).fetchone()["api_key"]
        assert raw != "abcdef1234567890"           # not stored in the clear
        assert "abcdef1234567890" not in raw

    def test_clear_removes_the_key(self, models):
        uid = models.create_user("b@x.io", "password12")
        models.save_user_news_key(uid, "cryptopanic", "abcdef1234567890")
        models.clear_user_news_key(uid)
        assert models.get_user_news_key(uid) == ("", "")

    def test_unset_reads_empty(self, models):
        uid = models.create_user("c@x.io", "password12")
        assert models.get_user_news_key(uid) == ("", "")

    def test_replace_updates_in_place(self, models):
        uid = models.create_user("d@x.io", "password12")
        models.save_user_news_key(uid, "cryptopanic", "first1234567890a")
        models.save_user_news_key(uid, "cryptopanic", "second234567890b")
        assert models.get_user_news_key(uid) == ("cryptopanic", "second234567890b")
