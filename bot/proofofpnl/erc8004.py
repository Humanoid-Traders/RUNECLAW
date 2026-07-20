"""ERC-8004 identity + reputation binding — the portable-identity layer.

    Not "trust our reputation score" — here is a signed card binding a public
    identity to a track record you can re-derive from raw fills.

Builds an **Agent Identity Card**: a deterministic, content-hashed, Ed25519-signed
object binding an agent's ERC-8004 on-chain identity to (a) the commitment of a
*published* Proof-of-PnL statement, (b) outcome-based reputation derived from that
statement's fills only, and (c) the bound Authority Envelope's custody posture.

Two honesty invariants, mechanical:

* **No reputation without a published statement.** ``build_identity_card`` refuses
  to attach a reputation block to an ``INCOMPLETE``/unpublished statement — a
  reputation number must trace to fills that reconciled, or it does not exist.
* **Trust tier is carried forward, never inflated.** The card's reputation tier is
  the statement's epoch tier (the minimum across its fills).

Constants (Base Sepolia, verified deterministic addresses):
* IdentityRegistry   ``0x8004A818BFB912233c491871b3d84c89A494BD9e``
* ReputationRegistry ``0x8004B663056A597Dffe9eCcC1965A193B7388713``
The Validation Registry ABI is NOT frozen → any Validation interaction is marked
UNVERIFIED, never faked. The on-chain reputation anchor itself is ``UNVERIFIED``
until a real tx is submitted and confirmed (``anchor_plan``).

Pure except for the optional Ed25519 signature, which reuses
``bot.utils.attestation`` (the same engine that signs CSF statements).
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from bot.proofofpnl import csf

ERC8004_VERSION = "erc8004-card-v0"

# Base Sepolia deterministic registry addresses (lowercased for comparison).
IDENTITY_REGISTRY = "0x8004a818bfb912233c491871b3d84c89a494bd9e"
REPUTATION_REGISTRY = "0x8004b663056a597dffe9eccc1965a193b7388713"
BASE_SEPOLIA_CHAIN_ID = 84532

# The metric fields carried from a statement into the reputation block.
_REP_METRICS = ("net_pnl", "pf", "sharpe", "max_dd", "round_trips", "fees")


def _addr(x: Any) -> str:
    return str(x or "").strip().lower()


def reputation_from_statement(statement: Optional[dict]) -> Optional[dict]:
    """Outcome-based reputation derived from a *published* statement's fills ONLY.

    Returns ``None`` when the statement is missing or not ``published`` — a
    reputation number that cannot trace to reconciled fills does not exist.
    Carries the statement's epoch ``trust_tier`` forward unchanged (never
    inflated), so the reputation can never claim more confidence than the weakest
    fill behind it.
    """
    if not statement or statement.get("status") != "published":
        return None
    metrics = statement.get("metrics") or {}
    return {
        "source": "proof_of_pnl_statement",
        "trust_tier": statement.get("trust_tier"),
        "merkle_root": statement.get("merkle_root"),
        "commitment": statement.get("commitment"),
        "range_start": statement.get("range_start"),
        "range_end": statement.get("range_end"),
        "metrics": {k: metrics.get(k) for k in _REP_METRICS if k in metrics},
        "self_reported": False,
    }


def _custody_posture(envelope: Optional[dict]) -> Optional[dict]:
    """A compact, honest summary of the bound Authority Envelope for the card."""
    if not envelope or not isinstance(envelope, dict):
        return None
    return {
        "envelope_hash": envelope.get("compiled_hash"),
        "envelope_id": envelope.get("envelope_id"),
        "allowed_venues": envelope.get("allowed_venues") or [],
        "max_notional_per_trade_usd": envelope.get("max_notional_per_trade_usd"),
        "withdraw_allowed": bool(envelope.get("withdraw_allowed")),
        "revoked": bool(envelope.get("revoked")),
    }


def anchor_plan(card_hash: str) -> dict:
    """The intended on-chain anchor for a card — honestly UNVERIFIED until a real
    tx confirms it. Names the ReputationRegistry it *would* anchor to; never
    claims an anchor exists."""
    return {
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "reputation_registry": REPUTATION_REGISTRY,
        "card_hash": card_hash,
        "status": "UNVERIFIED",
        "note": "designed anchor; no on-chain tx submitted — not verified",
    }


def _card_core(identity: Any, reputation: Optional[dict],
               custody: Optional[dict]) -> dict:
    """The fields a card hash + signature binds (excludes the hash/signature)."""
    return {
        "version": ERC8004_VERSION,
        "identity": identity,
        "reputation": reputation,
        "custody": custody,
    }


def card_hash(core: dict) -> str:
    """SHA-256 over the canonical card core (deterministic, decimal-string safe —
    reuses the CSF canonicalizer so numbers are never hashed as floats)."""
    return hashlib.sha256(csf.canonical(core)).hexdigest()


def build_identity_card(agent_address: str,
                        statement: Optional[dict] = None,
                        *,
                        envelope: Optional[dict] = None,
                        chain_id: int = BASE_SEPOLIA_CHAIN_ID,
                        identity_registry: str = IDENTITY_REGISTRY,
                        engine: Any = None,
                        sign: bool = True) -> dict:
    """Assemble a deterministic Agent Identity Card.

    Binds the agent's ERC-8004 identity to a *published* Proof-of-PnL statement's
    commitment + outcome-based reputation, plus the bound Authority Envelope's
    custody posture. When the statement is not published (or absent), the card is
    marked ``status="unbacked"`` and carries NO reputation — an honest identity
    with no verifiable track record behind it yet.
    """
    identity = {
        "chain_id": int(chain_id),
        "identity_registry": _addr(identity_registry),
        "agent_address": _addr(agent_address),
    }
    reputation = reputation_from_statement(statement)
    custody = _custody_posture(envelope)
    core = _card_core(identity, reputation, custody)
    h = card_hash(core)

    card = dict(core)
    card["card_hash"] = h
    card["card_id"] = "card_" + h[:8]
    card["status"] = "backed" if reputation is not None else "unbacked"
    card["anchor"] = anchor_plan(h)

    attestation = {"type": "none"}
    if sign:
        eng = engine
        if eng is None:
            try:
                from bot.utils.attestation import AttestationEngine
                eng = AttestationEngine()
            except Exception:
                eng = None
        if eng is not None and getattr(eng, "available", False):
            res = eng.sign_batch([h])
            if getattr(res, "valid", False):
                attestation = {
                    "type": "ed25519",
                    "card_hash": h,
                    "signature": res.signature_hex,
                    "pubkey": res.public_key_hex,
                }
    card["attestation"] = attestation
    # MH2: upgrade the anchor section from the designed plan to VERIFIED/STALE
    # when (and only when) a confirmed on-chain record matches the card's
    # current identity (address + signing pubkey). Fail-soft: any hiccup keeps
    # the honest UNVERIFIED plan.
    try:
        from bot.proofofpnl.anchor import anchor_for_card
        card["anchor"] = anchor_for_card(
            identity["agent_address"], attestation.get("pubkey", ""),
            h, card["anchor"])
    except Exception:
        pass
    return card


def verify_card(card: Optional[dict], engine: Any = None) -> tuple[bool, list[str]]:
    """Re-derive a card's hash and check its signature. Returns ``(ok, diffs)``.

    No RUNECLAW server: recomputes ``card_hash`` from the bound core and verifies
    the Ed25519 signature over it. A mutated bound field (root, tier, metric,
    envelope hash, agent address) changes the hash and fails. The on-chain anchor
    is reported but not required to pass — it is honestly ``UNVERIFIED``.
    """
    diffs: list[str] = []
    if not card or not isinstance(card, dict):
        return False, ["no card"]

    core = _card_core(card.get("identity"), card.get("reputation"),
                      card.get("custody"))
    want = card.get("card_hash")
    got = card_hash(core)
    if want != got:
        diffs.append(f"card_hash mismatch: {want} != {got}")

    # Reputation, when present, must not claim a higher tier than its statement's.
    rep = card.get("reputation")
    if rep is not None and not rep.get("trust_tier"):
        diffs.append("reputation present but no trust_tier (cannot bound confidence)")
    if rep is not None and rep.get("self_reported"):
        diffs.append("reputation is self_reported — not admissible")

    att = card.get("attestation") or {}
    if att.get("type") == "ed25519":
        ok_sig = _ed25519_verify(got, att.get("signature", ""), att.get("pubkey", ""),
                                 engine)
        if not ok_sig:
            diffs.append("Ed25519 signature does NOT verify over the card hash")
    elif card.get("status") == "backed":
        diffs.append("backed card carries no ed25519 attestation")

    return (len(diffs) == 0), diffs


def _ed25519_verify(hash_hex: str, sig_hex: str, pubkey_hex: str,
                    engine: Any = None) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
            bytes.fromhex(sig_hex), bytes.fromhex(hash_hex))
        return True
    except Exception:
        return False


def human_readable(card: Optional[dict]) -> str:
    """Plain-text render of an identity card (no markup)."""
    if not card or not isinstance(card, dict):
        return "No identity card."
    idn = card.get("identity") or {}
    lines = [f"{card.get('card_id', '')}  ·  status: {card.get('status')}  ·  "
             f"agent {idn.get('agent_address', '')[:10]}… on chain {idn.get('chain_id')}"]
    rep = card.get("reputation")
    if rep:
        m = rep.get("metrics") or {}
        lines.append(f"• Track record ({rep.get('trust_tier')}): "
                     f"net_pnl={m.get('net_pnl')} pf={m.get('pf')} "
                     f"sharpe={m.get('sharpe')} max_dd={m.get('max_dd')} "
                     f"round_trips={m.get('round_trips')}")
        lines.append(f"• Merkle root: {(rep.get('merkle_root') or '')[:16]}…")
    else:
        lines.append("• No verifiable track record bound yet (unbacked).")
    cust = card.get("custody")
    if cust:
        wd = "withdraw ALLOWED" if cust.get("withdraw_allowed") else "non-custodial (no withdraw)"
        lines.append(f"• Custody: {', '.join(cust.get('allowed_venues') or []) or 'no venue'}; "
                     f"per-trade ${cust.get('max_notional_per_trade_usd')}; {wd}")
    anc = card.get("anchor") or {}
    lines.append(f"• On-chain anchor: {anc.get('status')} "
                 f"(ReputationRegistry {anc.get('reputation_registry', '')[:10]}…)")
    return "\n".join(lines)
