"""AI-2: structured outputs for the thesis/voter JSON contract.

Schema-constrained JSON via output_config.format on models that support it
(Claude 5 family + Opus 4.6+) — eliminates parse failures in the 60/40 LLM
blend. The tolerant parser stays as the fallback layer for every other
provider, and every schema call carries a strip-and-retry net so an
unexpected rejection degrades to today's free-form behavior instead of
taking the brain down (the temperature-deprecation incident class).

Also fixes a latent forced-trade hazard: the old opus-only schema required
direction ∈ {LONG, SHORT}, so a schema-constrained model literally could not
answer "no trade". THESIS_JSON_SCHEMA admits null.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.llm.provider import (
    LLMConfig,
    LLMProvider,
    llm_complete,
    model_supports_structured_output,
    model_thinking_always_on,
)
from bot.core.analyzer import THESIS_JSON_SCHEMA, Analyzer


# ── model capability gates ─────────────────────────────────────────

class TestStructuredOutputGate:
    def test_claude5_family_and_opus46_plus_supported(self):
        for m in ("claude-sonnet-5", "claude-fable-5", "claude-mythos-5",
                  "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                  "claude-sonnet-5-20260203"):
            assert model_supports_structured_output(m), m

    def test_older_models_not_supported(self):
        for m in ("claude-3-opus-20240229", "claude-haiku-4-5-20251001",
                  "claude-sonnet-4-6", "gpt-4o", "gemini-2.5-pro", ""):
            assert not model_supports_structured_output(m), m

    def test_thinking_always_on_is_fable_mythos_only(self):
        assert model_thinking_always_on("claude-fable-5")
        assert model_thinking_always_on("claude-mythos-5")
        assert not model_thinking_always_on("claude-sonnet-5")
        assert not model_thinking_always_on("claude-opus-4-8")


# ── the thesis contract itself ─────────────────────────────────────

class TestThesisSchema:
    def test_direction_admits_null_no_trade(self):
        # A LONG/SHORT-only enum would FORCE a direction on every no-setup
        # answer — the schema must keep "no trade" expressible.
        d = THESIS_JSON_SCHEMA["properties"]["direction"]
        assert None in d["enum"]
        assert "null" in d["type"]

    def test_contract_is_exactly_what_the_parser_reads(self):
        assert set(THESIS_JSON_SCHEMA["required"]) == {
            "direction", "confidence", "reasoning"}
        assert THESIS_JSON_SCHEMA["additionalProperties"] is False
        conf = THESIS_JSON_SCHEMA["properties"]["confidence"]
        assert conf["minimum"] == 0 and conf["maximum"] == 1

    def test_parser_accepts_schema_shaped_no_trade(self):
        r = Analyzer._parse_llm_response(
            '{"direction": null, "confidence": 0.0, '
            '"reasoning": "No actionable setup"}')
        assert r["_parsed"] is True
        assert r["direction"] is None
        assert r["confidence"] == 0.0


# ── llm_complete wiring ────────────────────────────────────────────

def _resp(text='{"direction": null, "confidence": 0.0, "reasoning": "x"}',
          stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason)


class _FakeMessages:
    def __init__(self, outcomes):
        self.calls: list[dict] = []
        self._outcomes = list(outcomes)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self._outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class _FakeClient:
    def __init__(self, *outcomes):
        self.messages = _FakeMessages(outcomes)


def _cfg(model, effort=""):
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="sk-ant-test",
                     model=model, effort=effort)


class TestLlmCompleteSchema:
    def test_sonnet5_gets_output_config_format(self):
        client = _FakeClient(_resp())
        text = asyncio.run(llm_complete(
            client, _cfg("claude-sonnet-5"), "sys json", "user",
            json_schema=THESIS_JSON_SCHEMA))
        assert text.startswith('{"direction"')
        kw = client.messages.calls[0]
        fmt = kw["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["schema"] is THESIS_JSON_SCHEMA
        assert "thinking" not in kw

    def test_fable5_merges_schema_with_effort(self):
        # AI-1's effort dial and AI-2's schema ride in the SAME dict — the
        # schema attach must merge, never overwrite.
        client = _FakeClient(_resp())
        asyncio.run(llm_complete(
            client, _cfg("claude-fable-5", effort="high"), "sys json", "user",
            json_schema=THESIS_JSON_SCHEMA))
        oc = client.messages.calls[0]["output_config"]
        assert oc["effort"] == "high"
        assert oc["format"]["type"] == "json_schema"
        assert "thinking" not in client.messages.calls[0]

    def test_unsupported_model_never_gets_the_parameter(self):
        client = _FakeClient(_resp())
        asyncio.run(llm_complete(
            client, _cfg("claude-haiku-4-5-20251001"), "sys json", "user",
            json_schema=THESIS_JSON_SCHEMA))
        assert "output_config" not in client.messages.calls[0]

    def test_schema_rejection_strips_and_retries_once(self):
        client = _FakeClient(
            RuntimeError("invalid_request_error: output_config.format is "
                         "not supported for this model"),
            _resp())
        text = asyncio.run(llm_complete(
            client, _cfg("claude-sonnet-5"), "sys json", "user",
            json_schema=THESIS_JSON_SCHEMA))
        assert text
        assert len(client.messages.calls) == 2
        assert "output_config" in client.messages.calls[0]
        assert "output_config" not in client.messages.calls[1]

    def test_unrelated_error_is_not_swallowed_by_the_retry_net(self):
        client = _FakeClient(RuntimeError("429 rate limited"))
        with pytest.raises(RuntimeError, match="429"):
            asyncio.run(llm_complete(
                client, _cfg("claude-sonnet-5"), "sys json", "user",
                json_schema=THESIS_JSON_SCHEMA))
        assert len(client.messages.calls) == 1

    def test_refusal_still_raises_with_schema_active(self):
        client = _FakeClient(_resp(stop_reason="refusal"))
        with pytest.raises(RuntimeError, match="refusal"):
            asyncio.run(llm_complete(
                client, _cfg("claude-fable-5"), "sys json", "user",
                json_schema=THESIS_JSON_SCHEMA))
