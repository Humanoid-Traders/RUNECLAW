"""
RUNECLAW Backtest Data Loader -- fetches or generates OHLCV data.
Supports: Bitget API fetch, CSV file load, and synthetic data generation.
"""

from __future__ import annotations

import csv
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

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
                    ts = datetime.fromisoformat(ts_raw)
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
        if seed is not None:
            np.random.seed(seed)
            random.seed(seed)

        result: list[BacktestBar] = []
        price = start_price
        vol = volatility
        base_volume = 50_000_000  # $50M base daily volume
        start_time = datetime(2025, 1, 1, 0, 0, 0)

        # Track volatility state for clustering
        vol_state = volatility

        for i in range(bars):
            # --- Volatility clustering (simplified GARCH) ---
            vol_shock = np.random.normal(0, 0.3)
            vol_state = 0.9 * vol_state + 0.1 * volatility * (1 + abs(vol_shock))
            current_vol = max(vol_state, volatility * 0.3)

            # --- Price returns with fat tails ---
            # Mix of normal and t-distribution for fat tails
            if random.random() < 0.05:
                # 5% chance of a tail event (2-4x normal move)
                ret = np.random.standard_t(df=4) * current_vol * 2
            else:
                ret = np.random.normal(trend, current_vol)

            # Mean reversion overlay: pull back toward start_price band
            mean_rev_strength = 0.001
            deviation = (price - start_price) / start_price
            ret -= deviation * mean_rev_strength

            # --- Build the candle ---
            open_price = price
            close_price = open_price * (1 + ret)
            close_price = max(close_price, open_price * 0.9)  # cap single-bar at -10%
            close_price = min(close_price, open_price * 1.1)

            # Intrabar high/low
            intra_vol = abs(ret) + current_vol * 0.5
            high_ext = abs(np.random.normal(0, intra_vol * 0.5))
            low_ext = abs(np.random.normal(0, intra_vol * 0.5))
            high_price = max(open_price, close_price) * (1 + high_ext)
            low_price = min(open_price, close_price) * (1 - low_ext)

            # --- Volume modeling ---
            # Base volume with intraday pattern (higher during market hours)
            hour = i % 24
            intraday_factor = 1.0 + 0.5 * math.sin(math.pi * (hour - 6) / 12)
            intraday_factor = max(intraday_factor, 0.4)

            # Volume spikes correlate with price moves
            move_factor = 1.0 + abs(ret) / current_vol * 2
            vol_noise = max(0.3, np.random.lognormal(0, 0.4))
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
