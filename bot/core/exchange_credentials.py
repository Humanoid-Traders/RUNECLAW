"""
Per-user exchange credentials — encrypted at rest.

RUNECLAW today trades ONE shared operator Bitget account via the global
``CONFIG.exchange`` keys. To let each user trade THEIR OWN account, every user
links their own Bitget API key / secret / passphrase. Those are secrets that can
move real money, so this store keeps them **encrypted at rest** (Fernet / AES)
and only ever hands the plaintext back to the live-execution layer at trade time.

Design (mirrors bot/utils/attestation.py key handling):
  - One symmetric master key (Fernet). Sourced from the ``RUNECLAW_SECRETS_KEY``
    env var if set; otherwise generated once and persisted to a 0600 key file so
    ciphertext stays decryptable across restarts. A loud warning is logged when
    auto-generated, telling the operator to pin it in the environment.
  - Credentials are stored keyed by **Telegram id** (the id the execution layer
    has via ``confirm_trade(user_id=...)``), as a JSON map of Fernet ciphertexts.
  - Nothing here ever logs or returns a full key except ``get()`` (used only by
    the executor). Status surfaces use ``fingerprint()`` instead.

This module is pure storage + validation. It does NOT place trades and is not
wired into execution by itself — enabling per-user live trading is gated
separately (see PER_USER_LIVE_ENABLED and docs/LIVE_TRADING_ENABLEMENT.md).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("runeclaw.exchange_creds")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_CREDS_FILE = os.path.join(_STATE_DIR, "exchange_creds.enc")
_KEY_FILE = os.path.join(_STATE_DIR, ".exchange_secret.key")

# Credential field names stored per user.
_FIELDS = ("api_key", "api_secret", "passphrase")


def _load_or_create_master_key(key_file: str = _KEY_FILE) -> bytes:
    """Return the Fernet master key.

    Precedence: RUNECLAW_SECRETS_KEY env (a urlsafe-base64 Fernet key) > a
    persisted key file > a freshly generated key (persisted, 0600, with a loud
    warning so the operator pins it in the environment).
    """
    from cryptography.fernet import Fernet

    env_key = os.environ.get("RUNECLAW_SECRETS_KEY", "").strip()
    if env_key:
        # Validate it is a usable Fernet key; fail loud rather than silently
        # falling back to a different key (which would orphan existing data).
        Fernet(env_key.encode())  # raises if malformed
        return env_key.encode()

    p = Path(key_file)
    if p.exists():
        return p.read_bytes().strip()

    key = Fernet.generate_key()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(key)
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    log.warning(
        "RUNECLAW_SECRETS_KEY is not set — generated a new exchange-encryption "
        "key and persisted it to %s (0600). For production, set "
        "RUNECLAW_SECRETS_KEY=%s in the environment so the key is managed "
        "explicitly and survives a wiped data dir.",
        key_file, key.decode(),
    )
    return key


class ExchangeCredentialStore:
    """Fernet-encrypted per-user Bitget credential store, keyed by Telegram id."""

    def __init__(self, creds_file: str = _CREDS_FILE, key_file: str = _KEY_FILE) -> None:
        self._path = Path(creds_file)
        self._lock = threading.Lock()
        self._key_file = key_file
        self._fernet = None  # lazy — only when crypto is actually needed
        # Raw on-disk map: { telegram_id: { field: ciphertext_str } }
        self._enc: dict[str, dict] = {}
        self._load()

    # -- crypto ---------------------------------------------------------------

    def _cipher(self):
        if self._fernet is None:
            from cryptography.fernet import Fernet
            self._fernet = Fernet(_load_or_create_master_key(self._key_file))
        return self._fernet

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._enc = {}
            return
        try:
            with open(self._path) as f:
                self._enc = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.error("exchange_creds file unreadable — starting empty (existing "
                      "linked accounts will need to /connect again)")
            self._enc = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._enc, f, indent=2)
        try:
            os.chmod(str(tmp), 0o600)
        except OSError:
            pass
        tmp.rename(self._path)

    # -- public API -----------------------------------------------------------

    def set(self, telegram_id, api_key: str, api_secret: str, passphrase: str) -> None:
        """Encrypt and store a user's credentials (overwrites any existing)."""
        c = self._cipher()
        plain = {"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase}
        enc = {field: c.encrypt(plain[field].encode()).decode() for field in _FIELDS}
        with self._lock:
            self._enc[str(telegram_id)] = enc
            self._save()
        log.info("Stored encrypted exchange credentials for user %s", telegram_id)

    def has(self, telegram_id) -> bool:
        with self._lock:
            return str(telegram_id) in self._enc

    def get(self, telegram_id) -> Optional[dict]:
        """Decrypt and return ``{api_key, api_secret, passphrase}`` or None.

        Used by the execution layer at trade time. Returns None (never raises)
        if the user has no credentials or decryption fails (e.g. the master key
        changed) — the caller treats that as 'not connected'.
        """
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return None
        try:
            c = self._cipher()
            return {field: c.decrypt(enc[field].encode()).decode() for field in _FIELDS}
        except Exception as exc:  # InvalidToken, missing field, etc.
            log.error("Failed to decrypt exchange credentials for %s: %s", telegram_id, exc)
            return None

    def delete(self, telegram_id) -> bool:
        with self._lock:
            existed = str(telegram_id) in self._enc
            self._enc.pop(str(telegram_id), None)
            if existed:
                self._save()
        if existed:
            log.info("Deleted exchange credentials for user %s", telegram_id)
        return existed

    def fingerprint(self, telegram_id) -> str:
        """A safe, non-reversible identifier of the stored key for status display.

        Returns e.g. ``"BG-1a2b…f9"`` (a short hash of the api_key) or "" if none.
        Never reveals the key itself.
        """
        creds = self.get(telegram_id)
        if not creds or not creds.get("api_key"):
            return ""
        import hashlib
        h = hashlib.sha256(creds["api_key"].encode()).hexdigest()
        return f"BG-{h[:4]}…{h[-2:]}"


async def validate_bitget_credentials(
    api_key: str, api_secret: str, passphrase: str, sandbox: bool = False
) -> tuple[bool, str]:
    """Functionally validate Bitget credentials with a READ-ONLY balance fetch.

    Returns (ok, detail). ``detail`` is a short free USDT summary on success or a
    trimmed error string on failure. Proves the keys authenticate before we store
    them and before any order is ever placed. Never places an order.
    """
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        client = ccxt.bitget({
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "sandbox": sandbox,
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        bal = await client.fetch_balance({"type": "swap"})
        free = 0.0
        try:
            free = float((bal.get("USDT") or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} USDT free"
    except Exception as exc:
        return False, str(exc)[:200]
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


_STORE: Optional["ExchangeCredentialStore"] = None
_STORE_LOCK = threading.Lock()


def get_credential_store() -> "ExchangeCredentialStore":
    """Process-wide singleton credential store (lazy)."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = ExchangeCredentialStore()
    return _STORE


def basic_key_format_ok(api_key: str, api_secret: str, passphrase: str) -> bool:
    """Cheap sanity check before the network validation: non-empty, no spaces,
    plausible lengths. Not a security control — just catches obvious paste
    mistakes early."""
    for v in (api_key, api_secret, passphrase):
        if not v or " " in v or "\n" in v:
            return False
    return len(api_key) >= 12 and len(api_secret) >= 12 and len(passphrase) >= 1
