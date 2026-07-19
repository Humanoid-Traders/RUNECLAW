"""ERC-8004 Agent Identity Card — pre-registered predictions E1–E4.

E1 no reputation without a published statement, E2 trust-tier carried forward,
E3 determinism + signature (mutation fails), E4 anchor honestly UNVERIFIED.
Pure except the optional Ed25519 signature.
"""
import json

import pytest

from bot.proofofpnl import csf, statement, erc8004
from bot.guardian import authority as auth

_AGENT = "0x00000000000000000000000000000000AbCdEf01"


def _published():
    """A flat→flat published BTC round-trip: buy 1@100 (fee .1), sell 1@110
    (fee .11); net 9.79 reconciles against a 9.79 balance delta."""
    fills = [
        csf.make_fill("bitget", "cex", "BTC/USDT", "buy", 100, 1, "0.1", "USDT",
                      1000, "t1@o1", "cex_operator_signed"),
        csf.make_fill("bitget", "cex", "BTC/USDT", "sell", 110, 1, "0.11", "USDT",
                      2000, "t2@o2", "cex_operator_signed"),
    ]
    return statement.build_epoch(
        fills, account_ids=["acct"],
        open_snapshot={"balance": "1000", "ccy": "USDT", "ts": 1000},
        close_snapshot={"balance": "1009.79", "ccy": "USDT", "ts": 2000},
        range_start=1000, range_end=2000, sign=False)


def _incomplete():
    """One fill with no fee → INCOMPLETE (cannot reconcile)."""
    fills = [csf.make_fill("bitget", "cex", "BTC/USDT", "buy", 100, 1, None, "",
                           1000, "t1@o1", "cex_operator_signed")]
    return statement.build_epoch(fills, account_ids=["acct"], open_snapshot=None,
                                 close_snapshot=None, range_start=0, range_end=0,
                                 sign=False)


# ── E1 — no reputation without a published statement ──────────────────

def test_e1_incomplete_statement_is_unbacked():
    st = _incomplete()
    assert st["status"] != "published"
    card = erc8004.build_identity_card(_AGENT, st, sign=False)
    assert card["status"] == "unbacked"
    assert card["reputation"] is None


def test_e1_no_statement_is_unbacked():
    card = erc8004.build_identity_card(_AGENT, None, sign=False)
    assert card["status"] == "unbacked"
    assert card["reputation"] is None


def test_e1_published_statement_is_backed():
    st = _published()
    assert st["status"] == "published"
    card = erc8004.build_identity_card(_AGENT, st, sign=False)
    assert card["status"] == "backed"
    assert card["reputation"] is not None
    assert card["reputation"]["self_reported"] is False


# ── E2 — trust tier carried forward, never inflated ───────────────────

def test_e2_tier_matches_statement():
    st = _published()
    card = erc8004.build_identity_card(_AGENT, st, sign=False)
    assert card["reputation"]["trust_tier"] == st["trust_tier"] == "cex_operator_signed"
    assert card["reputation"]["merkle_root"] == st["merkle_root"]


# ── E3 — determinism + signature (mutation fails) ─────────────────────

def test_e3_determinism():
    st = _published()
    a = erc8004.build_identity_card(_AGENT, st, sign=False)
    b = erc8004.build_identity_card(_AGENT, st, sign=False)
    assert a["card_hash"] == b["card_hash"]
    assert a["card_id"] == b["card_id"]


def test_e3_signed_card_verifies_then_mutation_fails():
    st = _published()
    card = erc8004.build_identity_card(_AGENT, st, sign=True)
    if (card.get("attestation") or {}).get("type") != "ed25519":
        pytest.skip("Ed25519 signing unavailable (cryptography missing)")
    ok, diffs = erc8004.verify_card(card)
    assert ok, diffs
    # mutate a bound reputation metric → hash + signature fail
    bad = json.loads(json.dumps(card))
    bad["reputation"]["metrics"]["net_pnl"] = "999.0"
    ok2, diffs2 = erc8004.verify_card(bad)
    assert not ok2 and any("card_hash" in d for d in diffs2)
    # mutate the agent address → also fails
    bad2 = json.loads(json.dumps(card))
    bad2["identity"]["agent_address"] = "0xdeadbeef"
    ok3, _ = erc8004.verify_card(bad2)
    assert not ok3


def test_e3_custody_posture_bound():
    st = _published()
    env = auth.compile_envelope({"allowed_venues": ["bitget"],
                                 "max_notional_per_trade_usd": 1000})
    card = erc8004.build_identity_card(_AGENT, st, envelope=env, sign=False)
    assert card["custody"]["envelope_hash"] == env["compiled_hash"]
    assert card["custody"]["withdraw_allowed"] is False
    # the envelope hash is inside the card hash → changing the envelope changes it
    env2 = auth.compile_envelope({"allowed_venues": ["bitget"],
                                  "max_notional_per_trade_usd": 500})
    card2 = erc8004.build_identity_card(_AGENT, st, envelope=env2, sign=False)
    assert card2["card_hash"] != card["card_hash"]


# ── E4 — anchor honestly UNVERIFIED ───────────────────────────────────

def test_e4_anchor_is_unverified():
    card = erc8004.build_identity_card(_AGENT, _published(), sign=False)
    assert card["anchor"]["status"] == "UNVERIFIED"
    assert card["anchor"]["reputation_registry"] == erc8004.REPUTATION_REGISTRY
    # the card never claims a confirmed on-chain anchor
    assert "no on-chain tx" in card["anchor"]["note"]
