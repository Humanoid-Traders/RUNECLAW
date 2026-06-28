"""
Per-user exchange credential store — encryption-at-rest + safety guarantees.

These cover bot/core/exchange_credentials.py: the Fernet-encrypted, per-user
(Telegram-id-keyed) Bitget credential store that backs /connect /disconnect
/exchange. The non-negotiables proven here:

  * round-trip: set -> get returns the exact plaintext, keyed by user
  * AT REST the ciphertext file contains NONE of the plaintext secrets
  * a freshly-constructed store reads the same key file and decrypts (survives
    restart)
  * a DIFFERENT master key cannot decrypt (get() returns None, never raises)
  * fingerprint() is a non-reversible tag — never the key
  * basic_key_format_ok rejects obvious paste mistakes

Everything runs against tmp files (env RUNECLAW_SECRETS_KEY / tmp paths) so the
real data/ dir and any operator key are never touched. No network.
"""

from cryptography.fernet import Fernet

from bot.core.exchange_credentials import (
    ExchangeCredentialStore,
    basic_key_format_ok,
    _load_or_create_master_key,
)

API_KEY = "bg_publickey_abcdef123456"
API_SECRET = "supersecretsecret_value_7890"
PASSPHRASE = "myPassphrase!1"


def _store(tmp_path, key=None):
    creds = tmp_path / "exchange_creds.enc"
    kf = tmp_path / ".exchange_secret.key"
    if key is not None:
        kf.write_bytes(key)
    return ExchangeCredentialStore(creds_file=str(creds), key_file=str(kf)), creds, kf


def test_round_trip_and_has(tmp_path):
    s, _, _ = _store(tmp_path, key=Fernet.generate_key())
    assert s.has("111") is False
    assert s.get("111") is None
    s.set("111", API_KEY, API_SECRET, PASSPHRASE)
    assert s.has("111") is True
    got = s.get("111")
    assert got == {"api_key": API_KEY, "api_secret": API_SECRET, "passphrase": PASSPHRASE}


def test_keyed_per_user(tmp_path):
    s, _, _ = _store(tmp_path, key=Fernet.generate_key())
    s.set("111", API_KEY, API_SECRET, PASSPHRASE)
    s.set("222", "k2_otherkey0000", "s2_othersecret00", "pp2")
    assert s.get("111")["api_key"] == API_KEY
    assert s.get("222")["api_key"] == "k2_otherkey0000"
    assert s.has("333") is False


def test_ciphertext_has_no_plaintext_at_rest(tmp_path):
    s, creds, _ = _store(tmp_path, key=Fernet.generate_key())
    s.set("111", API_KEY, API_SECRET, PASSPHRASE)
    raw = creds.read_text()
    for secret in (API_KEY, API_SECRET, PASSPHRASE):
        assert secret not in raw, f"plaintext {secret!r} leaked to disk"


def test_survives_reload_same_key(tmp_path):
    key = Fernet.generate_key()
    s1, creds, kf = _store(tmp_path, key=key)
    s1.set("111", API_KEY, API_SECRET, PASSPHRASE)
    # Brand-new store object, same files -> must decrypt (simulates restart)
    s2 = ExchangeCredentialStore(creds_file=str(creds), key_file=str(kf))
    assert s2.get("111") == {
        "api_key": API_KEY, "api_secret": API_SECRET, "passphrase": PASSPHRASE,
    }


def test_wrong_key_returns_none_never_raises(tmp_path):
    key = Fernet.generate_key()
    s1, creds, _ = _store(tmp_path, key=key)
    s1.set("111", API_KEY, API_SECRET, PASSPHRASE)
    # Same ciphertext file but a DIFFERENT master key -> cannot decrypt.
    other_kf = tmp_path / "other.key"
    other_kf.write_bytes(Fernet.generate_key())
    s2 = ExchangeCredentialStore(creds_file=str(creds), key_file=str(other_kf))
    assert s2.has("111") is True          # record exists...
    assert s2.get("111") is None          # ...but is undecryptable -> None, no raise


def test_delete(tmp_path):
    s, _, _ = _store(tmp_path, key=Fernet.generate_key())
    s.set("111", API_KEY, API_SECRET, PASSPHRASE)
    assert s.delete("111") is True
    assert s.has("111") is False
    assert s.delete("111") is False       # idempotent


def test_fingerprint_is_safe(tmp_path):
    s, _, _ = _store(tmp_path, key=Fernet.generate_key())
    assert s.fingerprint("111") == ""     # nothing linked
    s.set("111", API_KEY, API_SECRET, PASSPHRASE)
    fp = s.fingerprint("111")
    assert fp.startswith("BG-")
    # Never reveals any actual secret material.
    for secret in (API_KEY, API_SECRET, PASSPHRASE):
        assert secret not in fp
    # Stable for the same key.
    assert s.fingerprint("111") == fp


def test_env_key_precedence(tmp_path, monkeypatch):
    env_key = Fernet.generate_key()
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", env_key.decode())
    # Even though a key file exists with a different key, env wins.
    other = tmp_path / ".exchange_secret.key"
    other.write_bytes(Fernet.generate_key())
    assert _load_or_create_master_key(str(other)) == env_key


def test_generates_and_persists_key_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNECLAW_SECRETS_KEY", raising=False)
    kf = tmp_path / ".exchange_secret.key"
    assert not kf.exists()
    k1 = _load_or_create_master_key(str(kf))
    assert kf.exists()
    Fernet(k1)                            # is a valid Fernet key
    # Second call reuses the persisted key (stable across "restarts").
    assert _load_or_create_master_key(str(kf)) == k1


def test_basic_key_format_ok():
    assert basic_key_format_ok(API_KEY, API_SECRET, PASSPHRASE) is True
    assert basic_key_format_ok("", API_SECRET, PASSPHRASE) is False        # empty
    assert basic_key_format_ok("short", API_SECRET, PASSPHRASE) is False   # too short
    assert basic_key_format_ok("has space key here", API_SECRET, PASSPHRASE) is False
    assert basic_key_format_ok(API_KEY, API_SECRET, "") is False           # empty passphrase
    assert basic_key_format_ok(API_KEY + "\n", API_SECRET, PASSPHRASE) is False  # newline
