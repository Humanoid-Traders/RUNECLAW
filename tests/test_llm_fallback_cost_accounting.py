"""
Cascading-fallback LLM cost accounting (deep-audit medium).

The primary-provider path increments the daily call counter and records
token/dollar cost, but _try_llm_fallback made real billable calls — including
the priciest provider, Anthropic Sonnet — without touching either counter. A
flapping primary could silently blow through BOTH the daily call limit and the
daily dollar cap via fallbacks.

When CONFIG.llm.fallback_cost_accounting_enabled is ON, each successful fallback
call increments _llm_calls_today and records cost (exact usage on OpenAI-compat
providers; a char-length estimate on the Anthropic path, whose helper discards
usage). Default OFF keeps accounting byte-identical to today.
"""

import asyncio
from types import SimpleNamespace

import bot.core.analyzer as analyzer_mod
from bot.core.analyzer import Analyzer
from bot.core.cost import CostTracker


# ── Fake OpenAI-compatible client whose response carries usage ──
class _FakeUsage:
    prompt_tokens = 1234
    completion_tokens = 321


class _FakeResp:
    usage = _FakeUsage()
    choices = [SimpleNamespace(message=SimpleNamespace(
        content='{"direction":"LONG","confidence":0.7,"reasoning":"ok"}'))]


class _FakeCompletions:
    async def create(self, **kwargs):
        return _FakeResp()


class _FakeClient:
    chat = SimpleNamespace(completions = _FakeCompletions())


def _signal():
    return SimpleNamespace(symbol="BTC/USDT", volume_spike=False, change_pct_24h=0.0)


def _run_fallback(monkeypatch, *, accounting: bool):
    """Drive _try_llm_fallback so exactly one OpenAI-compat provider (GROQ)
    succeeds, and return (analyzer, cost_tracker, result)."""
    # Only GROQ has a key → it's the provider that succeeds.
    for env in ("ALIBABA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
                "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    # Any client construction returns our fake.
    monkeypatch.setattr(analyzer_mod, "create_llm_client", lambda cfg: _FakeClient())

    # Build the analyzer under the REAL config (init reads it heavily), then
    # swap in a namespace that only flips the gate for the fallback call.
    analyzer = Analyzer()
    analyzer._llm_calls_today = 0
    cost = CostTracker()
    analyzer._cost = cost

    real_llm = analyzer_mod.CONFIG.llm
    fake_cfg = SimpleNamespace(llm=SimpleNamespace(
        temperature=real_llm.temperature,
        max_tokens=real_llm.max_tokens,
        timeout_seconds=real_llm.timeout_seconds,
        fallback_cost_accounting_enabled=accounting,
    ))
    monkeypatch.setattr(analyzer_mod, "CONFIG", fake_cfg)

    result = asyncio.run(analyzer._try_llm_fallback(
        "prompt text", _signal(), use_full_model=False, failed_provider="openai"))
    return analyzer, cost, result


class TestEstimateTokens:
    def test_empty_is_zero(self):
        assert Analyzer._estimate_tokens("") == 0

    def test_roughly_four_chars_per_token(self):
        assert Analyzer._estimate_tokens("a" * 400) == 100

    def test_non_empty_is_at_least_one(self):
        assert Analyzer._estimate_tokens("ab") == 1


class TestAccountingGate:
    def test_disabled_records_nothing(self, monkeypatch):
        analyzer, cost, result = _run_fallback(monkeypatch, accounting=False)
        # Fallback still succeeds and returns a parsed idea...
        assert result is not None and result["direction"] == "LONG"
        # ...but the counters are untouched (byte-identical legacy behaviour).
        assert analyzer._llm_calls_today == 0
        assert cost.snapshot().llm_calls == 0
        assert cost.snapshot().llm_cost_usd == 0.0

    def test_enabled_increments_call_counter(self, monkeypatch):
        analyzer, cost, result = _run_fallback(monkeypatch, accounting=True)
        assert result is not None and result["direction"] == "LONG"
        assert analyzer._llm_calls_today == 1

    def test_enabled_records_exact_usage_on_openai_path(self, monkeypatch):
        analyzer, cost, result = _run_fallback(monkeypatch, accounting=True)
        snap = cost.snapshot()
        assert snap.llm_calls == 1
        # Exact token usage from resp.usage flows into the cost tracker.
        assert snap.prompt_tokens == 1234
        assert snap.completion_tokens == 321


class TestDefaultOff:
    def test_flag_defaults_off(self):
        from bot.config import LLMConfig
        assert LLMConfig().fallback_cost_accounting_enabled is False
