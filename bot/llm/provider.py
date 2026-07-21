"""
RUNECLAW — Multi-Provider LLM System
====================================
Supports: OpenAI, Anthropic Claude, Google Gemini, Groq, Mistral,
          DeepSeek, Together AI, Ollama (local), and any OpenAI-compatible endpoint.

BYOK (Bring Your Own Key): operators supply their own API key via .env
or at runtime via Telegram /setllm command. Keys are never logged.

Author: Humanoid Traders — RuneMule
"""

from __future__ import annotations

import os
import re
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ════════════════════════════════════════════════════════════
#  PROVIDER REGISTRY
# ════════════════════════════════════════════════════════════

class LLMProvider(str, Enum):
    """All supported LLM providers. OpenAI-compatible ones share the same client."""
    OPENAI        = "openai"          # OpenAI — GPT-4o, GPT-4-turbo, o1
    ANTHROPIC     = "anthropic"       # Claude 3.5 Sonnet / Opus (native SDK)
    GEMINI        = "gemini"          # Google Gemini via OpenAI-compat endpoint
    GROQ          = "groq"            # Groq — llama3, mixtral (very fast, free tier)
    MISTRAL       = "mistral"         # Mistral AI — mistral-large, codestral
    DEEPSEEK      = "deepseek"        # DeepSeek — deepseek-chat, deepseek-coder
    TOGETHER      = "together"        # Together AI — many open models
    OLLAMA        = "ollama"          # Local Ollama — fully private, no cost
    OPENROUTER    = "openrouter"      # OpenRouter — routes to 100+ models
    ALIBABA       = "alibaba"         # Alibaba Cloud / DashScope — Qwen models
    RUNECLAW      = "runeclaw"        # In-house fine-tuned RUNECLAW model (self-hosted)
    CUSTOM        = "custom"          # Any OpenAI-compatible base URL


# Provider metadata — refreshed 2026-07 to current model names (Anthropic Sonnet
# 5 is the live Sonnet tier; admin routing uses it). Non-Anthropic IDs are left
# as-is unless independently verified — a wrong model id breaks live LLM calls,
# so operators switch models via the admin controls / LLM_TIER_*_MODEL env after
# validating on the replay/backtest harness. Runtime tier defaults are unchanged.
PROVIDER_CATALOG: dict[LLMProvider, dict] = {
    LLMProvider.OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "recommended_models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini"],
        "sdk": "openai",
        "free_tier": False,
        "speed": "medium",
        "cost": "high",
        "notes": "Best reasoning. gpt-4o-mini for cost savings at lower quality.",
        "get_key_url": "https://platform.openai.com/api-keys",
    },
    LLMProvider.ANTHROPIC: {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-5",
        "recommended_models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5",
                               "claude-haiku-4-5-20251001"],
        "sdk": "anthropic",
        "free_tier": False,
        "speed": "medium",
        "cost": "medium",
        "notes": ("Excellent reasoning, strong safety. Fable 5 is the top tier "
                  "($10/$50 per MTok — ULTRA routing only), Opus 4.8 for deep "
                  "analysis, Haiku for speed."),
        "get_key_url": "https://console.anthropic.com/",
    },
    LLMProvider.GEMINI: {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-pro",
        "recommended_models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        "sdk": "openai",
        "free_tier": True,         # Most generous free tier — unlimited rate-limited
        "speed": "fast",
        "cost": "very_low",
        "notes": "Best free tier (2026). 1M+ context, strong JSON output, agentic workflows.",
        "get_key_url": "https://aistudio.google.com/apikey",
    },
    LLMProvider.GROQ: {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "recommended_models": ["llama-3.3-70b-versatile", "llama-4-scout-17b-16e-instruct", "qwen-qwq-32b"],
        "sdk": "openai",
        "free_tier": True,         # Free tier with rate limits
        "speed": "very_fast",      # <1s per call — Groq hardware
        "cost": "very_low",
        "notes": "FASTEST inference. Free tier. Best for high-frequency scans.",
        "get_key_url": "https://console.groq.com/keys",
    },
    LLMProvider.MISTRAL: {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "recommended_models": ["mistral-large-latest", "mistral-small-latest"],
        "sdk": "openai",
        "free_tier": False,
        "speed": "fast",
        "cost": "low",
        "notes": "Good balance of quality and cost. European provider.",
        "get_key_url": "https://console.mistral.ai/api-keys/",
    },
    LLMProvider.DEEPSEEK: {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "recommended_models": ["deepseek-chat", "deepseek-reasoner"],
        "sdk": "openai",
        "free_tier": False,
        "speed": "medium",
        "cost": "very_low",        # ~$0.14/M tokens vs OpenAI $15/M
        "notes": "Extremely cheap. Strong at agentic finance tasks.",
        "get_key_url": "https://platform.deepseek.com/api_keys",
    },
    LLMProvider.TOGETHER: {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3-70b-chat-hf",
        "recommended_models": [
            "meta-llama/Llama-3-70b-chat-hf",
            "mistralai/Mixtral-8x22B-Instruct-v0.1",
        ],
        "sdk": "openai",
        "free_tier": True,
        "speed": "medium",
        "cost": "low",
        "notes": "100+ open-source models. Good for experimentation.",
        "get_key_url": "https://api.together.ai/settings/api-keys",
    },
    LLMProvider.OLLAMA: {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "recommended_models": ["llama3", "qwen2.5", "deepseek-v2", "gemma2"],
        "sdk": "openai",
        "free_tier": True,
        "speed": "variable",
        "cost": "zero",
        "notes": "100% private. No data leaves your machine. Needs GPU for speed.",
        "get_key_url": "https://ollama.com/download",
    },
    LLMProvider.OPENROUTER: {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-5",
        "recommended_models": [
            "anthropic/claude-sonnet-5",
            "openai/gpt-4o",
            "google/gemini-2.0-flash-001",
            "meta-llama/llama-3.1-70b-instruct:free",
        ],
        "sdk": "openai",
        "free_tier": True,
        "speed": "variable",
        "cost": "variable",
        "notes": "Single key for 100+ models. Great for quick switching.",
        "get_key_url": "https://openrouter.ai/keys",
    },
    LLMProvider.ALIBABA: {
        "base_url": "https://hackathon.bitgetops.com/v1",
        "default_model": "qwen3.6-plus",
        "recommended_models": ["qwen3.6-plus", "qwen3.6-flash"],
        "sdk": "openai",
        "free_tier": False,
        "speed": "fast",
        "cost": "low",
        "notes": "Alibaba Cloud Qwen via Bitget Hackathon endpoint. $30 credits included.",
        "get_key_url": "https://dashscope.console.aliyun.com/apiKey",
    },
    LLMProvider.RUNECLAW: {
        # The in-house fine-tuned RUNECLAW model (Llama 3.1 8B + LoRA), served
        # from any OpenAI-compatible runtime the operator controls. Default is
        # vLLM's port; for Ollama set RUNECLAW_LLM_BASE_URL=http://localhost:11434/v1.
        # Keyless by default (local serving); RUNECLAW_LLM_API_KEY secures a
        # remote endpoint and is vault-managed like every other provider key.
        "base_url": os.getenv("RUNECLAW_LLM_BASE_URL", "http://localhost:8000/v1"),
        "default_model": os.getenv("RUNECLAW_LLM_MODEL", "runeclaw-v6"),
        "recommended_models": ["runeclaw-v6"],
        "sdk": "openai",
        "free_tier": True,
        "speed": "fast",
        "cost": "zero",
        "notes": "In-house fine-tuned RUNECLAW model. Self-hosted (vLLM/Ollama), fully private, zero per-token cost. See docs/RUNECLAW_LLM.md.",
        "get_key_url": None,
    },
    LLMProvider.CUSTOM: {
        "base_url": "",
        "default_model": "",
        "recommended_models": [],
        "sdk": "openai",
        "free_tier": None,
        "speed": "unknown",
        "cost": "unknown",
        "notes": "Any OpenAI-compatible endpoint. Set LLM_BASE_URL in .env.",
        "get_key_url": None,
    },
}


# ════════════════════════════════════════════════════════════
#  MULTI-TIER LLM ROUTING
# ════════════════════════════════════════════════════════════

class LLMTier(str, Enum):
    """Task tiers — each can be routed to a different provider."""
    SCAN = "scan"             # High-frequency scans: needs speed, tolerates lower quality
    THESIS = "thesis"         # Trade thesis generation: needs strong reasoning + JSON
    LEARNING = "learning"     # Reflection / macro learner: needs depth, can be slower
    CHAT = "chat"             # User Q&A via Telegram: balanced speed + quality


# Default tier-to-provider routing.
# Operators can override individual tiers via env:
#   LLM_TIER_SCAN_PROVIDER=groq
#   LLM_TIER_THESIS_PROVIDER=gemini
#   LLM_TIER_LEARNING_PROVIDER=gemini
#   LLM_TIER_CHAT_PROVIDER=groq
#
# When a tier env is not set, falls back to the primary LLM_PROVIDER.
DEFAULT_TIER_ROUTING: dict[LLMTier, dict] = {
    LLMTier.SCAN: {
        "provider": LLMProvider.ALIBABA,
        "model": "qwen3.6-flash",
        "reason": "Fast and cheap — handles high-frequency scans without burning Anthropic/Gemini quota",
    },
    LLMTier.THESIS: {
        "provider": LLMProvider.GEMINI,
        "model": "gemini-2.5-pro",
        "reason": "Anthropic is reserved for admin (see ADMIN_TIER_ROUTING) — best free-tier reasoning for trade thesis otherwise",
    },
    LLMTier.LEARNING: {
        "provider": LLMProvider.GEMINI,
        "model": "gemini-2.5-flash",
        "reason": "1M+ context, good at reflection/analysis, free tier for occasional use",
    },
    LLMTier.CHAT: {
        "provider": LLMProvider.ALIBABA,
        "model": "qwen3.6-flash",
        "reason": "Fast, cheap user-facing responses — Anthropic is reserved for admin",
    },
}


# Admin tier routing — premium models for all tasks.
# Used when the requesting user is in ADMIN_TELEGRAM_IDS. This is the ONLY
# routing table that should ever reference LLMProvider.ANTHROPIC — the
# operator's Claude key is reserved for admin use, and resolve_tier_config()
# enforces this with a hard guard regardless of what any other table or env
# override says.
ADMIN_TIER_ROUTING: dict[LLMTier, dict] = {
    LLMTier.SCAN: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-sonnet-5",
        "reason": "Admin premium: full Sonnet reasoning for scan analysis",
    },
    LLMTier.THESIS: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-sonnet-5",
        "reason": "Admin premium: Sonnet for trade thesis generation",
    },
    LLMTier.LEARNING: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-sonnet-5",
        "reason": "Admin premium: Sonnet for deep reflection and learning",
    },
    LLMTier.CHAT: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-sonnet-5",
        "reason": "Admin premium: Sonnet for user Q&A — best quality responses",
    },
}


# ULTRA admin routing — Claude Fable 5 (Anthropic's most capable model) on the
# deep-reasoning tiers, Sonnet 5 on the latency-sensitive ones. Opt-in ONLY
# (env LLM_ULTRA_ENABLED or admin /ultra) because Fable 5 costs 2x Opus
# ($10/$50 per MTok) — the operator chooses the spend, never a default.
# Same admin-only Anthropic guard as ADMIN_TIER_ROUTING: non-admin callers
# can never resolve into this table.
#
# `effort` maps to Fable 5's output_config.effort (thinking is always on for
# the Fable/Mythos family — the `thinking` parameter itself is rejected, so
# effort is the depth dial). Scan/chat stay on Sonnet 5: burning $10/MTok on
# high-frequency scans buys latency, not quality.
ULTRA_TIER_ROUTING: dict[LLMTier, dict] = {
    LLMTier.SCAN: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-sonnet-5",
        "reason": "Ultra: Sonnet keeps high-frequency scans fast and affordable",
    },
    LLMTier.THESIS: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-fable-5",
        "effort": "high",
        "reason": "Ultra: Fable 5 deep reasoning for trade theses",
    },
    LLMTier.LEARNING: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-fable-5",
        "effort": "max",
        "reason": "Ultra: Fable 5 max effort for reflection/learning (infrequent)",
    },
    LLMTier.CHAT: {
        "provider": LLMProvider.ANTHROPIC,
        "model": "claude-sonnet-5",
        "reason": "Ultra: Sonnet keeps chat responsive",
    },
}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


# Runtime ULTRA switch — boot default from LLM_ULTRA_ENABLED, toggled live by
# the admin /ultra command. Applies only to admin routing resolution.
_ULTRA_MODE: bool = _env_flag("LLM_ULTRA_ENABLED")


def is_ultra_mode() -> bool:
    """True when admin routing resolves through ULTRA_TIER_ROUTING."""
    return _ULTRA_MODE


def set_ultra_mode(enabled: bool, primary_config=None) -> tuple[bool, str]:
    """Toggle ULTRA admin routing. Returns (ok, detail).

    Enabling requires a usable Anthropic key (same fail-loud-at-set-time rule
    as set_tier_override) — an ultra mode that silently falls back to cheap
    routing would misrepresent what the operator is paying for."""
    global _ULTRA_MODE
    if enabled:
        from bot.llm import key_health as _kh
        _src, _key = _kh.pick_anthropic_key(primary_config, BYOK._runtime_config)
        if not _key:
            return False, ("no Anthropic key found — set ANTHROPIC_API_KEY or "
                           "/setllm anthropic <key> first")
        _ULTRA_MODE = True
        return True, ("ULTRA ON — admin thesis/learning → claude-fable-5 "
                      "(effort high/max), scan/chat → claude-sonnet-5. "
                      "Fable 5 bills $10/$50 per MTok.")
    _ULTRA_MODE = False
    return True, "ULTRA OFF — admin routing back to claude-sonnet-5."


def _admin_routing() -> dict:
    """The admin routing table in effect (ULTRA when toggled on)."""
    return ULTRA_TIER_ROUTING if _ULTRA_MODE else ADMIN_TIER_ROUTING


# Elite user-tier routing — best non-Anthropic models, cheap scan (LLM
# Optimization Plan P2). Anthropic/Claude is reserved for admin only — see
# resolve_tier_config()'s hard non-admin guard, which refuses to hand out the
# operator's Claude key regardless of what any routing table says.
ELITE_TIER_ROUTING: dict[LLMTier, dict] = {
    LLMTier.SCAN: {"provider": LLMProvider.ALIBABA, "model": "qwen3.6-flash",
                   "reason": "Elite: fast/cheap scan"},
    LLMTier.THESIS: {"provider": LLMProvider.GEMINI, "model": "gemini-2.5-pro",
                     "reason": "Elite: best free-tier reasoning for thesis"},
    LLMTier.LEARNING: {"provider": LLMProvider.GEMINI, "model": "gemini-2.5-pro",
                       "reason": "Elite: best free-tier reasoning for learning"},
    LLMTier.CHAT: {"provider": LLMProvider.GEMINI, "model": "gemini-2.5-pro",
                   "reason": "Elite: best free-tier reasoning for chat"},
}

# Pro user-tier routing — mid models (LLM Optimization Plan P2).
PRO_TIER_ROUTING: dict[LLMTier, dict] = {
    LLMTier.SCAN: {"provider": LLMProvider.ALIBABA, "model": "qwen3.6-flash",
                   "reason": "Pro: fast/cheap scan"},
    LLMTier.THESIS: {"provider": LLMProvider.GEMINI, "model": "gemini-2.5-flash",
                     "reason": "Pro: Gemini thesis"},
    LLMTier.LEARNING: {"provider": LLMProvider.GEMINI, "model": "gemini-2.5-flash",
                       "reason": "Pro: Gemini learning"},
    LLMTier.CHAT: {"provider": LLMProvider.ALIBABA, "model": "qwen3.6-flash",
                   "reason": "Pro: Qwen chat"},
}

# Map a user TIER (from the user store) to its premium routing table. Tiers not
# listed here (basic / free / unknown) use the existing default routing — they
# are never downgraded. "admin" keeps using the is_admin path.
USER_TIER_ROUTING: dict[str, dict] = {
    "admin": ADMIN_TIER_ROUTING,
    "elite": ELITE_TIER_ROUTING,
    "pro": PRO_TIER_ROUTING,
}


def routing_for_user_tier(user_tier) -> "Optional[dict]":
    """Premium routing table for a user tier, or None to use default routing."""
    if not user_tier:
        return None
    return USER_TIER_ROUTING.get(str(user_tier).strip().lower())


# ── Runtime tier overrides (admin /settier) ─────────────────────────
# {LLMTier: {"provider": LLMProvider, "model": str}} — set at runtime by the
# operator to promote a tier without a restart or env edit (THE promotion
# path after a winning /llmab shadow A/B: `/settier chat runeclaw`).
# Highest priority in resolve_tier_config; the non-admin Anthropic guard
# still applies on every resolution.
_RUNTIME_TIER_OVERRIDES: dict = {}

_PROVIDER_KEY_ENV = {
    LLMProvider.GEMINI: "GEMINI_API_KEY",
    LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    LLMProvider.GROQ: "GROQ_API_KEY",
    LLMProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
    LLMProvider.OPENAI: "OPENAI_API_KEY",
    LLMProvider.ALIBABA: "ALIBABA_API_KEY",
    LLMProvider.MISTRAL: "MISTRAL_API_KEY",
    LLMProvider.TOGETHER: "TOGETHER_API_KEY",
    LLMProvider.OPENROUTER: "OPENROUTER_API_KEY",
    LLMProvider.RUNECLAW: "RUNECLAW_LLM_API_KEY",
}

_KEYLESS_PROVIDERS = (LLMProvider.OLLAMA, LLMProvider.RUNECLAW)


def set_tier_override(tier: "LLMTier", provider: "LLMProvider",
                      model: str = "") -> tuple[bool, str]:
    """Set a runtime routing override for one tier. Returns (ok, detail).

    Validates that the provider is actually usable (a key exists in env, or
    the provider is keyless-local) BEFORE storing — a bad override must fail
    loudly at set time, not silently break every call of that tier."""
    if provider not in _KEYLESS_PROVIDERS and not os.getenv(
            _PROVIDER_KEY_ENV.get(provider, ""), ""):
        return False, (f"no key found for {provider.value} "
                       f"(set {_PROVIDER_KEY_ENV.get(provider, 'its key env')} first)")
    _RUNTIME_TIER_OVERRIDES[tier] = {"provider": provider, "model": model}
    catalog = PROVIDER_CATALOG.get(provider, {})
    return True, (f"{tier.value} → {provider.value}/"
                  f"{model or catalog.get('default_model', 'default')}")


def clear_tier_override(tier: "Optional[LLMTier]" = None) -> int:
    """Clear one tier's runtime override, or all when tier is None."""
    if tier is not None:
        return 1 if _RUNTIME_TIER_OVERRIDES.pop(tier, None) is not None else 0
    n = len(_RUNTIME_TIER_OVERRIDES)
    _RUNTIME_TIER_OVERRIDES.clear()
    return n


def get_tier_overrides() -> dict:
    """Snapshot of the runtime tier overrides (for /llmstatus display)."""
    return {t.value: {"provider": o["provider"].value, "model": o["model"]}
            for t, o in _RUNTIME_TIER_OVERRIDES.items()}


def resolve_tier_config(
    tier: LLMTier,
    primary_config: "LLMConfig",
    is_admin: bool = False,
    routing_override: "Optional[dict]" = None,
) -> "LLMConfig":
    """Resolve LLM config for a specific task tier.

    Priority order:
      0. routing_override: an explicit routing table (e.g. a user-tier table) —
         used directly, like the admin premium path (skips env tier overrides)
      1. Admin routing: if is_admin, use ADMIN_TIER_ROUTING (premium models)
      2. Env override: LLM_TIER_{SCAN|THESIS|LEARNING|CHAT}_PROVIDER + _KEY + _MODEL
      3. Default tier routing (cheap models for non-admin)
      4. Fall back to primary_config (the global LLM_PROVIDER)

    This lets operators run per-user quality tiers: admin gets Sonnet,
    everyone else gets the cheapest route.

    Hard non-admin guard: the operator's Anthropic/Claude key is reserved for
    admin use ONLY. When is_admin is False, ANY step above that would
    otherwise resolve to LLMProvider.ANTHROPIC (an explicit env tier
    override, a routing-table entry, or the final primary_config fallback)
    is skipped in favor of the next step, so a non-admin caller can never be
    handed the admin's Claude key regardless of how routing is configured.
    """
    # 0. Runtime override (admin /settier) — highest priority, applies to
    # every caller of the tier. The non-admin Anthropic guard still holds:
    # an Anthropic override only resolves for admin callers; everyone else
    # falls through to the normal steps below.
    _rt = _RUNTIME_TIER_OVERRIDES.get(tier)
    if _rt is not None:
        _rt_provider = _rt["provider"]
        if is_admin or _rt_provider != LLMProvider.ANTHROPIC:
            if _rt_provider == LLMProvider.ANTHROPIC:
                from bot.llm import key_health as _kh
                _src, _rt_key = _kh.pick_anthropic_key(
                    primary_config, BYOK._runtime_config)
            else:
                _rt_key = os.getenv(_PROVIDER_KEY_ENV.get(_rt_provider, ""), "")
                if not _rt_key and _rt_provider == primary_config.provider:
                    _rt_key = primary_config.api_key
            if _rt_key or _rt_provider in _KEYLESS_PROVIDERS:
                _rt_catalog = PROVIDER_CATALOG.get(_rt_provider, {})
                return LLMConfig(
                    provider=_rt_provider,
                    api_key=_rt_key or "",
                    model=_rt["model"] or _rt_catalog.get("default_model", ""),
                    base_url=_rt_catalog.get("base_url", ""),
                )
        # No usable key / non-admin Anthropic → normal resolution below.

    # routing_override (user-tier table) or admin → premium routing, skip env
    # tier overrides; otherwise the default cheap routing.
    use_table_directly = routing_override is not None or is_admin
    routing = (routing_override if routing_override is not None
               else (_admin_routing() if is_admin else DEFAULT_TIER_ROUTING))

    tier_upper = tier.value.upper()

    # Admin + Anthropic route: resolve the KEY through key_health's
    # deterministic candidate order (runtime BYOK > ANTHROPIC_API_KEY >
    # primary .env), skipping keys marked invalid by a real 401. This fixes
    # two recurring failure shapes (live incident 2026-07-11):
    #   1. the old step-2 guard skipped the admin table whenever the PRIMARY
    #      provider was also Anthropic, silently binding every tier to the
    #      primary/BYOK slot — whichever key happened to live there;
    #   2. one stale key in any slot captured the call path forever, with no
    #      path to the other (valid) keys. Now a 401 condemns only that key
    #      and the resolver auto-heals onto the next candidate.
    if is_admin and routing_override is None:
        _route = routing.get(tier, {})
        if _route.get("provider") == LLMProvider.ANTHROPIC:
            from bot.llm import key_health as _kh
            _src, _key = _kh.pick_anthropic_key(
                primary_config, BYOK._runtime_config)
            if _key:
                _catalog = PROVIDER_CATALOG.get(LLMProvider.ANTHROPIC, {})
                return LLMConfig(
                    provider=LLMProvider.ANTHROPIC,
                    api_key=_key,
                    model=_route.get("model",
                                     _catalog.get("default_model", "")),
                    base_url=_catalog.get("base_url", ""),
                    effort=_route.get("effort", ""),
                )
            # No Anthropic key anywhere → fall through to the generic steps.

    # For non-admin without an explicit override: check explicit tier env override
    if not use_table_directly:
        tier_provider_str = os.getenv(f"LLM_TIER_{tier_upper}_PROVIDER", "")
        if tier_provider_str:
            try:
                tier_provider = LLMProvider(tier_provider_str.lower())
            except ValueError:
                tier_provider = None

            if tier_provider:
                tier_key = os.getenv(f"LLM_TIER_{tier_upper}_KEY", "")
                tier_model = os.getenv(f"LLM_TIER_{tier_upper}_MODEL", "")

            # If no tier-specific key, try provider-specific env fallbacks
                if not tier_key:
                    key_env_map = {
                        LLMProvider.GEMINI: "GEMINI_API_KEY",
                        LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
                        LLMProvider.GROQ: "GROQ_API_KEY",
                        LLMProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
                        LLMProvider.OPENAI: "OPENAI_API_KEY",
                        LLMProvider.ALIBABA: "ALIBABA_API_KEY",
                        LLMProvider.RUNECLAW: "RUNECLAW_LLM_API_KEY",
                    }
                    fallback_env = key_env_map.get(tier_provider, "")
                    tier_key = os.getenv(fallback_env, "") if fallback_env else ""

                # Still no key? If the tier provider matches primary, use primary key
                if not tier_key and tier_provider == primary_config.provider:
                    tier_key = primary_config.api_key

                # Keyless local providers (Ollama, the self-hosted RUNECLAW
                # model) are valid WITHOUT a key — the key guard below exists
                # to avoid silently running a tier keyless against a hosted
                # API, which doesn't apply to a local endpoint. Without this,
                # LLM_TIER_SCAN_PROVIDER=runeclaw would be silently ignored.
                keyless_ok = tier_provider in (LLMProvider.OLLAMA,
                                               LLMProvider.RUNECLAW)

                # LLM-2: only honor the override when a key is actually
                # available; otherwise fall through to default routing / primary
                # config rather than returning a keyless config that silently
                # runs the tier with no LLM (the default-routing branch below
                # already guards this way with `if alt_key:`).
                # Non-admin guard: this whole branch only runs when NOT
                # is_admin (use_table_directly is False here), so an explicit
                # env override asking for Anthropic must not be honored —
                # fall through to the next step instead.
                if (tier_key or keyless_ok) and tier_provider != LLMProvider.ANTHROPIC:
                    catalog = PROVIDER_CATALOG.get(tier_provider, {})
                    return LLMConfig(
                        provider=tier_provider,
                        api_key=tier_key,
                        model=tier_model or catalog.get("default_model", ""),
                        base_url=catalog.get("base_url", ""),
                    )

    # 2. Check if the selected routing has a different provider with a key available
    # Non-admin guard: `routing` is ADMIN_TIER_ROUTING when is_admin, so this
    # only excludes Anthropic for the non-admin routing tables (default /
    # per-user-tier), never for the admin table itself.
    default_route = routing.get(tier, {})
    default_provider = default_route.get("provider")
    if (default_provider and default_provider != primary_config.provider
            and (is_admin or default_provider != LLMProvider.ANTHROPIC)):
        # Try to find a key for the default tier provider
        key_env_map = {
            LLMProvider.GEMINI: "GEMINI_API_KEY",
            LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
            LLMProvider.GROQ: "GROQ_API_KEY",
            LLMProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
            LLMProvider.OPENAI: "OPENAI_API_KEY",
            LLMProvider.ALIBABA: "ALIBABA_API_KEY",
        }
        fallback_env = key_env_map.get(default_provider, "")
        alt_key = os.getenv(fallback_env, "") if fallback_env else ""

        # Also check if the primary provider happens to be the default tier provider
        if not alt_key and default_provider.value == (primary_config.provider.value if isinstance(primary_config.provider, LLMProvider) else primary_config.provider):
            alt_key = primary_config.api_key

        if alt_key:
            catalog = PROVIDER_CATALOG.get(default_provider, {})
            return LLMConfig(
                provider=default_provider,
                api_key=alt_key,
                model=default_route.get("model", catalog.get("default_model", "")),
                base_url=catalog.get("base_url", ""),
            )

    # 3. Fall back to primary config (single-provider mode)
    # Non-admin guard: if the operator's global LLM_PROVIDER is itself
    # Anthropic, a non-admin caller must not be handed it here either.
    # Return an unconfigured stand-in (empty key) instead of a working
    # Anthropic config — callers already treat "not configured" as a signal
    # to degrade gracefully (rule-based logic / "no LLM configured" chat
    # reply), which is exactly the right behavior here: there is no cheap
    # alternative key configured at all, so the tier truly has nothing to
    # route to for this non-admin caller.
    if not is_admin and primary_config.provider == LLMProvider.ANTHROPIC:
        return LLMConfig(provider=primary_config.provider, api_key="")
    return primary_config


# ════════════════════════════════════════════════════════════
#  UPDATED LLM CONFIG
# ════════════════════════════════════════════════════════════

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class LLMConfig:
    """
    Extended LLM configuration supporting multiple providers.
    
    Priority order for provider selection:
    1. LLM_PROVIDER env var (explicit)
    2. Inferred from LLM_BASE_URL (if custom endpoint)
    3. Fallback: OPENAI if LLM_API_KEY set, else rule-based
    """
    provider: LLMProvider = LLMProvider(
        _env("LLM_PROVIDER", LLMProvider.OPENAI.value)
    )
    api_key: str = _env("LLM_API_KEY", "")
    model: str = _env("LLM_MODEL", "")               # Auto-selects default if empty
    base_url: str = _env("LLM_BASE_URL", "")         # Override endpoint (for CUSTOM/Ollama)
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout_seconds: float = 15.0
    # Fable/Mythos-family reasoning depth (output_config.effort): "" omits the
    # parameter; "low"/"medium"/"high"/"max" only sent for that family.
    effort: str = ""

    def __post_init__(self) -> None:
        # Resolve model default if not set
        if not self.model:
            catalog = PROVIDER_CATALOG.get(self.provider, {})
            object.__setattr__(self, "model", catalog.get("default_model", "gpt-4o"))

    def resolved_base_url(self) -> str:
        """Return the effective base URL for this provider."""
        if self.base_url:
            return self.base_url
        catalog = PROVIDER_CATALOG.get(self.provider, {})
        return catalog.get("base_url", "https://api.openai.com/v1")

    def is_configured(self) -> bool:
        """True if an API key is set (or for keyless local providers —
        Ollama and the self-hosted RUNECLAW model need no key)."""
        if self.provider in (LLMProvider.OLLAMA, LLMProvider.RUNECLAW):
            return True
        return bool(self.api_key)

    def key_fingerprint(self) -> str:
        """Safe display of key — first 6 chars + hash. Never log the full key."""
        if not self.api_key:
            return "NOT SET"
        prefix = self.api_key[:6]
        suffix = hashlib.sha256(self.api_key.encode()).hexdigest()[:8]
        return f"{prefix}...{suffix}"

    def sdk_type(self) -> str:
        """Which SDK to use for this provider."""
        catalog = PROVIDER_CATALOG.get(self.provider, {})
        return catalog.get("sdk", "openai")


# ════════════════════════════════════════════════════════════
#  LLM CLIENT FACTORY
# ════════════════════════════════════════════════════════════

def create_llm_client(config: LLMConfig):
    """
    Factory: returns the correct async LLM client for the configured provider.
    
    - OpenAI-compatible providers: returns AsyncOpenAI with custom base_url
    - Anthropic: returns AsyncAnthropic (different API format)
    - Ollama: returns AsyncOpenAI pointed at localhost (no key needed)
    - Not configured: returns None (triggers rule-based fallback)
    """
    if not config.is_configured():
        return None

    sdk = config.sdk_type()

    if sdk == "anthropic":
        try:
            from anthropic import AsyncAnthropic
            return AsyncAnthropic(api_key=config.api_key)
        except ImportError:
            raise ImportError(
                "anthropic package required: pip install anthropic\n"
                "Or switch to LLM_PROVIDER=openai"
            )

    else:
        # All other providers use OpenAI-compatible SDK
        try:
            from openai import AsyncOpenAI
            kwargs = {"api_key": config.api_key or "not-needed", "max_retries": 3}
            if config.resolved_base_url() != "https://api.openai.com/v1":
                kwargs["base_url"] = config.resolved_base_url()
            return AsyncOpenAI(**kwargs)
        except ImportError:
            raise ImportError("openai package required: pip install openai")


# ════════════════════════════════════════════════════════════
#  UNIFIED INFERENCE — handles both OpenAI + Anthropic APIs
# ════════════════════════════════════════════════════════════

# The Claude 5 family (claude-sonnet-5, claude-fable-5, dated variants …)
# DEPRECATED the explicit `temperature` parameter — sending one now returns
# 400 invalid_request_error ("`temperature` is deprecated for this model"),
# which took the whole analysis brain down to the rule engine on 2026-07-16.
_TEMPERATURE_DEPRECATED_RE = re.compile(r"^claude-[a-z]+-5\b|^claude-[a-z]+-5-")

# Fable/Mythos (Claude 5 top tier): thinking is ALWAYS on and the `thinking`
# request parameter is rejected with 400 — depth is steered through
# output_config.effort instead. Sending `thinking: adaptive` to these models
# would take the brain down exactly like the temperature deprecation did.
_THINKING_ALWAYS_ON_RE = re.compile(r"^claude-(?:fable|mythos)-5(?:$|[.-])")

# AI-2: models that accept schema-constrained JSON via output_config.format
# (structured outputs) — the whole Claude 5 family plus Opus 4.6+. Older
# models (claude-3-opus, Sonnet 4.5, Haiku …) reject the parameter with 400,
# so callers must gate on this before attaching a schema.
_STRUCTURED_OUTPUT_RE = re.compile(
    r"^claude-[a-z]+-5(?:$|[.-])|^claude-opus-4-[6-9](?:$|[.-])")


def model_accepts_temperature(model: str) -> bool:
    """False when the model rejects an explicit `temperature` (Claude 5
    family). Callers must omit the parameter entirely for those models —
    provider-default sampling applies."""
    return not _TEMPERATURE_DEPRECATED_RE.match((model or "").strip().lower())


def model_thinking_always_on(model: str) -> bool:
    """True for the Fable/Mythos family: the `thinking` parameter is rejected
    (always-on) and reasoning depth is steered via output_config.effort."""
    return bool(_THINKING_ALWAYS_ON_RE.match((model or "").strip().lower()))


def model_supports_structured_output(model: str) -> bool:
    """True when the model accepts output_config.format json_schema
    (Claude 5 family + Opus 4.6/4.7/4.8)."""
    return bool(_STRUCTURED_OUTPUT_RE.match((model or "").strip().lower()))


async def llm_complete(
    client,
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    history: list[dict] | None = None,
    json_schema: dict | None = None,
) -> str:
    """
    Unified completion call — handles OpenAI-format and Anthropic-format.
    Returns the text response string.
    Raises on failure so caller can catch and use rule-based fallback.

    Args:
        client: LLM client (AsyncOpenAI or AsyncAnthropic)
        config: LLM configuration
        system_prompt: System prompt string
        user_prompt: Current user message
        history: Optional list of prior messages [{role, content}, ...]
                 for multi-turn conversation context.
        json_schema: Optional JSON Schema for the response. On models with
                 structured-output support (Claude 5 family, Opus 4.6+) it is
                 enforced via output_config.format — the response is then
                 guaranteed-valid JSON. On other Anthropic models it is
                 ignored (they reject the parameter); on OpenAI-compatible
                 providers it degrades to response_format json_object mode.
                 Callers keep their tolerant parser as the fallback either way.
    """
    import asyncio

    sdk = config.sdk_type()

    async def _call():
        if sdk == "anthropic":
            # Anthropic API format — history goes into messages array
            # Use prompt caching for the system prompt to reduce costs
            # (cache_control marks the system prompt for reuse across calls)
            messages = []
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_prompt})

            # Build system with cache_control for prompt caching
            # System prompt is large and identical across calls — caching
            # drops input cost from $5/MTok to $0.50/MTok (90% savings)
            system_content = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

            model_l = (config.model or "").strip().lower()
            extra: dict = {}
            if _THINKING_ALWAYS_ON_RE.match(model_l):
                # Fable/Mythos: `thinking` param is rejected (always-on);
                # output_config.effort is the depth dial.
                if config.effort:
                    extra["output_config"] = {"effort": config.effort}
            elif "opus" in model_l:
                # Enable adaptive thinking for Opus 4.8+ models
                extra["thinking"] = {"type": "adaptive"}

            # AI-2: schema-constrained JSON on models that support it. Rides
            # in the SAME output_config dict as the Fable effort dial — merge,
            # never overwrite. Unsupported models simply don't get the field;
            # the caller's tolerant parser handles their free-form JSON.
            if json_schema and _STRUCTURED_OUTPUT_RE.match(model_l):
                extra.setdefault("output_config", {})["format"] = {
                    "type": "json_schema",
                    "schema": json_schema,
                }

            try:
                response = await client.messages.create(
                    model=config.model,
                    max_tokens=config.max_tokens,
                    system=system_content,
                    messages=messages,
                    **extra,
                )
            except Exception as _so_exc:
                # Future-proof net (mirrors the analyzer's temperature retry):
                # if a model unexpectedly rejects output_config.format, strip
                # the schema and retry ONCE — a degraded free-form answer beats
                # failing every call until a code change ships.
                _so_msg = str(_so_exc).lower()
                _had_format = "format" in extra.get("output_config", {})
                if _had_format and ("output_config" in _so_msg
                                    or "json_schema" in _so_msg
                                    or "structured" in _so_msg):
                    extra["output_config"].pop("format", None)
                    if not extra["output_config"]:
                        extra.pop("output_config")
                    response = await client.messages.create(
                        model=config.model,
                        max_tokens=config.max_tokens,
                        system=system_content,
                        messages=messages,
                        **extra,
                    )
                else:
                    raise
            # Fable/Mythos-class models can end with stop_reason="refusal" —
            # no usable content. Raise so the caller's existing failure path
            # (rule-based fallback) runs instead of treating "" as an answer.
            if getattr(response, "stop_reason", "") == "refusal":
                raise RuntimeError("LLM declined to answer (stop_reason=refusal)")
            # Handle thinking blocks — extract text block
            raw_text = ""
            if response.content:
                for block in response.content:
                    if getattr(block, "type", "") == "text":
                        raw_text = block.text
                        break
                if not raw_text and hasattr(response.content[0], "text"):
                    raw_text = response.content[0].text
            return raw_text or ""

        else:
            # OpenAI-compatible format (works for OpenAI, Groq, Gemini, etc.)
            messages = [{"role": "system", "content": system_prompt}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_prompt})
            _oai_kwargs: dict = {}
            if json_schema:
                # Best available approximation on OpenAI-compatible providers:
                # json_object mode (the system prompt must mention "json",
                # which every schema-passing caller's prompt does). The
                # tolerant parser remains the validation layer.
                _oai_kwargs["response_format"] = {"type": "json_object"}
            response = await client.chat.completions.create(
                model=config.model,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                messages=messages,
                **_oai_kwargs,
            )
            # content can be None (content-filter finish, tool-call-only, or
            # empty completion); normalize to "" so callers that .strip() it
            # don't hit AttributeError — mirrors the Anthropic branch above.
            return response.choices[0].message.content or ""

    # Apply timeout — prevents hanging scan cycle
    return await asyncio.wait_for(_call(), timeout=config.timeout_seconds)


# ════════════════════════════════════════════════════════════
#  BYOK — RUNTIME KEY INJECTION (via Telegram /setllm)
# ════════════════════════════════════════════════════════════

class BYOKManager:
    """
    Manage BYOK (Bring Your Own Key) runtime configuration.
    Keys are stored in memory only — never persisted to disk or logs.
    
    Telegram commands:
        /setllm openai sk-abc123           → set OpenAI key at runtime
        /setllm groq gsk_abc123            → switch to Groq
        /setllm ollama                     → switch to local Ollama (no key)
        /setllm deepseek sk-abc123         → switch to DeepSeek
        /llmstatus                         → show current provider + key fingerprint
        /llmreset                          → clear runtime key, revert to .env
    """

    def __init__(self) -> None:
        self._runtime_config: Optional[LLMConfig] = None

    def set_provider(
        self,
        provider_str: str,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
    ) -> tuple[bool, str]:
        """
        Set a new LLM provider at runtime.
        Returns (success, message).
        """
        # Validate provider
        try:
            provider = LLMProvider(provider_str.lower())
        except ValueError:
            valid = ", ".join(p.value for p in LLMProvider)
            return False, f"Unknown provider '{provider_str}'. Valid: {valid}"

        # Validate API key format (basic sanity check)
        if api_key and not self._validate_key_format(provider, api_key):
            return False, f"API key format looks wrong for {provider.value}. Check and retry."

        # Resolve model
        catalog = PROVIDER_CATALOG.get(provider, {})
        resolved_model = model or catalog.get("default_model", "gpt-4o")
        resolved_url = base_url or catalog.get("base_url", "")

        # If no key provided, try env variable for this provider
        if not api_key:
            env_key_map = {
                LLMProvider.GEMINI: "GEMINI_API_KEY",
                LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
                LLMProvider.GROQ: "GROQ_API_KEY",
                LLMProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
                LLMProvider.OPENAI: "OPENAI_API_KEY",
                LLMProvider.ALIBABA: "ALIBABA_API_KEY",
                LLMProvider.RUNECLAW: "RUNECLAW_LLM_API_KEY",
            }
            env_var = env_key_map.get(provider, "")
            api_key = os.getenv(env_var, "") if env_var else ""

        self._runtime_config = LLMConfig(
            provider=provider,
            api_key=api_key,
            model=resolved_model,
            base_url=resolved_url,
        )

        # Verify client can be created
        try:
            client = create_llm_client(self._runtime_config)
            if client is None and provider not in (LLMProvider.OLLAMA,
                                                   LLMProvider.RUNECLAW):
                return False, "No API key provided and provider requires one."
        except ImportError as e:
            return False, str(e)

        fingerprint = self._runtime_config.key_fingerprint()
        return True, (
            f"✅ LLM switched to {provider.value.upper()}\n"
            f"Model: {resolved_model}\n"
            f"Key: {fingerprint}\n"
            f"Note: key stored in memory only. Not saved to disk."
        )

    def get_active_config(self, fallback: LLMConfig) -> LLMConfig:
        """Return runtime config if set, else fall back to .env config."""
        return self._runtime_config or fallback

    def reset(self) -> str:
        """Clear runtime config, revert to .env settings."""
        self._runtime_config = None
        return "🔄 LLM config reset. Using .env settings."

    def status(self, fallback: LLMConfig) -> str:
        """Return human-readable status for /llmstatus command."""
        cfg = self.get_active_config(fallback)
        source = "runtime (BYOK)" if self._runtime_config else ".env file"
        catalog = PROVIDER_CATALOG.get(cfg.provider, {})

        lines = [
            "🤖 **LLM Status**",
            f"Provider: `{cfg.provider.value.upper()}`",
            f"Model: `{cfg.model}`",
            f"Key: `{cfg.key_fingerprint()}`",
            f"Source: {source}",
            f"Speed: {catalog.get('speed', '?')}",
            f"Cost: {catalog.get('cost', '?')}",
            f"Free tier: {catalog.get('free_tier', '?')}",
        ]
        if not cfg.is_configured():
            lines.append("⚠️ No key set — using rule-based fallback")
        return "\n".join(lines)

    @staticmethod
    def _validate_key_format(provider: LLMProvider, key: str) -> bool:
        """Basic format checks — not a complete validator."""
        patterns = {
            LLMProvider.OPENAI:     r"^sk-[A-Za-z0-9\-_]{20,}$",
            LLMProvider.ANTHROPIC:  r"^sk-ant-[A-Za-z0-9\-_]{20,}$",
            LLMProvider.GROQ:       r"^gsk_[A-Za-z0-9]{20,}$",
            LLMProvider.GEMINI:     r"^AIza[A-Za-z0-9\-_]{30,}$",
        }
        pattern = patterns.get(provider)
        if pattern is None:
            return True  # Unknown provider — allow any key
        return bool(re.match(pattern, key))


# Singleton for use across the app
BYOK = BYOKManager()


# ════════════════════════════════════════════════════════════
#  .env.example ADDITIONS  (print to show operator)
# ════════════════════════════════════════════════════════════

ENV_ADDITIONS = """
# ── LLM Provider (BYOK — Bring Your Own Key) ──────────────────────
# Choose ONE provider. Each provider needs its own API key.
# Leave LLM_API_KEY blank to use the rule-based fallback (no AI calls).
#
# PROVIDER OPTIONS:
#   openai      → GPT-4o, GPT-4-turbo, o1-mini  (best reasoning, paid)
#   anthropic   → Claude Sonnet/Opus            (great safety, paid)
#   gemini      → Gemini 2.0 Flash              (free tier available!)
#   groq        → Llama3, Mixtral               (fastest, free tier!)
#   mistral     → Mistral Large                 (cheap, fast, EU)
#   deepseek    → DeepSeek Chat                 (extremely cheap)
#   together    → 100+ open models              ($5 free credits)
#   ollama      → Local models (Llama3, Mistral) (free, private, no key)
#   openrouter  → Routes to 100+ models         (free models available)
#   custom      → Any OpenAI-compatible endpoint

LLM_PROVIDER=groq                          # ← Recommended for hackathon (free + fast)
LLM_API_KEY=gsk_your_groq_key_here         # Get free key: https://console.groq.com/keys
LLM_MODEL=llama-3.3-70b-versatile          # Auto-selected if left blank
LLM_BASE_URL=                              # Only needed for CUSTOM or Ollama non-default port

# Groq free key: https://console.groq.com/keys
# OpenAI key:    https://platform.openai.com/api-keys
# Anthropic key: https://console.anthropic.com/
# Gemini key:    https://aistudio.google.com/apikey
# DeepSeek key:  https://platform.deepseek.com/api_keys
# OpenRouter:    https://openrouter.ai/keys
# Ollama local:  https://ollama.com/download  (no API key needed)
"""

if __name__ == "__main__":
    # Quick test
    print("RUNECLAW LLM Provider System")
    print("=" * 50)
    print(f"Supported providers: {[p.value for p in LLMProvider]}")
    print("\nProvider Catalog:")
    for provider, info in PROVIDER_CATALOG.items():
        free = "✅ FREE TIER" if info.get("free_tier") else "💳 PAID"
        speed = info.get("speed", "?")
        print(f"  {provider.value:<12} | {free} | Speed: {speed:<10} | {info.get('default_model', '')}")
    print("\n.env additions:")
    print(ENV_ADDITIONS)
