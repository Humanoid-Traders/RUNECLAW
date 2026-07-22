"""Web3 live-execution SIGNER — the first slice that ACTUALLY signs + broadcasts.

This is Slice 2, the step past the preview (bot/web/web3_exec_gate + the preview
handler). It signs a real EVM transaction and broadcasts it — but under the
strictest possible envelope this platform can express:

  * TESTNET ONLY. A mainnet chain is refused here regardless of
    WEB3_LIVE_EXEC_ALLOW_MAINNET. Mainnet signing is a separate, later,
    separately-gated slice — blast radius here is testnet funds (worthless).
  * ADMIN ONLY, re-checked server-side.
  * DEFAULT-OFF behind its OWN dedicated flag WEB3_LIVE_EXEC_SIGN_ENABLED, on top
    of the preview feature flag — signing never turns on by enabling previews.
  * ENVELOPE-ENFORCED. The action still runs through the Authority Envelope
    authorize() as a transfer (withdraw_allowed + dest on the allowlist).
  * REVIEW-QUEUED. Every sign is recorded to the Guardian review queue.

The signing key (WEB3_SIGNER_PRIVATE_KEY) is admin-only, resolved from the
environment (encrypted at rest via the operator secrets vault), and is NEVER
logged, NEVER returned, and NEVER placed into an exception surfaced to a caller
(F-15). The elliptic-curve signing itself uses the audited ``eth-account``
library — we do NOT hand-roll secp256k1. eth-account is imported LAZILY as an
operator-installed optional extra: until the operator installs it
(``pip install eth-account``) the signer is fail-closed ("signing library not
installed"), so activating live signing is a conscious, multi-step operator act
at the flag, key, AND dependency level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bot.web import web3_exec_gate as gate

# The env var holding the admin signing key. Its VALUE is never returned by any
# function here — only its presence is ever reported.
_SIGNER_KEY_ENV = "WEB3_SIGNER_PRIVATE_KEY"

# Testnet chain ids we will broadcast to. Derived from the gate's network table
# so the two can never drift. A chain id outside this set is never broadcast.
_TESTNET_CHAIN_IDS = frozenset(
    n["chain_id"] for n in gate.NETWORKS.values() if n.get("testnet"))


def signing_enabled(env: Optional[dict] = None) -> bool:
    """The signer's OWN master switch. Default OFF. Separate from the preview flag."""
    import os
    raw = (env or os.environ).get("WEB3_LIVE_EXEC_SIGN_ENABLED", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _resolve_key(env: Optional[dict] = None) -> str:
    """INTERNAL: the raw signing key. Never call from a path that returns to a
    caller. Only the sign/address helpers below may use it, and they never
    surface it."""
    import os
    return str((env or os.environ).get(_SIGNER_KEY_ENV, "") or "").strip()


def signer_key_present(env: Optional[dict] = None) -> bool:
    """Whether a signing key is configured. Presence only — never the value."""
    return bool(_resolve_key(env))


def _signing_lib():
    """The audited eth-account signer, or None when the operator has not
    installed it. Lazy so the module (and every gate/no-key path) works without
    the optional crypto dependency present."""
    try:
        from eth_account import Account
        return Account
    except Exception:
        return None


def signer_available() -> bool:
    """True only when the eth-account signing library is installed."""
    return _signing_lib() is not None


def signer_address(env: Optional[dict] = None) -> Optional[str]:
    """The public address of the configured signing key (safe to show), or None.
    Wrapped so a malformed key never leaks via an exception."""
    account = _signing_lib()
    if account is None:
        return None
    key = _resolve_key(env)
    if not key:
        return None
    try:
        return account.from_key(key).address
    except Exception:
        return None                     # never surface the key on a parse error


def signer_status(env: Optional[dict] = None) -> dict:
    """A safe, key-free snapshot for the admin signer UI: the flags, whether the
    library + key are present, the signer's public address, and — per testnet —
    whether an RPC endpoint is configured. NEVER includes the private key."""
    testnets = []
    for name, n in gate.NETWORKS.items():
        if not n.get("testnet"):
            continue
        testnets.append({
            "network": name, "label": n.get("label"), "chain_id": n.get("chain_id"),
            "rpc_configured": bool(rpc_url_for(name, env)),
            "explorer": n.get("explorer", ""),
        })
    return {
        "feature_enabled": gate.feature_enabled(env),
        "signing_enabled": signing_enabled(env),
        "signer_library_installed": signer_available(),
        "signer_key_present": signer_key_present(env),
        "signer_address": signer_address(env),          # public address or None
        "testnet_only": True,                            # hard invariant in this slice
        "testnets": testnets,
    }


@dataclass(frozen=True)
class SignDecision:
    allowed: bool
    reason: str
    checklist: dict = field(default_factory=dict)
    network: Optional[dict] = None


_SIGN_CHECKS = (
    ("feature_enabled",
     "on-chain execution is not enabled by the operator (WEB3_LIVE_EXEC_ENABLED)"),
    ("is_admin", "on-chain signing is admin-only"),
    ("signing_enabled",
     "live signing is not enabled — flip WEB3_LIVE_EXEC_SIGN_ENABLED (its own "
     "default-OFF switch, separate from previews)"),
    ("testnet_only",
     "signing is TESTNET-ONLY in this slice — target a supported testnet "
     "(mainnet signing is a separate, later, separately-gated slice)"),
    ("signer_key_present",
     "no signing key is configured (set WEB3_SIGNER_PRIVATE_KEY, admin-only)"),
    ("signer_library_installed",
     "the signing library is not installed — an operator must "
     "`pip install eth-account` to activate live signing"),
    ("envelope_enforcing",
     "bind an Authority Envelope in enforce mode — it caps and authorizes every "
     "on-chain action and is revocable at any time"),
)


def evaluate_sign(*, is_admin: bool, network: str, envelope_enforcing: bool,
                  env: Optional[dict] = None) -> SignDecision:
    """Decide whether a real signing+broadcast may proceed. Fail-closed, and
    hard-limited to testnets regardless of the mainnet-allow flag."""
    net = gate.resolve_network(network)
    state = {
        "feature_enabled": gate.feature_enabled(env),
        "is_admin": bool(is_admin),
        "signing_enabled": signing_enabled(env),
        # Testnet-only — a known network AND net.testnet True. No mainnet override.
        "testnet_only": bool(net) and bool(net.get("testnet")),
        "signer_key_present": signer_key_present(env),
        "signer_library_installed": signer_available(),
        "envelope_enforcing": bool(envelope_enforcing),
    }
    for key, reason in _SIGN_CHECKS:
        if not state[key]:
            return SignDecision(allowed=False, reason=reason, checklist=state, network=net)
    return SignDecision(allowed=True, reason="all signing preconditions met",
                        checklist=state, network=net)


def build_and_sign(*, network: str, to: str, value_wei: int, nonce: int,
                   gas: int = 21000, max_fee_wei: int = 2_000_000_000,
                   max_priority_wei: int = 1_000_000_000, data: str = "0x",
                   env: Optional[dict] = None) -> dict:
    """Build + sign an EIP-1559 testnet transaction. Returns
    ``{ok, raw, tx_hash, from, chain_id}`` on success, or ``{ok: False, error}``.
    NEVER returns or logs the signing key. Refuses off-testnet chains and a
    missing library/key — fail-closed."""
    account = _signing_lib()
    if account is None:
        return {"ok": False, "error": "signing library not installed"}
    net = gate.resolve_network(network)
    if not net or not net.get("testnet"):
        return {"ok": False, "error": "signing is testnet-only"}
    chain_id = int(net["chain_id"])
    if chain_id not in _TESTNET_CHAIN_IDS:
        return {"ok": False, "error": "not a testnet chain"}
    key = _resolve_key(env)
    if not key:
        return {"ok": False, "error": "no signing key configured"}
    try:
        tx = {
            "to": to, "value": int(value_wei), "nonce": int(nonce),
            "gas": int(gas), "maxFeePerGas": int(max_fee_wei),
            "maxPriorityFeePerGas": int(max_priority_wei),
            "chainId": chain_id, "type": 2, "data": data or "0x",
        }
        acct = account.from_key(key)
        signed = acct.sign_transaction(tx)
        # SDK attribute names differ across eth-account versions.
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        txhash = getattr(signed, "hash")

        def _hex0x(v):
            s = v.hex() if hasattr(v, "hex") else str(v)
            return s if s.startswith("0x") else "0x" + s
        return {"ok": True, "raw": _hex0x(raw), "tx_hash": _hex0x(txhash),
                "from": acct.address, "chain_id": chain_id}
    except Exception:
        # F-15: an exception here can carry the key material — never surface it.
        return {"ok": False, "error": "signing failed"}


def rpc_url_for(network: str, env: Optional[dict] = None) -> str:
    """The testnet RPC endpoint for a network, from WEB3_RPC_<NETWORK> (e.g.
    WEB3_RPC_SEPOLIA, WEB3_RPC_BASE_SEPOLIA). Empty when not configured."""
    import os
    key = "WEB3_RPC_" + str(network or "").strip().upper().replace("-", "_")
    return str((env or os.environ).get(key, "") or "").strip()


async def _rpc_call(rpc_url: str, method: str, params: list) -> dict:
    """One JSON-RPC round-trip. Returns ``{ok, result}`` or ``{ok: False, error}``.
    Generic errors only — an RPC endpoint or response never carries key material,
    but we keep the surface clean regardless (F-15)."""
    if not rpc_url:
        return {"ok": False, "error": "no testnet RPC configured"}
    try:
        import aiohttp
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(rpc_url, json=payload) as resp:
                data = await resp.json()
        if isinstance(data, dict) and "result" in data and data["result"] is not None:
            return {"ok": True, "result": data["result"]}
        return {"ok": False, "error": "rpc call returned no result"}
    except Exception:
        return {"ok": False, "error": "rpc call failed"}


async def prepare_tx(*, network: str, address: str, env: Optional[dict] = None) -> dict:
    """Auto-fetch the next nonce + EIP-1559 gas fees for ``address`` on a testnet,
    so the signer form never needs a hand-computed nonce. TESTNET-ONLY, read-only
    RPC (getTransactionCount + gasPrice/baseFee). Returns
    ``{ok, nonce, max_fee_wei, max_priority_wei, base_fee_wei, gas}`` or
    ``{ok: False, error}``. Never signs, never touches the key."""
    net = gate.resolve_network(network)
    if not net or not net.get("testnet"):
        return {"ok": False, "error": "prepare is testnet-only"}
    try:
        if int(net["chain_id"]) not in _TESTNET_CHAIN_IDS:
            return {"ok": False, "error": "not a testnet chain"}
    except (TypeError, ValueError):
        return {"ok": False, "error": "unknown chain"}
    addr = str(address or "").strip()
    if not addr:
        return {"ok": False, "error": "no signer address — configure the signing key"}
    rpc_url = rpc_url_for(network, env)
    if not rpc_url:
        return {"ok": False, "error": "no testnet RPC configured (WEB3_RPC_<NETWORK>)"}

    # Pending nonce — includes any not-yet-mined sends so we never collide.
    nres = await _rpc_call(rpc_url, "eth_getTransactionCount", [addr, "pending"])
    if not nres.get("ok"):
        return {"ok": False, "error": "could not read the account nonce"}
    try:
        nonce = int(str(nres["result"]), 16)
    except (TypeError, ValueError):
        return {"ok": False, "error": "malformed nonce from rpc"}

    # EIP-1559 fees: base fee from the latest block + a priority tip. Fall back to
    # legacy gasPrice when the endpoint doesn't surface a base fee.
    priority = 1_000_000_000                       # 1 gwei default tip
    pres = await _rpc_call(rpc_url, "eth_maxPriorityFeePerGas", [])
    if pres.get("ok"):
        try:
            priority = int(str(pres["result"]), 16)
        except (TypeError, ValueError):
            pass
    base_fee = 0
    bres = await _rpc_call(rpc_url, "eth_getBlockByNumber", ["latest", False])
    if bres.get("ok") and isinstance(bres["result"], dict):
        try:
            base_fee = int(str(bres["result"].get("baseFeePerGas") or "0x0"), 16)
        except (TypeError, ValueError):
            base_fee = 0
    if base_fee <= 0:                              # pre-1559 endpoint — use gasPrice
        gres = await _rpc_call(rpc_url, "eth_gasPrice", [])
        if gres.get("ok"):
            try:
                base_fee = int(str(gres["result"]), 16)
            except (TypeError, ValueError):
                base_fee = 0
    # maxFee = 2×base + tip (headroom for the next few blocks), tip capped ≤ maxFee.
    max_fee = base_fee * 2 + priority
    if max_fee <= 0:
        max_fee = priority
    priority = min(priority, max_fee)
    return {"ok": True, "nonce": nonce, "gas": 21000, "base_fee_wei": base_fee,
            "max_fee_wei": max_fee, "max_priority_wei": priority}


async def broadcast(raw_hex: str, rpc_url: str, chain_id: int) -> dict:
    """Broadcast a signed raw tx via eth_sendRawTransaction — TESTNET ONLY.
    Returns ``{ok, tx_hash}`` or ``{ok: False, error}``. Never leaks secrets: the
    raw tx and RPC belong to the caller, and any failure returns a generic error."""
    try:
        if int(chain_id) not in _TESTNET_CHAIN_IDS:
            return {"ok": False, "error": "broadcast refused: not a testnet chain"}
    except (TypeError, ValueError):
        return {"ok": False, "error": "broadcast refused: unknown chain"}
    if not rpc_url:
        return {"ok": False, "error": "no testnet RPC configured — signed only, not sent"}
    try:
        import aiohttp
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction",
                   "params": [raw_hex if str(raw_hex).startswith("0x") else "0x" + str(raw_hex)]}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(rpc_url, json=payload) as resp:
                data = await resp.json()
        if isinstance(data, dict) and data.get("result"):
            return {"ok": True, "tx_hash": data["result"]}
        err = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
        return {"ok": False, "error": "rpc rejected the transaction",
                "detail": str(err)[:200] if err else None}
    except Exception:
        return {"ok": False, "error": "broadcast failed"}
