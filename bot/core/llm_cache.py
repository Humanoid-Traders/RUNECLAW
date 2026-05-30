"""
RUNECLAW Semantic LLM Cache -- TTL-based response caching for similar market conditions.

Key idea: two signals with the same symbol, regime, confluence bucket, and RSI zone
are likely to produce the same LLM thesis.  Cache the response and skip the API call.

Safety guarantees:
  - TTL is short (default 300s / 5 min) -- stale data cannot persist
  - Cache keys are bucketed, not exact -- prevents overfitting to noise
  - Cache is in-memory only -- no persistence across restarts
  - Max size with LRU eviction -- bounded memory
  - Cache stats are exposed for cost tracking and auditability
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from bot.utils.logger import audit, system_log


@dataclass
class CacheEntry:
    """Single cached LLM response."""
    key: str
    response: dict
    created_at: float
    ttl: float
    symbol: str

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.created_at > self.ttl


@dataclass
class CacheStats:
    """Lifetime cache performance metrics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    estimated_tokens_saved: int = 0
    estimated_cost_saved_usd: float = 0.0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total > 0 else 0.0

    @property
    def total_lookups(self) -> int:
        return self.hits + self.misses


class SemanticLLMCache:
    """TTL-based LLM response cache with semantic bucketing.

    Cache key components (bucketed to reduce cardinality):
      - symbol (exact)
      - regime (exact: TREND_UP, TREND_DOWN, RANGE, CHOP)
      - confluence bucket (rounded to 0.1)
      - RSI zone (oversold/neutral/overbought)
      - MACD direction (positive/negative)
      - ADX bucket (low/medium/high)

    This means two BTC/USDT signals with the same regime, similar confluence,
    same RSI zone, and same MACD direction will share a cached response.
    """

    # Average tokens per LLM call (prompt + completion) for savings estimation
    AVG_TOKENS_PER_CALL = 800
    AVG_COST_PER_CALL = 0.003  # USD

    def __init__(self, max_size: int = 200, default_ttl: float = 300.0) -> None:
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._stats = CacheStats()

    @staticmethod
    def build_cache_key(symbol: str, indicators: dict) -> str:
        """Build a bucketed cache key from signal + indicators.

        Bucketing strategy:
          - Confluence: round to nearest 0.1
          - RSI: 3 zones (oversold < 35, overbought > 65, neutral)
          - ADX: 3 buckets (low < 20, medium 20-30, high > 30)
          - MACD: sign only (positive/negative)
        """
        regime = indicators.get("regime", "UNKNOWN")
        confluence = indicators.get("confluence", 0.5)
        rsi = indicators.get("rsi", 50)
        macd_hist = indicators.get("macd_histogram", 0)
        adx = indicators.get("adx", 0)

        # Bucket confluence to 0.1 increments
        conf_bucket = round(confluence, 1)

        # RSI zones
        if rsi < 35:
            rsi_zone = "oversold"
        elif rsi > 65:
            rsi_zone = "overbought"
        else:
            rsi_zone = "neutral"

        # ADX buckets
        if adx < 20:
            adx_bucket = "low"
        elif adx <= 30:
            adx_bucket = "medium"
        else:
            adx_bucket = "high"

        # MACD sign
        macd_dir = "pos" if macd_hist >= 0 else "neg"

        raw_key = f"{symbol}|{regime}|{conf_bucket}|{rsi_zone}|{macd_dir}|{adx_bucket}"
        return hashlib.sha256(raw_key.encode()).hexdigest()[:16]

    def get(self, key: str) -> Optional[dict]:
        """Look up a cached response. Returns None on miss or expiry."""
        entry = self._cache.get(key)
        if entry is None:
            self._stats.misses += 1
            return None

        if entry.is_expired:
            self._cache.pop(key, None)
            self._stats.expirations += 1
            self._stats.misses += 1
            return None

        # Cache hit -- move to end (LRU)
        self._cache.move_to_end(key)
        self._stats.hits += 1
        self._stats.estimated_tokens_saved += self.AVG_TOKENS_PER_CALL
        self._stats.estimated_cost_saved_usd += self.AVG_COST_PER_CALL

        audit(
            system_log,
            f"LLM cache HIT: {entry.symbol}",
            action="llm_cache",
            result="HIT",
            data={"symbol": entry.symbol, "key": key},
        )
        return entry.response

    def put(self, key: str, response: dict, symbol: str, ttl: Optional[float] = None) -> None:
        """Store an LLM response in the cache."""
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = CacheEntry(
                key=key,
                response=response,
                created_at=time.monotonic(),
                ttl=ttl or self._default_ttl,
                symbol=symbol,
            )
            return

        # Evict LRU if at capacity
        while len(self._cache) >= self._max_size:
            evicted_key, _ = self._cache.popitem(last=False)
            self._stats.evictions += 1

        self._cache[key] = CacheEntry(
            key=key,
            response=response,
            created_at=time.monotonic(),
            ttl=ttl or self._default_ttl,
            symbol=symbol,
        )

    def clear(self) -> None:
        """Clear all cached entries (stats preserved)."""
        self._cache.clear()

    def purge_expired(self) -> int:
        """Remove all expired entries. Returns count of purged entries."""
        expired_keys = [k for k, v in self._cache.items() if v.is_expired]
        for k in expired_keys:
            del self._cache[k]
            self._stats.expirations += 1
        return len(expired_keys)

    @property
    def stats(self) -> CacheStats:
        return self._stats

    @property
    def size(self) -> int:
        return len(self._cache)

    def snapshot(self) -> dict:
        """Full cache state for dashboard / debugging."""
        return {
            "size": self.size,
            "max_size": self._max_size,
            "default_ttl": self._default_ttl,
            "hit_rate": self._stats.hit_rate,
            "hits": self._stats.hits,
            "misses": self._stats.misses,
            "evictions": self._stats.evictions,
            "expirations": self._stats.expirations,
            "estimated_tokens_saved": self._stats.estimated_tokens_saved,
            "estimated_cost_saved_usd": round(self._stats.estimated_cost_saved_usd, 4),
        }
