"""ERC-8004 anchor tooling (MH2) — Base mainnet, operator-gated, non-custodial.

Turns the identity card's honest ``UNVERIFIED`` anchor into a real one, without
the bot EVER holding a signing key:

1. ``build_anchor_tx()`` — a DRY-RUN plan: the exact transaction the operator
   sends **from their own wallet** (MetaMask hex-data, or ``cast send``): a
   0-value self-transaction on Base whose calldata commits to the agent's
   identity (address + Ed25519 attestation pubkey). Includes a best-effort
   cost estimate via public RPC.
2. The operator signs and broadcasts it themselves. Nothing here executes.
3. ``confirm_anchor(tx_hash)`` — verifies ON-CHAIN via RPC that the tx is
   confirmed, was sent FROM the agent address (proving key control), and its
   calldata contains the commitment — only then records the anchor.
4. ``anchor_for_card()`` — the card's anchor section upgrades to VERIFIED only
   while the recorded commitment still matches the card's current identity;
   a rotated key honestly reads STALE, never silently VERIFIED.

Why calldata-commitment rather than a registry call by default: it is
verifiable by anyone with a block explorer, costs almost nothing on Base, and
depends on no third-party contract. When a canonical ERC-8004 registry
deployment on Base mainnet is confirmed, set ``ANCHOR_REGISTRY_ADDRESS`` and
the plan targets the registry instead (same confirm flow). Promotion path per
the operator's decision: if the identity becomes canonical, mirror the same
commitment on Ethereum mainnet (chain_id 1) — the state file keeps one record
per chain, so a later mainnet anchor adds to, never replaces, the Base one.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from bot.proofofpnl import csf

BASE_CHAIN_ID = 8453
_DEFAULT_RPC = "https://mainnet.base.org"
_EXPLORER = {8453: "https://basescan.org/tx/", 1: "https://etherscan.io/tx/"}

# Calldata = 0x + "RUNECLAW" (ascii hex) + 64-hex identity commitment.
_MAGIC = "52554e45434c4157"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _state_path() -> Path:
    return Path(_env("ANCHOR_STATE_PATH", "data/anchor_state.json"))


def _chain_id() -> int:
    try:
        return int(_env("ANCHOR_CHAIN_ID", str(BASE_CHAIN_ID)))
    except ValueError:
        return BASE_CHAIN_ID


def identity_commitment(agent_address: str, pubkey_hex: str) -> str:
    """Stable across publications: what the on-chain anchor binds. sha256 over
    the canonical {agent_address, ed25519 pubkey} pair — rotating either one
    changes the commitment (and honestly stales the anchor)."""
    core = {
        "agent_address": str(agent_address or "").lower(),
        "ed25519_pubkey": str(pubkey_hex or "").lower(),
    }
    return hashlib.sha256(csf.canonical(core)).hexdigest()


def anchor_calldata(commitment: str) -> str:
    return "0x" + _MAGIC + commitment.lower()


# ── RPC (urllib, fail-soft, no deps) ─────────────────────────────────────────

def _rpc(method: str, params: list) -> Any:
    url = _env("ANCHOR_RPC_URL", _DEFAULT_RPC)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — operator-configured RPC
        out = json.loads(resp.read().decode())
    if "error" in out:
        raise RuntimeError(f"RPC {method}: {out['error']}")
    return out.get("result")


def build_anchor_tx(agent_address: str, pubkey_hex: str) -> dict:
    """DRY RUN ONLY — the transaction the operator sends from their own
    wallet. Never signs, never broadcasts."""
    commitment = identity_commitment(agent_address, pubkey_hex)
    data = anchor_calldata(commitment)
    chain = _chain_id()
    registry = _env("ANCHOR_REGISTRY_ADDRESS")
    to = registry or str(agent_address).lower()

    est: dict = {"available": False}
    try:
        gas_hex = _rpc("eth_estimateGas", [{
            "from": str(agent_address).lower(), "to": to, "value": "0x0",
            "data": data,
        }])
        price_hex = _rpc("eth_gasPrice", [])
        gas, price = int(gas_hex, 16), int(price_hex, 16)
        est = {
            "available": True,
            "gas": gas,
            "gas_price_gwei": round(price / 1e9, 6),
            "est_cost_eth": round(gas * price / 1e18, 10),
        }
    except Exception as exc:  # network weather never blocks planning
        est = {"available": False, "note": f"estimate unavailable: {exc}"}

    return {
        "dry_run": True,
        "chain_id": chain,
        "mode": "registry" if registry else "calldata-commitment",
        "from": str(agent_address).lower(),
        "to": to,
        "value": "0",
        "data": data,
        "commitment": commitment,
        "estimate": est,
        "instructions": [
            f"Send this from the agent wallet ({str(agent_address).lower()}) — "
            "sending FROM that address is what proves key control.",
            "MetaMask: Send → to the address above → amount 0 → advanced/hex "
            "data → paste the data field → confirm on "
            + ("Base" if chain == BASE_CHAIN_ID else f"chain {chain}") + ".",
            f"Or: cast send {to} {data} --value 0 "
            f"--rpc-url {_env('ANCHOR_RPC_URL', _DEFAULT_RPC)} "
            "--private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_BOT>",
            "Then run /anchor confirm <tx_hash> — the anchor is recorded only "
            "after on-chain verification.",
        ],
        "promotion_note": "If this identity later becomes canonical, mirror the "
            "same commitment on Ethereum mainnet (ANCHOR_CHAIN_ID=1 + mainnet "
            "RPC) — records are kept per chain; Base stays the root anchor.",
    }


def confirm_anchor(tx_hash: str, agent_address: str, pubkey_hex: str) -> tuple[bool, list[str]]:
    """Verify the anchoring tx ON-CHAIN, then record it. Returns (ok, problems).
    Nothing is recorded unless every check passes."""
    problems: list[str] = []
    txh = str(tx_hash or "").strip().lower()
    if not txh.startswith("0x") or len(txh) != 66:
        return False, ["tx hash must be 0x + 64 hex chars"]
    commitment = identity_commitment(agent_address, pubkey_hex)
    try:
        tx = _rpc("eth_getTransactionByHash", [txh])
        rcpt = _rpc("eth_getTransactionReceipt", [txh])
    except Exception as exc:
        return False, [f"RPC failed: {exc}"]
    if not tx:
        return False, ["transaction not found on the configured chain"]
    if not rcpt:
        return False, ["transaction not yet mined — try again after confirmation"]
    if str(rcpt.get("status", "")).lower() != "0x1":
        problems.append("transaction FAILED on-chain (status != 0x1)")
    if commitment not in str(tx.get("input", "")).lower():
        problems.append("calldata does not contain the identity commitment")
    sender = str(tx.get("from", "")).lower()
    if sender != str(agent_address).lower():
        problems.append(
            f"sent from {sender or 'unknown'}, not the agent address — "
            "the anchor must prove control of the agent wallet")
    if problems:
        return False, problems

    record = {
        "chain_id": _chain_id(),
        "tx_hash": txh,
        "block_number": int(str(rcpt.get("blockNumber", "0x0")), 16),
        "commitment": commitment,
        "agent_address": str(agent_address).lower(),
        "ed25519_pubkey": str(pubkey_hex or "").lower(),
        "mode": "registry" if _env("ANCHOR_REGISTRY_ADDRESS") else "calldata-commitment",
    }
    state = read_anchor_state()
    state[str(record["chain_id"])] = record
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))
    return True, []


def read_anchor_state() -> dict:
    try:
        data = json.loads(_state_path().read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def anchor_for_card(agent_address: str, pubkey_hex: str, card_hash: str,
                    unverified_plan: dict) -> dict:
    """The card's anchor section. VERIFIED only while a recorded on-chain
    commitment matches the card's CURRENT identity; a rotated key or address
    reads STALE — honesty over continuity. No record → the caller's
    UNVERIFIED plan, unchanged."""
    state = read_anchor_state()
    if not state:
        return unverified_plan
    current = identity_commitment(agent_address, pubkey_hex)
    anchors = []
    stale = []
    for rec in state.values():
        if not isinstance(rec, dict) or not rec.get("tx_hash"):
            continue
        entry = {
            "chain_id": rec.get("chain_id"),
            "tx_hash": rec.get("tx_hash"),
            "block_number": rec.get("block_number"),
            "explorer": _EXPLORER.get(rec.get("chain_id"), "") + str(rec.get("tx_hash", "")),
            "mode": rec.get("mode"),
        }
        (anchors if rec.get("commitment") == current else stale).append(entry)
    if anchors:
        return {
            "status": "VERIFIED",
            "commitment": current,
            "card_hash": card_hash,
            "anchors": anchors,
            "note": "identity commitment (agent address + signing key) confirmed "
                    "on-chain; each statement is then verified by that key's "
                    "signature. Re-check the tx yourself via the explorer link.",
        }
    return {
        **unverified_plan,
        "status": "STALE",
        "stale_anchors": stale,
        "note": "an on-chain anchor exists but binds a DIFFERENT identity "
                "(key or address rotated since) — not claimed as verified.",
    }
