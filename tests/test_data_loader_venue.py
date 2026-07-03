"""Backtest data must come from the PRODUCTION venue, never the demo engine.

Bitget's demo (sandbox) environment is a separate matching engine: its candles
differ on every bar (different wicks and volumes) and its market list is a
small subset of production perps. A data loader that inherits
CONFIG.exchange.sandbox therefore measures the wrong market whenever the dev
environment runs with sandbox=True — which is exactly how the robustness
suite's hold-out runs silently shrank from 10 symbols to 2 (the other 8 are
not listed on the demo venue).
"""

import asyncio
from unittest.mock import patch

from bot.backtest.data_loader import DataLoader


class _CaptureExchange:
    """Stands in for ccxt.bitget; records the constructor config."""

    captured: dict = {}

    def __init__(self, cfg):
        type(self).captured = dict(cfg)

    async def fetch_ohlcv(self, symbol, timeframe, limit=None, since=None,
                          params=None):
        return [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]

    async def close(self):
        pass


class TestProductionVenueOnly:
    def test_loader_never_opts_into_sandbox(self):
        with patch("ccxt.async_support.bitget", _CaptureExchange):
            asyncio.run(DataLoader.from_bitget("BTC/USDT:USDT", "1h", limit=10))
        cfg = _CaptureExchange.captured
        assert cfg.get("sandbox") is False
        # Public candles need no credentials — none must be passed, so a
        # demo-keyed dev environment can't flip the venue via auth either.
        for key in ("apiKey", "secret", "password"):
            assert key not in cfg
