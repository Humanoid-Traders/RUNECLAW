"""Token Safety Scanner — pre-registered predictions T1–T4.

T1 no-data-is-not-safe, T2 hard flag forces danger, T3 clean token clears,
T4 veto-feature mapping + determinism (+ detection-never-generation). Pure module.
"""
from bot.core import token_safety as ts
from bot.guardian import integrity_veto as iv


def _clean():
    """A well-evidenced, benign token."""
    return {
        "honeypot_cannot_sell": False,
        "mint_authority_active": False,
        "freeze_authority_active": False,
        "sell_tax_pct": 0.0,
        "buy_tax_pct": 0.0,
        "top_holder_pct": 0.08,
        "ownership_renounced": True,
        "lp_locked": True,
        "liquidity_usd": 500_000,
        "holder_count": 5000,
        "listing_age_hours": 4000,
    }


# ── T1 — no data is not safe ──────────────────────────────────────────

def test_t1_empty_bundle_is_caution_not_safe():
    r = ts.assess_token({})
    assert r["verdict"] == ts.CAUTION
    assert r["verdict"] != ts.SAFE
    assert r["unknowns"] == len(r["checks"])


def test_t1_mostly_unknown_is_caution():
    # a couple of benign readings, but most checks have no data → cannot certify
    r = ts.assess_token({"buy_tax_pct": 0.0, "listing_age_hours": 9999})
    assert r["verdict"] == ts.CAUTION


# ── T2 — a hard flag forces danger ────────────────────────────────────

def test_t2_honeypot_is_danger():
    feats = _clean()
    feats["honeypot_cannot_sell"] = True
    r = ts.assess_token(feats)
    assert r["verdict"] == ts.DANGER
    assert any(c["status"] == ts.HARD and c["name"] == "honeypot_cannot_sell"
               for c in r["checks"])


def test_t2_live_mint_authority_is_danger():
    feats = _clean()
    feats["mint_authority_active"] = True
    assert ts.assess_token(feats)["verdict"] == ts.DANGER


def test_t2_majority_holder_is_danger():
    feats = _clean()
    feats["top_holder_pct"] = 0.62
    assert ts.assess_token(feats)["verdict"] == ts.DANGER


def test_t2_exit_trap_sell_tax_is_danger():
    feats = _clean()
    feats["sell_tax_pct"] = 40.0
    assert ts.assess_token(feats)["verdict"] == ts.DANGER


# ── T3 — clean, well-evidenced token clears ───────────────────────────

def test_t3_clean_token_is_safe():
    r = ts.assess_token(_clean())
    assert r["verdict"] == ts.SAFE
    assert r["flags"] == []
    assert r["score"] == 0.0


def test_t3_soft_flags_accumulate_to_caution_then_danger():
    # a few soft risks but no hard trigger
    feats = _clean()
    feats["ownership_renounced"] = False   # +1.0
    feats["lp_locked"] = False             # +1.5  → 2.5 == caution band
    mid = ts.assess_token(feats)
    assert mid["verdict"] == ts.CAUTION
    feats["holder_count"] = 10             # +1.0  → 3.5 >= danger
    assert ts.assess_token(feats)["verdict"] == ts.DANGER


# ── T4 — veto-feature mapping + determinism + no positive output ──────

def test_t4_feeds_the_integrity_veto():
    # a dangerous token's mapped features should make the veto veto too
    feats = _clean()
    feats["top_holder_pct"] = 0.9         # rug-level concentration
    r = ts.assess_token(feats)
    vf = r["veto_features"]
    assert vf["holder_concentration"] == 0.9
    veto = iv.assess(vf)
    assert veto["verdict"] == iv.VETO


def test_t4_determinism_and_no_positive_verdict():
    feats = _clean()
    a = ts.assess_token(feats)
    b = ts.assess_token(dict(feats))
    assert a["verdict"] == b["verdict"]
    assert a["score"] == b["score"]
    # detection-never-generation: the only verdicts are stand-down/neutral; there
    # is no buy/approve output anywhere.
    for feats2 in ({}, _clean(), {"honeypot_cannot_sell": True}):
        assert ts.assess_token(feats2)["verdict"] in (ts.SAFE, ts.CAUTION, ts.DANGER)
    assert not hasattr(ts, "BUY")
