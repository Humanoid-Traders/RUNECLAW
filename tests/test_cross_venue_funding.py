"""
Cross-venue funding enrichment (Round B, 2026-07-12).

Contract: bulk-cached per-venue funding maps (2 HTTP calls per TTL window
for the whole universe), fail-open on any venue error, and PURE
observability — the order-flow rule votes and confidence are computed
from the same fixed weight set as before.
"""

from __future__ import annotations

import inspect

import pytest

from bot.core.cross_venue import CrossVenueFunding, base_of


# ── symbol normalization ─────────────────────────────────────────────
def test_base_of_handles_all_forms():
    assert base_of("BTC/USDT:USDT") == "BTC"
    assert base_of("BTC/USDC:USDC") == "BTC"
    assert base_of("BTC/USDT") == "BTC"
    assert base_of("btc") == "BTC"


# ── payload parsing ──────────────────────────────────────────────────
def test_parse_rates_maps_bases_and_keeps_extreme():
    raw = {
        "BTC/USDT:USDT": {"fundingRate": 0.0001},
        "ETH/USDC:USDC": {"fundingRate": -0.0003},
        "BTC/USDC:USDC": {"fundingRate": 0.0009},  # same base, bigger |rate|
        "BROKEN/USDT:USDT": {"fundingRate": None},
        "JUNK": "not-a-dict",
    }
    out = CrossVenueFunding._parse_rates(raw)
    assert out["BTC"] == 0.0009
    assert out["ETH"] == -0.0003
    assert "BROKEN" not in out and "JUNK" not in out


# ── caching + fail-open ──────────────────────────────────────────────
class _FakeEx:
    def __init__(self, payload=None, exc=None):
        self.payload = payload or {}
        self.exc = exc
        self.calls = 0

    async def fetch_funding_rates(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.payload

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_bulk_cache_one_fetch_per_ttl(monkeypatch):
    cv = CrossVenueFunding(ttl_seconds=600)
    fakes = {
        "bybit": _FakeEx({"BTC/USDT:USDT": {"fundingRate": 0.0002}}),
        "hyperliquid": _FakeEx({"BTC/USDC:USDC": {"fundingRate": 0.0007}}),
    }

    async def _ex(venue_id):
        return fakes[venue_id]

    monkeypatch.setattr(cv, "_exchange", _ex)
    r1 = await cv.rates_for("BTC/USDT")
    r2 = await cv.rates_for("BTC/USDT:USDT")
    r3 = await cv.rates_for("ETH/USDT")   # different base, same cached maps
    assert r1 == {"bybit": 0.0002, "hyperliquid": 0.0007}
    assert r2 == r1
    assert r3 == {}                        # not in either map
    # ONE bulk fetch per venue despite three lookups
    assert fakes["bybit"].calls == 1
    assert fakes["hyperliquid"].calls == 1


@pytest.mark.asyncio
async def test_venue_failure_is_fail_open(monkeypatch):
    cv = CrossVenueFunding(ttl_seconds=600)
    fakes = {
        "bybit": _FakeEx(exc=Exception("cloudfront 403")),
        "hyperliquid": _FakeEx({"SOL/USDC:USDC": {"fundingRate": -0.0004}}),
    }

    async def _ex(venue_id):
        return fakes[venue_id]

    monkeypatch.setattr(cv, "_exchange", _ex)
    rates = await cv.rates_for("SOL/USDT")
    assert rates == {"hyperliquid": -0.0004}   # partial map, no raise


@pytest.mark.asyncio
async def test_fetches_on_freshly_booted_host(monkeypatch):
    """Regression (caught by CI): time.monotonic() counts from BOOT. With a
    0.0 'never fetched' sentinel, a host up for < TTL seconds (fresh CI
    runner, rebooted trading VPS) made the empty cache look fresh and the
    provider returned {} until uptime exceeded the TTL."""
    import bot.core.cross_venue as cvmod
    monkeypatch.setattr(cvmod.time, "monotonic", lambda: 42.0)  # < ttl
    cv = CrossVenueFunding(ttl_seconds=600)
    fake = _FakeEx({"BTC/USDT:USDT": {"fundingRate": 0.0003}})

    async def _ex(venue_id):
        return fake

    monkeypatch.setattr(cv, "_exchange", _ex)
    rates = await cv.rates_for("BTC/USDT")
    assert rates.get("bybit") == 0.0003        # fetched despite uptime < ttl
    assert fake.calls >= 1


# ── divergence math ──────────────────────────────────────────────────
def test_divergence_needs_two_venues():
    assert CrossVenueFunding.divergence({}) is None
    assert CrossVenueFunding.divergence({"bybit": 0.0001}) is None
    d = CrossVenueFunding.divergence({"bybit": 0.0001}, home_rate=0.0007)
    assert d is not None
    assert d["venues"] == 2
    assert abs(d["spread"] - 0.0006) < 1e-12


# ── order-flow wiring: observability only ────────────────────────────
def test_orderflow_fields_default_none():
    from bot.core.order_flow import OrderFlowSignal
    sig = OrderFlowSignal(symbol="BTC/USDT")
    assert sig.cross_venue_funding is None
    assert sig.cross_venue_spread is None


def test_orderflow_enrichment_wired_and_flagged():
    from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig
    src = inspect.getsource(OrderFlowAnalyzer.analyze)
    assert "CROSS_VENUE.rates_for" in src
    assert "cross-venue funding n/a" in src          # fail-open note
    assert OrderFlowConfig().cross_venue_funding is True  # enrichment default ON


def _scored_signal(analyzer, cross_venue=None):
    from bot.core.order_flow import OrderFlowSignal
    sig = OrderFlowSignal(symbol="BTC/USDT:USDT")
    sig.book_imbalance = 0.4
    sig.funding_rate = 0.0004          # home longs paying
    sig.cross_venue_funding = cross_venue
    analyzer._fill_composite(sig, ok=["book"])
    return sig


def test_vote_off_is_byte_identical_scoring():
    """Default (OF_CROSS_VENUE_VOTE_ENABLED off): the composite score and
    confidence must be IDENTICAL whether or not cross-venue data is
    attached — enrichment stays observability-only until the A/B."""
    from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig
    cfg = OrderFlowConfig()
    assert cfg.cross_venue_vote_enabled is False   # shipped default
    a = OrderFlowAnalyzer(cfg)
    bare = _scored_signal(a, cross_venue=None)
    rich = _scored_signal(a, cross_venue={"bybit": 0.0001,
                                          "hyperliquid": -0.0002})
    assert rich.smart_money_score == bare.smart_money_score
    assert rich.confidence == bare.confidence


def test_vote_on_leans_contrarian_to_home_crowding():
    """Enabled: home funding ABOVE the cross-venue mean = local longs are
    the crowded side = the score must move bearish vs. vote-off."""
    import dataclasses
    from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig
    off = OrderFlowConfig()
    on = dataclasses.replace(OrderFlowConfig(), cross_venue_vote_enabled=True)
    cheap_elsewhere = {"bybit": 0.0000, "hyperliquid": -0.0001}
    s_off = _scored_signal(OrderFlowAnalyzer(off), cheap_elsewhere)
    s_on = _scored_signal(OrderFlowAnalyzer(on), cheap_elsewhere)
    assert s_on.smart_money_score < s_off.smart_money_score
    # confidence denominator includes the extra weight only when enabled
    assert s_on.confidence != s_off.confidence or s_on.confidence == 1.0


# ── /funding command ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_funding_command_renders_all_venues(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    import bot.core.cross_venue as cvmod

    engine = RuneClawEngine()
    handler = TelegramHandler(engine)

    fut_ex = MagicMock()
    fut_ex.fetch_funding_rate = AsyncMock(return_value={"fundingRate": 0.0001})

    async def _get_fut():
        return fut_ex

    monkeypatch.setattr(engine.scanner, "_get_futures_exchange", _get_fut)
    monkeypatch.setattr(cvmod.CROSS_VENUE, "rates_for",
                        AsyncMock(return_value={"bybit": 0.0006,
                                                "hyperliquid": -0.0002}))

    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 6307156912
    update.effective_chat = MagicMock()
    update.effective_chat.id = 6307156912
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = ["BTC"]

    await handler._cmd_funding(update, ctx)
    texts = [c[0][0] if c[0] else c.kwargs.get("text", "")
             for c in update.message.reply_text.call_args_list]
    text = "\n".join(texts)
    assert "BTC funding across venues" in text
    assert "bitget" in text and "bybit" in text and "hyperliquid" in text
    assert "Spread across 3 venues" in text
