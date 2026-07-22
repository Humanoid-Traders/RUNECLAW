"""WEB3-LIVE-EXEC slice 2 — the testnet live signer.

Locks the safety envelope of the first slice that ACTUALLY signs + broadcasts:
triple-gated default-OFF, admin-only, TESTNET-ONLY (mainnet refused regardless of
the mainnet-allow flag), envelope-enforced, and — the hard money-path invariant —
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

def test_default_off_denies(monkeypatch):
    d = _ev(monkeypatch, env={"WEB3_LIVE_EXEC_SIGN_ENABLED": "1",
                              "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})  # feature flag off
    assert d.allowed is False and "not enabled" in d.reason.lower()


def test_signing_has_its_own_flag(monkeypatch):
    # preview feature on, but the signing switch is off → denied.
    d = _ev(monkeypatch, env={"WEB3_LIVE_EXEC_ENABLED": "1",
                              "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert d.allowed is False and "signing" in d.reason.lower()


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
