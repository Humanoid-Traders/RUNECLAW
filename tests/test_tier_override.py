"""Runtime tier overrides (/settier) — the runeclaw promotion path.

After a winning /llmab shadow A/B the operator flips one tier to the
in-house model with NO restart: `/settier chat runeclaw`. Contracts:
highest priority in resolve_tier_config, keyless-local providers accepted
without a key, hosted providers refused at SET time when no key exists,
and the non-admin Anthropic guard survives overrides.
"""

from __future__ import annotations

import pytest

import bot.llm.provider as prov
from bot.llm.provider import (
    LLMConfig, LLMProvider, LLMTier, clear_tier_override,
    get_tier_overrides, resolve_tier_config, set_tier_override,
)


@pytest.fixture(autouse=True)
def _clean_overrides():
    clear_tier_override()
    yield
    clear_tier_override()


def _primary():
    return LLMConfig(provider=LLMProvider.OPENAI, api_key="sk-primary",
                     model="gpt-4o")


def test_runeclaw_override_wins_for_everyone(monkeypatch):
    monkeypatch.delenv("RUNECLAW_LLM_API_KEY", raising=False)
    ok, detail = set_tier_override(LLMTier.CHAT, LLMProvider.RUNECLAW)
    assert ok, detail
    for is_admin in (False, True):
        cfg = resolve_tier_config(LLMTier.CHAT, _primary(), is_admin=is_admin)
        assert cfg.provider == LLMProvider.RUNECLAW
    # Other tiers untouched by a CHAT override.
    assert resolve_tier_config(LLMTier.SCAN, _primary()).provider != LLMProvider.RUNECLAW
    assert get_tier_overrides() == {
        "chat": {"provider": "runeclaw", "model": ""}}


def test_override_model_and_clear(monkeypatch):
    monkeypatch.delenv("RUNECLAW_LLM_API_KEY", raising=False)
    set_tier_override(LLMTier.SCAN, LLMProvider.RUNECLAW, "runeclaw-v6")
    cfg = resolve_tier_config(LLMTier.SCAN, _primary())
    assert cfg.model == "runeclaw-v6"
    assert clear_tier_override(LLMTier.SCAN) == 1
    assert get_tier_overrides() == {}
    assert resolve_tier_config(LLMTier.SCAN, _primary()).provider != LLMProvider.RUNECLAW


def test_hosted_provider_without_key_refused_at_set_time(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ok, detail = set_tier_override(LLMTier.CHAT, LLMProvider.GROQ)
    assert ok is False and "GROQ_API_KEY" in detail
    assert get_tier_overrides() == {}       # a bad override is never stored


def test_hosted_provider_with_key_resolves(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    ok, _ = set_tier_override(LLMTier.THESIS, LLMProvider.GROQ)
    assert ok
    cfg = resolve_tier_config(LLMTier.THESIS, _primary())
    assert cfg.provider == LLMProvider.GROQ and cfg.api_key == "gsk-test"


def test_anthropic_override_never_reaches_non_admin(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    ok, _ = set_tier_override(LLMTier.CHAT, LLMProvider.ANTHROPIC)
    assert ok
    cfg = resolve_tier_config(LLMTier.CHAT, _primary(), is_admin=False)
    assert cfg.provider != LLMProvider.ANTHROPIC   # guard holds through overrides
    cfg_admin = resolve_tier_config(LLMTier.CHAT, _primary(), is_admin=True)
    assert cfg_admin.provider == LLMProvider.ANTHROPIC


def test_override_state_is_module_global_and_snapshotable(monkeypatch):
    monkeypatch.delenv("RUNECLAW_LLM_API_KEY", raising=False)
    set_tier_override(LLMTier.CHAT, LLMProvider.RUNECLAW)
    set_tier_override(LLMTier.SCAN, LLMProvider.RUNECLAW, "runeclaw-v6")
    snap = get_tier_overrides()
    assert set(snap) == {"chat", "scan"}
    assert clear_tier_override() == 2
    assert prov._RUNTIME_TIER_OVERRIDES == {}
