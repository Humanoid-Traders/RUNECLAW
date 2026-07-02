"""
RUNECLAW Backtest Data Loader -- fetches or generates OHLCV data.
Supports: Bitget API fetch, Binance public API, CSV file load, and synthetic data generation.
"""

from __future__ import annotations

import csv
import logging
import math
import random
from datetime import datetime, timedelta
from bot.compat import UTC
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

from bot.backtest.models import BacktestBar


class DataLoader:
    """Load or generate OHLCV candle data for backtesting."""

    @staticmethod
    def from_csv(path: str) -> list[BacktestBar]:
        """
        Load OHLCV data from a CSV file.
        Expected columns: timestamp, open, high, low, close, volume
        Timestamp can be ISO format or Unix milliseconds.
        """
        bars: list[BacktestBar] = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts_raw = row.get("timestamp", row.get("date", ""))
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    ts = datetime.fromtimestamp(int(ts_raw) / 1000, tz=UTC)

                bars.append(BacktestBar(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                ))
        bars.sort(key=lambda b: b.timestamp)
        return bars

    @staticmethod
    def from_ohlcv_list(raw: list[list], symbol: str = "BTC/USDT") -> list[BacktestBar]:
        """Convert ccxt-style OHLCV [[ts, o, h, l, c, v], ...] to BacktestBar list."""
        bars = []
        for candle in raw:
            bars.append(BacktestBar(
                timestamp=datetime.fromtimestamp(candle[0] / 1000, tz=UTC),
                open=float(candle[1]),
                high=float(candle[2]),
                low=float(candle[3]),
                close=float(candle[4]),
                volume=float(candle[5]) if len(candle) > 5 else 0,
            ))
        bars.sort(key=lambda b: b.timestamp)
        return bars

    @staticmethod
    async def from_bitget(
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        limit: int = 500,
    ) -> list[BacktestBar]:
        """Fetch historical OHLCV from Bitget via ccxt."""
        import ccxt.async_support as ccxt
        from bot.config import CONFIG

        exchange = ccxt.bitget({
            "apiKey": CONFIG.exchange.api_key,
            "secret": CONFIG.exchange.api_secret,
            "password": CONFIG.exchange.passphrase,
            "sandbox": CONFIG.exchange.sandbox,
            # Honor standard proxy env vars (HTTPS_PROXY + CA bundle) so real
            # data fetches work behind egress proxies (e.g. Claude Code cloud
            # sandboxes). No-op when no proxy env is set.
            "aiohttp_trust_env": True,
        })
        try:
            raw = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return DataLoader.from_ohlcv_list(raw, symbol)
        finally:
            await exchange.close()

    @staticmethod
    def generate_synthetic(
        bars: int = 720,
        start_price: float = 65000.0,
        volatility: float = 0.015,
        trend: float = 0.0001,
        seed: Optional[int] = 42,
    ) -> list[BacktestBar]:
        """
        Generate realistic synthetic OHLCV data for testing.

        Uses geometric Brownian motion with mean-reversion overlay,
        volume clustering, and intraday patterns to produce data
        that exhibits real market properties:
        - Fat tails (kurtosis > 3)
        - Volatility clustering (GARCH-like)
        - Volume-price correlation during moves
        - Mean-reversion at extremes

        Parameters:
            bars: number of 1h candles (720 = 30 days)
            start_price: initial price level
            volatility: base hourly volatility (σ)
            trend: hourly drift (μ)
            seed: random seed for reproducibility (None = random)
        """
        rng = np.random.default_rng(seed)
        if seed is not None:
            random.seed(seed)

        result: list[BacktestBar] = []
        price = start_price
        vol = volatility
        base_volume = 50_000_000  # $50M base daily volume
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

        # Track volatility state for clustering
        vol_state = volatility

        for i in range(bars):
            # --- Volatility clustering (simplified GARCH) ---
            vol_shock = rng.normal(0, 0.3)
            vol_state = 0.9 * vol_state + 0.1 * volatility * (1 + abs(vol_shock))
            current_vol = max(vol_state, volatility * 0.3)

            # --- Price returns with fat tails ---
            # Mix of normal and t-distribution for fat tails
            if random.random() < 0.05:
                # 5% chance of a tail event (2-4x normal move)
                ret = rng.standard_t(df=4) * current_vol * 2
            else:
                ret = rng.normal(trend, current_vol)

            # Mean reversion overlay: pull back toward start_price band.
            # Guard start_price == 0: a long downtrend on a very-low-priced asset
            # (e.g. PEPE at 1.3e-5) can underflow the price to 0.0, and when that
            # 0.0 is fed back as the next segment's start_price the division blew
            # up with ZeroDivisionError. With no reference band, skip the pull-back.
            mean_rev_strength = 0.001
            if start_price:
                deviation = (price - start_price) / start_price
                ret -= deviation * mean_rev_strength

            # --- Build the candle ---
            open_price = price
            close_price = open_price * (1 + ret)
            # Roadmap: allow realistic fat-tail / gap bars through. The old ±10%
            # clamp neutered exactly the flash-crash / gap events the 5% tail-event
            # branch above is meant to generate (and that stops must survive). Keep
            # a wide but bounded band so a single bar can't underflow the price to
            # <= 0, while permitting up to a -40% gap-down / +60% spike.
            close_price = max(close_price, open_price * 0.6)
            close_price = min(close_price, open_price * 1.6)

            # Intrabar high/low
            intra_vol = abs(ret) + current_vol * 0.5
            high_ext = abs(rng.normal(0, intra_vol * 0.5))
            low_ext = abs(rng.normal(0, intra_vol * 0.5))
            high_price = max(open_price, close_price) * (1 + high_ext)
            low_price = min(open_price, close_price) * (1 - low_ext)

            # --- Volume modeling ---
            # Base volume with intraday pattern (higher during market hours)
            hour = i % 24
            intraday_factor = 1.0 + 0.5 * math.sin(math.pi * (hour - 6) / 12)
            intraday_factor = max(intraday_factor, 0.4)

            # Volume spikes correlate with price moves
            move_factor = 1.0 + abs(ret) / current_vol * 2
            vol_noise = max(0.3, rng.lognormal(0, 0.4))
            bar_volume = (base_volume / 24) * intraday_factor * move_factor * vol_noise

            result.append(BacktestBar(
                timestamp=start_time + timedelta(hours=i),
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=round(bar_volume, 2),
            ))

            price = close_price

        return result

    @staticmethod
    async def from_binance_public(
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
        limit: int = 1000,
        start_time: Optional[int] = None,
    ) -> list[BacktestBar]:
        """
        Fetch historical OHLCV from the Binance public klines API.

        No API key required -- uses the unauthenticated endpoint.

        Parameters:
            symbol: Binance-style pair, e.g. "BTCUSDT"
            timeframe: candle interval (1m, 5m, 15m, 1h, 4h, 1d, etc.)
            limit: number of candles to fetch (max 1000)
            start_time: optional start time as Unix-ms timestamp
        """
        import aiohttp

        url = "https://api.binance.com/api/v3/klines"
        params: dict = {
            "symbol": symbol.upper(),
            "interval": timeframe,
            "limit": min(limit, 1000),
        }
        if start_time is not None:
            params["startTime"] = start_time

        # Derive a human-readable symbol for the BacktestBar (e.g. BTCUSDT -> BTC/USDT)
        readable_symbol = symbol.upper()
        for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
            if readable_symbol.endswith(quote) and len(readable_symbol) > len(quote):
                readable_symbol = f"{readable_symbol[:-len(quote)]}/{quote}"
                break

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("Binance API error %s: %s", resp.status, body)
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("Failed to fetch klines from Binance")
            return []

        bars: list[BacktestBar] = []
        for kline in data:
            # Binance kline format:
            # [open_time, open, high, low, close, volume, close_time, ...]
            bars.append(BacktestBar(
                timestamp=datetime.fromtimestamp(int(kline[0]) / 1000, tz=UTC),
                open=float(kline[1]),
                high=float(kline[2]),
                low=float(kline[3]),
                close=float(kline[4]),
                volume=float(kline[5]),
                symbol=readable_symbol,
            ))

        bars.sort(key=lambda b: b.timestamp)
        return bars

    @staticmethod
    async def from_public_api(
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        limit: int = 1000,
    ) -> list[BacktestBar]:
        """
        Convenience wrapper over :meth:`from_binance_public`.

        Accepts human-readable symbols (``BTC/USDT``) and normalises them
        to the Binance format (``BTCUSDT``) before fetching.
        """
        binance_symbol = symbol.replace("/", "").replace("-", "").upper()
        return await DataLoader.from_binance_public(
            symbol=binance_symbol,
            timeframe=timeframe,
            limit=limit,
        )

    @staticmethod
    def save_csv(bars: list[BacktestBar], path: str) -> None:
        """Save bar data to CSV for reuse."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for bar in bars:
                writer.writerow([
                    bar.timestamp.isoformat(),
                    bar.open, bar.high, bar.low, bar.close, bar.volume,
                ])
