"""
Tier-based operator LLM routing (LLM Optimization Plan P2).

When PER_USER_LLM_TIERS_ENABLED is ON, a user's TIER (admin/elite/pro) maps to
operator-funded LLM quality for their hand-run /analyze; basic/free/unknown keep
the default routing (never downgraded). A user's own BYOK key takes precedence.
Tests cover the routing-table resolver and the gated _maybe_tier_client helper.
"""

from unittest.mock import patch

from bot.core.analyzer import Analyzer
from bot.llm.provider import (
    ADMIN_TIER_ROUTING,
    ELITE_TIER_ROUTING,
    PRO_TIER_ROUTING,
    routing_for_user_tier,
)


class TestRoutingForUserTier:
    def test_known_tiers_map_to_tables(self):
        assert routing_for_user_tier("admin") is ADMIN_TIER_ROUTING
        assert routing_for_user_tier("elite") is ELITE_TIER_ROUTING
        assert routing_for_user_tier("pro") is PRO_TIER_ROUTING

    def test_case_insensitive(self):
        assert routing_for_user_tier("Elite") is ELITE_TIER_ROUTING

    def test_basic_free_unknown_none(self):
        assert routing_for_user_tier("basic") is None
        assert routing_for_user_tier("free") is None
        assert routing_for_user_tier("nonsense") is None
        assert routing_for_user_tier(None) is None
        assert routing_for_user_tier("") is None


class TestMaybeTierClient:
    def _analyzer(self):
        a = Analyzer.__new__(Analyzer)
        a._llm_config = object()  # truthy; the real resolve is patched below
        return a

    def _cfg(self, enabled):
        p = patch("bot.core.analyzer.CONFIG")
        m = p.start()
        m.analyzer.per_user_llm_tiers_enabled = enabled
        return p

    def test_disabled_returns_none(self):
        p = self._cfg(enabled=False)
        try:
            assert self._analyzer()._maybe_tier_client("elite", True) == (None, None, None)
        finally:
            p.stop()

    def test_default_tier_returns_none(self):
        # basic/free/unknown → no premium table → no override.
        p = self._cfg(enabled=True)
        try:
            assert self._analyzer()._maybe_tier_client("basic", True) == (None, None, None)
        finally:
            p.stop()

    def test_premium_tier_routes(self):
        p = self._cfg(enabled=True)
        from types import SimpleNamespace
        fake_cfg = SimpleNamespace(provider=SimpleNamespace(value="anthropic"), model="claude-sonnet-5")
        sentinel = object()
        a = self._analyzer()
        try:
            with patch("bot.llm.provider.resolve_tier_config", return_value=fake_cfg), \
                 patch.object(Analyzer, "_build_client_for_config", staticmethod(lambda cfg: sentinel)):
                client, cfg, model = a._maybe_tier_client("elite", True)
            assert client is sentinel
            assert cfg is fake_cfg
            assert model == "claude-sonnet-5"
        finally:
            p.stop()

    def test_unbuildable_client_falls_back(self):
        p = self._cfg(enabled=True)
        from types import SimpleNamespace
        fake_cfg = SimpleNamespace(provider=SimpleNamespace(value="gemini"), model="gemini-2.5-flash")
        a = self._analyzer()
        try:
            with patch("bot.llm.provider.resolve_tier_config", return_value=fake_cfg), \
                 patch.object(Analyzer, "_build_client_for_config", staticmethod(lambda cfg: None)):
                assert a._maybe_tier_client("pro", False) == (None, None, None)
        finally:
            p.stop()

    def test_no_llm_config_returns_none(self):
        p = self._cfg(enabled=True)
        a = Analyzer.__new__(Analyzer)
        a._llm_config = None  # operator has no LLM configured at all
        try:
            assert a._maybe_tier_client("elite", True) == (None, None, None)
        finally:
            p.stop()
