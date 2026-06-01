"""
Market Cap & FDV Provider for RUNECLAW.

Fetches market capitalization, fully diluted valuation, and circulating
supply data from the CoinGecko free API (50 calls/min, no auth required).

Provides valuation context for trade decisions — helps distinguish
between large-cap stability and small-cap volatility risk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# CoinGecko symbol mapping (ccxt symbol -> coingecko id)
_SYMBOL_MAP: dict[str, str] = {
    "BTC/USDT": "bitcoin", "ETH/USDT": "ethereum", "SOL/USDT": "solana",
    "BNB/USDT": "binancecoin", "ADA/USDT": "cardano", "DOT/USDT": "polkadot",
    "AVAX/USDT": "avalanche-2", "LINK/USDT": "chainlink", "NEAR/USDT": "near",
    "SUI/USDT": "sui", "APT/USDT": "aptos", "ARB/USDT": "arbitrum",
    "OP/USDT": "optimism", "DOGE/USDT": "dogecoin", "SHIB/USDT": "shiba-inu",
    "PEPE/USDT": "pepe", "WIF/USDT": "dogwifcoin", "BONK/USDT": "bonk",
    "JUP/USDT": "jupiter-exchange-solana", "RENDER/USDT": "render-token",
    "FET/USDT": "artificial-superintelligence-alliance",
    "INJ/USDT": "injective-protocol", "TIA/USDT": "celestia",
    "SEI/USDT": "sei-network", "TON/USDT": "the-open-network",
    "ATOM/USDT": "cosmos", "UNI/USDT": "uniswap", "AAVE/USDT": "aave",
}

# Cap tier thresholds
LARGE_CAP = 10_000_000_000    # $10B+
MID_CAP = 1_000_000_000       # $1B+
SMALL_CAP = 100_000_000       # $100M+


@dataclass
class MarketCapData:
    """Market capitalization data for a token."""
    symbol: str
    coingecko_id: str
    market_cap_usd: float = 0.0
    fdv_usd: float = 0.0
    circulating_supply: float = 0.0
    total_supply: float = 0.0
    max_supply: Optional[float] = None
    supply_ratio: float = 0.0          # circulating / total
    cap_tier: str = "UNKNOWN"          # "LARGE" | "MID" | "SMALL" | "MICRO" | "UNKNOWN"
    fdv_mcap_ratio: float = 0.0        # FDV / market_cap (>2.0 = high inflation risk)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MarketCapProvider:
    """Fetches market cap data from CoinGecko free API.

    Caches aggressively (5 min TTL) since market cap changes slowly.
    Falls back gracefully if API is unavailable.
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, MarketCapData]] = {}

    async def get_market_cap(self, symbol: str) -> Optional[MarketCapData]:
        """Fetch market cap data for a symbol.

        Args:
            symbol: ccxt-style symbol (e.g. "BTC/USDT")

        Returns:
            MarketCapData or None if unavailable
        """
        # Check cache
        cached = self._cache.get(symbol)
        if cached and (time.monotonic() - cached[0]) < self._ttl:
            return cached[1]

        cg_id = _SYMBOL_MAP.get(symbol)
        if not cg_id:
            return None

        try:
            import aiohttp
            url = f"https://api.coingecko.com/api/v3/coins/{cg_id}"
            params = {
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 429:
                        logger.warning("CoinGecko rate limited")
                        return self._cache.get(symbol, (0, None))[1]
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            md = data.get("market_data", {})
            mcap = md.get("market_cap", {}).get("usd", 0)
            fdv = md.get("fully_diluted_valuation", {}).get("usd", 0)
            circ = md.get("circulating_supply", 0) or 0
            total = md.get("total_supply", 0) or 0
            max_s = md.get("max_supply")

            # Classify tier
            if mcap >= LARGE_CAP:
                tier = "LARGE"
            elif mcap >= MID_CAP:
                tier = "MID"
            elif mcap >= SMALL_CAP:
                tier = "SMALL"
            elif mcap > 0:
                tier = "MICRO"
            else:
                tier = "UNKNOWN"

            supply_ratio = circ / total if total > 0 else 0
            fdv_ratio = fdv / mcap if mcap > 0 else 0

            result = MarketCapData(
                symbol=symbol,
                coingecko_id=cg_id,
                market_cap_usd=mcap,
                fdv_usd=fdv,
                circulating_supply=circ,
                total_supply=total,
                max_supply=max_s,
                supply_ratio=round(supply_ratio, 4),
                cap_tier=tier,
                fdv_mcap_ratio=round(fdv_ratio, 2),
            )
            self._cache[symbol] = (time.monotonic(), result)
            return result

        except ImportError:
            logger.warning("aiohttp not installed — MarketCapProvider disabled")
            return None
        except Exception as exc:
            logger.warning(f"MarketCap fetch failed for {symbol}: {exc}")
            return None

    def get_cached(self, symbol: str) -> Optional[MarketCapData]:
        """Get cached data without async call."""
        cached = self._cache.get(symbol)
        return cached[1] if cached else None
