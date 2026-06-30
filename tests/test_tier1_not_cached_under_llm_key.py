"""
Tier-1 rule theses are not cached under the LLM cache key (deep-audit low #42).

_llm_thesis builds a semantic cache key from coarse indicator buckets. Tier-1
(rule-engine) results used to be stored under that same key, so a later signal
whose buckets collided but classified tier 2/3 would hit the cache and be served
the cheap rule thesis as "LLM_CACHED" instead of running the LLM. The tier-1
branch no longer writes to the cache; the key stays LLM-only.
"""

import pytest

import bot.core.analyzer as az
from bot.core.analyzer import Analyzer
from bot.core.llm_cache import SemanticLLMCache
from bot.utils.models import MarketSignal

_SIG = MarketSignal(symbol="BTC/USDT", price=50000.0, change_pct_24h=1.0,
                    volume_usd_24h=1e8, momentum_score=0.2)
_IND = {"confluence": 0.8, "regime": "TREND_UP", "rsi": 62, "macd_histogram": 1.0,
        "adx": 30, "obv_trend": "rising", "fib_zone": "", "candle_patterns": {}}


def _analyzer():
    a = Analyzer()
    # Truthy stub: pass the `_llm is None` early rule fallback so we reach the
    # tier path. The tier-1 branch returns before the client is ever used.
    a._llm = object()
    return a


@pytest.fixture(autouse=True)
def _force_tier1_path(monkeypatch):
    # Reach the tier branch (skip adaptive-frequency early return) and force tier 1.
    monkeypatch.setattr(az.AdaptiveFrequency, "should_use_llm", staticmethod(lambda s, i: True))
    monkeypatch.setattr(az.TieredPipeline, "classify_tier", staticmethod(lambda i, s: 1))


class TestTier1NotCached:
    async def test_tier1_result_not_written_to_cache(self, monkeypatch):
        a = _analyzer()
        monkeypatch.setattr(a, "_rule_based_thesis",
                            lambda sig, ind: {"direction": "LONG", "confidence": 0.6,
                                              "reasoning": "rule", "_parsed": True})
        result = await a._llm_thesis(_SIG, _IND)
        assert result["source"] == "RULE_ENGINE_TIER1"

        # The LLM cache key for these inputs must remain empty — a tier-2/3 lookup
        # with the same buckets would otherwise be served this rule thesis.
        key = SemanticLLMCache.build_cache_key(_SIG.symbol, _IND, scope="")
        assert a._llm_cache.get(key) is None

    async def test_repeat_tier1_call_is_not_served_as_cached(self, monkeypatch):
        a = _analyzer()
        monkeypatch.setattr(a, "_rule_based_thesis",
                            lambda sig, ind: {"direction": "LONG", "confidence": 0.6,
                                              "reasoning": "rule", "_parsed": True})
        first = await a._llm_thesis(_SIG, _IND)
        second = await a._llm_thesis(_SIG, _IND)
        # Both freshly computed from the rule engine; never a cached LLM hit.
        assert first["source"] == "RULE_ENGINE_TIER1"
        assert second["source"] == "RULE_ENGINE_TIER1"
        assert "_CACHED" not in second["source"]
