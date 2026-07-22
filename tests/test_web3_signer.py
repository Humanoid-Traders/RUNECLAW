"""WEB3-LIVE-EXEC slice 2 — the testnet live signer.

Locks the safety envelope of the first slice that ACTUALLY signs + broadcasts.
The feature + signing switches now DEFAULT ON so testnet signing works out of the
box — but the envelope is unchanged and airtight: admin-only, TESTNET-ONLY
(mainnet refused regardless of the mainnet-allow flag), envelope-enforced, inert
without the operator's key + eth-account, and — the hard money-path invariant —
the signing key is NEVER returned, logged, or surfaced in an error. The elliptic
signing itself is proven correct offline (the signature recovers to the signer's
own address) when the optional eth-account library is installed.
"""

import inspect

import pytest

from bot.web import web3_signer as signer
from bot.web import user_gateway

# canonical anvil/hardhat test account #0 — public, never holds real funds.
_TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_DEST = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

_ON = {
    "WEB3_LIVE_EXEC_ENABLED": "1",
    "WEB3_LIVE_EXEC_SIGN_ENABLED": "1",
    "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY,
}


def _ev(monkeypatch, *, lib=True, **kw):
    # Decouple the gate-logic tests from whether eth-account is installed.
    monkeypatch.setattr(signer, "signer_available", lambda: lib)
    kw.setdefault("is_admin", True)
    kw.setdefault("network", "sepolia")
    kw.setdefault("envelope_enforcing", True)
    kw.setdefault("env", _ON)
    return signer.evaluate_sign(**kw)


# ── the fail-closed signing gate ───────────────────────────────────────

def test_feature_default_on_but_hard_disable_wins(monkeypatch):
    # With nothing set, the feature is ON — testnet signing works out of the box
    # (key + testnet + envelope + lib all present here).
    on = _ev(monkeypatch, env={"WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert on.allowed is True
    # …but an explicit WEB3_LIVE_EXEC_ENABLED=0 still hard-disables everything.
    off = _ev(monkeypatch, env={"WEB3_LIVE_EXEC_ENABLED": "0",
                                "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert off.allowed is False and "not enabled" in off.reason.lower()


def test_signing_has_its_own_hard_disable(monkeypatch):
    # signing defaults ON, but its OWN switch can hard-disable it independently.
    off = _ev(monkeypatch, env={"WEB3_LIVE_EXEC_SIGN_ENABLED": "0",
                                "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert off.allowed is False and "signing" in off.reason.lower()


def test_defaults_on_still_need_the_operator_key(monkeypatch):
    # Defaulting the switches ON does NOT bypass the operator's own setup: with no
    # signing key, signing is still refused — "on" is inert until the key is set.
    d = _ev(monkeypatch, env={})                      # everything default, no key
    assert d.allowed is False and "key" in d.reason.lower()


def test_mainnet_still_refused_by_default_with_everything_on(monkeypatch):
    # THE safety invariant of this change: even with the feature + signing ON by
    # default and a key present, a mainnet target is refused (testnet-only), with
    # no mainnet-allow flag anywhere in sight.
    d = _ev(monkeypatch, network="ethereum",
            env={"WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert d.allowed is False and "testnet" in d.reason.lower()


def test_new_testnets_pass_the_gate(monkeypatch):
    # the added testnets are usable end-to-end (gate allows; chain in testnet set).
    for net in ("polygon-amoy", "avalanche-fuji", "scroll-sepolia",
                "linea-sepolia", "blast-sepolia", "bsc-testnet"):
        d = _ev(monkeypatch, network=net, env={"WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
        assert d.allowed is True, net
        assert d.network["testnet"] is True
        assert d.network["chain_id"] in signer._TESTNET_CHAIN_IDS


def test_admin_only(monkeypatch):
    d = _ev(monkeypatch, is_admin=False)
    assert d.allowed is False and "admin" in d.reason.lower()


def test_mainnet_is_refused_even_with_allow_flag(monkeypatch):
    env = dict(_ON, WEB3_LIVE_EXEC_ALLOW_MAINNET="1")   # mainnet allowed for previews…
    d = _ev(monkeypatch, network="ethereum", env=env)   # …but signing stays testnet-only
    assert d.allowed is False and "testnet" in d.reason.lower()


def test_unknown_network_denied(monkeypatch):
    assert _ev(monkeypatch, network="does-not-exist").allowed is False


def test_key_absent_denies(monkeypatch):
    env = {"WEB3_LIVE_EXEC_ENABLED": "1", "WEB3_LIVE_EXEC_SIGN_ENABLED": "1"}  # no key
    d = _ev(monkeypatch, env=env)
    assert d.allowed is False and "key" in d.reason.lower()


def test_library_absent_denies(monkeypatch):
    d = _ev(monkeypatch, lib=False)
    assert d.allowed is False and "library" in d.reason.lower()


def test_requires_enforcing_envelope(monkeypatch):
    d = _ev(monkeypatch, envelope_enforcing=False)
    assert d.allowed is False and "envelope" in d.reason.lower()


def test_all_preconditions_met_allows(monkeypatch):
    d = _ev(monkeypatch)
    assert d.allowed is True
    assert d.network and d.network["testnet"] is True
    assert all(d.checklist.values())


# ── the key never leaks ────────────────────────────────────────────────

def test_key_presence_is_boolean_never_the_value():
    assert signer.signer_key_present(_ON) is True
    assert signer.signer_key_present({}) is False


def test_no_public_helper_returns_the_key():
    # address derivation returns the ADDRESS, never the private key.
    addr = signer.signer_address(_ON)
    if addr is not None:                 # only when eth-account is installed
        assert addr.lower() == _TEST_ADDR.lower()
        assert _TEST_KEY not in addr


def test_signer_source_never_returns_resolved_key():
    # _resolve_key is internal; assert no public function passes it to a caller.
    for fn in (signer.signer_key_present, signer.signer_address, signer.evaluate_sign,
               signer.build_and_sign):
        src = inspect.getsource(fn)
        assert "return _resolve_key" not in src
        assert "return key" not in src


# ── real offline signing (skips when eth-account is not installed) ──────

def test_build_and_sign_produces_a_recoverable_testnet_signature():
    Account = pytest.importorskip("eth_account").Account
    out = signer.build_and_sign(network="sepolia", to=_DEST, value_wei=10**15,
                                nonce=0, env=_ON)
    assert out["ok"] is True
    assert out["from"].lower() == _TEST_ADDR.lower()
    assert out["chain_id"] == 11155111
    assert out["raw"].startswith("0x02")           # EIP-1559 typed tx
    # the signature must recover to the signer's OWN address — proof it is valid.
    recovered = Account.recover_transaction(out["raw"])
    assert recovered.lower() == _TEST_ADDR.lower()
    # the private key must appear nowhere in the returned payload.
    assert _TEST_KEY not in str(out)


def test_build_and_sign_refuses_mainnet():
    pytest.importorskip("eth_account")
    out = signer.build_and_sign(network="ethereum", to=_DEST, value_wei=1,
                                nonce=0, env=_ON)
    assert out["ok"] is False and "testnet" in out["error"].lower()


def test_build_and_sign_refuses_without_key():
    out = signer.build_and_sign(network="sepolia", to=_DEST, value_wei=1,
                                nonce=0, env={})
    assert out["ok"] is False


async def test_broadcast_refuses_non_testnet_chain():
    out = await signer.broadcast("0x02aa", "http://localhost:8545", 1)   # mainnet chain id
    assert out["ok"] is False and "testnet" in out["error"].lower()


async def test_broadcast_without_rpc_is_signed_only():
    out = await signer.broadcast("0x02aa", "", 11155111)                 # testnet, no rpc
    assert out["ok"] is False and "rpc" in out["error"].lower()


# ── gateway handler invariants ─────────────────────────────────────────

def test_sign_handler_gates_and_never_returns_the_key():
    src = inspect.getsource(user_gateway.handle_web3_sign)
    assert "evaluate_sign(" in src        # testnet-only signing gate
    assert "authorize(" in src            # envelope transfer gate
    assert "_is_admin_id" in src          # admin re-check
    assert "get_review_queue" in src      # recorded to the review queue
    # the handler delegates signing to web3_signer and never touches the key.
    assert "WEB3_SIGNER_PRIVATE_KEY" not in src
    assert "private_key" not in src


def test_sign_route_registered():
    src = inspect.getsource(user_gateway.build_gateway)
    assert 'add_post("/web3/sign", handle_web3_sign)' in src


def test_signer_key_is_vaulted():
    # the signing key is encrypted at rest (managed by the secrets vault).
    from bot.core import secrets_vault
    assert "WEB3_SIGNER_PRIVATE_KEY" in secrets_vault._DEFAULT_MANAGED


# ── signer status (key-free snapshot for the web UI) ───────────────────

def test_signer_status_is_key_free_and_testnet_scoped():
    st = signer.signer_status(_ON)
    # never the private key — only presence booleans + the public address.
    assert _TEST_KEY not in str(st)
    assert st["signer_key_present"] is True
    assert st["feature_enabled"] is True and st["signing_enabled"] is True
    assert st["testnet_only"] is True
    # every listed network is a testnet, with an rpc_configured flag.
    assert st["testnets"] and all(t["chain_id"] and "rpc_configured" in t
                                  for t in st["testnets"])
    labels = {t["network"] for t in st["testnets"]}
    assert "sepolia" in labels and "ethereum" not in labels   # no mainnet listed


def test_signer_status_reports_rpc_readiness():
    env = dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia")
    st = signer.signer_status(env)
    by = {t["network"]: t for t in st["testnets"]}
    assert by["sepolia"]["rpc_configured"] is True
    assert by["base-sepolia"]["rpc_configured"] is False


# ── prepare (nonce/gas auto-fetch) — testnet-only, never signs ─────────

async def test_prepare_refuses_mainnet():
    out = await signer.prepare_tx(network="ethereum", address=_TEST_ADDR, env=_ON)
    assert out["ok"] is False and "testnet" in out["error"].lower()


async def test_prepare_without_rpc_is_refused():
    out = await signer.prepare_tx(network="sepolia", address=_TEST_ADDR, env=_ON)  # no RPC
    assert out["ok"] is False and "rpc" in out["error"].lower()


async def test_prepare_without_address_is_refused():
    env = dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia")
    out = await signer.prepare_tx(network="sepolia", address="", env=env)
    assert out["ok"] is False and "address" in out["error"].lower()


async def test_prepare_composes_eip1559_fees(monkeypatch):
    # stub the RPC round-trip so we can prove nonce + fee composition without a net.
    async def fake_rpc(rpc_url, method, params):
        return {
            "eth_getTransactionCount": {"ok": True, "result": "0x5"},
            "eth_maxPriorityFeePerGas": {"ok": True, "result": hex(1_500_000_000)},
            "eth_getBlockByNumber": {"ok": True, "result": {"baseFeePerGas": hex(3_000_000_000)}},
        }[method]
    monkeypatch.setattr(signer, "_rpc_call", fake_rpc)
    env = dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia")
    out = await signer.prepare_tx(network="sepolia", address=_TEST_ADDR, env=env)
    assert out["ok"] is True
    assert out["nonce"] == 5
    assert out["base_fee_wei"] == 3_000_000_000
    assert out["max_priority_wei"] == 1_500_000_000
    # maxFee = 2×base + tip, and tip never exceeds maxFee.
    assert out["max_fee_wei"] == 2 * 3_000_000_000 + 1_500_000_000
    assert out["max_priority_wei"] <= out["max_fee_wei"]


# ── the prepare + status gateway handlers (admin + gate, never the key) ─

def test_prepare_and_status_handlers_are_admin_gated_and_keyless():
    for fn in (user_gateway.handle_web3_prepare, user_gateway.handle_web3_sign_status):
        src = inspect.getsource(fn)
        assert "_is_admin_id" in src            # admin re-check server-side
        assert "WEB3_SIGNER_PRIVATE_KEY" not in src and "private_key" not in src
    # prepare runs the same fail-closed signing gate before touching any RPC.
    assert "evaluate_sign(" in inspect.getsource(user_gateway.handle_web3_prepare)


def test_prepare_and_status_routes_registered():
    src = inspect.getsource(user_gateway.build_gateway)
    assert 'add_get("/web3/sign/status", handle_web3_sign_status)' in src
    assert 'add_post("/web3/sign/prepare", handle_web3_prepare)' in src


# ── block-explorer links (one click from a testnet sign to the on-chain record) ─

def test_explorer_tx_url_builds_per_network_links():
    from bot.web.web3_exec_gate import explorer_tx_url
    h = "0x" + "a" * 64
    assert explorer_tx_url("sepolia", h) == f"https://sepolia.etherscan.io/tx/{h}"
    assert explorer_tx_url("base-sepolia", h) == f"https://sepolia.basescan.org/tx/{h}"
    assert explorer_tx_url("optimism-sepolia", h).startswith("https://sepolia-optimism.etherscan.io/tx/")


def test_explorer_tx_url_refuses_unknown_or_malformed():
    from bot.web.web3_exec_gate import explorer_tx_url
    assert explorer_tx_url("does-not-exist", "0x" + "a" * 64) == ""  # unknown net
    assert explorer_tx_url("sepolia", "not-a-hash") == ""           # non-0x hash
    assert explorer_tx_url("sepolia", "") == ""                     # empty hash


def test_every_testnet_has_an_explorer():
    from bot.web import web3_exec_gate as gate
    for name, n in gate.NETWORKS.items():
        if n.get("testnet"):
            assert n.get("explorer", "").startswith("https://"), f"{name} missing explorer"


def test_sign_handler_returns_an_explorer_url():
    src = inspect.getsource(user_gateway.handle_web3_sign)
    assert "explorer_url" in src and "explorer_tx_url" in src
    # only when the broadcast actually succeeded — never a link to a tx that
    # never hit the chain.
    assert 'if bcast.get("ok") else ""' in src
