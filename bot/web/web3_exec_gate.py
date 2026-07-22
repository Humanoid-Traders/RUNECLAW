"""Web3 live-execution gate — the ONE decision for 'may this action touch chain'.

RUNECLAW has NO on-chain execution infrastructure today (no signer, no on-chain
key store, no swap/bridge/stake adapters — every web3 lib is read-only). Live
on-chain execution is being built in careful, gated slices toward the operator's
goal of full live signing and, eventually, autonomous auto-signing. THIS gate is
the safety spine every slice runs through, from the very first preview to a
future auto-signer.

Slice 1 (current) produces a DRY-RUN PREVIEW ONLY — it never signs or broadcasts.
But the gate already enforces the full fail-closed precondition set so the
authorization surface is proven before any real transaction is ever sent:

    1. feature_enabled     — operator master switch (env WEB3_LIVE_EXEC_ENABLED,
                             default OFF). Nothing on-chain happens without it.
    2. is_admin            — admin-only for now. On-chain execution is not opened
                             to general users in this slice.
    3. network_ok          — the target network is known AND, unless the operator
                             explicitly allowed mainnet, it is a TESTNET (real
                             live testing starts on testnet — zero mainnet risk).
    4. not_broadcast_yet   — this slice is preview-only; a real send is refused
                             here until the signer slice ships behind this gate.
    5. envelope_enforcing  — a bound Authority Envelope in ENFORCE mode caps and
                             authorizes the action (notional, asset, destination)
                             and is revocable. No on-chain action — preview or
                             (future) live — exists outside one.

All must hold. FAILS CLOSED: any missing/unknown input → deny, naming the first
unmet precondition. Pure and deterministic; the gateway sources the inputs, runs
the Authority Envelope authorize() separately, and wires the result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# Known EVM networks and whether each is a testnet. Live testing starts here.
NETWORKS = {
    "sepolia": {"chain_id": 11155111, "testnet": True, "label": "Ethereum Sepolia"},
    "base-sepolia": {"chain_id": 84532, "testnet": True, "label": "Base Sepolia"},
    "arbitrum-sepolia": {"chain_id": 421614, "testnet": True, "label": "Arbitrum Sepolia"},
    "optimism-sepolia": {"chain_id": 11155420, "testnet": True, "label": "Optimism Sepolia"},
    "ethereum": {"chain_id": 1, "testnet": False, "label": "Ethereum"},
    "base": {"chain_id": 8453, "testnet": False, "label": "Base"},
    "arbitrum": {"chain_id": 42161, "testnet": False, "label": "Arbitrum"},
    "optimism": {"chain_id": 10, "testnet": False, "label": "Optimism"},
    "polygon": {"chain_id": 137, "testnet": False, "label": "Polygon"},
}

_CHECKS = (
    ("feature_enabled",
     "on-chain execution is not enabled by the operator yet (WEB3_LIVE_EXEC_ENABLED)"),
    ("is_admin", "on-chain execution is admin-only in this phase"),
    ("network_ok",
     "target a supported testnet (real live testing starts on testnet; mainnet "
     "is off unless the operator explicitly allows it)"),
    ("not_broadcast",
     "this build is preview-only — signing and broadcast ship in a later, "
     "separately-gated slice"),
    ("envelope_enforcing",
     "bind an Authority Envelope in enforce mode — it caps and authorizes every "
     "on-chain action and is revocable at any time"),
)


@dataclass(frozen=True)
class Web3ExecDecision:
    allowed: bool
    reason: str
    checklist: dict = field(default_factory=dict)
    network: Optional[dict] = None


def feature_enabled(env: Optional[dict] = None) -> bool:
    """Operator master switch. Default OFF. Truthy = 1/true/yes/on."""
    raw = (env or os.environ).get("WEB3_LIVE_EXEC_ENABLED", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def mainnet_allowed(env: Optional[dict] = None) -> bool:
    """Separate, explicit opt-in to leave testnet. Default OFF — testnet-first."""
    raw = (env or os.environ).get("WEB3_LIVE_EXEC_ALLOW_MAINNET", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def resolve_network(name: str):
    return NETWORKS.get(str(name or "").strip().lower())


def evaluate(*, is_admin: bool, network: str, envelope_enforcing: bool,
             broadcast: bool = False, env: Optional[dict] = None) -> Web3ExecDecision:
    """Decide whether an on-chain action may proceed to (this slice: a preview).
    Fail-closed. ``broadcast=True`` is refused in this preview-only slice."""
    net = resolve_network(network)
    network_ok = bool(net) and (net["testnet"] or mainnet_allowed(env))
    state = {
        "feature_enabled": feature_enabled(env),
        "is_admin": bool(is_admin),
        "network_ok": network_ok,
        # Preview-only invariant: a real broadcast is never allowed here.
        "not_broadcast": not bool(broadcast),
        "envelope_enforcing": bool(envelope_enforcing),
    }
    for key, reason in _CHECKS:
        if not state[key]:
            return Web3ExecDecision(allowed=False, reason=reason, checklist=state,
                                    network=net)
    return Web3ExecDecision(allowed=True, reason="all preconditions met",
                            checklist=state, network=net)
