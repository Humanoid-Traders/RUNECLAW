#!/usr/bin/env python3
"""verify.py — independently re-compute and check a Proof-of-PnL statement.

    Don't trust the dashboard — verify the fills.

Usage:  python verify.py <statement.json>

Re-derives everything from the fills in the statement and checks it against the
statement's own claims. Depends only on the open CSF reference (``bot.proofofpnl``,
pure stdlib) + ``cryptography`` for the Ed25519 check — no RUNECLAW server, no
exchange ``summary`` field. Exit 0 = PASS, non-zero = a precise diff.

Coverage (v0):
* CEX (``cex_operator_signed``) fills: recompute fill hashes, Merkle root, metrics,
  commitment; re-run the completeness + balance-delta reconciliation; verify the
  Ed25519 signature over the commitment; enforce the trust-tier-minimum invariant.
* On-chain fills: re-derivation from a public RPC is NOT yet implemented — an
  on-chain fill is reported ``UNVERIFIED`` (never silently passed).
"""

from __future__ import annotations

import json
import sys

from bot.proofofpnl import csf
from bot.proofofpnl.reconcile import reconcile
from bot.proofofpnl.statement import commitment_hash


def _fail(diffs: list[str]) -> None:
    print("VERIFY: FAIL")
    for d in diffs:
        print(f"  ✗ {d}")
    sys.exit(1)


def _ed25519_verify(commit_hex: str, sig_hex: str, pubkey_hex: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
            bytes.fromhex(sig_hex), bytes.fromhex(commit_hex))
        return True
    except Exception:
        return False


def verify_statement(stmt: dict) -> tuple[bool, list[str]]:
    diffs: list[str] = []

    # 0) No summary field may appear anywhere in the statement path.
    if _contains_key(stmt, "summary"):
        diffs.append("forbidden 'summary' field present in statement")

    fills = stmt.get("fills") or []
    if not fills:
        diffs.append("statement has no fills")
        return False, diffs

    # 1) Per-fill hash re-computation.
    for f in fills:
        want = f.get("fill_hash")
        got = csf.fill_hash(f)
        if want != got:
            diffs.append(f"fill_hash mismatch @ {f.get('source_ref')}: {want} != {got}")

    # 2) Merkle root.
    root_want = stmt.get("merkle_root")
    root_got = csf.merkle_root(fills)
    if root_want != root_got:
        diffs.append(f"merkle_root mismatch: {root_want} != {root_got}")

    # 3) Metrics re-derived from fills only.
    m_want = stmt.get("metrics") or {}
    m_got = csf.compute_metrics(fills)
    for k, v in m_got.items():
        if str(m_want.get(k)) != str(v):
            diffs.append(f"metrics.{k} mismatch: {m_want.get(k)} != {v}")

    # 4) Trust-tier honesty: headline == minimum over fills, never higher.
    tier_want = stmt.get("trust_tier")
    tier_got = csf.epoch_tier(fills)
    if tier_want != tier_got:
        diffs.append(f"trust_tier not the epoch minimum: {tier_want} != {tier_got}")

    # 5) Reconciliation status re-run from fills + signed snapshots.
    ob = (stmt.get("open_snapshot") or {}).get("balance")
    cb = (stmt.get("close_snapshot") or {}).get("balance")
    rec = reconcile(fills, ob, cb)
    if rec["status"] != stmt.get("status"):
        diffs.append(f"status mismatch: claimed {stmt.get('status')} but recomputed {rec['status']} ({'; '.join(rec['reasons'])})")
    # A statement is only a VERIFIED PROOF if it is published. An honestly-labeled
    # INCOMPLETE/UNVERIFIED statement is internally consistent but is NOT a proof —
    # verify must reject it (non-zero), never render it as passing.
    if rec["status"] != "published":
        diffs.append(f"not a published proof (status={rec['status']}): "
                     f"{'; '.join(rec['reasons']) or 'incomplete provenance'}")

    # 6) Commitment + signature.
    core = {k: stmt.get(k) for k in (
        "csf_version", "range_start", "range_end", "account_ids",
        "open_snapshot", "close_snapshot", "merkle_root", "metrics",
        "trust_tier", "status")}
    commit_got = commitment_hash(core)
    if stmt.get("commitment") != commit_got:
        diffs.append(f"commitment mismatch: {stmt.get('commitment')} != {commit_got}")
    att = stmt.get("attestation") or {}
    if att.get("type") == "ed25519":
        if not _ed25519_verify(commit_got, att.get("signature", ""), att.get("pubkey", "")):
            diffs.append("Ed25519 signature does NOT verify over the commitment")
    elif stmt.get("status") == "published":
        diffs.append("status 'published' but no ed25519 attestation")

    # 7) On-chain fills: re-derivation not implemented in v0 → UNVERIFIED, never pass.
    for f in fills:
        if f.get("venue_type") == "onchain":
            diffs.append(f"UNVERIFIED: on-chain re-derivation not implemented for {f.get('source_ref')}")

    return (len(diffs) == 0), diffs


def _contains_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_contains_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(v, key) for v in obj)
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python verify.py <statement.json>")
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        stmt = json.load(fh)
    ok, diffs = verify_statement(stmt)
    if ok:
        print("VERIFY: PASS")
        print(f"  tier={stmt.get('trust_tier')} status={stmt.get('status')} "
              f"root={stmt.get('merkle_root', '')[:16]}… "
              f"net_pnl={ (stmt.get('metrics') or {}).get('net_pnl') }")
        return 0
    _fail(diffs)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
