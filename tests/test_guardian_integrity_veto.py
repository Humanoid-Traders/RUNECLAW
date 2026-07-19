"""Guardian Market-Integrity Veto — pre-registered predictions V1–V4.

V1 veto-only (never a positive verdict), V2 hard flag forces veto, V3 clean
features clear, V4 fail-open per feature + determinism. Pure module.
"""
from bot.guardian import integrity_veto as iv


def _clean():
    """Every reading in the benign range."""
    return {
        "social_spike_ratio": 1.2,
        "new_account_ratio": 0.05,
        "sentiment_uniformity": 0.3,
        "price_liquidity_divergence": 0.5,
        "wash_volume_ratio": 0.05,
        "holder_concentration": 0.2,
        "listing_age_hours": 720,
    }


# ── V1 — veto-only, never a positive verdict ──────────────────────────

def test_v1_only_ever_clear_caution_or_veto():
    for feats in ({}, _clean(), {"holder_concentration": 0.9},
                  {"social_spike_ratio": 6}, {"wash_volume_ratio": 0.9},
                  {"new_account_ratio": 0.5, "sentiment_uniformity": 0.9}):
        r = iv.assess(feats)
        assert r["verdict"] in (iv.CLEAR, iv.CAUTION, iv.VETO)
    # there is no positive/approve verdict constant at all
    assert not hasattr(iv, "APPROVE")
    assert set([iv.CLEAR, iv.CAUTION, iv.VETO]) == {"clear", "caution", "veto"}


# ── V2 — a single hard flag forces veto ───────────────────────────────

def test_v2_hard_flag_forces_veto():
    # holder concentration 0.9 (> 0.7 hard) while everything else is clean
    feats = _clean()
    feats["holder_concentration"] = 0.9
    r = iv.assess(feats)
    assert r["verdict"] == iv.VETO
    assert any(f["severity"] == "hard" and f["feature"] == "holder_concentration"
               for f in r["flags"])


def test_v2_brand_new_listing_is_hard_veto():
    # listing_age_hours is a "low = risk" feature; 1h (<= 2h hard) → veto
    r = iv.assess({"listing_age_hours": 1})
    assert r["verdict"] == iv.VETO


# ── V3 — clean features clear ─────────────────────────────────────────

def test_v3_clean_bundle_clears():
    r = iv.assess(_clean())
    assert r["verdict"] == iv.CLEAR
    assert r["flags"] == []
    assert r["score"] == 0.0


def test_v3_soft_flags_accumulate_to_caution_then_veto():
    # one soft flag → caution band or below
    one = iv.assess({"social_spike_ratio": 6})       # soft (>=5), weight 1.0
    assert one["verdict"] in (iv.CLEAR, iv.CAUTION)
    # several soft flags stack past the veto score
    many = iv.assess({
        "new_account_ratio": 0.5,           # soft, w1.5
        "price_liquidity_divergence": 4.0,  # soft, w1.5
        "holder_concentration": 0.55,       # soft, w2.0  → 5.0 total >= 3.0
    })
    assert many["verdict"] == iv.VETO


# ── V4 — fail-open per feature + determinism ──────────────────────────

def test_v4_missing_features_skipped_not_fabricated():
    r = iv.assess({"social_spike_ratio": 1.0})   # only one feature present
    assert r["checked"] == 1
    assert r["skipped"] == len(iv._FEATURES) - 1
    assert r["verdict"] == iv.CLEAR

    # a non-numeric / None reading is skipped, never counted or raised
    r2 = iv.assess({"holder_concentration": None, "wash_volume_ratio": "oops"})
    assert r2["checked"] == 0 and r2["verdict"] == iv.CLEAR


def test_v4_determinism():
    feats = _clean()
    feats["wash_volume_ratio"] = 0.9   # hard
    a = iv.assess(feats)
    b = iv.assess(dict(feats))
    assert a["verdict"] == b["verdict"] == iv.VETO
    assert a["score"] == b["score"]


def test_v4_empty_input_is_clear():
    assert iv.assess(None)["verdict"] == iv.CLEAR
    assert iv.assess({})["verdict"] == iv.CLEAR
