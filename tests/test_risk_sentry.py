"""Risk sentry — pre-registered predictions S1–S7.

S1 clean book is clear; S2 envelope drift (symbol no longer allowed); S3 over
per-trade cap; S4 daily-spend near cap; S5 concentration; S6 stacked correlated;
S7 book leverage + determinism + detection-only (no mutation). Pure module.
"""
from bot.guardian import risk_sentry as rs


def _pos(sym, side, usd):
    return {"symbol": sym, "side": side, "notional_usd": usd}


# ── S1 — clean book ───────────────────────────────────────────────────

def test_s1_clean_book_is_clear():
    r = rs.assess([_pos("BTC/USDT", "long", 100)],
                  envelope={"symbol_allowlist": ["BTC"], "max_notional_per_trade_usd": 500})
    assert r["worst_level"] == "clear"
    assert r["alerts"] == []
    assert r["gross_usd"] == 100


# ── S2 — envelope drift ───────────────────────────────────────────────

def test_s2_symbol_no_longer_allowed():
    r = rs.assess([_pos("SOL/USDT", "long", 100)],
                  envelope={"symbol_allowlist": ["BTC", "ETH"]})
    cats = [a["category"] for a in r["alerts"]]
    assert "outside_authority" in cats
    assert r["worst_level"] == "warn"


def test_s2_blocklisted_held():
    r = rs.assess([_pos("DOGE/USDT", "long", 50)],
                  envelope={"symbol_blocklist": ["DOGE"]})
    assert any(a["category"] == "blocklisted_held" for a in r["alerts"])


# ── S3 — over per-trade cap ───────────────────────────────────────────

def test_s3_over_per_trade_cap():
    r = rs.assess([_pos("BTC/USDT", "long", 900)],
                  envelope={"symbol_allowlist": ["BTC"], "max_notional_per_trade_usd": 500})
    over = [a for a in r["alerts"] if a["category"] == "over_cap"]
    assert over and "exceeds" in over[0]["msg"]


# ── S4 — daily spend near cap ─────────────────────────────────────────

def test_s4_daily_spend_warns_near_and_over():
    r = rs.assess([], spent_today_usd=850, daily_cap=1000)
    ds = [a for a in r["alerts"] if a["category"] == "daily_spend"]
    assert ds and ds[0]["level"] == "caution"          # 85% → caution
    r2 = rs.assess([], spent_today_usd=1100, daily_cap=1000)
    ds2 = [a for a in r2["alerts"] if a["category"] == "daily_spend"]
    assert ds2 and ds2[0]["level"] == "warn"           # over → warn


def test_s4_below_threshold_silent():
    r = rs.assess([], spent_today_usd=500, daily_cap=1000)
    assert not any(a["category"] == "daily_spend" for a in r["alerts"])


# ── S5 — concentration ────────────────────────────────────────────────

def test_s5_concentration_flagged():
    r = rs.assess([_pos("BTC/USDT", "long", 800), _pos("ETH/USDT", "long", 100)],
                  concentration_pct=40)
    conc = [a for a in r["alerts"] if a["category"] == "concentration"]
    assert conc and conc[0]["symbol"] == "BTC"


def test_s5_single_position_not_concentration():
    # one position is trivially 100% — not a "concentration" flag (needs >1 sym)
    r = rs.assess([_pos("BTC/USDT", "long", 800)])
    assert not any(a["category"] == "concentration" for a in r["alerts"])


# ── S6 — stacked correlated ───────────────────────────────────────────

def test_s6_stacked_correlated_longs():
    r = rs.assess([_pos("BTC/USDT", "long", 100), _pos("ETH/USDT", "long", 100),
                   _pos("SOL/USDT", "long", 100)])
    st = [a for a in r["alerts"] if a["category"] == "stacked_correlated"]
    assert st and "long" in st[0]["msg"]


def test_s6_opposite_sides_not_stacked():
    r = rs.assess([_pos("BTC/USDT", "long", 100), _pos("ETH/USDT", "short", 100)])
    assert not any(a["category"] == "stacked_correlated" for a in r["alerts"])


# ── S7 — book leverage + determinism ──────────────────────────────────

def test_s7_book_leverage():
    r = rs.assess([_pos("BTC/USDT", "long", 5000)], equity_usd=1000)
    assert any(a["category"] == "book_leverage" for a in r["alerts"])


def test_s7_deterministic_and_ranked():
    book = [_pos("SOL/USDT", "long", 900), _pos("BTC/USDT", "long", 100)]
    env = {"symbol_allowlist": ["BTC"], "max_notional_per_trade_usd": 500}
    a = rs.assess(book, envelope=env)
    b = rs.assess(book, envelope=env)
    assert a == b                                       # deterministic
    # ranked worst-first
    levels = [rs._ORDER[x["level"]] for x in a["alerts"]]
    assert levels == sorted(levels, reverse=True)
    assert book[0]["notional_usd"] == 900               # detection-only: input untouched
