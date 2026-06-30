"""
Scoped semantic-LLM-cache key (deep-audit medium: cache leak across models).

The semantic cache keys on bucketed market conditions only, not on which model
answers. The answering model depends on the pipeline tier (rule/scan/thesis),
the admin/basic boundary, a user's BYOK key, and their premium tier — so under a
single namespace an admin/premium/BYOK thesis (or a tier-1 rule result) is served
to a basic user with the same buckets, and vice-versa.

When CONFIG.analyzer.llm_cache_scoped_key is ON, the key is salted with that
routing identity. Default OFF keeps the key byte-identical to the legacy key.
The scope can only make the key MORE specific, so it is strictly safe-direction
(it can avoid a wrong reuse, never create one).
"""

import hashlib
from types import SimpleNamespace

import bot.core.analyzer as analyzer_mod
from bot.core.analyzer import Analyzer
from bot.core.llm_cache import SemanticLLMCache

_IND = {"regime": "TREND_UP", "confluence": 0.5, "rsi": 50,
        "macd_histogram": 0.2, "adx": 10, "plus_di": 5, "minus_di": 5}


def _legacy_hash(symbol, indicators):
    # Mirror build_cache_key's raw-key construction with no scope.
    conf = round(indicators["confluence"], 1)
    rsi = indicators["rsi"]
    rsi_zone = "oversold" if rsi < 35 else "overbought" if rsi > 65 else "neutral"
    adx = indicators["adx"]
    adx_bucket = "low" if adx < 20 else "medium" if adx <= 30 else "high"
    macd_dir = "pos" if indicators["macd_histogram"] >= 0 else "neg"
    raw = f"{symbol}|{indicators['regime']}|{conf}|{rsi_zone}|{macd_dir}|{adx_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()


class TestBuildCacheKey:
    def test_no_scope_is_legacy_identical(self):
        # Default (scope="") must reproduce the pre-fix key byte-for-byte.
        assert (SemanticLLMCache.build_cache_key("BTC/USDT", _IND)
                == _legacy_hash("BTC/USDT", _IND))

    def test_empty_scope_equals_omitted_scope(self):
        assert (SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="")
                == SemanticLLMCache.build_cache_key("BTC/USDT", _IND))

    def test_scope_changes_key(self):
        base = SemanticLLMCache.build_cache_key("BTC/USDT", _IND)
        scoped = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t3|admin")
        assert scoped != base

    def test_same_scope_same_key(self):
        a = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t2|user")
        b = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t2|user")
        assert a == b

    def test_distinct_scopes_distinct_keys(self):
        k_admin = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t2|admin")
        k_user = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t2|user")
        assert k_admin != k_user


class TestCachePartitioning:
    def test_scopes_do_not_cross_serve(self):
        cache = SemanticLLMCache()
        k_admin = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t3|admin")
        k_user = SemanticLLMCache.build_cache_key("BTC/USDT", _IND, scope="t2|user")
        cache.put(k_admin, {"thesis": "premium"}, "BTC/USDT")
        # A basic-user lookup (different scope) must MISS, not get the admin thesis.
        assert cache.get(k_user) is None
        assert cache.get(k_admin) == {"thesis": "premium"}


def _scope(is_admin=False, user_id=None, user_tier=None,
           byok=False, tiers=False):
    """Call the unbound scope helper (it ignores self) under patched flags."""
    sig = SimpleNamespace(volume_spike=False, change_pct_24h=0.0)
    saved = analyzer_mod.CONFIG
    analyzer_mod.CONFIG = SimpleNamespace(analyzer=SimpleNamespace(
        per_user_llm_enabled=byok, per_user_llm_tiers_enabled=tiers))
    try:
        return Analyzer._llm_cache_scope(
            object(), sig, _IND, is_admin, user_id, user_tier)
    finally:
        analyzer_mod.CONFIG = saved


class TestScopeSalt:
    def test_tier2_market_is_stable_component(self):
        # _IND (rsi 50, adx 10, conf 0.5, balanced DI, no spike) → tier 2.
        assert _scope().startswith("t2|")

    def test_admin_vs_user_differ(self):
        assert _scope(is_admin=True) != _scope(is_admin=False)
        assert "admin" in _scope(is_admin=True)
        assert "user" in _scope(is_admin=False)

    def test_byok_isolated_only_when_enabled(self):
        # Flag OFF: user_id ignored → no byok namespace.
        assert "byok" not in _scope(user_id=42, byok=False)
        # Flag ON: each user_id is isolated.
        assert "byok:42" in _scope(user_id=42, byok=True)
        assert _scope(user_id=42, byok=True) != _scope(user_id=99, byok=True)

    def test_premium_tier_isolated_only_when_enabled(self):
        assert "tier" not in _scope(user_tier="elite", tiers=False)
        assert "tier:elite" in _scope(user_tier="elite", tiers=True)
        assert _scope(user_tier="elite", tiers=True) != _scope(user_tier="pro", tiers=True)

    def test_none_identity_is_coarse(self):
        # No user → just tier + admin/user, no byok/tier parts.
        s = _scope(is_admin=False, user_id=None, user_tier=None, byok=True, tiers=True)
        assert "byok" not in s and "tier:" not in s


class TestDefaultFlagOff:
    def test_scoped_key_defaults_off(self):
        from bot.config import AnalyzerConfig
        assert AnalyzerConfig().llm_cache_scoped_key is False
