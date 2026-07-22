"""WEB3-LIVE-EXEC slice 1 — the fail-closed gate + the preview-only invariant.

The gate is the safety spine every on-chain slice runs through. This locks:
default-OFF, admin-only, testnet-first, preview-only (a broadcast is refused),
and envelope-enforcing — all fail-closed. Plus the hard invariant that the
gateway handler NEVER signs or broadcasts a transaction in this slice.
"""

import inspect
import re

from bot.web import web3_exec_gate as gate
from bot.web import user_gateway


ON = {"WEB3_LIVE_EXEC_ENABLED": "1"}


def _ev(**kw):
    kw.setdefault("is_admin", True)
    kw.setdefault("network", "sepolia")
    kw.setdefault("envelope_enforcing", True)
    kw.setdefault("env", ON)
    return gate.evaluate(**kw)


def test_default_off_denies_everything():
    d = gate.evaluate(is_admin=True, network="sepolia", envelope_enforcing=True, env={})
    assert d.allowed is False
    assert "not enabled" in d.reason.lower()


def test_admin_only():
    d = _ev(is_admin=False)
    assert d.allowed is False and "admin" in d.reason.lower()


def test_testnet_first_mainnet_off_by_default():
    d = _ev(network="ethereum")           # mainnet, not explicitly allowed
    assert d.allowed is False and "testnet" in d.reason.lower()
    # A supported testnet is fine.
    assert _ev(network="sepolia").allowed is True
    # Mainnet only when the operator explicitly opts in.
    on_main = dict(ON, WEB3_LIVE_EXEC_ALLOW_MAINNET="1")
    assert gate.evaluate(is_admin=True, network="ethereum",
                         envelope_enforcing=True, env=on_main).allowed is True


def test_unknown_network_denied():
    assert _ev(network="mainnet-x").allowed is False


def test_preview_only_broadcast_is_refused():
    d = _ev(broadcast=True)
    assert d.allowed is False and "preview" in d.reason.lower()


def test_requires_enforcing_envelope():
    d = _ev(envelope_enforcing=False)
    assert d.allowed is False and "envelope" in d.reason.lower()


def test_all_preconditions_met_allows_preview():
    d = _ev()
    assert d.allowed is True
    assert d.network and d.network["testnet"] is True
    assert all(d.checklist.values())


def test_handler_never_signs_or_broadcasts():
    # The hard money-path invariant for this slice: no signer, no broadcast.
    src = inspect.getsource(user_gateway.handle_web3_execute)
    for forbidden in ("eth_sendRawTransaction", "sendRawTransaction", "signTransaction",
                      "send_transaction", "private_key", "privateKey", "eth_sendTransaction",
                      "Account.from", "Keypair", "ethers.Wallet"):
        assert forbidden not in src, f"preview slice must never {forbidden}"
    # It IS a dry-run preview and runs the envelope authorize() gate.
    assert '"dry_run": True' in src
    assert "authorize(" in src
    assert "_is_admin_id" in src


def test_handler_registered_as_post_route():
    src = (inspect.getsource(user_gateway.build_gateway))
    assert re.search(r'add_post\("/web3/execute", handle_web3_execute\)', src)
