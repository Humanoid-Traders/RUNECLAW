"""Portfolio Digital Twin — deterministic stress simulator.

The twin shocks the open book against parametric price scenarios and reports the
projected P&L, drawdown, and which positions would be liquidated. These tests pin
the isolated-margin liquidation math, the P&L sign for longs vs shorts, the
scenario/drawdown rollup, per-position fragility ordering, the fail-safe (garbage
never raises), and the compact record shape.
"""
import json

from bot.guardian import digital_twin as dt


# ── liquidation math ──────────────────────────────────────────────────

def test_liquidation_move_scales_inversely_with_leverage():
    # 10x ≈ 9.95% adverse move (with 0.5% maintenance); 2x ≈ 49.75%.
    assert abs(dt.liquidation_move_frac(10) - (1 - 0.005) / 10) < 1e-9
    assert dt.liquidation_move_frac(2) > dt.liquidation_move_frac(20)
    # unusable leverage -> None (can't estimate, never "safe")
    assert dt.liquidation_move_frac(0) is None
    assert dt.liquidation_move_frac(-3) is None
    assert dt.liquidation_move_frac("x") is None


def test_liquidation_price_sides():
    # LONG liquidates below entry, SHORT above.
    long_liq = dt.liquidation_price(100, 10, "LONG")
    short_liq = dt.liquidation_price(100, 10, "SHORT")
    assert long_liq < 100 < short_liq
    assert abs(long_liq - 100 * (1 - (1 - 0.005) / 10)) < 1e-6


# ── P&L sign ──────────────────────────────────────────────────────────

def test_position_pnl_sign():
    # LONG loses when price falls; SHORT gains.
    assert dt.position_pnl(100, 2, "LONG", 90) == -20
    assert dt.position_pnl(100, 2, "SHORT", 90) == 20
    assert dt.position_pnl(100, 2, "LONG", 110) == 20
    assert dt.position_pnl(None, 2, "LONG", 90) is None


# ── scenario simulation ───────────────────────────────────────────────

def _book():
    # A 10x long BTC and a 5x long ETH — both fragile to a crash.
    return [
        {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
         "leverage": 10, "group": "BTC"},
        {"symbol": "ETHUSDT", "direction": "LONG", "entry": 50.0, "qty": 2.0,
         "leverage": 5, "group": "ETH"},
    ]


def test_flash_crash_liquidates_the_10x_long():
    # −20% crash: the 10x long (liquidates ~−10%) is wiped; the 5x long
    # (liquidates ~−20%) is right at the edge.
    res = dt.simulate_scenario(_book(), equity=30.0,
                               scenario={"name": "c", "shocks": {"*": -0.20}})
    assert "BTCUSDT" in res["liquidations"]
    assert res["projected_pnl_usd"] < 0
    assert res["risk"] == "high"           # a liquidation forces high


def test_short_squeeze_hurts_shorts_not_longs():
    book = [{"symbol": "X", "direction": "SHORT", "entry": 100.0, "qty": 1.0,
             "leverage": 10, "group": "*"}]
    res = dt.simulate_scenario(book, equity=10.0,
                               scenario={"name": "sq", "shocks": {"*": 0.20}})
    assert "X" in res["liquidations"]      # +20% wipes a 10x short
    assert res["projected_pnl_usd"] < 0


def test_alt_capitulation_spares_majors_hits_alts():
    book = [
        {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
         "leverage": 3, "group": "BTC"},
        {"symbol": "PEPEUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
         "leverage": 3, "group": "MEME"},
    ]
    # BTC −10%, everything-else −35%. 3x liquidates ~−33%, so only the alt goes.
    scen = next(s for s in dt.scenarios() if s["name"] == "alt_capitulation")
    res = dt.simulate_scenario(book, equity=200.0, scenario=scen)
    assert "PEPEUSDT" in res["liquidations"]
    assert "BTCUSDT" not in res["liquidations"]


# ── full run + rollup ─────────────────────────────────────────────────

def test_run_reports_worst_and_fragility_order():
    report = dt.run(_book(), equity=30.0)
    assert report["position_count"] == 2
    assert report["risk"] == "high"
    assert report["worst"] is not None
    # fragility: the 10x (≈9.95%) is more fragile than the 5x (≈19.9%)
    assert [f["symbol"] for f in report["fragile"]] == ["BTCUSDT", "ETHUSDT"]
    assert report["fragile"][0]["liq_move_pct"] < report["fragile"][1]["liq_move_pct"]


def test_empty_book_is_calm():
    report = dt.run([], equity=1000.0)
    assert report["position_count"] == 0
    assert report["risk"] == "none"
    assert report["scenarios"] and all(s["risk"] == "none" for s in report["scenarios"])


def test_run_never_raises_on_garbage():
    for bad in (None, "x", [1, 2, 3], [{"entry": "?", "qty": None}]):
        r = dt.run(bad, equity="?")
        assert isinstance(r, dict) and "risk" in r


# ── record shape ──────────────────────────────────────────────────────

def test_twin_payload_is_compact_and_serialisable():
    p = dt.twin_payload(_book(), equity=30.0)
    assert p["risk"] == "high"
    assert p["worst_scenario"]
    assert p["position_count"] == 2
    assert len(p["scenarios"]) == len(dt.scenarios())
    json.dumps(p)                          # rides the flight-record sync
    calm = dt.twin_payload([], equity=1000.0)
    assert calm["risk"] == "none" and calm["worst_liquidations"] == []
