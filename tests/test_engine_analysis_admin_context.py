"""
Autonomous engine analyses run in ADMIN context (live incident 2026-07-11).

The operator pointed every LLM tier at their paid Anthropic key, but the
engine's autonomous scan path defaulted to is_admin=False — and
resolve_tier_config's non-admin guard deliberately SKIPS any step resolving
to Anthropic, so the bot's own trading brain could never reach the paid key.
Routing fell to the exhausted cheap chain (Alibaba/Gemini) and the bot ran on
the rule engine while looking configured.

The autonomous engine IS the operator's own process running the operator's
keys — admin identity is correct for it. ENGINE_ANALYSIS_AS_ADMIN (default
True) gates the behaviour.
"""
import inspect

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.llm.provider import (ADMIN_TIER_ROUTING, LLMConfig, LLMProvider,
                              LLMTier, resolve_tier_config)


def test_flag_defaults_on():
    assert CONFIG.analyzer.engine_analysis_as_admin is True


def test_batched_analysis_passes_admin_context():
    """The single choke point for autonomous analyses must forward is_admin
    from the flag (both _tick and force_scan route through it)."""
    src = inspect.getsource(RuneClawEngine._analyze_signals_batched)
    assert "engine_analysis_as_admin" in src
    assert "is_admin=_as_admin" in src


def test_admin_context_reaches_anthropic(monkeypatch):
    """With admin identity (and the operator's key present), every tier
    resolves to the Anthropic routing — the exact thing the non-admin guard
    blocks."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    primary = LLMConfig(provider=LLMProvider.GEMINI, api_key="g-key")
    for tier in (LLMTier.SCAN, LLMTier.THESIS):
        cfg = resolve_tier_config(tier, primary, is_admin=True)
        assert cfg.provider == LLMProvider.ANTHROPIC
        assert cfg.model == ADMIN_TIER_ROUTING[tier]["model"]


def test_non_admin_still_never_reaches_anthropic(monkeypatch):
    """The protection for OTHER users is unchanged: non-admin resolution must
    keep skipping Anthropic even when env tier overrides ask for it."""
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_TIER_SCAN_MODEL", "claude-sonnet-4-6")
    primary = LLMConfig(provider=LLMProvider.GEMINI, api_key="g-key")
    cfg = resolve_tier_config(LLMTier.SCAN, primary, is_admin=False)
    assert cfg.provider != LLMProvider.ANTHROPIC


def test_admin_fallback_chain_includes_anthropic():
    """The all-provider fallback chain must include Anthropic for admin
    callers, so a transient cheap-provider outage falls THROUGH to the paid
    key instead of the rule engine."""
    from bot.core.analyzer import Analyzer
    src = inspect.getsource(Analyzer._try_llm_fallback)
    assert "if is_admin" in src
    assert "LLMProvider.ANTHROPIC" in src
