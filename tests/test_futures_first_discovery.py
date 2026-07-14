"""
Futures-first crypto discovery (2026-07-12, SCAN_VOLUME_SOURCE).

The bot trades USDT-FUTURES, but the legacy scanner gated the crypto
universe on SPOT volume and required a spot listing — perp liquidity was
never measured and perp-only listings were invisible. Default is now
"futures": gate on the perp's own 24h volume, emit spot-form symbols when
a spot pair exists (all analysis paths unchanged) and futures-form for
perp-only listings (engine routes those to the futures exchange).
SCAN_VOLUME_SOURCE=spot restores the legacy path byte-for-byte.
"""

from __future__ import annotations

import inspect

import pytest

import bot.core.market_scanner as ms
from bot.core.market_scanner import MarketScanner


class _CfgProxy:
    """getattr-delegating CONFIG stand-in with selective overrides —
    AppConfig is frozen, so tests override via proxy, not setattr."""

    def __init__(self, real, **over):
        self._real = real
        self._over = over

    def __getattr__(self, k):
        if k in ("_real", "_over"):
            raise AttributeError(k)
        if k in self._over:
            return self._over[k]
        return getattr(self._real, k)


def _tick(volume, pct=1.0):
    return {"last": 1.0, "percentage": pct, "quoteVolume": volume}


SPOT = {
    "BTC/USDT": _tick(500_000_000),
    "ALT/USDT": _tick(9_000_000),      # spot-listed alt
    "THIN/USDT": _tick(4_000_000),     # spot liquid, perp thin
}
FUTURES = {
    "BTC/USDT:USDT": _tick(2_000_000_000),
    "ALT/USDT:USDT": _tick(30_000_000),
    "THIN/USDT:USDT": _tick(200_000),      # below the $1.5M floor on the perp
    "PERPONLY/USDT:USDT": _tick(8_000_000),  # no spot pair at all
    "XAU/USDT:USDT": _tick(15_000_000),      # TradFi — separate pass
}


async def _run_scan(monkeypatch, source):
    # async (pytest-asyncio) rather than asyncio.run(): a bare run() closes
    # the policy loop and breaks suite-mates that still use the legacy
    # asyncio.get_event_loop() pattern.
    # scan_class_metals=True: these tests exercise DISCOVERY mechanics with
    # XAU as the TradFi fixture; since 2026-07-14 metals are off by default
    # (parity evidence), which is class policy, not what's under test here.
    scanner = MarketScanner()
    monkeypatch.setattr(ms, "CONFIG",
                        _CfgProxy(ms.CONFIG, scan_volume_source=source,
                                  scan_class_metals=True))

    async def _spot():
        return dict(SPOT)

    async def _fut():
        return dict(FUTURES)

    monkeypatch.setattr(scanner, "_fetch_spot_tickers", _spot)
    monkeypatch.setattr(scanner, "_fetch_futures_tickers", _fut)
    return await scanner._scan_all_markets()


@pytest.mark.asyncio
async def test_futures_first_gates_on_perp_volume(monkeypatch):
    signals = await _run_scan(monkeypatch, "futures")
    syms = {s.symbol for s in signals}
    # spot-listed crypto emitted in SPOT form (analysis paths unchanged)
    assert "BTC/USDT" in syms
    assert "ALT/USDT" in syms
    # perp volume is the gate: spot-liquid but perp-thin symbol is dropped
    assert not any(s.startswith("THIN/") for s in syms)
    # perp-only listing enters, in FUTURES form
    assert "PERPONLY/USDT:USDT" in syms
    # TradFi pass still runs
    assert "XAU/USDT:USDT" in syms


@pytest.mark.asyncio
async def test_perp_only_signal_is_crypto_category(monkeypatch):
    signals = await _run_scan(monkeypatch, "futures")
    perp_only = next(s for s in signals if s.symbol == "PERPONLY/USDT:USDT")
    assert perp_only.asset_category == "Crypto"


@pytest.mark.asyncio
async def test_spot_source_restores_legacy_behavior(monkeypatch):
    signals = await _run_scan(monkeypatch, "spot")
    syms = {s.symbol for s in signals}
    # legacy: spot volume gating, spot listing required
    assert "BTC/USDT" in syms and "ALT/USDT" in syms
    assert "THIN/USDT" in syms                   # spot volume clears the floor
    assert "PERPONLY/USDT:USDT" not in syms      # invisible without spot pair
    assert not any(s == "PERPONLY/USDT" for s in syms)
    assert "XAU/USDT:USDT" in syms


def test_default_volume_source_is_futures():
    from bot.config import CONFIG
    assert CONFIG.scan_volume_source == "futures"


def test_engine_routes_futures_form_crypto_to_futures_exchange():
    """Perp-only symbols have no spot market — their OHLCV/order-flow must
    come from the futures exchange (source pin on the routing branch)."""
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._analyze_signal)
    assert 'category != "Crypto" or ":" in signal.symbol' in src


def test_order_flow_falls_back_to_symbol_as_derivative():
    """For futures-form symbols the derivatives fallback IS the symbol —
    funding/OI voters resolve natively."""
    from bot.core.order_flow import OrderFlowAnalyzer
    src = inspect.getsource(OrderFlowAnalyzer.analyze)
    assert "derivatives_symbol or symbol" in src
