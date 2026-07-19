"""Idle-Asset Yield Optimizer — pre-registered predictions Y1–Y4.

Y1 best-rate wins (non-custodial preference honest), Y2 no fabricated yield,
Y3 filters + dust, Y4 accounting + determinism. Pure module.
"""
from bot.core import idle_yield as iy


def _holdings():
    return [
        {"asset": "ETH", "free_amount": 2.0, "usd_value": 6000, "location": "wallet:base"},
        {"asset": "USDT", "free_amount": 4000, "usd_value": 4000, "location": "bitget"},
        {"asset": "PEPE", "free_amount": 1e9, "usd_value": 50, "location": "wallet"},   # no option
        {"asset": "DUST", "free_amount": 1, "usd_value": 3, "location": "wallet"},       # below min
    ]


def _options():
    return [
        # ETH: a higher custodial rate and a slightly-lower non-custodial one
        {"asset": "ETH", "source": "Bitget Earn", "kind": "cex_earn", "apy": 3.5},
        {"asset": "ETH", "source": "Lido", "kind": "staking", "apy": 3.1, "lockup_days": 0},
        # USDT: two custodial options + a locked one
        {"asset": "USDT", "source": "Bitget Earn", "kind": "cex_earn", "apy": 5.0},
        {"asset": "USDT", "source": "Aave", "kind": "defi_lending", "apy": 4.2},
        {"asset": "USDT", "source": "Bitget Fixed", "kind": "cex_earn", "apy": 9.0, "lockup_days": 90},
    ]


# ── Y1 — best rate wins; non-custodial preference is honest ───────────

def test_y1_prefer_noncustodial_within_margin():
    r = iy.optimize(_holdings(), _options(), prefer_noncustodial=True,
                    noncustodial_margin=1.0)
    eth = next(x for x in r["recommendations"] if x["asset"] == "ETH")
    # Lido 3.1% non-custodial beats Bitget 3.5% custodial (within 1.0pt margin)
    assert eth["best"]["source"] == "Lido"
    assert eth["best"]["custodial"] is False
    assert "you keep custody" in eth["note"]


def test_y1_highest_rate_wins_without_preference():
    r = iy.optimize(_holdings(), _options(), prefer_noncustodial=False)
    eth = next(x for x in r["recommendations"] if x["asset"] == "ETH")
    assert eth["best"]["source"] == "Bitget Earn"   # 3.5% > 3.1%
    assert eth["best"]["custodial"] is True


def test_y1_custodial_flag_always_present():
    r = iy.optimize(_holdings(), _options())
    for rec in r["recommendations"]:
        if rec["best"]:
            assert "custodial" in rec["best"]


# ── Y2 — no fabricated yield ──────────────────────────────────────────

def test_y2_no_option_is_honest():
    r = iy.optimize(_holdings(), _options())
    pepe = next(x for x in r["recommendations"] if x["asset"] == "PEPE")
    assert pepe["status"] == "no_option"
    assert pepe["best"] is None and pepe["est_year_usd"] == 0.0
    assert "PEPE" in r["unmatched"]


# ── Y3 — filters + dust ───────────────────────────────────────────────

def test_y3_below_min_not_deployed():
    r = iy.optimize(_holdings(), _options(), min_usd=10.0)
    dust = next(x for x in r["recommendations"] if x["asset"] == "DUST")
    assert dust["status"] == "below_min"
    assert dust["asset"] not in [o for o in r["unmatched"]]   # not "no option", just too small


def test_y3_max_lockup_excludes_locked_option():
    # With max_lockup_days=0, USDT's best drops from the 9% 90-day-locked to a
    # flexible option.
    r = iy.optimize(_holdings(), _options(), max_lockup_days=0, prefer_noncustodial=False)
    usdt = next(x for x in r["recommendations"] if x["asset"] == "USDT")
    assert usdt["best"]["lockup_days"] == 0
    assert usdt["best"]["apy"] == 5.0            # the 9% locked one is filtered out


def test_y3_locked_option_wins_when_allowed():
    r = iy.optimize(_holdings(), _options(), max_lockup_days=365, prefer_noncustodial=False)
    usdt = next(x for x in r["recommendations"] if x["asset"] == "USDT")
    assert usdt["best"]["apy"] == 9.0 and usdt["best"]["lockup_days"] == 90


# ── Y4 — accounting + determinism ─────────────────────────────────────

def test_y4_total_matches_sum_of_recommended():
    r = iy.optimize(_holdings(), _options())
    s = sum(x["est_year_usd"] for x in r["recommendations"] if x["status"] == "recommended")
    assert abs(r["total_est_year_usd"] - round(s, 2)) < 0.011
    # deployable excludes the dust + no-option assets
    assert r["total_deployable_usd"] == 6000 + 4000


def test_y4_determinism_and_ranking():
    a = iy.optimize(_holdings(), _options())
    b = iy.optimize(_holdings(), _options())
    assert a == b
    # recommendations ranked by incremental $/yr, biggest first
    recd = [x["est_year_usd"] for x in a["recommendations"] if x["status"] == "recommended"]
    assert recd == sorted(recd, reverse=True)


# ── adapter: composes with the existing Yield Radar catalog ───────────

def test_options_from_savings_catalog():
    # shape from bot.core.yield_radar.fetch_savings_catalog
    catalog = {"ETH": {"flexible": 3.5, "fixed": 6.0, "flexible_id": "p1"},
               "USDT": {"flexible": 5.0}}
    opts = iy.options_from_savings_catalog(catalog)
    assert all(o["custodial"] is True and o["kind"] == iy.CEX_EARN for o in opts)
    # flexible tier is lockup-free; fixed carries a nominal lockup
    eth_flex = next(o for o in opts if o["asset"] == "ETH" and o["lockup_days"] == 0)
    eth_fixed = next(o for o in opts if o["asset"] == "ETH" and o["lockup_days"] > 0)
    assert eth_flex["apy"] == 3.5 and eth_fixed["apy"] == 6.0
    # feed straight into the optimizer with a non-custodial Lido option → Lido wins
    holdings = [{"asset": "ETH", "usd_value": 3000}]
    opts += [{"asset": "ETH", "source": "Lido", "kind": "staking", "apy": 3.1}]
    rec = iy.optimize(holdings, opts, max_lockup_days=0)["recommendations"][0]
    assert rec["best"]["source"] == "Lido"   # non-custodial, within margin, flexible
