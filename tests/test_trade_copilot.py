"""Trade co-pilot — pre-registered predictions C1–C6.

C1 geometry validation; C2 reward:risk flag; C3 stop-distance flags; C4 size vs
equity; C5 engine-bias alignment + exposure notes; C6 determinism + advice-only
(no side effects). Pure module.
"""
from bot.core import trade_copilot as cp


def _good_long():
    return {"direction": "LONG", "symbol": "SOL", "entry": 100.0, "sl": 97.0, "tp": 109.0}


# ── C1 — geometry ─────────────────────────────────────────────────────

def test_c1_wrong_side_stop_is_invalid():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 101, "tp": 110})
    assert r["verdict"] == "invalid"
    assert r["flags"][0]["level"] == "block"


def test_c1_valid_long_geometry_ok():
    r = cp.review(_good_long())
    assert r["verdict"] in ("clear", "caution")
    assert r["rr"] == 3.0                       # reward 9 / risk 3
    assert r["stop_pct"] == 3.0


def test_c1_short_geometry():
    r = cp.review({"direction": "SHORT", "symbol": "ETH", "entry": 100, "sl": 103, "tp": 91})
    assert r["verdict"] != "invalid"
    assert r["rr"] == 3.0


# ── C2 — reward:risk ──────────────────────────────────────────────────

def test_c2_low_rr_flags_and_lowers_score():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 97, "tp": 102})
    assert r["rr"] == 0.67
    assert any("Reward:risk" in f["msg"] for f in r["flags"])
    assert r["verdict"] == "caution"
    assert r["score"] < 100


# ── C3 — stop distance ────────────────────────────────────────────────

def test_c3_tight_stop_flagged():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 99.8, "tp": 101})
    assert any("wicked out" in f["msg"] for f in r["flags"])


def test_c3_wide_stop_flagged():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 80, "tp": 160})
    assert any("wide stop" in f["msg"] for f in r["flags"])


# ── C4 — size vs equity ───────────────────────────────────────────────

def test_c4_heavy_concentration_flagged():
    r = cp.review({**_good_long(), "margin": 500}, equity_usd=1000)
    assert any("concentration" in f["msg"] for f in r["flags"])


def test_c4_reasonable_size_noted_not_flagged():
    r = cp.review({**_good_long(), "margin": 50}, equity_usd=1000)
    assert not any("concentration" in f["msg"] for f in r["flags"])
    assert any("% of equity" in n for n in r["notes"])


# ── C5 — bias alignment + exposure ────────────────────────────────────

def test_c5_counter_bias_is_caution():
    r = cp.review(_good_long(), engine_bias="short")
    assert any("counter to the engine" in f["msg"] for f in r["flags"])
    assert r["verdict"] == "caution"


def test_c5_aligned_bias_noted():
    r = cp.review(_good_long(), engine_bias="long")
    assert any("Aligned" in n for n in r["notes"])


def test_c5_stacking_exposure_note():
    r = cp.review(_good_long(), existing_exposure="long")
    assert any("stacks the position" in n for n in r["notes"])


def test_c5_hedge_exposure_note():
    r = cp.review(_good_long(), existing_exposure="short")
    assert any("hedge/reduce" in n for n in r["notes"])


# ── C6 — determinism ──────────────────────────────────────────────────

def test_c6_deterministic():
    a = cp.review(_good_long(), equity_usd=1000, engine_bias="short", existing_exposure="long")
    b = cp.review(_good_long(), equity_usd=1000, engine_bias="short", existing_exposure="long")
    assert a == b


def test_c6_human_readable_renders():
    txt = cp.human_readable(cp.review(_good_long(), engine_bias="short"))
    assert "R:R" in txt and "score" in txt
