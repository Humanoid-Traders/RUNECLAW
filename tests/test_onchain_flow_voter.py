"""On-chain flow voter (PR JJ) — keyless DEX taker-flow mode.

The dormant BYOK on-chain scaffold (bot/core/onchain.py) gains a keyless
source: the web app's DEX taker-flow radar over the bot-secret sync channel.
Everything stays gated default-OFF and fail-open: with neither ONCHAIN flag
set the provider is inert and the analyzer's confluence is byte-identical to
before this change.
"""
from __future__ import annotations

import asyncio

import pytest

import bot.core.onchain as oc


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ONCHAIN_ENABLED", "ONCHAIN_API_KEY", "ONCHAIN_BASE_URL",
              "ONCHAIN_FLOW_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    oc.reset_flow_radar_cache()
    yield
    oc.reset_flow_radar_cache()


def test_default_off_provider_is_inert():
    assert not oc.onchain_enabled()
    assert not oc.onchain_flow_enabled()
    snap = asyncio.run(oc.OnChainProvider().fetch("BTC"))
    assert snap is None


def test_flow_flag_alone_enables_the_keyless_mode(monkeypatch):
    monkeypatch.setenv("ONCHAIN_FLOW_ENABLED", "1")
    assert oc.onchain_flow_enabled()
    assert not oc.onchain_enabled()          # BYOK path still needs a key


def test_dex_taker_flow_metric_feeds_bias_with_capped_confidence():
    snap = oc.compute_bias({"symbol": "BTC", "dex_taker_flow": 0.4})
    assert snap.dex_taker_flow == 0.4
    assert snap.bias == pytest.approx(0.4)
    assert snap.confidence == pytest.approx(1 / 3)
    votes = snap.to_confluence_votes()
    assert len(votes) == 1
    name, vote, weight = votes[0]
    assert name == "onchain_flow"
    assert vote == pytest.approx(0.4)
    # Solo keyless metric votes at a THIRD of the full-stack weight.
    assert weight == pytest.approx(0.7 * (1 / 3))


def test_full_metric_stack_confidence_never_exceeds_one():
    snap = oc.compute_bias({
        "exchange_netflow": -0.5, "whale_net": 0.5,
        "stablecoin_supply_change": 0.2, "dex_taker_flow": 0.3,
    })
    assert snap.confidence == 1.0
    assert len(snap.components_ok) == 4


def test_flow_metrics_from_web_maps_base_and_respects_sample(monkeypatch):
    radar = {"bases": [
        {"base": "BTC", "flow_bias": 0.25, "sample": "ok"},
        {"base": "ETH", "flow_bias": -0.9, "sample": "junk"},
    ]}
    import bot.utils.web_data_pull as pull
    monkeypatch.setattr(pull, "fetch_onchain_flow", lambda: radar)
    assert oc._flow_metrics_from_web("BTC/USDT:USDT") == {"dex_taker_flow": 0.25}
    oc.reset_flow_radar_cache()
    assert oc._flow_metrics_from_web("ETH") is None       # bad sample marker
    assert oc._flow_metrics_from_web("SOL") is None       # not covered


def test_flow_fetch_end_to_end_and_radar_cache(monkeypatch):
    monkeypatch.setenv("ONCHAIN_FLOW_ENABLED", "1")
    calls = {"n": 0}

    def fake_pull():
        calls["n"] += 1
        return {"bases": [{"base": "SOL", "flow_bias": -0.15, "sample": "thin"}]}

    import bot.utils.web_data_pull as pull
    monkeypatch.setattr(pull, "fetch_onchain_flow", fake_pull)

    provider = oc.OnChainProvider()
    snap = asyncio.run(provider.fetch("SOL"))
    assert snap is not None and snap.dex_taker_flow == pytest.approx(-0.15)
    assert snap.bias == pytest.approx(-0.15)
    # Second symbol reuses the radar cache — one web pull covers all bases.
    asyncio.run(provider.fetch("BTC"))
    assert calls["n"] == 1


def test_web_pull_failure_is_silent(monkeypatch):
    monkeypatch.setenv("ONCHAIN_FLOW_ENABLED", "1")
    import bot.utils.web_data_pull as pull
    monkeypatch.setattr(pull, "fetch_onchain_flow", lambda: None)
    snap = asyncio.run(oc.OnChainProvider().fetch("BTC"))
    assert snap is None


def test_analyzer_scores_onchain_votes_only_when_snapshot_present():
    from bot.core.analyzer import Analyzer

    class _Sig:
        # Permissive stub: any attribute the scorer probes that we haven't
        # pinned reads as None/falsy, which every voter treats as "no data".
        symbol = "BTC/USDT:USDT"
        change_pct = 0.0
        volume_spike = False

        def __getattr__(self, name):
            return None

    indicators = {"rsi": 50.0, "macd_hist": 0.0}
    sig = _Sig()
    from bot.core.ta_utils import Regime
    regime = list(Regime)[0]

    base_breakdown: list = []
    base = Analyzer._score_confluence(indicators, regime, sig,
                                      breakdown=base_breakdown)
    with_breakdown: list = []
    snap = oc.compute_bias({"symbol": "BTC", "dex_taker_flow": 0.6})
    scored = Analyzer._score_confluence(indicators, regime, sig,
                                        breakdown=with_breakdown,
                                        onchain_snapshot=snap)
    names = [str(v.get("name", v)) for v in with_breakdown] \
        if with_breakdown and isinstance(with_breakdown[0], dict) \
        else [str(v[0]) for v in with_breakdown if isinstance(v, (list, tuple))]
    assert any("onchain_flow" in n for n in names), f"voter recorded: {names}"
    base_names = [str(v.get("name", v)) for v in base_breakdown] \
        if base_breakdown and isinstance(base_breakdown[0], dict) \
        else [str(v[0]) for v in base_breakdown if isinstance(v, (list, tuple))]
    assert not any("onchain_flow" in n for n in base_names), \
        "no snapshot -> no on-chain voter -> default behavior unchanged"
    assert isinstance(base, float) and isinstance(scored, float)
