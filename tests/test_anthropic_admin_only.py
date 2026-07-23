"""
Anthropic/Claude is reserved for admin use only — every other user gets the
best cheap/free alternative instead.

Real request: the operator wants non-admin users routed to the cheapest good
LLM option, with his own Anthropic/Claude key reserved for himself (admin).
Before this fix, DEFAULT_TIER_ROUTING and ELITE_TIER_ROUTING both routed
THESIS (and, for elite, LEARNING/CHAT) to Anthropic using the SAME
ANTHROPIC_API_KEY env var the admin table uses — so non-admin trade-thesis
generation was already quietly spending the operator's Claude budget. Two
separate hardcoded fallback chains (telegram_handler.py's _llm_chat and
analyzer.py's _try_llm_fallback) also unconditionally tried Anthropic
whenever ANTHROPIC_API_KEY was set, regardless of caller identity.

resolve_tier_config() now hard-refuses to resolve to Anthropic for any
non-admin caller at every one of its 3 resolution steps (explicit env tier
override, routing-table lookup, final primary-config fallback), and both
hardcoded fallback chains only include Anthropic when is_admin is True.
"""

import asyncio
from unittest.mock import AsyncMock


from bot.llm.provider import (
    ADMIN_TIER_ROUTING,
    DEFAULT_TIER_ROUTING,
    ELITE_TIER_ROUTING,
    PRO_TIER_ROUTING,
    LLMConfig,
    LLMProvider,
    LLMTier,
    resolve_tier_config,
)


def _run(coro):
    return asyncio.run(coro)


class TestNonAdminRoutingTablesExcludeAnthropic:
    """The routing tables themselves should never route a non-admin tier to
    Anthropic — resolve_tier_config's guard is defense-in-depth, not the
    only line of defense."""

    def test_default_routing_has_no_anthropic(self):
        for tier, route in DEFAULT_TIER_ROUTING.items():
            assert route["provider"] != LLMProvider.ANTHROPIC, (
                f"DEFAULT_TIER_ROUTING[{tier}] routes non-admin users to Anthropic")

    def test_pro_routing_has_no_anthropic(self):
        for tier, route in PRO_TIER_ROUTING.items():
            assert route["provider"] != LLMProvider.ANTHROPIC

    def test_elite_routing_has_no_anthropic(self):
        for tier, route in ELITE_TIER_ROUTING.items():
            assert route["provider"] != LLMProvider.ANTHROPIC

    def test_admin_routing_still_uses_anthropic(self):
        """Confirms the guard didn't accidentally neuter the admin path too."""
        for tier, route in ADMIN_TIER_ROUTING.items():
            assert route["provider"] == LLMProvider.ANTHROPIC


class TestResolveTierConfigHardGuard:
    def test_non_admin_never_gets_anthropic_from_routing_table(self, monkeypatch):
        # Only an Anthropic key is available anywhere.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-key")
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
                  "OPENAI_API_KEY", "ALIBABA_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="")

        cfg = resolve_tier_config(LLMTier.THESIS, primary, is_admin=False)

        assert cfg.provider != LLMProvider.ANTHROPIC
        # No non-Anthropic key exists anywhere -> correctly unconfigured,
        # NOT silently handed the admin's Claude key.
        assert not cfg.is_configured()

    def test_non_admin_primary_config_fallback_never_anthropic(self, monkeypatch):
        """If the operator's global LLM_PROVIDER is itself Anthropic, a
        non-admin caller must not fall back to it either."""
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
                  "OPENAI_API_KEY", "ALIBABA_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        primary = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="claude-primary-key")

        cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=False)

        assert cfg.provider != LLMProvider.ANTHROPIC or not cfg.is_configured()
        assert not cfg.is_configured()

    def test_admin_primary_config_fallback_can_still_be_anthropic(self, monkeypatch):
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
                  "OPENAI_API_KEY", "ALIBABA_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        primary = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="claude-primary-key")

        cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)

        # The admin Anthropic route now resolves through key_health and
        # returns a fresh config (same key, catalog base_url) instead of
        # the primary object itself — assert semantics, not identity.
        assert cfg.provider == LLMProvider.ANTHROPIC
        assert cfg.api_key == "claude-primary-key"

    def test_non_admin_gemini_alternative_is_still_used(self, monkeypatch):
        """The guard only blocks Anthropic -- a cheap alternative in the
        routing table still resolves normally for non-admin."""
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        for k in ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="")

        cfg = resolve_tier_config(LLMTier.THESIS, primary, is_admin=False)

        assert cfg.provider == LLMProvider.GEMINI
        assert cfg.api_key == "gemini-key"


class TestChatFallbackChainAdminGate:
    def _stub(self):
        from types import SimpleNamespace
        from bot.core.cost import CostTracker
        return SimpleNamespace(
            engine=SimpleNamespace(cost=CostTracker()),
            conversations=SimpleNamespace(get_recent_as_llm_messages=lambda *a, **kw: []),
            _build_chat_system_prompt=lambda user_id, user_name="": "sys",
        )

    def test_non_admin_chat_never_falls_back_to_anthropic(self, monkeypatch):
        import bot.skills.telegram_handler as th_mod
        from bot.skills.telegram_handler import TelegramHandler as H

        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-key")
        for k in ("GEMINI_API_KEY", "ALIBABA_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(th_mod, "resolve_tier_config",
                             lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
        create_client_mock = AsyncMock()
        monkeypatch.setattr(th_mod, "create_llm_client", create_client_mock)

        answer = _run(H._llm_chat(self._stub(), "hello", is_admin=False))

        # With only the admin-only Anthropic key available, a non-admin caller
        # gets the friendly public fallback — never an Anthropic answer, and
        # never a leaky internal-config hint (F-15). The security property is
        # that the admin-only provider was never reached.
        low = answer.lower()
        assert "runeclaw" in low                       # the public fallback text
        assert "no llm configured" not in low          # no raw internal error
        assert "setllm" not in low and ".env" not in low and "api_key" not in low
        create_client_mock.assert_not_called()         # admin-only provider untouched

    def test_admin_chat_can_use_anthropic_fallback(self, monkeypatch):
        import bot.skills.telegram_handler as th_mod
        from bot.skills.telegram_handler import TelegramHandler as H

        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-key")
        for k in ("GEMINI_API_KEY", "ALIBABA_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(th_mod, "resolve_tier_config",
                             lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
        monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
        monkeypatch.setattr(th_mod, "llm_complete", AsyncMock(return_value="claude says hi"))

        answer = _run(H._llm_chat(self._stub(), "hello", is_admin=True))

        assert answer == "claude says hi"


class TestAnalyzerFallbackChainAdminGate:
    def _analyzer(self):
        from bot.core.analyzer import Analyzer
        a = Analyzer.__new__(Analyzer)
        a._llm_config = None
        a._llm_calls_today = 0
        a._cost = None
        return a

    def _signal(self):
        from types import SimpleNamespace
        return SimpleNamespace(symbol="BTC/USDT", volume_spike=False, change_pct_24h=0.0)

    def test_non_admin_fallback_never_tries_anthropic(self, monkeypatch):
        import bot.core.analyzer as analyzer_mod

        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-key")
        for k in ("ALIBABA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        create_client_mock = AsyncMock()
        monkeypatch.setattr(analyzer_mod, "create_llm_client", create_client_mock)

        result = _run(self._analyzer()._try_llm_fallback(
            "prompt", self._signal(), use_full_model=False, is_admin=False))

        assert result is None
        create_client_mock.assert_not_called()

    def test_admin_fallback_can_use_anthropic(self, monkeypatch):
        import bot.core.analyzer as analyzer_mod

        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-key")
        for k in ("ALIBABA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        class _FakeResponse:
            content = [type("Block", (), {"type": "text", "text":
                '{"direction":"LONG","confidence":0.7,"reasoning":"ok"}'})()]
            usage = None

        class _FakeMessages:
            async def create(self, **kwargs):
                return _FakeResponse()

        class _FakeClient:
            messages = _FakeMessages()

        monkeypatch.setattr(analyzer_mod, "create_llm_client", lambda cfg: _FakeClient())

        result = _run(self._analyzer()._try_llm_fallback(
            "prompt", self._signal(), use_full_model=False, is_admin=True))

        assert result is not None
        assert result.get("direction") == "LONG"
