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
from typing import Any, Optional

log = logging.getLogger("runeclaw.exchange_creds")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_CREDS_FILE = os.path.join(_STATE_DIR, "exchange_creds.enc")
_KEY_FILE = os.path.join(_STATE_DIR, ".exchange_secret.key")

# Credential field names stored per user, per venue. Each venue authenticates
# with a different shape: Bitget uses an API key triple; Bybit/BingX use a plain
# key+secret; Hyperliquid uses the account wallet address + an *agent* (API)
# wallet private key (never the main wallet key). Adding a venue here + a
# matching create_exchange branch in bot/core/venues.py is all it takes to make
# it connectable (must match the venue ids registered in venues.py).
_VENUE_FIELDS: dict[str, tuple[str, ...]] = {
    "bitget": ("api_key", "api_secret", "passphrase"),
    "bybit": ("api_key", "api_secret"),
    "bingx": ("api_key", "api_secret"),
    "hyperliquid": ("wallet_address", "agent_private_key"),
}
_DEFAULT_VENUE = "bitget"
# Legacy alias — the pre-multi-venue field tuple. Kept so any external reference
# still resolves; the Bitget path is byte-identical to before.
_FIELDS = _VENUE_FIELDS[_DEFAULT_VENUE]


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
        # Persist the env key to the 0600 file too, so that a wiped .env — which
        # removes RUNECLAW_SECRETS_KEY from the environment — falls back to the
        # SAME key from disk on the next boot instead of generating a fresh one
        # and orphaning all ciphertext (the secrets-vault + per-user store both
        # rely on this). Only write when the file is absent or differs.
        try:
            p = Path(key_file)
            if not p.exists() or p.read_bytes().strip() != env_key.encode():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(env_key.encode())
                try:
                    os.chmod(str(p), 0o600)
                except OSError:
                    pass
        except OSError as exc:
            log.debug("Could not persist master key to %s: %s", key_file, exc)
        return env_key.encode()

    p = Path(key_file)
    if p.exists():
        return p.read_bytes().strip()

    key: bytes = Fernet.generate_key()
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
        # Annotated Any (not None) so the lazy ``Fernet`` assignment in _cipher
        # type-checks without importing cryptography at module top (it's an
        # optional extra). Now reachable by the gated mypy run via
        # config -> secrets_vault -> exchange_credentials.
        self._fernet: Any = None  # lazy — only when crypto is actually needed
        # Raw on-disk map. Two record shapes coexist:
        #   NEW:    { telegram_id: { "venue": "bitget", "fields": { field: ct } } }
        #   LEGACY: { telegram_id: { field: ct } }  (implicitly Bitget)
        # _read_record() normalizes both; legacy files decrypt with zero rewrite.
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

    # -- record normalization -------------------------------------------------

    @staticmethod
    def _read_record(enc: dict) -> tuple[str, dict]:
        """Normalize an on-disk record to ``(venue, {field: ciphertext})``.

        A record carrying explicit ``venue``/``fields`` keys is used as-is; any
        other (legacy flat) record is treated as Bitget — so pre-multi-venue
        ``exchange_creds.enc`` files keep decrypting without a rewrite.
        """
        if isinstance(enc, dict) and "fields" in enc and "venue" in enc:
            return str(enc["venue"]), dict(enc["fields"])
        return _DEFAULT_VENUE, dict(enc)

    # -- public API -----------------------------------------------------------

    def set(self, telegram_id, api_key: str, api_secret: str, passphrase: str) -> None:
        """Encrypt and store a user's BITGET credentials (overwrites any existing).

        Kept for the Bitget path (its 3-positional signature is unchanged); it
        delegates to the venue-aware ``set_venue``.
        """
        self.set_venue(telegram_id, "bitget", {
            "api_key": api_key, "api_secret": api_secret, "passphrase": passphrase,
        })

    def set_venue(self, telegram_id, venue: str, fields: dict) -> None:
        """Encrypt and store a user's credentials for ``venue`` (overwrites any
        existing). ``fields`` must contain exactly the venue's required keys
        (see ``_VENUE_FIELDS``). Raises ValueError on an unknown venue or a
        missing field, so a bad connect can never persist a half-record."""
        venue = str(venue).lower().strip()
        expected = _VENUE_FIELDS.get(venue)
        if expected is None:
            raise ValueError(f"unknown venue {venue!r}")
        missing = [f for f in expected if not fields.get(f)]
        if missing:
            raise ValueError(f"missing {venue} credential field(s): {missing}")
        c = self._cipher()
        enc = {f: c.encrypt(str(fields[f]).encode()).decode() for f in expected}
        with self._lock:
            self._enc[str(telegram_id)] = {"venue": venue, "fields": enc}
            self._save()
        log.info("Stored encrypted %s credentials for user %s", venue, telegram_id)

    def has(self, telegram_id) -> bool:
        with self._lock:
            return str(telegram_id) in self._enc

    def user_ids(self) -> list:
        """All Telegram ids with stored credentials. Used at startup to rehydrate
        per-user executors so their open positions resume being monitored."""
        with self._lock:
            return list(self._enc.keys())

    def get(self, telegram_id) -> Optional[dict]:
        """Decrypt and return the venue-specific credential fields, or None.

        Bitget records return ``{api_key, api_secret, passphrase}`` (unchanged);
        Hyperliquid records return ``{wallet_address, agent_private_key}``. Used
        by the execution layer at trade time. Returns None (never raises) if the
        user has no credentials or decryption fails (e.g. the master key
        changed) — the caller treats that as 'not connected'.
        """
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return None
        venue, fields_enc = self._read_record(enc)
        field_names = _VENUE_FIELDS.get(venue, _FIELDS)
        try:
            c = self._cipher()
            return {f: c.decrypt(fields_enc[f].encode()).decode() for f in field_names}
        except Exception as exc:  # InvalidToken, missing field, etc.
            log.error("Failed to decrypt exchange credentials for %s: %s", telegram_id, exc)
            return None

    def get_venue(self, telegram_id) -> str:
        """The venue a user's stored credentials belong to (``"bitget"`` default,
        including for legacy records and users with nothing stored)."""
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return _DEFAULT_VENUE
        venue, _ = self._read_record(enc)
        return venue

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

        Returns e.g. ``"BG-1a2b…f9"`` (Bitget, a short hash of the api_key) or
        ``"HL-…"`` (Hyperliquid, hash of the wallet address), or "" if none.
        Never reveals the key itself.
        """
        creds = self.get(telegram_id)
        if not creds:
            return ""
        venue = self.get_venue(telegram_id)
        # Fingerprint the venue's identity field (Bitget key stays byte-identical).
        ident_field = "api_key" if venue == "bitget" else _VENUE_FIELDS.get(
            venue, ("",))[0]
        ident = creds.get(ident_field)
        if not ident:
            return ""
        import hashlib
        prefix = "BG" if venue == "bitget" else "HL" if venue == "hyperliquid" else venue[:2].upper()
        h = hashlib.sha256(ident.encode()).hexdigest()
        return f"{prefix}-{h[:4]}…{h[-2:]}"


# Bitget error for API keys that belong to the OTHER environment: a
# demo-trading key hitting the live API (or a live key hitting demo).
_WRONG_ENV_CODE = "40099"


async def _bitget_balance_probe(api_key: str, api_secret: str,
                                passphrase: str, sandbox: bool) -> tuple[bool, str]:
    """One read-only balance fetch against ONE Bitget environment.

    ``sandbox=True`` activates Bitget demo trading via ccxt's
    set_sandbox_mode (sends the PAPTRADING=1 header). Returns (ok, detail).
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
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "uta": True,  # Support Bitget Unified Trading Account
            },
        })
        # Explicit and version-stable: for bitget this toggles the demo-trading
        # header rather than relying on a constructor key.
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
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


async def validate_bitget_credentials(
    api_key: str, api_secret: str, passphrase: str, sandbox: bool = False
) -> tuple[bool, str]:
    """Functionally validate Bitget credentials with a READ-ONLY balance fetch.

    Returns (ok, detail). ``detail`` is a short free USDT summary on success or a
    trimmed error string on failure. Proves the keys authenticate before we store
    them and before any order is ever placed. Never places an order.

    Bitget code 40099 ("exchange environment is incorrect") means the key
    belongs to the OTHER environment (demo vs live). We retry once against the
    opposite environment purely to diagnose, and if the key authenticates
    there, return a precise actionable message instead of the raw JSON —
    without ever storing a wrong-environment key.
    """
    ok, detail = await _bitget_balance_probe(api_key, api_secret, passphrase, sandbox)
    if ok or _WRONG_ENV_CODE not in detail:
        return ok, detail
    # 40099: diagnose which environment the key actually belongs to.
    other_ok, _ = await _bitget_balance_probe(api_key, api_secret, passphrase,
                                              not sandbox)
    if other_ok:
        if sandbox:
            return False, (
                "These are LIVE Bitget keys, but this bot runs in DEMO "
                "(paper) trading (BITGET_SANDBOX=true in the bot's .env). "
                "Create the API keys inside Bitget demo trading, with "
                "USDT-M futures read + trade permission — or ask the "
                "operator to set BITGET_SANDBOX=false for production.")
        return False, (
            "These are DEMO-trading Bitget keys, but this bot trades LIVE "
            "(bot environment: PRODUCTION). Create the API keys in your "
            "main Bitget account (API Management, not demo trading), with "
            "USDT-M futures read + trade permission.")
    return False, detail


async def _hyperliquid_balance_probe(wallet_address: str, agent_private_key: str,
                                     sandbox: bool) -> tuple[bool, str]:
    """One read-only balance fetch against Hyperliquid (USDC perps).

    Hyperliquid authenticates with the account's public wallet address plus an
    *agent* (API) wallet private key — never the main wallet key. ``sandbox``
    routes to the testnet. Returns (ok, detail).
    """
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        client = ccxt.hyperliquid({
            "walletAddress": wallet_address,
            "privateKey": agent_private_key,
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance()
        free = 0.0
        try:
            free = float((bal.get("USDC") or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} USDC free"
    except Exception as exc:
        return False, str(exc)[:200]
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def validate_hyperliquid_credentials(
    wallet_address: str, agent_private_key: str, sandbox: bool = False
) -> tuple[bool, str]:
    """Functionally validate Hyperliquid credentials with a READ-ONLY balance
    fetch. Returns (ok, detail) — a short free USDC summary on success or a
    trimmed error on failure. Proves the agent key authenticates for the wallet
    before we store it and before any order is placed. Never places an order."""
    return await _hyperliquid_balance_probe(wallet_address, agent_private_key, sandbox)


async def _keysecret_balance_probe(exchange_id: str, api_key: str,
                                   api_secret: str, sandbox: bool) -> tuple[bool, str]:
    """Read-only balance fetch for a plain key+secret ccxt venue (Bybit, BingX).

    Both are USDT-margined swap exchanges that authenticate with apiKey/secret
    only. Returns (ok, detail). Never places an order."""
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        factory = getattr(ccxt, exchange_id, None)
        if factory is None:
            return False, f"ccxt has no exchange {exchange_id!r}"
        client = factory({
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance()
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


async def validate_venue_credentials(venue: str, fields: dict,
                                     sandbox: bool = False) -> tuple[bool, str]:
    """Read-only-validate a user's credentials for ``venue`` (dispatches to the
    per-venue probe). Returns (ok, detail). Never places an order."""
    venue = str(venue).lower().strip()
    if venue == "bitget":
        return await validate_bitget_credentials(
            fields["api_key"], fields["api_secret"], fields["passphrase"], sandbox)
    if venue == "hyperliquid":
        return await validate_hyperliquid_credentials(
            fields["wallet_address"], fields["agent_private_key"], sandbox)
    if venue in ("bybit", "bingx"):
        return await _keysecret_balance_probe(
            venue, fields["api_key"], fields["api_secret"], sandbox)
    return False, f"unknown venue {venue!r}"


def _balance_total(bal: dict, currency: str) -> float:
    """Total (free+used) of ``currency`` from a ccxt fetch_balance dict.

    Pure and defensive: prefers the 'total' field, falls back to free+used,
    and returns 0.0 on any malformed shape — a balance display must never
    raise over an exchange's response quirks."""
    try:
        row = bal.get(currency) or {}
        total = row.get("total")
        if total is not None:
            return float(total)
        return float(row.get("free") or 0.0) + float(row.get("used") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


async def balance_snapshot(venue: str, fields: dict,
                           sandbox: bool = False) -> dict:
    """READ-ONLY equity snapshot for a user's stored venue credentials.

    One fetch_balance — the exact same call the connect-time validators
    make — returning numbers instead of a validation string:
    ``{ok, venue, currency, equity_usd, detail}``. Never raises, never
    writes, never places an order; credentials are used in-process only
    and never appear in the returned dict.
    """
    venue = str(venue).lower().strip()
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "venue": venue, "equity_usd": None,
                "detail": f"ccxt unavailable: {exc}"}
    currency = "USDC" if venue == "hyperliquid" else "USDT"
    try:
        if venue == "bitget":
            client = ccxt.bitget({
                "apiKey": fields["api_key"], "secret": fields["api_secret"],
                "password": fields["passphrase"], "timeout": 15000,
                "enableRateLimit": True,
                "options": {"defaultType": "swap", "uta": True},
            })
        elif venue == "hyperliquid":
            client = ccxt.hyperliquid({
                "walletAddress": fields["wallet_address"],
                "privateKey": fields["agent_private_key"],
                "timeout": 15000, "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
        elif venue in ("bybit", "bingx"):
            factory = getattr(ccxt, venue, None)
            if factory is None:
                return {"ok": False, "venue": venue, "equity_usd": None,
                        "detail": f"ccxt has no exchange {venue!r}"}
            client = factory({
                "apiKey": fields["api_key"], "secret": fields["api_secret"],
                "timeout": 15000, "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
        else:
            return {"ok": False, "venue": venue, "equity_usd": None,
                    "detail": f"unknown venue {venue!r}"}
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        params = {"type": "swap"} if venue == "bitget" else {}
        bal = await client.fetch_balance(params)
        equity = _balance_total(bal, currency)
        return {"ok": True, "venue": venue, "currency": currency,
                "equity_usd": round(equity, 2),
                "detail": f"{equity:.2f} {currency} total"}
    except Exception as exc:
        return {"ok": False, "venue": venue, "equity_usd": None,
                "detail": str(exc)[:200]}
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def basic_venue_format_ok(venue: str, fields: dict) -> bool:
    """Cheap per-venue paste-mistake check before the network probe."""
    venue = str(venue).lower().strip()
    if venue == "bitget":
        return basic_key_format_ok(
            fields.get("api_key", ""), fields.get("api_secret", ""),
            fields.get("passphrase", ""))
    if venue == "hyperliquid":
        return basic_hl_format_ok(
            fields.get("wallet_address", ""), fields.get("agent_private_key", ""))
    if venue in ("bybit", "bingx"):
        ak, sec = fields.get("api_key", ""), fields.get("api_secret", "")
        for v in (ak, sec):
            if not v or " " in v or "\n" in v:
                return False
        return len(ak) >= 8 and len(sec) >= 8
    return False


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


def basic_hl_format_ok(wallet_address: str, agent_private_key: str) -> bool:
    """Cheap sanity check for Hyperliquid: a 0x-prefixed 40-hex-char wallet
    address and a 0x-prefixed 64-hex-char private key (with or without the 0x).
    Not a security control — catches obvious paste mistakes before the network
    probe."""
    for v in (wallet_address, agent_private_key):
        if not v or " " in v or "\n" in v:
            return False
    addr = wallet_address[2:] if wallet_address.lower().startswith("0x") else wallet_address
    key = agent_private_key[2:] if agent_private_key.lower().startswith("0x") else agent_private_key
    hexset = set("0123456789abcdefABCDEF")
    if len(addr) != 40 or any(ch not in hexset for ch in addr):
        return False
    if len(key) != 64 or any(ch not in hexset for ch in key):
        return False
    return True


def valid_venue_ids() -> tuple[str, ...]:
    """Venues the per-user credential store can hold (for /connect + web)."""
    return tuple(_VENUE_FIELDS.keys())
