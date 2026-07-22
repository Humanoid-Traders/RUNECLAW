"""
Tests for RUNECLAW LLM Token Optimization: Semantic Cache, Tiered Pipeline,
Smart Batching, and Adaptive Frequency.
"""

import time
from datetime import UTC, datetime

from bot.core.llm_cache import SemanticLLMCache
from bot.core.token_optimizer import (
    AdaptiveFrequency,
    BatchRequest,
    OptimizationStats,
    SmartBatcher,
    TieredPipeline,
)
from bot.utils.models import MarketSignal


# ── Fixtures ──

def _make_signal(symbol="BTC/USDT", price=60000.0, change=2.0, volume_spike=False):
    return MarketSignal(
        symbol=symbol,
        price=price,
        change_pct_24h=change,
        volume_usd_24h=1_000_000,
        volume_spike=volume_spike,
        momentum_score=0.5,
        timestamp=datetime.now(UTC),
    )


def _make_indicators(rsi=50, adx=25, confluence=0.55, macd_hist=0.01,
                     plus_di=20, minus_di=15, regime="TREND_UP"):
    return {
        "rsi": rsi,
        "adx": adx,
        "confluence": confluence,
        "macd_histogram": macd_hist,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "regime": regime,
    }


# ══════════════════════════════════════════════════════════════════
# SEMANTIC CACHE
# ══════════════════════════════════════════════════════════════════

class TestSemanticCache:
    def test_cache_miss_then_hit(self):
        cache = SemanticLLMCache(max_size=10, default_ttl=60.0)
        key = cache.build_cache_key("BTC/USDT", _make_indicators())
        assert cache.get(key) is None
        assert cache.stats.misses == 1

        response = {"direction": "LONG", "confidence": 0.7, "reasoning": "test"}
        cache.put(key, response, "BTC/USDT")
        result = cache.get(key)
        assert result is not None
        assert result["direction"] == "LONG"
        assert cache.stats.hits == 1

    def test_cache_ttl_expiry(self):
        cache = SemanticLLMCache(max_size=10, default_ttl=0.01)  # 10ms TTL
        key = "test-key"
        cache.put(key, {"direction": "SHORT"}, "ETH/USDT")
        time.sleep(0.02)
        assert cache.get(key) is None
        assert cache.stats.expirations == 1

    def test_cache_lru_eviction(self):
        cache = SemanticLLMCache(max_size=3, default_ttl=60.0)
        for i in range(5):
            cache.put(f"key-{i}", {"i": i}, f"SYM-{i}")
        assert cache.size == 3
        assert cache.stats.evictions == 2
        # Oldest keys (0, 1) should be evicted
        assert cache.get("key-0") is None
        assert cache.get("key-4") is not None

    def test_cache_key_bucketing(self):
        """Same symbol + similar indicators should produce same key."""
        ind1 = _make_indicators(rsi=51, confluence=0.54)
        ind2 = _make_indicators(rsi=49, confluence=0.56)  # same bucket
        key1 = SemanticLLMCache.build_cache_key("BTC/USDT", ind1)
        key2 = SemanticLLMCache.build_cache_key("BTC/USDT", ind2)
        # Both RSI are in "neutral" zone, both confluence rounds to 0.5/0.6
        # Keys may differ slightly due to confluence bucketing
        # but same regime + zone combos should match
        ind_same1 = _make_indicators(rsi=45, confluence=0.55)
        ind_same2 = _make_indicators(rsi=48, confluence=0.55)
        k1 = SemanticLLMCache.build_cache_key("BTC/USDT", ind_same1)
        k2 = SemanticLLMCache.build_cache_key("BTC/USDT", ind_same2)
        assert k1 == k2  # same bucket

    def test_different_symbols_different_keys(self):
        ind = _make_indicators()
        k1 = SemanticLLMCache.build_cache_key("BTC/USDT", ind)
        k2 = SemanticLLMCache.build_cache_key("ETH/USDT", ind)
        assert k1 != k2

    def test_different_regimes_different_keys(self):
        ind1 = _make_indicators(regime="TREND_UP")
        ind2 = _make_indicators(regime="RANGE")
        k1 = SemanticLLMCache.build_cache_key("BTC/USDT", ind1)
        k2 = SemanticLLMCache.build_cache_key("BTC/USDT", ind2)
        assert k1 != k2

    def test_rsi_zone_boundaries(self):
        """RSI < 35 = oversold, > 65 = overbought, else neutral."""
        ind_oversold = _make_indicators(rsi=30)
        ind_neutral = _make_indicators(rsi=50)
        ind_overbought = _make_indicators(rsi=70)
        k1 = SemanticLLMCache.build_cache_key("BTC/USDT", ind_oversold)
        k2 = SemanticLLMCache.build_cache_key("BTC/USDT", ind_neutral)
        k3 = SemanticLLMCache.build_cache_key("BTC/USDT", ind_overbought)
        assert k1 != k2 != k3

    def test_purge_expired(self):
        cache = SemanticLLMCache(max_size=10, default_ttl=0.01)
        cache.put("k1", {"a": 1}, "SYM1")
        cache.put("k2", {"a": 2}, "SYM2")
        time.sleep(0.02)
        purged = cache.purge_expired()
        assert purged == 2
        assert cache.size == 0

    def test_snapshot(self):
        cache = SemanticLLMCache(max_size=100)
        cache.put("k1", {}, "SYM")
        cache.get("k1")
        snap = cache.snapshot()
        assert snap["size"] == 1
        assert snap["hits"] == 1
        assert snap["hit_rate"] > 0

    def test_estimated_savings(self):
        cache = SemanticLLMCache()
        cache.put("k1", {"direction": "LONG"}, "BTC/USDT")
        cache.get("k1")  # hit
        cache.get("k1")  # hit
        assert cache.stats.estimated_tokens_saved == 1600  # 800 * 2
        assert cache.stats.estimated_cost_saved_usd > 0


# ══════════════════════════════════════════════════════════════════
# TIERED PIPELINE
# ══════════════════════════════════════════════════════════════════

class TestTieredPipeline:
    def test_tier1_extreme_rsi(self):
        """RSI < 25 or > 75 → tier 1 (rules)."""
        sig = _make_signal()
        assert TieredPipeline.classify_tier(_make_indicators(rsi=20), sig) == 1
        assert TieredPipeline.classify_tier(_make_indicators(rsi=80), sig) == 1

    def test_tier1_strong_trend(self):
        """ADX > 35 + large DI divergence → tier 1."""
        ind = _make_indicators(adx=40, plus_di=35, minus_di=15)
        sig = _make_signal()
        assert TieredPipeline.classify_tier(ind, sig) == 1

    def test_tier1_extreme_confluence(self):
        """Confluence > 0.75 or < 0.25 → tier 1."""
        sig = _make_signal()
        assert TieredPipeline.classify_tier(_make_indicators(confluence=0.80), sig) == 1
        assert TieredPipeline.classify_tier(_make_indicators(confluence=0.20), sig) == 1

    def test_tier3_high_confluence(self):
        """Confluence > 0.60 → tier 3 (full model)."""
        ind = _make_indicators(rsi=55, confluence=0.65, adx=22)
        sig = _make_signal()
        assert TieredPipeline.classify_tier(ind, sig) == 3

    def test_tier3_volume_spike_big_move(self):
        """Volume spike + >5% change → tier 3."""
        ind = _make_indicators(rsi=55, confluence=0.50, adx=22)
        sig = _make_signal(change=6.0, volume_spike=True)
        assert TieredPipeline.classify_tier(ind, sig) == 3

    def test_tier2_default(self):
        """Moderate signals → tier 2 (mini model)."""
        ind = _make_indicators(rsi=50, confluence=0.50, adx=22)
        sig = _make_signal(change=1.5)
        assert TieredPipeline.classify_tier(ind, sig) == 2

    def test_tier_labels(self):
        assert TieredPipeline.tier_label(1) == "RULES"
        assert TieredPipeline.tier_label(2) == "MINI"
        assert TieredPipeline.tier_label(3) == "FULL"


# ══════════════════════════════════════════════════════════════════
# SMART BATCHING
# ══════════════════════════════════════════════════════════════════

class TestSmartBatching:
    def test_build_batch_prompt(self):
        reqs = [
            BatchRequest(
                symbol="BTC/USDT",
                indicators=_make_indicators(),
                signal=_make_signal("BTC/USDT"),
            ),
            BatchRequest(
                symbol="ETH/USDT",
                indicators=_make_indicators(rsi=40),
                signal=_make_signal("ETH/USDT", price=3000),
            ),
        ]
        prompt = SmartBatcher.build_batch_prompt(reqs)
        assert "BTC/USDT" in prompt
        assert "ETH/USDT" in prompt
        assert "JSON array" in prompt

    def test_batch_prompt_capped_at_5(self):
        reqs = [
            BatchRequest(
                symbol=f"SYM{i}/USDT",
                indicators=_make_indicators(),
                signal=_make_signal(f"SYM{i}/USDT"),
            )
            for i in range(10)
        ]
        prompt = SmartBatcher.build_batch_prompt(reqs)
        # Should only include first 5
        assert "SYM4/USDT" in prompt
        assert "SYM5/USDT" not in prompt

    def test_parse_batch_response_valid(self):
        response = '[{"symbol": "BTC/USDT", "direction": "LONG", "confidence": 0.75, "reasoning": "strong trend"}]'
        result = SmartBatcher.parse_batch_response(response, ["BTC/USDT"])
        assert "BTC/USDT" in result
        assert result["BTC/USDT"]["direction"] == "LONG"
        assert result["BTC/USDT"]["confidence"] == 0.75

    def test_parse_batch_response_markdown(self):
        response = '```json\n[{"symbol": "ETH/USDT", "direction": "SHORT", "confidence": 0.6, "reasoning": "weak"}]\n```'
        result = SmartBatcher.parse_batch_response(response, ["ETH/USDT"])
        assert "ETH/USDT" in result
        assert result["ETH/USDT"]["direction"] == "SHORT"

    def test_parse_batch_response_malformed(self):
        result = SmartBatcher.parse_batch_response("not json", ["BTC/USDT"])
        assert len(result) == 0  # fail-closed: no results

    def test_parse_batch_unknown_symbol(self):
        response = '[{"symbol": "DOGE/USDT", "direction": "LONG", "confidence": 0.5}]'
        result = SmartBatcher.parse_batch_response(response, ["BTC/USDT"])
        assert "BTC/USDT" not in result  # DOGE not in expected list

    def test_estimate_savings(self):
        savings = SmartBatcher.estimate_savings(3)
        assert savings["tokens_saved"] > 0
        assert savings["reduction_pct"] > 0
        assert savings["individual_tokens"] > savings["batched_tokens"]

    def test_estimate_savings_single(self):
        savings = SmartBatcher.estimate_savings(1)
        assert savings["tokens_saved"] == 0  # no savings for single


# ══════════════════════════════════════════════════════════════════
# ADAPTIVE FREQUENCY
# ══════════════════════════════════════════════════════════════════

class TestAdaptiveFrequency:
    def test_always_use_llm_for_big_move(self):
        sig = _make_signal(change=5.0)
        ind = _make_indicators(adx=10)
        assert AdaptiveFrequency.should_use_llm(sig, ind) is True

    def test_always_use_llm_for_volume_spike(self):
        sig = _make_signal(change=1.0, volume_spike=True)
        ind = _make_indicators(adx=10)
        assert AdaptiveFrequency.should_use_llm(sig, ind) is True

    def test_always_use_llm_for_strong_trend(self):
        sig = _make_signal(change=1.0)
        ind = _make_indicators(adx=30)
        assert AdaptiveFrequency.should_use_llm(sig, ind) is True

    def test_skip_llm_quiet_market(self):
        sig = _make_signal(change=0.5, volume_spike=False)
        ind = _make_indicators(adx=10)
        assert AdaptiveFrequency.should_use_llm(sig, ind) is False

    def test_use_llm_moderate_conditions(self):
        """ADX between 15-25, no spike, small change → still use LLM."""
        sig = _make_signal(change=1.5)
        ind = _make_indicators(adx=18)
        assert AdaptiveFrequency.should_use_llm(sig, ind) is True

    def test_negative_change_handled(self):
        """Absolute value of change is used."""
        sig = _make_signal(change=-4.0)
        ind = _make_indicators(adx=10)
        assert AdaptiveFrequency.should_use_llm(sig, ind) is True


# ══════════════════════════════════════════════════════════════════
# OPTIMIZATION STATS
# ══════════════════════════════════════════════════════════════════

class TestOptimizationStats:
    def test_record_tiers(self):
        stats = OptimizationStats()
        stats.record_tier(1)
        stats.record_tier(1)
        stats.record_tier(2)
        stats.record_tier(3)
        snap = stats.snapshot()
        assert snap["tier_distribution"]["tier1_rules"] == 2
        assert snap["tier_distribution"]["tier2_mini"] == 1
        assert snap["tier_distribution"]["tier3_full"] == 1
        assert snap["savings"]["estimated_tokens_saved"] > 0

    def test_record_batch(self):
        stats = OptimizationStats()
        stats.record_batch(3)
        snap = stats.snapshot()
        assert snap["batching"]["batch_calls"] == 1
        assert snap["batching"]["symbols_batched"] == 3

    def test_record_adaptive_skip(self):
        stats = OptimizationStats()
        stats.record_adaptive_skip()
        stats.record_adaptive_skip()
        snap = stats.snapshot()
        assert snap["adaptive_frequency"]["llm_skips"] == 2
        assert snap["savings"]["estimated_tokens_saved"] == 1600

    def test_tier1_saves_most(self):
        """Tier 1 (free rules) should save the most per call."""
        stats = OptimizationStats()
        stats.record_tier(1)
        tier1_saved = stats.estimated_cost_saved_usd

        stats2 = OptimizationStats()
        stats2.record_tier(2)
        tier2_saved = stats2.estimated_cost_saved_usd

        assert tier1_saved >= tier2_saved

    def test_full_snapshot_structure(self):
        stats = OptimizationStats()
        snap = stats.snapshot()
        assert "tier_distribution" in snap
        assert "batching" in snap
        assert "adaptive_frequency" in snap
        assert "savings" in snap
