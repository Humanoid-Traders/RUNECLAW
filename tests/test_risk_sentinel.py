"""Systemic Risk Sentinel — deterministic intra-book crowding detector.

The sentinel flags structural crowding in the agent's own book: correlated
concentration, directional bias, same-group/same-direction clusters, and shared
liquidation zones. These tests pin each concern, the diversified-book-is-calm
contract, the fail-safe, and the compact record shape.
"""
import json

from bot.guardian import risk_sentinel as rs


def _pos(symbol, direction, group, entry=100.0, qty=1.0, leverage=5):
    return {"symbol": symbol, "direction": direction, "group": group,
            "entry": entry, "qty": qty, "leverage": leverage, "cost_usd": entry * qty / leverage}


# ── concentration ─────────────────────────────────────────────────────

def test_correlated_concentration_flagged():
    # Whole book in one group → concentration high.
    book = [_pos("BTCUSDT", "LONG", "BTC"), _pos("ETHUSDT", "LONG", "BTC")]
    a = rs.analyze(book)
    kinds = {c["kind"] for c in a["concerns"]}
    assert "correlated_concentration" in kinds
    assert a["top_group"]["group"] == "BTC"
    assert a["top_group"]["share_pct"] == 100.0
    assert a["risk"] == "high"


# ── directional crowding ──────────────────────────────────────────────

def test_directional_crowding_flagged():
    # All longs across different groups → not concentrated, but fully crowded long.
    book = [_pos("A", "LONG", "BTC"), _pos("B", "LONG", "ETH"),
            _pos("C", "LONG", "L2"), _pos("D", "LONG", "AI")]
    a = rs.analyze(book)
    kinds = {c["kind"] for c in a["concerns"]}
    assert "directional_crowding" in kinds
    assert a["net_direction"] == "long" and a["net_bias"] == 1.0


def test_balanced_book_has_low_crowding():
    book = [_pos("A", "LONG", "BTC"), _pos("B", "SHORT", "ETH")]
    a = rs.analyze(book)
    assert a["net_direction"] == "balanced"
    assert all(c["kind"] != "directional_crowding" for c in a["concerns"])


# ── same-group same-direction cluster ─────────────────────────────────

def test_correlated_cluster_flagged():
    book = [_pos(s, "LONG", "MEME") for s in ("PEPE", "WIF", "BONK", "FLOKI")]
    a = rs.analyze(book)
    cluster = [c for c in a["concerns"] if c["kind"] == "correlated_cluster"]
    assert cluster and cluster[0]["severity"] == "high"   # 4 same group+dir


# ── shared liquidation zone ───────────────────────────────────────────

def test_shared_liquidation_zone_flagged():
    # Three longs all at 10x → all liquidate at ~9.95% → one tight zone.
    book = [_pos(s, "LONG", g, leverage=10)
            for s, g in (("A", "BTC"), ("B", "ETH"), ("C", "L2"))]
    a = rs.analyze(book)
    assert any(c["kind"] == "shared_liquidation_zone" for c in a["concerns"])


def test_spread_leverage_no_shared_zone():
    # Longs at very different leverage → liquidation moves far apart → no zone.
    book = [_pos("A", "LONG", "BTC", leverage=2),
            _pos("B", "LONG", "ETH", leverage=10),
            _pos("C", "LONG", "L2", leverage=25)]
    a = rs.analyze(book)
    assert all(c["kind"] != "shared_liquidation_zone" for c in a["concerns"])


# ── diversified book is calm ──────────────────────────────────────────

def test_diversified_book_is_calm():
    # Different groups, mixed direction, spread leverage → nothing trips.
    book = [_pos("A", "LONG", "BTC", leverage=3),
            _pos("B", "SHORT", "ETH", leverage=8),
            _pos("C", "LONG", "L2", leverage=20)]
    a = rs.analyze(book)
    assert a["risk"] == "none"
    assert a["concerns"] == []


def test_empty_book_is_calm():
    a = rs.analyze([])
    assert a["risk"] == "none" and a["position_count"] == 0


# ── fail-safe ─────────────────────────────────────────────────────────

def test_analyze_never_raises_on_garbage():
    for bad in (None, "x", [1, 2], [{"entry": "?", "qty": None}]):
        r = rs.analyze(bad)
        assert isinstance(r, dict) and "risk" in r


# ── record shape ──────────────────────────────────────────────────────

def test_sentinel_payload_is_compact_and_serialisable():
    book = [_pos("BTCUSDT", "LONG", "BTC"), _pos("ETHUSDT", "LONG", "BTC")]
    p = rs.sentinel_payload(book)
    assert p["risk"] == "high"
    assert p["top_group"]["group"] == "BTC"
    json.dumps(p)                          # rides the flight-record sync
    calm = rs.sentinel_payload([])
    assert calm["risk"] == "none" and calm["concerns"] == []
