"""
_llm_chat() must (1) count against the SAME daily LLM budget analyzer.py's
trade-thesis calls already respect, and (2) record cost for every provider,
including Anthropic.

Real incident: the operator reported "my Claude tokens go fast, feels like
all users use my tokens." Root cause traced to bot/skills/telegram_handler.py
_llm_chat(): every authorized user's free-text message that doesn't match a
rule-based command intent triggers a live LLM call, billed to the operator's
single shared key (per-user BYOK is opt-in and off by default). That call had
NO budget check at all (daily_call_limit/daily_budget_usd were only enforced
in analyzer.py's signal-thesis path), and its cost-recording block explicitly
SKIPPED Anthropic (`if cfg.sdk_type() != "anthropic"`) -- so any chat reply
served by Claude (the configured chat provider, or the hardcoded
ANTHROPIC_API_KEY fallback baked into this function) was both unbounded AND
invisible to /costs.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import TelegramHandler as H


def _run(coro):
    return asyncio.run(coro)


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8):
        return []


def _stub(cost: CostTracker):
    engine = SimpleNamespace(cost=cost)
    return SimpleNamespace(
        engine=engine,
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="": "system prompt",
        _is_admin=lambda update: False,
    )


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture(autouse=True)
def _not_configured_chat_tier(monkeypatch):
    """Force resolve_tier_config()'s result to be unconfigured, so only the
    hardcoded env-var-driven fallback list in _llm_chat matters -- isolates
    the test from admin/tier routing table internals."""
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))


@pytest.fixture(autouse=True)
def _no_env_provider_keys(monkeypatch):
    """Clear every fallback-provider env var and the primary LLM_API_KEY so
    configs_to_try only contains whatever a test explicitly sets."""
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "CONFIG",
                         replace(th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="")))


def _budget_config(*, daily_call_limit=500, daily_budget_usd=1.0):
    return replace(th_mod.CONFIG, llm=replace(
        th_mod.CONFIG.llm, api_key="",
        daily_call_limit=daily_call_limit, daily_budget_usd=daily_budget_usd))


class TestBudgetGuard:
    def test_refuses_to_call_once_daily_call_limit_reached(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(th_mod, "CONFIG", _budget_config(daily_call_limit=1))
        cost = CostTracker()
        cost.record_llm(model="claude-haiku-4-5-20251001", prompt_tokens=10, completion_tokens=10)
        assert cost.snapshot().llm_calls == 1

        create_client_mock = AsyncMock()
        monkeypatch.setattr(th_mod, "create_llm_client", create_client_mock)

        answer = _run(H._llm_chat(_stub(cost), "hello"))

        assert "budget" in answer.lower()
        create_client_mock.assert_not_called()

    def test_refuses_to_call_once_daily_dollar_budget_reached(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(th_mod, "CONFIG", _budget_config(daily_budget_usd=0.01))
        cost = CostTracker()
        cost.record_llm(model="claude-sonnet-4-6", prompt_tokens=1_000_000, completion_tokens=0)
        assert cost.snapshot().llm_cost_usd >= 0.01

        create_client_mock = AsyncMock()
        monkeypatch.setattr(th_mod, "create_llm_client", create_client_mock)

        answer = _run(H._llm_chat(_stub(cost), "hello"))

        assert "budget" in answer.lower()
        create_client_mock.assert_not_called()

    def test_dated_anthropic_id_arms_the_dollar_budget(self, monkeypatch):
        """Live incident 2026-07-11: operator set all 4 tiers to
        claude-sonnet-4-20250514 (a dated ID not in the exact price table).
        Every call booked $0.00 UNPRICED, silently DISARMING the daily dollar
        budget — with a paid key that meant unbounded spend up to the call
        limit. The family-prefix fallback must price it so the guard binds."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(th_mod, "CONFIG", _budget_config(daily_budget_usd=0.01))
        cost = CostTracker()
        cost.record_llm(model="claude-sonnet-4-20250514",
                        prompt_tokens=1_000_000, completion_tokens=0)
        snap = cost.snapshot()
        assert snap.llm_cost_usd >= 0.01      # priced, not $0
        assert snap.unpriced_calls == 0       # no longer counted as unknown

        create_client_mock = AsyncMock()
        monkeypatch.setattr(th_mod, "create_llm_client", create_client_mock)
        answer = _run(H._llm_chat(_stub(cost), "hello"))
        assert "budget" in answer.lower()
        create_client_mock.assert_not_called()

    def test_family_fallback_prices_unknown_dated_ids(self):
        from bot.core.cost import resolve_llm_price
        # Any future dated Anthropic ID prices by family, never $0.
        for mid, lo in (("claude-sonnet-4-5-20250929", 3.0),
                        ("claude-opus-4-7-20260105", 15.0),
                        ("claude-haiku-4-5-20251001", 0.8),
                        # Non-Anthropic routing defaults are now family-priced too
                        # so they can't book $0 and disarm the budget guard.
                        ("llama-3.3-70b-versatile", 0.20),
                        ("gemini-2.5-pro", 1.25),
                        ("qwen3.6-flash", 0.40)):
            price, _ = resolve_llm_price(mid)
            assert price is not None and price["in"] == lo
        # A genuinely unknown model still stays unpriced (tracked as unknown-cost).
        price, exact = resolve_llm_price("acme-frontier-9000")
        assert price is None and exact is False

    def test_calls_through_when_under_budget(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(th_mod, "CONFIG", _budget_config())
        cost = CostTracker()

        monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
        monkeypatch.setattr(th_mod, "llm_complete", AsyncMock(return_value="hi there"))

        answer = _run(H._llm_chat(_stub(cost), "hello"))

        assert answer == "hi there"


class TestAnthropicCostIsTracked:
    def test_anthropic_chat_reply_is_recorded_not_skipped(self, monkeypatch):
        """The exact regression: before this fix, a successful Anthropic chat
        reply recorded ZERO cost/calls, making it invisible to /costs and the
        budget guard above. Anthropic is admin-only (see TestNonAdminNeverGetsAnthropic
        below), so this exercises the admin path."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(th_mod, "CONFIG", _budget_config())
        cost = CostTracker()

        monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
        monkeypatch.setattr(th_mod, "llm_complete", AsyncMock(return_value="a" * 400))

        answer = _run(H._llm_chat(_stub(cost), "hello", is_admin=True))

        assert answer == "a" * 400
        snap = cost.snapshot()
        assert snap.llm_calls == 1
        # "chat" isn't one of cost.py's tracked categories (scan/analyze/
        # thesis/risk_decision/other), so it folds into "other" -- the
        # regression this guards is llm_calls/cost being recorded AT ALL.
        assert snap.calls_by_category.get("other", 0) == 1
        # completion_tokens estimated from the real answer length (~4 chars/token)
        assert snap.completion_tokens == 100

    def test_openai_compatible_chat_reply_is_still_recorded(self, monkeypatch):
        """Control: the previously-working (non-Anthropic) accounting path
        must keep working after the fix."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(th_mod, "CONFIG", _budget_config())
        cost = CostTracker()

        monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
        monkeypatch.setattr(th_mod, "llm_complete", AsyncMock(return_value="ok"))

        _run(H._llm_chat(_stub(cost), "hello"))

        assert cost.snapshot().llm_calls == 1
