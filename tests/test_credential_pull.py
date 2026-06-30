"""
Bot-side pull of website-queued exchange credentials (Stage 2b of web wallet).

The website encrypts a user's Bitget keys at rest (AES-256-GCM, app/lib/
creds_crypto.js); the bot pulls + decrypts them with WEB_CREDS_KEY and imports
into its Fernet store. These cover the decryptor (including a REAL Node-produced
envelope, proving cross-language compatibility) and the per-row apply/ack logic.
"""

import base64
import json

import pytest

import bot.utils.credential_pull as cp


# A real envelope produced by app/lib/creds_crypto.js (Node) with this key —
# decrypting it in Python proves the cross-language format is compatible.
NODE_KEY = "i9WZDHFdLxEAJYcGMeI7zEJojI2Ir5747f6SOhDMMU4="
NODE_ENVELOPE = ('{"v":1,"iv":"oxqTBtAZY9ek+Ohb","tag":"JNXo7PhQCuyCD79JN9NjwA==",'
                 '"ct":"50DNzqYtzyUNd1gcjl6e2dhld9EvHoNMaaBrQflf2r6XI8zp1FSS+kl/'
                 'IhpsU9Fz8ig9wNgbuLHSl7L0u0DQjL84uTA="}')


class _FakeStore:
    def __init__(self):
        self.creds = {}
        self.sets = []
        self.deletes = []

    def set(self, tg, api_key, api_secret, passphrase):
        self.creds[str(tg)] = (api_key, api_secret, passphrase)
        self.sets.append(str(tg))

    def delete(self, tg):
        self.deletes.append(str(tg))
        return self.creds.pop(str(tg), None) is not None


def _encrypt_py(obj, key_b64):
    """Encrypt in the website's {v,iv,tag,ct} format using Python (for fixtures)."""
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    iv = os.urandom(12)
    ct_tag = AESGCM(key).encrypt(iv, json.dumps(obj).encode(), None)
    ct, tag = ct_tag[:-16], ct_tag[-16:]
    return json.dumps({"v": 1, "iv": base64.b64encode(iv).decode(),
                       "tag": base64.b64encode(tag).decode(),
                       "ct": base64.b64encode(ct).decode()})


class TestDecrypt:
    def test_decrypts_real_node_envelope(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        out = cp.decrypt_payload(NODE_ENVELOPE)
        assert out == {"api_key": "NODE_AK", "api_secret": "NODE_SEC",
                       "passphrase": "NODE_PP"}

    def test_roundtrip_python_encrypted(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        env = _encrypt_py({"api_key": "A", "api_secret": "B", "passphrase": "C"}, NODE_KEY)
        assert cp.decrypt_payload(env)["api_secret"] == "B"

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("WEB_CREDS_KEY", raising=False)
        with pytest.raises(ValueError):
            cp.decrypt_payload(NODE_ENVELOPE)


class TestProcessPending:
    def _row(self, action="connect", uid=1, tg="999", payload=None):
        return {"user_id": uid, "telegram_id": tg, "action": action,
                "encrypted_payload": payload}

    def _payload(self):
        return _encrypt_py({"api_key": "AK", "api_secret": "SEC", "passphrase": "PP"}, NODE_KEY)

    def test_connect_imports_and_acks(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        changed = []
        acks = cp.process_pending([self._row(payload=self._payload())], store,
                                  on_change=changed.append)
        assert store.creds["999"] == ("AK", "SEC", "PP")
        assert acks == [{"user_id": 1, "action": "connect", "ok": True}]
        assert changed == ["999"]

    def test_disconnect_deletes_and_acks(self):
        store = _FakeStore()
        store.creds["999"] = ("x", "y", "z")
        acks = cp.process_pending([self._row(action="disconnect", payload=None)], store)
        assert store.deletes == ["999"]
        assert acks == [{"user_id": 1, "action": "disconnect", "ok": True}]

    def test_validator_false_rejects_without_import(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        acks = cp.process_pending([self._row(payload=self._payload())], store,
                                  validator=lambda c: False)
        assert store.sets == []
        assert acks[0]["ok"] is False

    def test_validator_none_skips_without_ack(self, monkeypatch):
        # Transient/unverifiable -> leave un-acked so it retries next poll.
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        acks = cp.process_pending([self._row(payload=self._payload())], store,
                                  validator=lambda c: None)
        assert store.sets == []
        assert acks == []

    def test_corrupt_payload_acked_failed(self, monkeypatch):
        # Undecryptable row must be acked (ok=False) so it isn't retried forever.
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        acks = cp.process_pending([self._row(payload='{"v":1,"iv":"AA","tag":"AA","ct":"AA"}')],
                                  store)
        assert store.sets == []
        assert acks[0]["ok"] is False

    def test_missing_telegram_id_skipped(self):
        store = _FakeStore()
        acks = cp.process_pending([self._row(tg="", payload=None)], store)
        assert acks == []


class TestConfigGate:
    def test_not_configured_pull_is_noop(self, monkeypatch):
        monkeypatch.delenv("WEB_CREDS_KEY", raising=False)
        assert cp.pull_and_apply() == 0
