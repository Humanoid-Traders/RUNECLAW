"""
The in-house RUNECLAW model is a first-class, self-hosted provider.

The operator is fine-tuning Llama 3.1 8B as "runeclaw-v6" and will serve it
from a local OpenAI-compatible runtime (vLLM/Ollama). These tests pin the
integration contract: the provider exists, needs no API key (local serving),
resolves its endpoint/model from env, and — critically — CAN be tier-routed
via LLM_TIER_*_PROVIDER without a key. Before this, the tier-override path
required a non-empty key, so routing a tier to any keyless local provider was
silently ignored.
"""

from bot.llm.provider import (
    DEFAULT_TIER_ROUTING,
    LLMConfig,
    LLMProvider,
    LLMTier,
    PROVIDER_CATALOG,
    resolve_tier_config,
)


def test_runeclaw_provider_exists_with_openai_compatible_sdk():
    assert LLMProvider("runeclaw") is LLMProvider.RUNECLAW
    entry = PROVIDER_CATALOG[LLMProvider.RUNECLAW]
    assert entry["sdk"] == "openai"          # any OpenAI-compatible runtime
    assert entry["base_url"]                 # env default resolves to something
    assert entry["default_model"]


def test_runeclaw_is_configured_without_key():
    # Local serving needs no key — like Ollama.
    cfg = LLMConfig(provider=LLMProvider.RUNECLAW, api_key="")
    assert cfg.is_configured() is True
    assert cfg.model  # default model resolved from the catalog


def test_keyless_tier_routing_honors_runeclaw(monkeypatch):
    # LLM_TIER_SCAN_PROVIDER=runeclaw with NO key must route the tier to the
    # self-hosted model instead of being silently ignored.
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "runeclaw")
    monkeypatch.delenv("LLM_TIER_SCAN_KEY", raising=False)
    monkeypatch.delenv("LLM_TIER_SCAN_MODEL", raising=False)
    monkeypatch.delenv("RUNECLAW_LLM_API_KEY", raising=False)

    primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="sk-primary")
    routed = resolve_tier_config(LLMTier.SCAN, primary, is_admin=False)
    assert routed.provider == LLMProvider.RUNECLAW
    assert routed.model == PROVIDER_CATALOG[LLMProvider.RUNECLAW]["default_model"]


def test_keyless_tier_routing_still_rejects_hosted_providers(monkeypatch):
    # The keyless carve-out is for LOCAL providers only: a hosted provider
    # with no key must still fall through (never run a tier keyless against
    # a paid API).
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "groq")
    monkeypatch.delenv("LLM_TIER_SCAN_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="sk-primary")
    routed = resolve_tier_config(LLMTier.SCAN, primary, is_admin=False)
    assert routed.provider != LLMProvider.GROQ


def test_default_routing_unchanged_until_benchmarked():
    # The in-house model is opt-in (env / /setllm) — it must NOT silently
    # become a default route before it wins the replay A/B on the frozen
    # benchmark (docs/RUNECLAW_LLM.md, rollout gate).
    for tier, route in DEFAULT_TIER_ROUTING.items():
        assert route["provider"] != LLMProvider.RUNECLAW, tier
