"""Proof-of-PnL v0 — CEX path validation.

Validates the four pre-registered predictions (docs/proof_of_pnl/PREDICTIONS.md):
P1 determinism, P2 reconciliation-catches-omission, P4 trust-tier-min honesty, and
the verify.py round-trip. Also pins the honest outcome for the real
`live_trade_proof.json`: it is NOT fills-grade (no fees, no prices on 2/3
round-trips) → the pipeline must mark it INCOMPLETE, never published.
"""
import json
import os

import pytest

from bot.proofofpnl import csf, ingest_cex, statement
import verify as verifier


# ── helpers ───────────────────────────────────────────────────────────

def _complete_fills():
    """A flat→flat BTC round-trip with known fees: buy 1@100 (fee 0.1), sell
    1@110 (fee 0.11). Net = (110-100) - 0.1 - 0.11 = 9.79."""
    return [
        csf.make_fill("bitget", "cex", "BTC/USDT", "buy", 100, 1, "0.1", "USDT",
                      1000, "t1@o1", "cex_operator_signed"),
        csf.make_fill("bitget", "cex", "BTC/USDT", "sell", 110, 1, "0.11", "USDT",
                      2000, "t2@o2", "cex_operator_signed"),
    ]


def _snap(balance, ts):
    return {"balance": str(balance), "ccy": "USDT", "ts": ts}


# ── P1 — determinism ──────────────────────────────────────────────────

def test_p1_determinism_and_mutation_changes_root():
    fills = _complete_fills()
    s1 = statement.build_epoch(fills, account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=False)
    s2 = statement.build_epoch(_complete_fills(), account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=False)
    # byte-identical roots + commitments across independent builds
    assert s1["merkle_root"] == s2["merkle_root"]
    assert s1["commitment"] == s2["commitment"]
    assert csf.canonical(s1["fills"]) == csf.canonical(s2["fills"])
    # mutate one fill's qty by a minor unit → root changes
    mutated = _complete_fills()
    mutated[0] = csf.make_fill("bitget", "cex", "BTC/USDT", "buy", 100, "1.0001",
                               "0.1", "USDT", 1000, "t1@o1", "cex_operator_signed")
    assert csf.merkle_root(mutated) != s1["merkle_root"]
    assert csf.compute_metrics(fills)["round_trips"] == 1


# ── P4 — trust-tier minimum ───────────────────────────────────────────

def test_p4_epoch_tier_is_the_minimum():
    mixed = [
        csf.make_fill("base:uniswap-v3", "onchain", "WETH/USDC", "buy", 3000, 1,
                      "1", "USDC", 1000, "0xabc#0", "onchain_public"),
        csf.make_fill("bitget", "cex", "BTC/USDT", "sell", 110, 1, "0.1", "USDT",
                      2000, "t2@o2", "cex_operator_signed"),
    ]
    # min(onchain_public=3, cex_operator_signed=1) → cex_operator_signed
    assert csf.epoch_tier(mixed) == "cex_operator_signed"


# ── P2 — reconciliation publishes; omission is caught ─────────────────

def test_p2_complete_epoch_publishes():
    fills = _complete_fills()
    # net = 9.79 → close-open must equal 9.79 to publish
    st = statement.build_epoch(fills, account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=False)
    assert st["status"] == "published"
    assert st["metrics"]["net_pnl"] == "9.79"


def test_p2_omission_is_caught():
    # Two round-trips: a winner (+9.79) and a loser. Publish requires both to
    # reconcile against the balance delta; dropping the loser breaks it.
    winner = _complete_fills()
    loser = [
        csf.make_fill("bitget", "cex", "ETH/USDT", "buy", 3000, 1, "0.1", "USDT",
                      3000, "t3@o3", "cex_operator_signed"),
        csf.make_fill("bitget", "cex", "ETH/USDT", "sell", 2900, 1, "0.1", "USDT",
                      4000, "t4@o4", "cex_operator_signed"),
    ]
    # loser net = (2900-3000) - 0.1 - 0.1 = -100.2 ; total net = 9.79 - 100.2 = -90.41
    all_fills = winner + loser
    full = statement.build_epoch(all_fills, account_ids=["acct"],
                                 open_snapshot=_snap("1000", 1000),
                                 close_snapshot=_snap("909.59", 4000),
                                 range_start=1000, range_end=4000, sign=False)
    assert full["status"] == "published"            # honest, complete set reconciles

    # Now omit the loser but keep the same (real) balance delta → must NOT publish.
    omitted = statement.build_epoch(winner, account_ids=["acct"],
                                    open_snapshot=_snap("1000", 1000),
                                    close_snapshot=_snap("909.59", 4000),
                                    range_start=1000, range_end=4000, sign=False)
    assert omitted["status"] == "INCOMPLETE"
    assert any("unreconciled" in r for r in omitted["reconciliation"]["reasons"])


# ── real proof file → INCOMPLETE (honest) ─────────────────────────────

def test_real_proof_file_is_incomplete_and_never_reads_summary():
    path = os.path.join(os.path.dirname(__file__), "..", "live_trade_proof.json")
    if not os.path.exists(path):
        pytest.skip("live_trade_proof.json not present")
    fills = ingest_cex.fills_from_proof_file(path)
    # 3 round-trips → 6 fills; none is metrics-complete (no fees; 2/3 have no price)
    assert len(fills) == 6
    assert all(not csf.fill_is_complete(f) for f in fills)
    st = statement.build_epoch(fills, account_ids=["bitget:UTA"],
                               open_snapshot=None, close_snapshot=None,
                               range_start=0, range_end=0, sign=False)
    assert st["status"] == "INCOMPLETE"
    # the statement path must carry no 'summary' field
    assert not verifier._contains_key(st, "summary")


# ── verify.py round-trip ──────────────────────────────────────────────

def test_verify_pass_then_tamper_fails(tmp_path):
    fills = _complete_fills()
    st = statement.build_epoch(fills, account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=True)
    if (st.get("attestation") or {}).get("type") != "ed25519":
        pytest.skip("Ed25519 signing unavailable (cryptography missing)")
    ok, diffs = verifier.verify_statement(st)
    assert ok, diffs

    # tamper a metric → verify must FAIL
    bad = json.loads(json.dumps(st))
    bad["metrics"]["net_pnl"] = "999.0"
    ok2, diffs2 = verifier.verify_statement(bad)
    assert not ok2 and any("net_pnl" in d for d in diffs2)

    # tamper a fill qty (leaving its stored hash) → hash + root mismatch
    bad2 = json.loads(json.dumps(st))
    bad2["fills"][0]["qty"] = "2"
    ok3, diffs3 = verifier.verify_statement(bad2)
    assert not ok3 and any("fill_hash" in d or "merkle_root" in d for d in diffs3)


def test_verify_flags_a_forbidden_summary_field():
    fills = _complete_fills()
    st = statement.build_epoch(fills, account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=False)
    st["open_snapshot"]["summary"] = {"equity": 1000}   # inject the forbidden field
    ok, diffs = verifier.verify_statement(st)
    assert not ok and any("summary" in d for d in diffs)
