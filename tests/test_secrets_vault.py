"""
Secrets vault — operator keys survive a wiped .env.

A redeploy that wipes .env used to leave the bot unable to authenticate (Bitget
40012 -> unprotected position). The vault mirrors present secrets into an
encrypted file under data/ and restores any the environment has lost on the next
boot. These tests prove the round-trip, the wiped-env self-heal, that the master
key survives even when RUNECLAW_SECRETS_KEY itself is wiped, and that the feature
is a clean no-op when disabled or idle.
"""

import os

import pytest

pytest.importorskip("cryptography")  # vault needs Fernet; optional extra

from bot.core import secrets_vault as sv


def _isolate(monkeypatch, tmp_path, enabled="true"):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SECRETS_VAULT_ENABLED", enabled)
    monkeypatch.delenv("RUNECLAW_SECRETS_KEY", raising=False)
    monkeypatch.delenv("RUNECLAW_VAULT_KEYS", raising=False)
    for k in sv._DEFAULT_MANAGED:
        monkeypatch.delenv(k, raising=False)


class TestSeedAndRestore:
    def test_seed_then_restore_round_trip(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("BITGET_API_KEY", "AKEY123456789")
        monkeypatch.setenv("BITGET_PASSPHRASE", "s3cret-pass")

        s = sv.seed_and_restore()
        assert "BITGET_API_KEY" in s["seeded"]
        assert "BITGET_PASSPHRASE" in s["seeded"]
        assert (tmp_path / "secrets_vault.enc").exists()

        # .env wipe: the keys vanish from the environment.
        monkeypatch.delenv("BITGET_API_KEY")
        monkeypatch.delenv("BITGET_PASSPHRASE")

        s2 = sv.seed_and_restore()
        assert set(s2["restored"]) == {"BITGET_API_KEY", "BITGET_PASSPHRASE"}
        assert os.environ["BITGET_API_KEY"] == "AKEY123456789"
        assert os.environ["BITGET_PASSPHRASE"] == "s3cret-pass"

    def test_store_secrets_persists_and_sets_env(self, tmp_path, monkeypatch):
        # Operator supplies a passphrase at runtime (admin /setexchange). It must
        # land in os.environ immediately AND persist encrypted so a later boot
        # with a wiped .env restores it.
        _isolate(monkeypatch, tmp_path)
        stored = sv.store_secrets({
            "BITGET_API_KEY": "OPKEY-123456",
            "BITGET_API_SECRET": "OPSEC-abcdef",
            "BITGET_PASSPHRASE": "op-pass-phrase",
        })
        assert set(stored) == {"BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE"}
        assert os.environ["BITGET_PASSPHRASE"] == "op-pass-phrase"
        assert (tmp_path / "secrets_vault.enc").exists()

        # Simulate a redeploy that wiped the .env — the passphrase self-heals.
        for k in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE"):
            monkeypatch.delenv(k, raising=False)
        restored = sv.seed_and_restore()["restored"]
        assert "BITGET_PASSPHRASE" in restored
        assert os.environ["BITGET_PASSPHRASE"] == "op-pass-phrase"

    def test_store_secrets_skips_blanks(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        stored = sv.store_secrets({"BITGET_API_KEY": "  ", "BITGET_PASSPHRASE": "real"})
        assert stored == ["BITGET_PASSPHRASE"]
        assert "BITGET_API_KEY" not in os.environ

    def test_store_secrets_sets_env_even_when_disabled(self, tmp_path, monkeypatch):
        # Disabled vault: the current process still recovers (env set), but nothing
        # is persisted to disk.
        _isolate(monkeypatch, tmp_path, enabled="false")
        stored = sv.store_secrets({"BITGET_PASSPHRASE": "live-only"})
        assert stored == ["BITGET_PASSPHRASE"]
        assert os.environ["BITGET_PASSPHRASE"] == "live-only"
        assert not (tmp_path / "secrets_vault.enc").exists()

    def test_master_key_survives_env_wipe(self, tmp_path, monkeypatch):
        from cryptography.fernet import Fernet
        _isolate(monkeypatch, tmp_path)
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", key)
        monkeypatch.setenv("BITGET_API_SECRET", "SEKRET-abcdef")

        sv.seed_and_restore()  # seeds + persists the env master key to disk
        assert (tmp_path / ".exchange_secret.key").exists()

        # Redeploy wipes BOTH the secret AND RUNECLAW_SECRETS_KEY from the env.
        monkeypatch.delenv("BITGET_API_SECRET")
        monkeypatch.delenv("RUNECLAW_SECRETS_KEY")

        s = sv.seed_and_restore()  # must decrypt via the file-persisted key
        assert "BITGET_API_SECRET" in s["restored"]
        assert os.environ["BITGET_API_SECRET"] == "SEKRET-abcdef"

    def test_reseed_is_idempotent(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        sv.seed_and_restore()
        s2 = sv.seed_and_restore()  # unchanged value -> not re-seeded
        assert s2["seeded"] == []
        assert s2["restored"] == []

    def test_custom_managed_key_via_env(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("RUNECLAW_VAULT_KEYS", "MY_CUSTOM_SECRET")
        monkeypatch.setenv("MY_CUSTOM_SECRET", "hunter2")
        s = sv.seed_and_restore()
        assert "MY_CUSTOM_SECRET" in s["seeded"]
        monkeypatch.delenv("MY_CUSTOM_SECRET")
        sv.seed_and_restore()
        assert os.environ.get("MY_CUSTOM_SECRET") == "hunter2"
        monkeypatch.delenv("MY_CUSTOM_SECRET", raising=False)


class TestNoOpSafety:
    def test_disabled_is_noop(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path, enabled="false")
        monkeypatch.setenv("BITGET_API_KEY", "X")
        s = sv.seed_and_restore()
        assert s == {"seeded": [], "restored": []}
        assert not (tmp_path / "secrets_vault.enc").exists()

    def test_idle_creates_no_files(self, tmp_path, monkeypatch):
        # Enabled, but nothing present and no existing vault -> no master key,
        # no vault file (keeps fresh checkouts and tests clean).
        _isolate(monkeypatch, tmp_path)
        s = sv.seed_and_restore()
        assert s == {"seeded": [], "restored": []}
        assert not (tmp_path / "secrets_vault.enc").exists()
        assert not (tmp_path / ".exchange_secret.key").exists()

    def test_crypto_absent_is_noop(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("BITGET_API_KEY", "X")
        monkeypatch.setattr(sv, "_cipher", lambda: None)  # simulate no crypto
        s = sv.seed_and_restore()
        assert s == {"seeded": [], "restored": []}
        assert not (tmp_path / "secrets_vault.enc").exists()
