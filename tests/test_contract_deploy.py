"""
Contract Studio slice 5 — one-click testnet deploy.

Covers the signer's contract-CREATION support (build_and_sign with to=None omits
the 'to' field; deploy gas is estimated not hardcoded; the CREATE address is
deterministic), the Authority Envelope's new 'deploy' kind (allowed under a
healthy enforcing envelope, denied when revoked/expired — never on mainnet), and
the explorer address link. eth-account/solcx aren't installed in CI, so the
signing happy-path is exercised only for its fail-closed guards; the pure logic
is fully tested.
"""

from bot.web import web3_signer as signer
from bot.web import web3_exec_gate as gate
from bot.guardian.authority import authorize


class TestSignerContractCreation:
    def test_build_and_sign_accepts_to_none_without_library(self):
        # With eth-account absent (CI), we still prove it fails CLOSED with a clear
        # reason rather than raising on a contract-creation (to=None) request.
        r = signer.build_and_sign(network="sepolia", to=None, value_wei=0, nonce=0,
                                  data="0x6001600155")
        assert r["ok"] is False
        assert "error" in r  # library/key gate — never a stack trace (F-15)

    def test_deploy_is_testnet_only(self):
        r = signer.build_and_sign(network="ethereum", to=None, value_wei=0, nonce=0,
                                  data="0x60")
        assert r["ok"] is False
        assert "testnet" in r["error"]

    def test_deploy_gas_fallback_is_deploy_sized_not_transfer_sized(self):
        # The hardcoded 21000 is a transfer amount; a deploy fallback must be far
        # larger so a real deploy is never under-gassed.
        assert signer._DEPLOY_GAS_FALLBACK > 100000

    def test_create_contract_address_is_deterministic_or_empty(self):
        # With the helper libs present it's a stable checksummed address; without
        # them it's "" — never a crash. Determinism: same inputs → same output.
        a1 = signer.create_contract_address("0x" + "ab" * 20, 0)
        a2 = signer.create_contract_address("0x" + "ab" * 20, 0)
        assert a1 == a2
        if a1:
            assert a1.startswith("0x") and len(a1) == 42
            # A different nonce yields a different address.
            assert signer.create_contract_address("0x" + "ab" * 20, 1) != a1

    def test_create_contract_address_never_raises_on_junk(self):
        assert signer.create_contract_address("not-an-address", 0) == ""


class TestExplorerAddressUrl:
    def test_address_url_for_a_known_testnet(self):
        url = gate.explorer_address_url("sepolia", "0x" + "cd" * 20)
        assert url.startswith("https://sepolia.etherscan.io/address/0x")

    def test_address_url_rejects_malformed(self):
        assert gate.explorer_address_url("sepolia", "not-hex") == ""
        assert gate.explorer_address_url("nope-net", "0x" + "cd" * 20) == ""


def _env(**over):
    base = {"revoked": False, "enforcing": True}
    base.update(over)
    return base


class TestAuthorityDeployKind:
    def test_deploy_allowed_under_a_healthy_enforcing_envelope(self):
        res = authorize(_env(), {"kind": "deploy", "network": "sepolia"},
                        now_ts=1000.0, spent_today_usd=0.0)
        assert res["decision"] == "allow"
        assert res["kind"] == "deploy"

    def test_deploy_denied_when_revoked(self):
        res = authorize(_env(revoked=True), {"kind": "deploy"},
                        now_ts=1000.0, spent_today_usd=0.0)
        assert res["decision"] == "deny"
        assert any("revoked" in r for r in res["reasons"])

    def test_deploy_denied_when_expired(self):
        res = authorize(_env(expiry_ts=500.0), {"kind": "deploy"},
                        now_ts=1000.0, spent_today_usd=0.0)
        assert res["decision"] == "deny"
        assert any("expired" in r for r in res["reasons"])

    def test_deploy_denied_with_no_envelope(self):
        res = authorize(None, {"kind": "deploy"}, now_ts=1000.0, spent_today_usd=0.0)
        assert res["decision"] == "deny"

    def test_deploy_moves_no_value_so_needs_no_destination(self):
        # Unlike a transfer, a deploy has no counterparty to allowlist — the
        # envelope's existence + kill-switch are the whole authority.
        res = authorize(_env(), {"kind": "deploy"}, now_ts=1000.0, spent_today_usd=0.0)
        assert res["decision"] == "allow"
