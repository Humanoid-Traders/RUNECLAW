"""
Regression test for LLM-2 (docs/AUDIT_REPORT_V6.1.md): a non-admin tier override
whose provider has no discoverable key must fall back to the primary config, not
return a keyless LLMConfig that silently runs the tier with no LLM.
"""
import pytest

from bot.llm.provider import LLMConfig, LLMProvider, LLMTier, resolve_tier_config

_KEY_ENVS = [
    "LLM_TIER_THESIS_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ALIBABA_API_KEY",
]


def test_tier_override_without_key_falls_back_to_primary(monkeypatch):
    # Clear every key source the override path could discover.
    for k in _KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    # Override the THESIS tier to a provider for which no key exists.
    monkeypatch.setenv("LLM_TIER_THESIS_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_TIER_THESIS_MODEL", raising=False)

    primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="primary-key",
                        model="gpt-4o")
    cfg = resolve_tier_config(LLMTier.THESIS, primary, is_admin=False)

    # Must NOT be a keyless anthropic config (the bug); must fall back to primary.
    assert cfg.api_key, "tier resolved to a keyless config (no LLM) instead of falling back"
    assert cfg is primary or cfg.api_key == "primary-key"


def test_tier_override_with_key_is_honored(monkeypatch):
    for k in _KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_TIER_THESIS_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_TIER_THESIS_KEY", "tier-key")

    primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="primary-key",
                        model="gpt-4o")
    cfg = resolve_tier_config(LLMTier.THESIS, primary, is_admin=False)
    assert cfg.provider == LLMProvider.ANTHROPIC
    assert cfg.api_key == "tier-key"
