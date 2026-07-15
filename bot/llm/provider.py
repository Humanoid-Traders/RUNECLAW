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
        "recommended_models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
        "sdk": "anthropic",
        "free_tier": False,
        "speed": "medium",
        "cost": "medium",
        "notes": "Excellent reasoning, strong safety. Opus 4.8 for best trade analysis, Haiku for speed.",
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
    # routing_override (user-tier table) or admin → premium routing, skip env
    # tier overrides; otherwise the default cheap routing.
    use_table_directly = routing_override is not None or is_admin
    routing = (routing_override if routing_override is not None
               else (ADMIN_TIER_ROUTING if is_admin else DEFAULT_TIER_ROUTING))

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
                    }
                    fallback_env = key_env_map.get(tier_provider, "")
                    tier_key = os.getenv(fallback_env, "") if fallback_env else ""

                # Still no key? If the tier provider matches primary, use primary key
                if not tier_key and tier_provider == primary_config.provider:
                    tier_key = primary_config.api_key

                # LLM-2: only honor the override when a key is actually
                # available; otherwise fall through to default routing / primary
                # config rather than returning a keyless config that silently
                # runs the tier with no LLM (the default-routing branch below
                # already guards this way with `if alt_key:`).
                # Non-admin guard: this whole branch only runs when NOT
                # is_admin (use_table_directly is False here), so an explicit
                # env override asking for Anthropic must not be honored —
                # fall through to the next step instead.
                if tier_key and tier_provider != LLMProvider.ANTHROPIC:
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
        """True if an API key is set (or if Ollama local — no key needed)."""
        if self.provider == LLMProvider.OLLAMA:
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

async def llm_complete(
    client,
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    history: list[dict] | None = None,
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

            response = await client.messages.create(
                model=config.model,
                max_tokens=config.max_tokens,
                system=system_content,
                messages=messages,
                # Enable adaptive thinking for Opus 4.8+ models
                **( {"thinking": {"type": "adaptive"}}
                    if "opus" in config.model.lower() else {}
                ),
            )
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
            response = await client.chat.completions.create(
                model=config.model,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                messages=messages,
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
            if client is None and provider != LLMProvider.OLLAMA:
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
