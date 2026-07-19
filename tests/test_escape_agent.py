"""Universal Escape Agent — deterministic emergency-exit planner.

The Escape Agent ranks the open book into a safe, ordered unwind: most-dangerous
first (fragility × exposure), with each step's freed margin and reason attached.
These tests pin the ordering, the urgency/risk rollup, the cumulative margin
accounting, the empty-book / fail-safe contracts, and the compact record shape.
It plans only — it never closes anything.
"""
import json

from bot.guardian import escape_agent as ea


def _pos(symbol, direction, group, entry=100.0, qty=1.0, leverage=5):
    return {"symbol": symbol, "direction": direction, "group": group,
            "entry": entry, "qty": qty, "leverage": leverage,
            "cost_usd": entry * qty / leverage}


# ── ordering: most dangerous first ────────────────────────────────────

def test_most_fragile_and_largest_closes_first():
    book = [
        _pos("SAFE", "LONG", "L2", entry=100, qty=1, leverage=2),    # far from liq, small
        _pos("DANGER", "LONG", "BTC", entry=100, qty=5, leverage=20),  # near liq, large
        _pos("MID", "LONG", "ETH", entry=100, qty=1, leverage=5),
    ]
    p = ea.plan(book)
    symbols = [s["symbol"] for s in p["steps"]]
    assert symbols[0] == "DANGER"          # biggest + most fragile → order 1
    assert symbols[-1] == "SAFE"           # smallest + safest → last
    assert p["steps"][0]["order"] == 1
    assert p["steps"][0]["reason"].startswith("close first")


def test_urgency_is_monotonic_down_the_plan():
    book = [_pos(f"S{i}", "LONG", "BTC", qty=i, leverage=10) for i in range(1, 5)]
    p = ea.plan(book)
    urg = [s["urgency"] for s in p["steps"]]
    assert urg == sorted(urg, reverse=True)   # steps ordered by descending urgency


# ── cumulative margin freed ───────────────────────────────────────────

def test_cumulative_margin_freed_increases_to_total():
    book = [_pos("A", "LONG", "BTC", leverage=10), _pos("B", "SHORT", "ETH", leverage=10)]
    p = ea.plan(book)
    freed = [s["margin_freed_cum_usd"] for s in p["steps"]]
    assert freed == sorted(freed)                      # monotonic non-decreasing
    assert abs(freed[-1] - p["total_margin_usd"]) < 0.01   # last step frees all margin


# ── urgency / risk rollup ─────────────────────────────────────────────

def test_near_liquidation_book_is_high_risk():
    # A 20x position liquidates on ~4.97% → below the 8% high threshold.
    p = ea.plan([_pos("X", "LONG", "BTC", leverage=20)])
    assert p["risk"] == "high"


def test_low_leverage_book_is_low_urgency():
    # 2x liquidates ~49.75% away → nothing urgent.
    p = ea.plan([_pos("X", "LONG", "BTC", leverage=2)])
    assert p["risk"] == "none"


# ── empty + fail-safe ─────────────────────────────────────────────────

def test_empty_book_has_nothing_to_unwind():
    p = ea.plan([])
    assert p["position_count"] == 0 and p["steps"] == []
    assert "nothing to unwind" in p["recommended"]


def test_plan_never_raises_on_garbage():
    for bad in (None, "x", [1, 2], [{"entry": "?", "qty": None}]):
        r = ea.plan(bad)
        assert isinstance(r, dict) and r["steps"] == []


def test_unknown_leverage_sorts_last_never_first():
    book = [_pos("KNOWN", "LONG", "BTC", qty=1, leverage=10)]
    book.append({"symbol": "NOLEV", "direction": "LONG", "group": "ETH",
                 "entry": 100.0, "qty": 1.0, "cost_usd": 100.0})  # no leverage
    p = ea.plan(book)
    assert p["steps"][0]["symbol"] == "KNOWN"   # estimable fragility ranks first


# ── record shape ──────────────────────────────────────────────────────

def test_escape_payload_is_compact_and_serialisable():
    book = [_pos("BTCUSDT", "LONG", "BTC", qty=3, leverage=15),
            _pos("ETHUSDT", "SHORT", "ETH", qty=1, leverage=4)]
    rec = ea.escape_payload(book)
    assert rec["position_count"] == 2
    assert rec["order"] and rec["order"][0]["order"] == 1
    assert rec["recommended"]
    json.dumps(rec)                              # rides the flight-record sync
    calm = ea.escape_payload([])
    assert calm["order"] == [] and calm["risk"] == "none"
