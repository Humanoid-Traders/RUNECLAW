"""
Per-user LLM API key encryption at rest (BYOK Phase 1 — security prerequisite).

LLM provider keys were stored in the user_settings table as PLAINTEXT. They are
real secrets (provider quota/billing), so they are now Fernet-encrypted at rest
with the same master key as the exchange-credential store, transparently at the
save/get boundary. Legacy plaintext rows read back unchanged and re-encrypt on
the next save.
"""

import importlib

import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def models(monkeypatch, tmp_path):
    # Hermetic: pin a fresh master key and an isolated DB file, then (re)import
    # the module so DB_PATH and the cipher pick them up.
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import bot.db.models as m
    importlib.reload(m)
    m._LLM_CIPHER = None  # force rebuild against the pinned key
    m.init_db()
    return m


class TestHelpers:
    def test_round_trip(self, models):
        assert models._decrypt_llm_key(models._encrypt_llm_key("sk-abc123")) == "sk-abc123"

    def test_empty_stays_empty(self, models):
        assert models._encrypt_llm_key("") == ""
        assert models._decrypt_llm_key("") == ""

    def test_ciphertext_is_not_plaintext(self, models):
        enc = models._encrypt_llm_key("sk-super-secret")
        assert enc != "sk-super-secret"
        assert "sk-super-secret" not in enc

    def test_legacy_plaintext_passthrough(self, models):
        # A value that isn't valid Fernet ciphertext is treated as legacy
        # plaintext and returned as-is (transparent migration).
        assert models._decrypt_llm_key("sk-legacy-plaintext-key") == "sk-legacy-plaintext-key"


class TestRoundTripThroughDb:
    def test_save_get_round_trips_plaintext(self, models):
        uid = models.create_user("a@x.io", "password12")
        models.save_user_settings(
            models.UserSettings(user_id=uid, llm_provider="anthropic", llm_api_key="sk-ant-xyz"))
        got = models.get_user_settings(uid)
        assert got.llm_api_key == "sk-ant-xyz"  # caller sees plaintext
        assert got.llm_provider == "anthropic"

    def test_stored_column_is_ciphertext(self, models):
        uid = models.create_user("b@x.io", "password12")
        models.save_user_settings(
            models.UserSettings(user_id=uid, llm_api_key="sk-plaintext-secret"))
        with models.get_db() as db:
            row = db.execute(
                "SELECT llm_api_key FROM user_settings WHERE user_id=?", (uid,)).fetchone()
        raw = row["llm_api_key"]
        assert raw != "sk-plaintext-secret"          # not stored in the clear
        assert "sk-plaintext-secret" not in raw
        assert models._decrypt_llm_key(raw) == "sk-plaintext-secret"

    def test_empty_key_round_trips(self, models):
        uid = models.create_user("c@x.io", "password12")
        models.save_user_settings(models.UserSettings(user_id=uid, llm_api_key=""))
        assert models.get_user_settings(uid).llm_api_key == ""
