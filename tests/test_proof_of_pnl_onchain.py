"""Proof-of-PnL v0 — on-chain (Base) path validation (prediction P3).

Validates the strongest trust tier: a fill re-derived by netting ERC-20 Transfer
logs from a real Base transaction receipt, re-checkable by any third party from a
public RPC. Uses a frozen real receipt fixture (tx
0x3a6d70d2…378e — a WETH→USDC sell by wallet 0x51c72848…) so the test is
deterministic and offline; the network re-fetch is exercised via an injected
fetcher, never a live RPC call in CI.

Registered prediction P3: fill_from_evm_receipt reconstructs side=sell,
market=WETH/USDC, qty≈0.21208 WETH, price≈1872 USDC/WETH, tier=onchain_public,
with a deterministic fill_hash; verify.py's section-7 re-derivation reproduces the
identical hash from the same receipt and reports UNVERIFIED (never PASS) when it
cannot.
"""
import json
import os
from decimal import Decimal

from bot.proofofpnl import statement
from bot.proofofpnl.ingest_onchain_evm import fill_from_evm_receipt
import verify as verifier

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "base_swap_receipt.json")
_WALLET = "0x51c72848c68a965f66fa7a88855f9f7784502a7f"


def _receipt():
    with open(_FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── P3 — a real Base swap re-derives to the expected fill ─────────────

def test_p3_fill_from_real_base_receipt():
    f = fill_from_evm_receipt(_receipt(), _WALLET)
    assert f is not None
    assert f["venue_type"] == "onchain"
    assert f["market"] == "WETH/USDC"
    assert f["side"] == "sell"                     # sent WETH, received USDC
    assert f["trust_tier"] == "onchain_public"
    assert f["fee_ccy"] == "ETH"
    # qty = 0.212088168854805184 WETH (18dp), exact from the raw Transfer value
    assert f["qty"] == "0.212088168854805184"
    # price ≈ 397.021088 USDC / 0.21208… WETH ≈ 1872
    assert Decimal("1871") < Decimal(f["price"]) < Decimal("1873")
    assert f["source_ref"] == _receipt()["transactionHash"]


def test_p3_derivation_is_deterministic():
    a = fill_from_evm_receipt(_receipt(), _WALLET)
    b = fill_from_evm_receipt(_receipt(), _WALLET)
    assert a["fill_hash"] == b["fill_hash"]        # byte-identical across builds


def test_p3_wrong_wallet_is_unverifiable_not_faked():
    # A wallet not party to the swap has no clean 2-leg movement → None (UNVERIFIED),
    # never a fabricated fill.
    other = "0x00000000000000000000000000000000deadbeef"
    assert fill_from_evm_receipt(_receipt(), other) is None


# ── verify.py section-7: chain re-derivation confirms / flags ─────────

def _onchain_statement():
    """A statement carrying the single real on-chain fill, with the wallet bound
    in account_ids. A lone sell can't round-trip so it stays INCOMPLETE — but the
    section-7 re-derivation still runs and must confirm the fill's hash."""
    fill = fill_from_evm_receipt(_receipt(), _WALLET)
    return statement.build_epoch(
        [fill], account_ids=[_WALLET],
        open_snapshot=None, close_snapshot=None,
        range_start=0, range_end=0, sign=False), fill


def test_p3_verify_rederives_from_injected_receipt():
    st, fill = _onchain_statement()
    fetched = {}

    def fake_fetch(txh):
        fetched["tx"] = txh
        return _receipt()

    ok, diffs = verifier.verify_statement(st, fetch_receipt=fake_fetch)
    # The statement is INCOMPLETE (single fill), so overall verify is not PASS,
    # but crucially there must be NO on-chain UNVERIFIED diff — the fill re-derived.
    assert fetched["tx"] == fill["source_ref"]
    assert not any("chain re-derivation" in d or "receipt fetch failed" in d
                   for d in diffs)


def test_p3_verify_flags_unverified_when_rpc_unreachable():
    st, _ = _onchain_statement()

    def broken_fetch(txh):
        raise RuntimeError("RPC unreachable")

    ok, diffs = verifier.verify_statement(st, fetch_receipt=broken_fetch)
    assert not ok
    assert any("receipt fetch failed" in d for d in diffs)


def test_p3_verify_flags_unverified_when_wallet_not_bound():
    fill = fill_from_evm_receipt(_receipt(), _WALLET)
    st = statement.build_epoch(
        [fill], account_ids=["bitget:UTA"],          # no on-chain wallet bound
        open_snapshot=None, close_snapshot=None,
        range_start=0, range_end=0, sign=False)
    ok, diffs = verifier.verify_statement(st, fetch_receipt=lambda t: _receipt())
    assert not ok
    assert any("no on-chain wallet bound" in d for d in diffs)


def test_p3_offline_mode_reports_unverified():
    st, _ = _onchain_statement()
    ok, diffs = verifier.verify_statement(st, offline=True)
    assert not ok
    assert any("offline" in d for d in diffs)
