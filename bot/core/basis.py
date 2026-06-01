"""
Spot-Futures Basis Analysis for RUNECLAW.

Computes the premium/discount between spot and perpetual futures prices.
Positive basis = futures premium (bullish leverage), negative = discount (bearish).
Extreme basis combined with high OI signals potential liquidation cascade.

Data source: Bitget via ccxt (spot ticker + futures ticker for same asset).
"""

from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BasisResult:
    """Spot-futures basis analysis output."""
    symbol: str
    spot_price: float
    futures_price: float
    basis_pct: float           # (futures - spot) / spot * 100
    basis_annualized_pct: float  # basis_pct * 365 (approximation for perps)
    sentiment: str             # "PREMIUM" | "DISCOUNT" | "NEUTRAL"
    extreme: bool              # True if |basis| > 0.5% (significant dislocation)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BasisAnalyzer:
    """Computes spot vs perpetual futures basis from Bitget.

    Uses ccxt to fetch both spot and swap tickers for the same asset.
    Caches results with configurable TTL to avoid excessive API calls.
    """

    def __init__(self, exchange_factory=None, ttl_seconds: float = 60.0):
        self._exchange_factory = exchange_factory
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, BasisResult]] = {}  # symbol -> (timestamp, result)

    async def get_basis(self, symbol: str) -> Optional[BasisResult]:
        """Get spot-futures basis for a symbol (e.g. 'BTC/USDT').

        Returns None if data unavailable or exchange not configured.
        """
        # Check cache
        cached = self._cache.get(symbol)
        if cached and (time.monotonic() - cached[0]) < self._ttl:
            return cached[1]

        if self._exchange_factory is None:
            return None

        try:
            exchange = self._exchange_factory()

            # Fetch spot ticker
            spot_ticker = await exchange.fetch_ticker(symbol)
            spot_price = spot_ticker.get("last", 0)

            # Fetch perpetual swap ticker (symbol:USDT convention in ccxt)
            swap_symbol = symbol + ":USDT" if ":USDT" not in symbol else symbol
            try:
                swap_ticker = await exchange.fetch_ticker(swap_symbol)
                futures_price = swap_ticker.get("last", 0)
            except Exception:
                # Some pairs don't have perp contracts
                return None

            if spot_price <= 0 or futures_price <= 0:
                return None

            basis_pct = (futures_price - spot_price) / spot_price * 100
            # Annualize (rough approximation for perpetual swaps)
            basis_ann = basis_pct * 365

            if basis_pct > 0.1:
                sentiment = "PREMIUM"
            elif basis_pct < -0.1:
                sentiment = "DISCOUNT"
            else:
                sentiment = "NEUTRAL"

            extreme = abs(basis_pct) > 0.5

            result = BasisResult(
                symbol=symbol,
                spot_price=round(spot_price, 6),
                futures_price=round(futures_price, 6),
                basis_pct=round(basis_pct, 4),
                basis_annualized_pct=round(basis_ann, 2),
                sentiment=sentiment,
                extreme=extreme,
            )
            self._cache[symbol] = (time.monotonic(), result)
            return result

        except Exception as exc:
            logger.warning(f"Basis fetch failed for {symbol}: {exc}")
            return None

    def get_cached(self, symbol: str) -> Optional[BasisResult]:
        """Get cached basis without async call (for synchronous contexts)."""
        cached = self._cache.get(symbol)
        if cached:
            return cached[1]
        return None
