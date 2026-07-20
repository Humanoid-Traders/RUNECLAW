"""AI-1 ULTRA mode — Fable 5 admin routing + Fable-safe call path.

Covers the three failure shapes that matter:
  1. routing: ULTRA resolves admin thesis/learning to claude-fable-5 with the
     effort dial, and the non-admin Anthropic guard survives the toggle;
  2. call path: the `thinking` parameter is NEVER sent to Fable/Mythos
     (rejected with 400 — same outage class as the temperature deprecation),
     while Opus keeps adaptive thinking;
  3. refusal: stop_reason="refusal" raises instead of returning "" (an empty
     string would be blended into the decision picture as a real answer).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.llm.provider import (
    _THINKING_ALWAYS_ON_RE,
    LLMConfig,
    LLMProvider,
    LLMTier,
    ULTRA_TIER_ROUTING,
    is_ultra_mode,
    llm_complete,
    resolve_tier_config,
    set_ultra_mode,
)

_TEST_KEY = "sk-ant-ultra-test-key-000000000000000000"


@pytest.fixture(autouse=True)
def _reset_ultra():
    yield
    set_ultra_mode(False)


@pytest.fixture
def anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", _TEST_KEY)
    yield


def _primary():
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key=_TEST_KEY,
                     model="claude-sonnet-5")


class TestUltraRouting:
    def test_ultra_table_is_anthropic_only_with_valid_efforts(self):
        for tier, route in ULTRA_TIER_ROUTING.items():
            assert route["provider"] == LLMProvider.ANTHROPIC, tier
            assert route.get("effort", "") in ("", "low", "medium", "high", "max")
        assert ULTRA_TIER_ROUTING[LLMTier.THESIS]["model"] == "claude-fable-5"
        assert ULTRA_TIER_ROUTING[LLMTier.LEARNING]["model"] == "claude-fable-5"

    def test_ultra_on_routes_admin_thesis_to_fable_with_effort(self, anthropic_env):
        ok, _ = set_ultra_mode(True, _primary())
        assert ok
        cfg = resolve_tier_config(LLMTier.THESIS, _primary(), is_admin=True)
        assert cfg.provider == LLMProvider.ANTHROPIC
        assert cfg.model == "claude-fable-5"
        assert cfg.effort == "high"
        learn = resolve_tier_config(LLMTier.LEARNING, _primary(), is_admin=True)
        assert learn.model == "claude-fable-5"
        assert learn.effort == "max"

    def test_ultra_scan_and_chat_stay_on_sonnet(self, anthropic_env):
        set_ultra_mode(True, _primary())
        for tier in (LLMTier.SCAN, LLMTier.CHAT):
            cfg = resolve_tier_config(tier, _primary(), is_admin=True)
            assert cfg.model == "claude-sonnet-5", tier
            assert cfg.effort == ""

    def test_ultra_off_keeps_existing_admin_routing(self, anthropic_env):
        set_ultra_mode(False)
        cfg = resolve_tier_config(LLMTier.THESIS, _primary(), is_admin=True)
        assert cfg.model == "claude-sonnet-5"
        assert cfg.effort == ""

    def test_non_admin_never_gets_fable_even_with_ultra_on(self, anthropic_env,
                                                           monkeypatch):
        # No cheap-provider keys anywhere: the only key in the world is the
        # operator's Anthropic key. A non-admin caller must still not get it.
        for env in ("GEMINI_API_KEY", "GROQ_API_KEY", "ALIBABA_API_KEY",
                    "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                    "LLM_TIER_THESIS_PROVIDER", "LLM_TIER_THESIS_KEY"):
            monkeypatch.delenv(env, raising=False)
        set_ultra_mode(True, _primary())
        cfg = resolve_tier_config(LLMTier.THESIS, _primary(), is_admin=False)
        assert cfg.model != "claude-fable-5"
        if cfg.provider == LLMProvider.ANTHROPIC:
            assert cfg.api_key == ""  # unconfigured stand-in, not the admin key


class TestUltraToggle:
    def test_enable_without_any_anthropic_key_fails_loud(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        no_key_primary = LLMConfig(provider=LLMProvider.GEMINI, api_key="g-key")
        ok, detail = set_ultra_mode(True, no_key_primary)
        assert not ok
        assert "key" in detail.lower()
        assert not is_ultra_mode()

    def test_enable_with_key_then_disable(self, anthropic_env):
        ok, detail = set_ultra_mode(True, _primary())
        assert ok and is_ultra_mode()
        assert "fable" in detail.lower()
        ok, _ = set_ultra_mode(False)
        assert ok and not is_ultra_mode()


class TestFableFamilyRegex:
    def test_matches_fable_and_mythos(self):
        for m in ("claude-fable-5", "claude-mythos-5", "claude-fable-5-20260101"):
            assert _THINKING_ALWAYS_ON_RE.match(m), m

    def test_does_not_match_other_claude_models(self):
        for m in ("claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5",
                  "my-claude-fable-5", "claude-fable-50"):
            assert not _THINKING_ALWAYS_ON_RE.match(m), m


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _ok_response():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="answer")],
        stop_reason="end_turn",
    )


def _complete(client, config):
    return asyncio.run(llm_complete(client, config, "sys", "user"))


class TestFableSafeCallPath:
    def test_fable_gets_effort_and_never_thinking(self):
        client = _FakeClient(_ok_response())
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                        model="claude-fable-5", effort="high")
        assert _complete(client, cfg) == "answer"
        sent = client.messages.kwargs
        assert "thinking" not in sent
        assert sent["output_config"] == {"effort": "high"}

    def test_fable_without_effort_sends_neither(self):
        client = _FakeClient(_ok_response())
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                        model="claude-fable-5")
        _complete(client, cfg)
        sent = client.messages.kwargs
        assert "thinking" not in sent and "output_config" not in sent

    def test_opus_keeps_adaptive_thinking(self):
        client = _FakeClient(_ok_response())
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                        model="claude-opus-4-8")
        _complete(client, cfg)
        sent = client.messages.kwargs
        assert sent["thinking"] == {"type": "adaptive"}
        assert "output_config" not in sent

    def test_sonnet_sends_neither(self):
        client = _FakeClient(_ok_response())
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                        model="claude-sonnet-5")
        _complete(client, cfg)
        sent = client.messages.kwargs
        assert "thinking" not in sent and "output_config" not in sent

    def test_refusal_raises_instead_of_returning_empty(self):
        refusal = SimpleNamespace(content=[], stop_reason="refusal")
        client = _FakeClient(refusal)
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                        model="claude-fable-5", effort="high")
        with pytest.raises(RuntimeError, match="refusal"):
            _complete(client, cfg)
