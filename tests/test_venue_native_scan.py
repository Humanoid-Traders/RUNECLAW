"""
Venue-native discovery (2026-07-12): scan the active venue's own catalog
when it is not Bitget.

Hyperliquid's builder perps (WTI $120M/day, S&P 500, gold, natgas,
memory-sector equities) exist on no CEX — the Bitget-only scanner could
never see them even while executing on HL. The overlay is a strict
no-op on Bitget (the current live deployment) and fail-open everywhere.
"""

from __future__ import annotations

import inspect

import pytest

import bot.core.market_scanner as ms
from bot.core.market_scanner import MarketScanner, _classify_symbol
from bot.core.venues import get_venue


class _CfgProxy:
    def __init__(self, real, **over):
        self._real = real
        self._over = over

    def __getattr__(self, k):
        if k in ("_real", "_over"):
            raise AttributeError(k)
        if k in self._over:
            return self._over[k]
        return getattr(self._real, k)


# ── builder-market classification ────────────────────────────────────
def test_hl_builder_classification():
    assert _classify_symbol("XYZ-CL/USDC:USDC") == "Commodity"
    assert _classify_symbol("XYZ-BRENTOIL/USDC:USDC") == "Commodity"
    assert _classify_symbol("XYZ-NATGAS/USDC:USDC") == "Commodity"
    assert _classify_symbol("XYZ-GOLD/USDC:USDC") == "Metal"
    assert _classify_symbol("XYZ-SILVER/USDC:USDC") == "Metal"
    assert _classify_symbol("XYZ-SP500/USDC:USDC") == "ETF"
    assert _classify_symbol("XYZ-XYZ100/USDC:USDC") == "ETF"
    assert _classify_symbol("XYZ-MU/USDC:USDC") == "Stock"       # unknown -> Stock
    assert _classify_symbol("XYZ-NVDA/USDC:USDC") == "Stock"
    assert _classify_symbol("HYPE/USDC:USDC") == "Crypto"        # non-builder
    assert _classify_symbol("BTC/USDT") == "Crypto"              # untouched


# ── overlay behavior ─────────────────────────────────────────────────
def _tick(volume, pct=1.0):
    return {"last": 1.0, "percentage": pct, "quoteVolume": volume}


VENUE_TICKERS = {
    "XYZ-CL/USDC:USDC": _tick(120_000_000),    # Commodity — the prize
    "XYZ-SP500/USDC:USDC": _tick(37_000_000),  # ETF
    "XYZ-GOLD/USDC:USDC": _tick(3_000_000),    # Metal
    "HYPE/USDC:USDC": _tick(50_000_000),       # HL-only crypto
    "BTC/USDC:USDC": _tick(1_000_000_000),     # base already on Bitget — skip
    "DUST/USDC:USDC": _tick(10_000),           # below the crypto floor
}


class _FakeVenueEx:
    def __init__(self, tickers):
        self._tickers = tickers
        self.calls = 0

    async def fetch_tickers(self):
        self.calls += 1
        return dict(self._tickers)


async def _overlay(monkeypatch, tickers=VENUE_TICKERS, seen=None, **cfg_over):
    scanner = MarketScanner()
    if cfg_over:
        monkeypatch.setattr(ms, "CONFIG", _CfgProxy(ms.CONFIG, **cfg_over))
    fake = _FakeVenueEx(tickers)

    async def _venue_ex():
        return fake

    monkeypatch.setattr(scanner, "_get_venue_data_exchange", _venue_ex)
    sigs = await scanner._scan_active_venue_extra(
        seen if seen is not None else {"BTC/USDT", "ETH/USDT"})
    return sigs, fake


@pytest.mark.asyncio
async def test_overlay_admits_venue_native_markets(monkeypatch):
    # metals enabled explicitly: XYZ-GOLD classifies as Metal, and since
    # 2026-07-14 metals are off by default (parity evidence) — overlay
    # MECHANICS are what's under test here, not class policy.
    sigs, _ = await _overlay(monkeypatch, scan_class_metals=True)
    syms = {s.symbol for s in sigs}
    assert "XYZ-CL/USDC:USDC" in syms         # commodity (tradfi floor)
    assert "XYZ-SP500/USDC:USDC" in syms
    assert "XYZ-GOLD/USDC:USDC" in syms
    assert "HYPE/USDC:USDC" in syms           # HL-only crypto above floor
    assert "BTC/USDC:USDC" not in syms        # base already covered on Bitget
    assert "DUST/USDC:USDC" not in syms       # below the crypto floor
    cl = next(s for s in sigs if s.symbol == "XYZ-CL/USDC:USDC")
    assert cl.asset_category == "Commodity"


@pytest.mark.asyncio
async def test_overlay_is_noop_on_bitget(monkeypatch):
    """When the active venue IS Bitget, _get_venue_data_exchange returns
    None and the overlay contributes nothing (the live default path)."""
    scanner = MarketScanner()
    assert get_venue().id == "bitget"          # test env default
    ex = await scanner._get_venue_data_exchange()
    assert ex is None
    sigs = await scanner._scan_active_venue_extra({"BTC/USDT"})
    assert sigs == []


@pytest.mark.asyncio
async def test_overlay_flag_off_makes_no_venue_call(monkeypatch):
    sigs, fake = await _overlay(monkeypatch, scan_venue_native_markets=False)
    assert sigs == [] and fake.calls == 0      # flag off: no venue call at all


@pytest.mark.asyncio
async def test_overlay_class_toggles_govern(monkeypatch):
    sigs, _ = await _overlay(monkeypatch, scan_class_metals=False)
    syms = {s.symbol for s in sigs}
    assert "XYZ-GOLD/USDC:USDC" not in syms    # class toggles govern here too
    assert "XYZ-CL/USDC:USDC" in syms


@pytest.mark.asyncio
async def test_overlay_fail_open_on_venue_error(monkeypatch):
    class _Boom:
        async def fetch_tickers(self):
            raise Exception("venue down")

    scanner = MarketScanner()

    async def _venue_ex():
        return _Boom()

    monkeypatch.setattr(scanner, "_get_venue_data_exchange", _venue_ex)
    sigs = await scanner._scan_active_venue_extra({"BTC/USDT"})
    assert sigs == []                          # never raises into the scan


# ── engine routing ───────────────────────────────────────────────────
def test_engine_routes_usdc_symbols_to_venue_exchange():
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._analyze_signal)
    assert '":USDC" in signal.symbol' in src
    assert "_get_venue_data_exchange" in src


def test_venue_data_exchange_rebuilds_on_venue_switch():
    src = inspect.getsource(MarketScanner._get_venue_data_exchange)
    assert "_venue_data_exchange_id != venue.id" in src   # cache keyed by venue
