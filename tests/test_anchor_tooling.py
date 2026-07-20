"""MH2 — ERC-8004 anchor tooling (Base root anchor, non-custodial).

The contract under test: the bot NEVER signs or broadcasts — it only plans a
transaction the operator sends from their own wallet, then verifies it
on-chain before anything upgrades to VERIFIED. A recorded anchor that no
longer matches the current identity (rotated key) reads STALE, never
silently VERIFIED. With no recorded anchor, the card's honest UNVERIFIED
plan passes through byte-identical (pinning the existing E4 contract).
"""

import json

import pytest

from bot.proofofpnl import anchor, erc8004

_AGENT = "0x" + "ab" * 20
_PUBKEY = "cd" * 32
_TX = "0x" + "11" * 32


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "anchor_state.json"
    monkeypatch.setenv("ANCHOR_STATE_PATH", str(p))
    monkeypatch.delenv("ANCHOR_REGISTRY_ADDRESS", raising=False)
    monkeypatch.delenv("ANCHOR_CHAIN_ID", raising=False)
    return p


# ── commitment + calldata ────────────────────────────────────────────────────

def test_identity_commitment_deterministic_and_case_insensitive():
    a = anchor.identity_commitment(_AGENT, _PUBKEY)
    b = anchor.identity_commitment(_AGENT.upper(), _PUBKEY.upper())
    assert a == b and len(a) == 64
    assert anchor.identity_commitment(_AGENT, "ee" * 32) != a, \
        "rotating the signing key must change the commitment"


def test_anchor_calldata_carries_magic_and_commitment():
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    data = anchor.anchor_calldata(c)
    assert data == "0x" + "52554e45434c4157" + c
    assert bytes.fromhex(data[2:18]).decode() == "RUNECLAW"


# ── dry-run plan (never signs, never sends) ──────────────────────────────────

def test_build_anchor_tx_is_dry_run_and_estimate_fail_soft(state_path, monkeypatch):
    monkeypatch.setattr(anchor, "_rpc",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    plan = anchor.build_anchor_tx(_AGENT, _PUBKEY)
    assert plan["dry_run"] is True
    assert plan["chain_id"] == anchor.BASE_CHAIN_ID
    assert plan["mode"] == "calldata-commitment"
    assert plan["to"] == _AGENT  # 0-value self-send
    assert plan["value"] == "0"
    assert plan["commitment"] in plan["data"]
    assert plan["estimate"]["available"] is False, "network weather never blocks planning"
    assert any("confirm" in s for s in plan["instructions"])
    assert "Ethereum mainnet" in plan["promotion_note"]
    src = open(anchor.__file__).read()
    for forbidden in ("signTransaction", "sendRawTransaction", "private_key",
                      "eth_sendTransaction", "eth_accounts"):
        assert forbidden not in src, f"anchor tooling must never {forbidden}"


def test_registry_mode_targets_registry(state_path, monkeypatch):
    monkeypatch.setenv("ANCHOR_REGISTRY_ADDRESS", "0x" + "99" * 20)
    monkeypatch.setattr(anchor, "_rpc",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    plan = anchor.build_anchor_tx(_AGENT, _PUBKEY)
    assert plan["mode"] == "registry"
    assert plan["to"] == "0x" + "99" * 20


# ── on-chain confirmation gate ───────────────────────────────────────────────

def _fake_rpc(tx=None, rcpt=None):
    def rpc(method, params):
        if method == "eth_getTransactionByHash":
            return tx
        if method == "eth_getTransactionReceipt":
            return rcpt
        raise AssertionError(method)
    return rpc


def _good_chain(commitment):
    tx = {"from": _AGENT, "input": anchor.anchor_calldata(commitment)}
    rcpt = {"status": "0x1", "blockNumber": "0x1a2b3c"}
    return tx, rcpt


def test_confirm_rejects_malformed_hash(state_path):
    ok, problems = anchor.confirm_anchor("nothex", _AGENT, _PUBKEY)
    assert not ok and "64 hex" in problems[0]


def test_confirm_records_only_after_all_checks_pass(state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    tx, rcpt = _good_chain(c)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, rcpt))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert ok and problems == []
    state = json.loads(state_path.read_text())
    rec = state[str(anchor.BASE_CHAIN_ID)]
    assert rec["tx_hash"] == _TX
    assert rec["block_number"] == 0x1A2B3C
    assert rec["commitment"] == c


@pytest.mark.parametrize("mutate,needle", [
    (lambda tx, rcpt: rcpt.update(status="0x0"), "FAILED"),
    (lambda tx, rcpt: tx.update(input="0xdeadbeef"), "does not contain"),
    (lambda tx, rcpt: tx.update({"from": "0x" + "77" * 20}), "not the agent address"),
])
def test_confirm_rejects_bad_chain_state(state_path, monkeypatch, mutate, needle):
    tx, rcpt = _good_chain(anchor.identity_commitment(_AGENT, _PUBKEY))
    mutate(tx, rcpt)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, rcpt))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert not ok and any(needle in p for p in problems)
    assert not state_path.exists(), "nothing recorded unless every check passes"


def test_confirm_unmined_and_missing(state_path, monkeypatch):
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(None, None))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert not ok and "not found" in problems[0]
    tx, _ = _good_chain(anchor.identity_commitment(_AGENT, _PUBKEY))
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, None))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert not ok and "not yet mined" in problems[0]


# ── card upgrade: UNVERIFIED → VERIFIED / STALE ──────────────────────────────

def test_no_state_passes_unverified_plan_through(state_path):
    plan = {"status": "UNVERIFIED", "note": "designed anchor"}
    assert anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, plan) is plan


def test_matching_anchor_upgrades_to_verified(state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    out = anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, {"status": "UNVERIFIED"})
    assert out["status"] == "VERIFIED"
    assert out["commitment"] == c
    assert out["anchors"][0]["explorer"] == f"https://basescan.org/tx/{_TX}"
    assert out["anchors"][0]["block_number"] == 0x1A2B3C


def test_rotated_key_reads_stale_never_verified(state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    out = anchor.anchor_for_card(_AGENT, "ee" * 32, "h" * 64,
                                 {"status": "UNVERIFIED", "note": "plan"})
    assert out["status"] == "STALE"
    assert "DIFFERENT identity" in out["note"]
    assert out["stale_anchors"][0]["tx_hash"] == _TX


def test_per_chain_records_coexist_base_root_plus_mainnet_mirror(
        state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    monkeypatch.setenv("ANCHOR_CHAIN_ID", "1")  # operator later promotes
    tx2 = "0x" + "22" * 32
    assert anchor.confirm_anchor(tx2, _AGENT, _PUBKEY)[0]
    out = anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, {})
    chains = sorted(a["chain_id"] for a in out["anchors"])
    assert chains == [1, anchor.BASE_CHAIN_ID], "mainnet mirror adds, never replaces"
    explorers = {a["chain_id"]: a["explorer"] for a in out["anchors"]}
    assert explorers[1].startswith("https://etherscan.io/tx/")
    assert explorers[anchor.BASE_CHAIN_ID].startswith("https://basescan.org/tx/")


# ── integration: build_identity_card picks the anchor up ─────────────────────

def test_build_identity_card_upgrades_anchor_from_state(state_path, monkeypatch):
    # sign=False → attestation pubkey is "" — the commitment binds exactly that.
    c = anchor.identity_commitment(_AGENT, "")
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, "")[0]
    card = erc8004.build_identity_card(_AGENT, None, sign=False)
    assert card["anchor"]["status"] == "VERIFIED"
    assert card["anchor"]["anchors"][0]["tx_hash"] == _TX
    # the card itself still verifies — the anchor is reported, not hashed in
    ok, diffs = erc8004.verify_card(card)
    assert ok, diffs


def test_build_identity_card_without_state_stays_unverified(state_path):
    card = erc8004.build_identity_card(_AGENT, None, sign=False)
    assert card["anchor"]["status"] == "UNVERIFIED"
    assert "no on-chain tx" in card["anchor"]["note"]
