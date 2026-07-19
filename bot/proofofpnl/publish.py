"""Continuous Proof-of-PnL publishing — the moat as a live, verifiable feed.

The assemble layer (``assemble_track_record``) already produces a public-safe
bundle: a fills-first CSF statement, an ERC-8004 identity card, and the card's
UNVERIFIED on-chain anchor plan. This module turns that into a *continuously
published* artifact — stamped with when it was built, hashed for integrity,
persisted as "the latest published statement", and re-verifiable by anyone.

"Don't trust the dashboard — verify the fills," productized: the scheduler
rebuilds the statement each epoch, this seals + persists it, and the web serves
it with an honest freshness marker and the anchor's UNVERIFIED status intact.

Discipline:
* PUBLIC-SAFE — refuses to publish a bundle carrying an exchange ``summary``
  (the same rule ``verify.py`` and ``assemble.is_public_safe`` enforce).
* NO FABRICATED PROOF — the anchor stays ``UNVERIFIED`` until a real tx confirms
  it; an incomplete epoch publishes as-is with its INCOMPLETE reconciliation.
* DETERMINISTIC — ``published_at`` is passed in (never wall-clock-read here), so
  the same bundle + timestamp seals to the same publish hash every time.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Optional

from bot.proofofpnl import csf
from bot.proofofpnl.assemble import is_public_safe

PUBLICATION_FORMAT = "runeclaw.proofofpnl.publication.v0"
DEFAULT_MAX_AGE_S = 86_400          # a day-old statement is stale


def publish_hash(bundle: Any) -> str:
    """SHA-256 over the canonical bundle (decimal-safe via the CSF canonicalizer)."""
    return hashlib.sha256(csf.canonical(bundle)).hexdigest()


def _anchor_of(bundle: dict) -> Optional[dict]:
    card = (bundle or {}).get("identity_card") or {}
    return card.get("anchor")


def _trust_tier(bundle: dict) -> Optional[str]:
    stmt = (bundle or {}).get("statement") or {}
    return stmt.get("trust_tier") or (stmt.get("epoch") or {}).get("trust_tier")


def build_publication(bundle: dict, *, published_at_ts: int,
                      epoch_seq: Optional[int] = None) -> dict:
    """Seal an assembled bundle into a publication. Raises ValueError if the
    bundle is not public-safe (never publish exchange internals)."""
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a dict")
    if not is_public_safe(bundle):
        raise ValueError("bundle is not public-safe — refusing to publish")
    h = publish_hash(bundle)
    anchor = _anchor_of(bundle)
    reconciled = ((bundle.get("statement") or {}).get("reconciliation") or {}).get("status")
    return {
        "format": PUBLICATION_FORMAT,
        "bundle": bundle,
        "publish_hash": h,
        "published_at": int(published_at_ts),
        "epoch_seq": epoch_seq,
        "trust_tier": _trust_tier(bundle),
        "reconciliation": reconciled,
        "anchor": anchor,                       # UNVERIFIED until a real tx confirms
        "verify_note": ("Re-derive publish_hash over `bundle` and re-run verify.py "
                        "section-7 on-chain re-derivation to confirm the fills."),
    }


def is_fresh(publication: Optional[dict], now_ts: int,
             max_age_s: int = DEFAULT_MAX_AGE_S) -> bool:
    """True when the publication was built within ``max_age_s`` of ``now_ts``."""
    if not isinstance(publication, dict):
        return False
    at = publication.get("published_at")
    if at is None:
        return False
    try:
        return 0 <= (int(now_ts) - int(at)) <= int(max_age_s)
    except (TypeError, ValueError):
        return False


def verify_publication(publication: Optional[dict]) -> tuple[bool, list[str]]:
    """Re-derive the publish hash + re-check public-safety. Returns (ok, problems).
    Does NOT claim the on-chain anchor exists — that stays UNVERIFIED."""
    problems: list[str] = []
    if not isinstance(publication, dict):
        return False, ["publication is not a dict"]
    bundle = publication.get("bundle")
    if not isinstance(bundle, dict):
        return False, ["publication has no bundle"]
    if not is_public_safe(bundle):
        problems.append("bundle is not public-safe")
    want = publication.get("publish_hash")
    got = publish_hash(bundle)
    if want != got:
        problems.append(f"publish_hash mismatch (recorded {str(want)[:12]}…, "
                        f"re-derived {got[:12]}…)")
    return (not problems), problems


class PublicationStore:
    """Thread-safe JSON store for the single latest publication."""

    def __init__(self, path: str = "data/proofofpnl_publication.json") -> None:
        self._path = path
        self._lock = threading.RLock()

    def write(self, publication: dict) -> bool:
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
                tmp = self._path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(publication, fh, separators=(",", ":"))
                os.replace(tmp, self._path)
            return True
        except OSError:
            return False

    def read(self) -> Optional[dict]:
        try:
            with self._lock, open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None


_STORE: Optional[PublicationStore] = None
_STORE_LOCK = threading.Lock()


def get_publication_store() -> PublicationStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = PublicationStore(
                os.environ.get("PROOFOFPNL_PUBLICATION_PATH",
                               "data/proofofpnl_publication.json"))
        return _STORE


def publish_now(bundle: dict, *, published_at_ts: int,
                epoch_seq: Optional[int] = None,
                store: Optional[PublicationStore] = None) -> dict:
    """Seal ``bundle`` into a publication and persist it as the latest. The unit a
    scheduler calls each epoch. Returns the publication (also written to store)."""
    pub = build_publication(bundle, published_at_ts=published_at_ts, epoch_seq=epoch_seq)
    (store or get_publication_store()).write(pub)
    return pub
