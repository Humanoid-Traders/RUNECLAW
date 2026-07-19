"""Track-record bundle assembler — raw CCXT data → verifiable, identity-bound bundle.

Pins: a complete dataset assembles to a PUBLISHED bundle whose statement verify.py
accepts and whose identity card is backed; an incomplete dataset assembles to an
honest INCOMPLETE bundle with an unbacked card; and the bundle is always
public-safe (no exchange summary field).
"""
import json

from bot.proofofpnl import assemble, erc8004
from bot.guardian import authority as auth
import verify as verifier

_AGENT = "0x0000000000000000000000000000000000A9e01"


def _trades():
    """CCXT-style unified trades: buy 1 BTC @100 (fee .1), sell 1 @110 (fee .11).
    Net = 10 - 0.21 = 9.79."""
    return [
        {"id": "t1", "order": "o1", "symbol": "BTC/USDT", "side": "buy",
         "price": 100, "amount": 1, "timestamp": 1000,
         "fee": {"cost": 0.1, "currency": "USDT"}},
        {"id": "t2", "order": "o2", "symbol": "BTC/USDT", "side": "sell",
         "price": 110, "amount": 1, "timestamp": 2000,
         "fee": {"cost": 0.11, "currency": "USDT"}},
    ]


def test_complete_dataset_publishes_and_verifies():
    env = auth.compile_envelope({"allowed_venues": ["bitget"],
                                 "max_notional_per_trade_usd": 1000})
    bundle = assemble.assemble_track_record(
        _trades(), account_ids=["bitget:UTA"],
        open_balance="1000", close_balance="1009.79",
        range_start=1000, range_end=2000,
        agent_address=_AGENT, envelope=env, sign=True)

    st = bundle["statement"]
    assert st["status"] == "published"
    assert st["metrics"]["net_pnl"] == "9.79"
    # verify.py accepts the statement (skip only if signing unavailable)
    if (st.get("attestation") or {}).get("type") == "ed25519":
        ok, diffs = verifier.verify_statement(st)
        assert ok, diffs
        # identity card is backed and re-verifies
        card = bundle["identity_card"]
        assert card["status"] == "backed"
        cok, cdiffs = erc8004.verify_card(card)
        assert cok, cdiffs
    # manifest reflects reality
    assert bundle["manifest"]["status"] == "published"
    assert bundle["manifest"]["trust_tier"] == "cex_operator_signed"
    assert bundle["manifest"]["card_hash"] == bundle["identity_card"]["card_hash"]


def test_missing_snapshots_is_incomplete_and_unbacked():
    bundle = assemble.assemble_track_record(
        _trades(), account_ids=["bitget:UTA"],
        open_balance=None, close_balance=None,   # no reconciliation anchors
        agent_address=_AGENT, sign=False)
    assert bundle["statement"]["status"] == "INCOMPLETE"
    assert bundle["identity_card"]["status"] == "unbacked"
    assert bundle["identity_card"]["reputation"] is None
    assert bundle["manifest"]["status"] == "INCOMPLETE"
    assert "not a published proof".lower() in bundle["manifest"]["disclaimer"].lower()


def test_omission_breaks_reconciliation():
    # Only the winning trade, but the real close balance reflects a loss too →
    # fills-net (9.79) can't match the balance delta → INCOMPLETE (omission caught).
    bundle = assemble.assemble_track_record(
        _trades(), account_ids=["bitget:UTA"],
        open_balance="1000", close_balance="900.00",   # real delta -100, fills say +9.79
        range_start=1000, range_end=2000, sign=False)
    assert bundle["statement"]["status"] == "INCOMPLETE"


def test_bundle_is_public_safe():
    bundle = assemble.assemble_track_record(
        _trades(), account_ids=["bitget:UTA"],
        open_balance="1000", close_balance="1009.79",
        range_start=1000, range_end=2000, agent_address=_AGENT, sign=False)
    assert assemble.is_public_safe(bundle)
    # inject a forbidden summary → detected
    bad = json.loads(json.dumps(bundle))
    bad["statement"]["summary"] = {"equity": 1000}
    assert not assemble.is_public_safe(bad)
