"""Meme-buy safety gate — pure, fail-closed precondition. Predictions M1-M8.

M1 all-good → allowed; M2 danger verdict blocks; M3 caution blocks (safe-only);
M4 thin liquidity blocks; M5 young pool blocks; M6 no prior sells blocks;
M7 oversized position blocks; M8 missing data fails closed; extreme tier blocks.
"""
from bot.core import meme_gate as mg


def _good(**over):
    kw = dict(
        safety_report={"verdict": "safe"},
        radar_risk={"tier": "high"},
        liquidity_usd=120_000, age_hours=240,
        sells_24h=210, buys_24h=300, size_usd=100,
    )
    kw.update(over)
    return mg.evaluate_meme_buy(**kw)


def test_m1_all_good_allows():
    d = _good()
    assert d["allowed"] is True, d["blocking"]
    assert d["blocking"] == []


def test_m2_danger_verdict_blocks():
    d = _good(safety_report={"verdict": "danger"})
    assert d["allowed"] is False
    assert "token_safety" in d["blocking"]


def test_m3_caution_blocks_when_safe_required():
    d = _good(safety_report={"verdict": "caution"})
    assert d["allowed"] is False and "token_safety" in d["blocking"]
    # ...but is allowed when the operator relaxes to caution-ok.
    d2 = _good(safety_report={"verdict": "caution"},
               params=mg.default_params(require_safe_verdict=False))
    assert "token_safety" not in d2["blocking"]


def test_m4_thin_liquidity_blocks():
    d = _good(liquidity_usd=5_000)
    assert d["allowed"] is False and "liquidity_floor" in d["blocking"]


def test_m5_young_pool_blocks():
    d = _good(age_hours=3)
    assert d["allowed"] is False and "age_floor" in d["blocking"]


def test_m6_no_prior_sells_blocks():
    d = _good(sells_24h=0)
    assert d["allowed"] is False and "can_exit" in d["blocking"]


def test_m7_oversized_position_blocks():
    d = _good(size_usd=10_000)          # > max_position_usd and > 1% of pool
    assert d["allowed"] is False
    assert "size_cap" in d["blocking"] and "size_vs_liquidity" in d["blocking"]


def test_m8_missing_data_fails_closed():
    d = mg.evaluate_meme_buy()          # nothing supplied at all
    assert d["allowed"] is False
    for name in ("token_safety", "liquidity_floor", "age_floor", "can_exit", "risk_tier"):
        assert name in d["blocking"], name


def test_extreme_tier_blocks():
    d = _good(radar_risk={"tier": "extreme"})
    assert d["allowed"] is False and "risk_tier" in d["blocking"]


def test_human_readable_marks_pass_and_fail():
    txt = mg.human_readable(_good())
    assert "BUY permitted" in txt
    txt2 = mg.human_readable(_good(age_hours=1))
    assert "BUY blocked" in txt2 and "age_floor" in txt2
