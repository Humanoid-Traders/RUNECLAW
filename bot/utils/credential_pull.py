"""
Pull pending exchange-credential requests from the website and apply them.

Stage 2b of web wallet management. The website (Stage 2a) encrypts a user's
submitted Bitget keys at rest and queues a `pending_credentials` row; this module
PULLS those rows over the shared-secret channel, decrypts them with WEB_CREDS_KEY
(AES-256-GCM, the envelope written by app/lib/creds_crypto.js), optionally
validates them against Bitget, imports them into the bot's own Fernet-encrypted
ExchangeCredentialStore (keyed by telegram_id), and ACKs so the website clears the
row and flips the user's connection status.

Security: the website never holds the long-term keys (the bot's Fernet store is
the single owner); raw keys are never logged here; a corrupt/undecryptable row is
ACKed as failed (not retried forever); a transient validation failure is left
un-ACKed so it retries next poll.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

log = logging.getLogger(__name__)

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://pmvc58g2.mule.page")
SYNC_SECRET = os.getenv("BOT_SYNC_SECRET", "")


def _load_key() -> Optional[bytes]:
    """The shared AES key (WEB_CREDS_KEY): base64 (standard or url-safe) 32 bytes."""
    raw = (os.getenv("WEB_CREDS_KEY", "") or "").strip()
    if not raw:
        return None
    b64 = raw.replace("-", "+").replace("_", "/")
    try:
        key = base64.b64decode(b64)
    except Exception:
        return None
    return key if len(key) == 32 else None


def is_configured() -> bool:
    return _load_key() is not None and bool(SYNC_SECRET)


def decrypt_payload(envelope) -> dict:
    """Decrypt a {v,iv,tag,ct} AES-256-GCM envelope from the website.

    Mirrors app/lib/creds_crypto.js: Node splits the GCM tag out, so Python's
    AESGCM (which expects ciphertext||tag) gets ``ct + tag``.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _load_key()
    if not key:
        raise ValueError("WEB_CREDS_KEY missing or not a 32-byte base64 key")
    e = json.loads(envelope) if isinstance(envelope, str) else envelope
    iv = base64.b64decode(e["iv"])
    tag = base64.b64decode(e["tag"])
    ct = base64.b64decode(e["ct"])
    pt = AESGCM(key).decrypt(iv, ct + tag, None)
    return json.loads(pt.decode())


def process_pending(rows, store, validator: Optional[Callable[[dict], Optional[bool]]] = None,
                    on_change: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Apply each pending row to ``store``; return the ack list for the website.

    ``validator(creds) -> True|False|None``: True = keys valid (import), False =
    keys rejected (ack failed, do NOT import), None = could not verify right now
    (transient) → SKIP without acking so it retries next poll.

    ``on_change(telegram_id)`` is called after a successful connect/disconnect so
    the caller can invalidate any cached per-user executor.
    """
    acks: list[dict] = []
    for r in rows:
        uid = r.get("user_id")
        action = r.get("action") or "connect"
        tg = str(r.get("telegram_id") or "")
        try:
            if uid is None or not tg:
                continue
            if action == "disconnect":
                # Venue-scoped disconnect (multi-venue store): remove only the
                # named venue when the row carries one; a row without a venue
                # (legacy) removes everything, preserving old behavior.
                venue = str(r.get("exchange") or "").lower().strip()
                if venue and hasattr(store, "delete_venue"):
                    store.delete_venue(tg, venue)
                else:
                    store.delete(tg)
                acks.append({"user_id": uid, "action": "disconnect", "ok": True})
                if on_change:
                    on_change(tg)
                continue
            # connect
            creds = decrypt_payload(r.get("encrypted_payload"))
            # Venue defaults to bitget so existing (venue-less) web rows import
            # unchanged. Each venue requires its own field set.
            venue = str(creds.get("venue") or "bitget").lower()
            # Local import: keeps the web-pull module importable without pulling
            # in the crypto-backed store at module load.
            from bot.core.exchange_credentials import _VENUE_FIELDS
            required = _VENUE_FIELDS.get(venue)
            if required is None or not all(creds.get(k) for k in required):
                acks.append({"user_id": uid, "action": "connect", "ok": False,
                             "error": "incomplete credentials"})
                continue
            if validator is not None:
                verdict = validator(creds)
                if verdict is False:
                    acks.append({"user_id": uid, "action": "connect", "ok": False,
                                 "error": "key validation failed"})
                    continue
                if verdict is None:
                    # Transient — leave un-acked so the row is retried next poll.
                    continue
            if venue == "bitget":
                # Byte-identical legacy path (keeps the 3-positional store.set).
                store.set(tg, creds["api_key"], creds["api_secret"], creds["passphrase"])
            else:
                store.set_venue(tg, venue, {k: creds[k] for k in required})
            acks.append({"user_id": uid, "action": "connect", "ok": True})
            if on_change:
                on_change(tg)
        except Exception as exc:
            # A corrupt/undecryptable row would otherwise retry forever — ack it
            # as failed so the website clears it. Never logs the payload.
            log.warning("credential pull: failed row user=%s action=%s: %s", uid, action, exc)
            acks.append({"user_id": uid, "action": action, "ok": False, "error": "processing error"})
    return acks


def _request(path: str, data: Optional[dict] = None) -> Optional[dict]:
    url = f"{WEBSITE_URL}{path}"
    headers = {"Accept": "application/json", "User-Agent": "RUNECLAW-Bot/1.0",
               "X-Bot-Secret": SYNC_SECRET}
    body = None
    method = "GET"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log.error("credential pull HTTP %s on %s", e.code, path)
        return None
    except Exception as exc:
        log.error("credential pull error on %s: %s", path, exc)
        return None


def pull_and_apply(store=None, validator=None, on_change=None) -> int:
    """Fetch pending credential requests, apply them, ack. Returns #acked.

    No-op (returns 0) when not configured (WEB_CREDS_KEY / BOT_SYNC_SECRET unset),
    so the default deployment is unaffected until the operator opts in.
    """
    if not is_configured():
        return 0
    resp = _request("/api/bot/sync/credentials/pending")
    rows = (resp or {}).get("pending", []) if resp else []
    if not rows:
        return 0
    if store is None:
        from bot.core.exchange_credentials import get_credential_store
        store = get_credential_store()
    acks = process_pending(rows, store, validator=validator, on_change=on_change)
    if acks:
        _request("/api/bot/sync/credentials/ack", {"acks": acks})
    return len(acks)
