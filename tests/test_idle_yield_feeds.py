"""Non-custodial yield feeders — pre-registered predictions F1–F5.

F1 curated allowlist filters the firehose; F2 base-APY + TVL discipline;
F3 no fabrication on failure; F4 assembler composes custodial + non-custodial;
F5 feeds straight into the optimizer with the non-custodial preference honoured.
Fully offline — the network call is an injected fake.
"""
from bot.core import idle_yield_feeds as f
from bot.core import idle_yield as iy


def _llama_payload():
    return {"status": "success", "data": [
        # Curated hits.
        {"project": "lido", "symbol": "STETH", "chain": "Ethereum",
         "apyBase": 3.1, "apy": 3.1, "tvlUsd": 30_000_000_000},
        {"project": "aave-v3", "symbol": "USDC", "chain": "Ethereum",
         "apyBase": 4.2, "apy": 4.8, "tvlUsd": 1_500_000_000},   # base<apy: take base
        {"project": "aave-v3", "symbol": "USDC", "chain": "Arbitrum",
         "apyBase": 3.9, "apy": 3.9, "tvlUsd": 800_000_000},     # lower → dropped for USDC
        {"project": "rocket-pool", "symbol": "RETH", "chain": "Ethereum",
         "apyBase": 2.9, "apy": 2.9, "tvlUsd": 2_000_000_000},
        # Below the TVL floor → dropped.
        {"project": "aave-v3", "symbol": "DAI", "chain": "Ethereum",
         "apyBase": 9.9, "apy": 9.9, "tvlUsd": 1_000},
        # Not in the allowlist → ignored (this is how memecoin farms stay out).
        {"project": "some-degen-farm", "symbol": "PEPE", "chain": "Ethereum",
         "apyBase": 900.0, "apy": 4000.0, "tvlUsd": 50_000_000},
        # Junk rows must not crash the parse.
        {"project": "lido"}, "not-a-dict", {"symbol": None, "apyBase": "x"},
    ]}


# ── F1/F2 — allowlist + base-APY + TVL discipline ─────────────────────

def test_f1_only_curated_pools_survive():
    opts = f.noncustodial_options_from_llama(_llama_payload())
    sources = {o["source"] for o in opts}
    assert sources == {"Lido", "Aave v3", "Rocket Pool"}
    # the 4000% degen farm never appears
    assert all(o["apy"] < 100 for o in opts)
    # every option is non-custodial, flexible, tagged with tvl + chain
    for o in opts:
        assert o["custodial"] is False and o["lockup_days"] == 0
        assert o["tvl_usd"] >= f.MIN_TVL_USD and o["chain"]


def test_f2_base_apy_and_best_per_asset_source():
    opts = f.noncustodial_options_from_llama(_llama_payload())
    usdc = next(o for o in opts if o["source"] == "Aave v3" and o["asset"] == "USDC")
    assert usdc["apy"] == 4.2               # base, not the 4.8 headline
    # the lower Arbitrum USDC pool lost to the Ethereum one (best-per-source)
    assert sum(1 for o in opts if o["source"] == "Aave v3" and o["asset"] == "USDC") == 1


def test_f2_below_tvl_floor_excluded():
    opts = f.noncustodial_options_from_llama(_llama_payload())
    # the 9.9% DAI pool was $1k TVL → not present, no fabricated DAI rate
    assert not any(o["asset"] == "DAI" for o in opts)


# ── F3 — no fabrication on failure ────────────────────────────────────

def test_f3_malformed_payloads_yield_nothing():
    for bad in (None, {}, {"data": None}, {"data": "nope"}, "string", 42):
        assert f.noncustodial_options_from_llama(bad) == []


def test_f3_fetch_failure_is_empty_not_invented():
    # injected fetch returns None (network down) → zero options, never a guess
    assert f.fetch_noncustodial_options(fetch=lambda url: None) == []
    opts = f.fetch_noncustodial_options(fetch=lambda url: _llama_payload())
    assert opts and all(o["custodial"] is False for o in opts)


# ── F4 — assembler composes custodial + non-custodial ─────────────────

def test_f4_build_idle_options_merges_sources():
    catalog = {"ETH": {"flexible": 3.5}, "USDC": {"flexible": 5.0}}
    extra = {"Bybit": {"ETH": {"flexible": 3.8}}}
    opts = f.build_idle_options(
        catalog, extra_catalogs=extra,
        fetch=lambda url: _llama_payload())
    # custodial CEX rows from both catalogs …
    assert any(o["source"] == "Bitget Earn" and o["custodial"] for o in opts)
    assert any(o["source"] == "Bybit" and o["custodial"] for o in opts)
    # … plus the non-custodial Lido/Aave rows.
    assert any(o["source"] == "Lido" and not o["custodial"] for o in opts)


def test_f4_include_noncustodial_false_skips_fetch():
    called = {"n": 0}
    def _fetch(url):
        called["n"] += 1
        return _llama_payload()
    opts = f.build_idle_options({"USDC": {"flexible": 5.0}},
                                include_noncustodial=False, fetch=_fetch)
    assert called["n"] == 0
    assert all(o["custodial"] for o in opts)   # only the CEX option


# ── F5 — end-to-end into the optimizer, non-custodial preference honest ─

def test_f5_optimizer_prefers_noncustodial_within_margin():
    catalog = {"ETH": {"flexible": 3.5}}          # Bitget custodial 3.5%
    opts = f.build_idle_options(catalog, fetch=lambda url: _llama_payload())
    holdings = [{"asset": "ETH", "usd_value": 6000}]
    rec = iy.optimize(holdings, opts, prefer_noncustodial=True,
                      noncustodial_margin=1.0)["recommendations"][0]
    # Lido 3.1% non-custodial beats Bitget 3.5% custodial within the margin.
    assert rec["best"]["source"] == "Lido"
    assert rec["best"]["custodial"] is False
    assert "you keep custody" in rec["note"]
