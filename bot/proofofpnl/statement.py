"""Build a signed, reconciled epoch statement from CSF fills.

Reuses the repo's existing tamper-evidence: ``bot.utils.attestation.AttestationEngine``
for the Ed25519 signature (over a commitment that binds the Merkle root, the
balance snapshots, and the epoch range). Everything else is the pure CSF core.

A statement's ``status`` is decided by ``reconcile`` — an operator can never flip
an ``INCOMPLETE`` epoch to ``published`` by hand, because the status is a function
of the fills + signed snapshots, and ``verify.py`` recomputes it.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Optional

from bot.proofofpnl import csf
from bot.proofofpnl.reconcile import DEFAULT_TOLERANCE, reconcile


def commitment_hash(epoch_core: dict) -> str:
    """SHA-256 over the canonical commitment: the fields a signature must bind so
    that neither a fill, a metric, a snapshot, nor the tier can be altered without
    invalidating the signature."""
    return hashlib.sha256(csf.canonical(epoch_core)).hexdigest()


def build_epoch(fills: list[dict], *,
                account_ids: list[str],
                open_snapshot: Optional[dict],
                close_snapshot: Optional[dict],
                range_start: int,
                range_end: int,
                tolerance: Decimal = DEFAULT_TOLERANCE,
                engine=None,
                sign: bool = True) -> dict:
    """Assemble a canonical epoch statement.

    ``open_snapshot``/``close_snapshot`` are ``{"balance","ccy","ts"}`` signed
    snapshots (or ``None`` when unavailable → the epoch reconciles to INCOMPLETE).
    Returns the full statement dict (JSON-native, decimal-string numbers).
    """
    ordered = csf.canonical_order(fills)
    root = csf.merkle_root(ordered)
    tier = csf.epoch_tier(ordered)
    metrics = csf.compute_metrics(ordered)

    ob = open_snapshot.get("balance") if open_snapshot else None
    cb = close_snapshot.get("balance") if close_snapshot else None
    rec = reconcile(ordered, ob, cb, tolerance)

    # The commitment binds everything a verifier recomputes.
    epoch_core = {
        "csf_version": csf.CSF_VERSION,
        "range_start": int(range_start),
        "range_end": int(range_end),
        "account_ids": [str(a) for a in account_ids],
        "open_snapshot": open_snapshot,
        "close_snapshot": close_snapshot,
        "merkle_root": root,
        "metrics": metrics,
        "trust_tier": tier,
        "status": rec["status"],
    }
    commit = commitment_hash(epoch_core)

    attestation = {"type": "none"}
    if sign and rec["status"] == "published":
        eng = engine
        if eng is None:
            try:
                from bot.utils.attestation import AttestationEngine
                eng = AttestationEngine()
            except Exception:
                eng = None
        if eng is not None and getattr(eng, "available", False):
            res = eng.sign_batch([commit])
            if getattr(res, "valid", False):
                attestation = {
                    "type": "ed25519",
                    "commitment": commit,
                    "signature": res.signature_hex,
                    "pubkey": res.public_key_hex,
                }

    statement = dict(epoch_core)
    statement["commitment"] = commit
    statement["fills"] = ordered
    statement["attestation"] = attestation
    statement["reconciliation"] = rec
    return statement
