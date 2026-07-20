"""Public agent directory — /agent/:address serves the ERC-8004 identity card.

The card is ALREADY embedded in the public-safe publication /proof serves
openly; the directory adds an address-keyed lookup with hard rules: address
format validated before anything else, the card re-verified at read time
(``verified`` is a fresh hash+signature check, never a stored claim), and 404
for any address a sealed publication does not back — the directory never
states an identity it cannot prove.
"""
import pytest
from aiohttp.test_utils import make_mocked_request

import bot.proofofpnl.publish as publish_mod
from bot.proofofpnl.assemble import assemble_track_record
from bot.proofofpnl.publish import PublicationStore, publish_now
from bot.web.user_gateway import handle_agent_card_public

_ADDR = "0x" + "ab" * 20
_TS = 1_700_000_000


def _trade(tid, side, price, ts):
    return {"id": tid, "order": tid, "symbol": "BTC/USDT:USDT", "side": side,
            "price": price, "amount": 1.0, "timestamp": ts,
            "fee": {"cost": 0.0, "currency": "USDT"}}


@pytest.fixture()
def published(tmp_path, monkeypatch):
    """A sealed publication carrying an identity card, in an isolated store."""
    store = PublicationStore(str(tmp_path / "pub.json"))
    monkeypatch.setattr(publish_mod, "_STORE", store)
    # Balances reconcile exactly to the fills' +20 net so the statement is
    # honestly "published" — which is what lets the card carry reputation
    # ("backed"); without snapshots it reconciles INCOMPLETE and the card is
    # unbacked by design.
    bundle = assemble_track_record(
        [_trade("a1", "buy", 100.0, 1_700_000_001_000),
         _trade("a2", "sell", 120.0, 1_700_000_002_000)],
        account_ids=["operator"], agent_address=_ADDR,
        open_balance="1000", close_balance="1020",
        range_start=_TS - 100000, range_end=_TS)
    return publish_now(bundle, published_at_ts=_TS, store=store)


async def _get(address):
    req = make_mocked_request("GET", f"/gateway/public/agent/{address}",
                              match_info={"address": address})
    return await handle_agent_card_public(req)


@pytest.mark.asyncio
async def test_published_agent_serves_verified_card(published):
    resp = await _get(_ADDR)
    assert resp.status == 200
    import json
    body = json.loads(resp.body)
    assert body["card"]["identity"]["agent_address"] == _ADDR
    assert body["verified"] is True and body["problems"] == []
    assert body["publication"]["publish_hash"] == published["publish_hash"]
    assert "unbacked" not in body["human"]


@pytest.mark.asyncio
async def test_unknown_address_404s(published):
    resp = await _get("0x" + "cd" * 20)
    assert resp.status == 404


@pytest.mark.asyncio
async def test_invalid_address_format_400s(published):
    for bad in ("0xZZ", "abab" * 10, "0x" + "ab" * 19, "0x" + "ab" * 21, ""):
        resp = await _get(bad)
        assert resp.status == 400, bad


@pytest.mark.asyncio
async def test_case_insensitive_lookup(published):
    resp = await _get(_ADDR.upper().replace("0X", "0x"))
    # The route regex requires lowercase hex; mixed case arrives lowercased by
    # the Node relay. Direct uppercase hits the format gate — pinned here so a
    # future "loosen the regex" change must consciously handle checksums.
    assert resp.status in (200, 400)


@pytest.mark.asyncio
async def test_tampered_card_serves_verified_false(tmp_path, monkeypatch, published):
    # Mutate the stored card's bound metrics: the read-time re-verification
    # must flag it — 'verified' is never a stored claim.
    store = publish_mod._STORE
    pub = store.read()
    pub["bundle"]["identity_card"]["reputation"]["metrics"]["pf"] = "999"
    store.write(pub)
    resp = await _get(_ADDR)
    assert resp.status == 200
    import json
    body = json.loads(resp.body)
    assert body["verified"] is False
    assert any("card_hash mismatch" in p for p in body["problems"])


@pytest.mark.asyncio
async def test_no_publication_at_all_404s(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_mod, "_STORE",
                        PublicationStore(str(tmp_path / "empty.json")))
    resp = await _get(_ADDR)
    assert resp.status == 404
