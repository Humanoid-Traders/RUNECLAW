"""
On-chain provider scaffolding.

Covers the inert-without-key gate, the metric→bias mapping (contrarian to
exchange flow), confidence scaling with metric availability, the bounded
confluence vote, payload normalisation robustness, and the cached/fail-open
fetch — all without network (fetch is stubbed / disabled).
"""

import pytest

from bot.core.onchain import (
    OnChainProvider, compute_bias, onchain_enabled, _normalise, get_onchain_provider,
)


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("ONCHAIN_ENABLED", raising=False)
    monkeypatch.delenv("ONCHAIN_API_KEY", raising=False)
    assert onchain_enabled() is False
    # Enabled flag but no key -> still inert.
    monkeypatch.setenv("ONCHAIN_ENABLED", "true")
    assert onchain_enabled() is False
    monkeypatch.setenv("ONCHAIN_API_KEY", "k")
    assert onchain_enabled() is True


@pytest.mark.asyncio
async def test_fetch_inert_when_disabled(monkeypatch):
    monkeypatch.delenv("ONCHAIN_ENABLED", raising=False)
    p = OnChainProvider()
    assert await p.fetch("BTC/USDT") is None


def test_outflow_is_bullish_inflow_bearish():
    bull = compute_bias({"exchange_netflow": -0.8})   # coins leaving exchanges
    bear = compute_bias({"exchange_netflow": 0.8})    # coins flooding in
    assert bull.bias > 0 and bear.bias < 0
    assert -1.0 <= bull.bias <= 1.0 and -1.0 <= bear.bias <= 1.0


def test_whale_and_stablecoin_directions():
    assert compute_bias({"whale_net": 0.9}).bias > 0          # accumulation bullish
    assert compute_bias({"whale_net": -0.9}).bias < 0
    assert compute_bias({"stablecoin_supply_change": 0.9}).bias > 0   # minting bullish


def test_confidence_scales_with_availability():
    one = compute_bias({"whale_net": 0.5})
    three = compute_bias({"exchange_netflow": -0.5, "whale_net": 0.5,
                          "stablecoin_supply_change": 0.5})
    assert one.confidence == pytest.approx(1 / 3)
    assert three.confidence == pytest.approx(1.0)
    assert set(three.components_ok) == {"netflow", "whale", "stablecoin"}


def test_no_metrics_is_neutral_no_vote():
    snap = compute_bias({"symbol": "BTC/USDT"})
    assert snap.bias == 0.0 and snap.confidence == 0.0
    assert snap.to_confluence_votes() == []


def test_confluence_vote_bounded():
    snap = compute_bias({"exchange_netflow": -1.0, "whale_net": 1.0,
                         "stablecoin_supply_change": 1.0})
    votes = snap.to_confluence_votes()
    assert len(votes) == 1
    name, vote, weight = votes[0]
    assert name == "onchain_flow"
    assert -1.0 <= vote <= 1.0
    assert 0.0 < weight <= 0.7


def test_normalise_is_defensive():
    assert _normalise({"exchange_netflow": "0.4", "junk": "x"}) == {"exchange_netflow": 0.4}
    assert _normalise({"whale_net": "not-a-number"}) == {}
    assert _normalise("garbage") == {}
    assert _normalise({}) == {}


@pytest.mark.asyncio
async def test_fetch_uses_compute_and_caches(monkeypatch):
    monkeypatch.setenv("ONCHAIN_ENABLED", "true")
    monkeypatch.setenv("ONCHAIN_API_KEY", "k")
    p = OnChainProvider()
    calls = {"n": 0}

    async def fake_metrics(symbol):
        calls["n"] += 1
        return {"exchange_netflow": -0.6, "whale_net": 0.4}

    monkeypatch.setattr(p, "_fetch_metrics", fake_metrics)
    snap = await p.fetch("BTC/USDT")
    assert snap is not None and snap.bias > 0
    # Second call within TTL -> served from cache, no extra fetch.
    await p.fetch("BTC/USDT")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_fetch_fail_open(monkeypatch):
    monkeypatch.setenv("ONCHAIN_ENABLED", "true")
    monkeypatch.setenv("ONCHAIN_API_KEY", "k")
    p = OnChainProvider()

    async def boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(p, "_fetch_metrics", boom)
    assert await p.fetch("BTC/USDT") is None     # error -> None, never raises


def test_singleton():
    assert get_onchain_provider() is get_onchain_provider()
