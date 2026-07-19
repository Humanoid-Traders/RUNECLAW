"""Continuous Proof-of-PnL publishing — predictions P1–P6.

P1 seals a public-safe bundle with a re-derivable hash; P2 refuses unsafe;
P3 anchor stays UNVERIFIED; P4 verify re-derives + catches tampering; P5
freshness window; P6 store round-trip + publish_now persists. Pure + store.
"""
import os
import tempfile

import pytest

from bot.proofofpnl import publish as pub


def _bundle(tier="onchain_public", recon="COMPLETE", summary=False):
    stmt = {"trust_tier": tier, "reconciliation": {"status": recon}}
    if summary:
        stmt["summary"] = {"secret": "exchange internals"}
    return {"format": "b", "statement": stmt,
            "identity_card": {"anchor": {"status": "UNVERIFIED", "chain_id": 84532,
                                         "card_hash": "abc"}},
            "manifest": {}}


# ── P1 — seal ─────────────────────────────────────────────────────────

def test_p1_seals_with_rederivable_hash():
    p = pub.build_publication(_bundle(), published_at_ts=1000, epoch_seq=5)
    assert p["format"] == pub.PUBLICATION_FORMAT
    assert p["publish_hash"] == pub.publish_hash(p["bundle"])
    assert p["published_at"] == 1000 and p["epoch_seq"] == 5
    assert p["trust_tier"] == "onchain_public"
    assert p["reconciliation"] == "COMPLETE"


# ── P2 — refuses unsafe ───────────────────────────────────────────────

def test_p2_refuses_unsafe_bundle():
    with pytest.raises(ValueError):
        pub.build_publication(_bundle(summary=True), published_at_ts=1)


# ── P3 — anchor stays UNVERIFIED ──────────────────────────────────────

def test_p3_anchor_unverified():
    p = pub.build_publication(_bundle(), published_at_ts=1)
    assert p["anchor"]["status"] == "UNVERIFIED"
    assert p["anchor"]["chain_id"] == 84532


def test_p3_incomplete_epoch_publishes_honestly():
    p = pub.build_publication(_bundle(tier="cex_operator_signed", recon="INCOMPLETE"),
                              published_at_ts=1)
    assert p["reconciliation"] == "INCOMPLETE"       # published as-is, honest


# ── P4 — verify re-derives + catches tampering ────────────────────────

def test_p4_verify_ok_then_tamper_detected():
    p = pub.build_publication(_bundle(), published_at_ts=1)
    ok, problems = pub.verify_publication(p)
    assert ok and problems == []
    # tamper the bundle after sealing → hash mismatch
    p["bundle"]["statement"]["trust_tier"] = "cex_operator_signed"
    ok2, problems2 = pub.verify_publication(p)
    assert ok2 is False
    assert any("publish_hash mismatch" in x for x in problems2)


# ── P5 — freshness ────────────────────────────────────────────────────

def test_p5_freshness_window():
    p = pub.build_publication(_bundle(), published_at_ts=1000)
    assert pub.is_fresh(p, 1000) is True
    assert pub.is_fresh(p, 1000 + pub.DEFAULT_MAX_AGE_S) is True
    assert pub.is_fresh(p, 1000 + pub.DEFAULT_MAX_AGE_S + 1) is False
    assert pub.is_fresh(p, 500) is False             # future-dated → not fresh
    assert pub.is_fresh(None, 1000) is False


# ── P6 — store + publish_now ──────────────────────────────────────────

def test_p6_store_roundtrip_and_publish_now():
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        store = pub.PublicationStore(path)
        p = pub.publish_now(_bundle(), published_at_ts=2000, epoch_seq=1, store=store)
        back = store.read()
        assert back is not None
        assert back["publish_hash"] == p["publish_hash"]
        ok, _ = pub.verify_publication(back)
        assert ok
    finally:
        os.unlink(path)
