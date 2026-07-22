"""AI-WEBSEARCH: real-time web search in the agent chat.

The web_search server tool is attached only on supported models and only when
the caller opts in (llm_complete web_search=True). The chat layer gates WHO may
opt in (admin/ULTRA only) elsewhere; these tests cover the provider contract:
tool attach, model gating, multi-block text extraction, citation collection,
and the strip-and-retry net that degrades to a plain answer on rejection.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.llm.provider import (
    LLMConfig,
    LLMProvider,
    WEB_SEARCH_TOOL_TYPE,
    llm_complete,
    model_supports_web_search,
)


# ── model capability gate ──────────────────────────────────────────

class TestWebSearchGate:
    def test_supported_models(self):
        for m in ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                  "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5",
                  "claude-mythos-5", "claude-sonnet-5-20260203"):
            assert model_supports_web_search(m), m

    def test_unsupported_models(self):
        for m in ("claude-3-opus-20240229", "claude-haiku-4-5-20251001",
                  "claude-opus-4-5", "gpt-4o", "gemini-2.5-pro", ""):
            assert not model_supports_web_search(m), m


# ── llm_complete wiring ────────────────────────────────────────────

def _cit(url, title):
    return SimpleNamespace(url=url, title=title)


def _text_block(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations or [])


def _search_result_block(results):
    return SimpleNamespace(
        type="web_search_tool_result",
        content=[SimpleNamespace(url=u, title=t) for u, t in results])


def _resp(content, stop_reason="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


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


def _cfg(model):
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="sk-ant-test",
                     model=model)


class TestWebSearchWiring:
    def test_supported_model_gets_the_tool(self):
        client = _FakeClient(_resp([_text_block("hi")]))
        asyncio.run(llm_complete(
            client, _cfg("claude-opus-4-8"), "sys", "what's new?",
            web_search=True))
        tools = client.messages.calls[0]["tools"]
        assert tools[0]["type"] == WEB_SEARCH_TOOL_TYPE
        assert tools[0]["name"] == "web_search"
        assert tools[0]["max_uses"] >= 1

    def test_unsupported_model_never_gets_the_tool(self):
        client = _FakeClient(_resp([_text_block("hi")]))
        asyncio.run(llm_complete(
            client, _cfg("claude-haiku-4-5-20251001"), "sys", "q",
            web_search=True))
        assert "tools" not in client.messages.calls[0]

    def test_off_by_default(self):
        client = _FakeClient(_resp([_text_block("hi")]))
        asyncio.run(llm_complete(
            client, _cfg("claude-opus-4-8"), "sys", "q"))
        assert "tools" not in client.messages.calls[0]

    def test_multi_block_text_is_concatenated_under_search(self):
        # With tool use the answer spans several text blocks; all must survive.
        client = _FakeClient(_resp([
            _text_block("Let me check."),
            _search_result_block([("https://x.com", "X")]),
            _text_block("BTC is up today."),
        ]))
        out = asyncio.run(llm_complete(
            client, _cfg("claude-opus-4-8"), "sys", "q", web_search=True))
        assert "Let me check." in out and "BTC is up today." in out

    def test_citations_are_collected_from_text_then_results(self):
        cited = _text_block(
            "Fresh.", citations=[_cit("https://a.com/1", "Source A")])
        client = _FakeClient(_resp([
            cited,
            _search_result_block([("https://a.com/1", "Source A"),
                                  ("https://b.com/2", "Source B")]),
        ]))
        cites: list = []
        asyncio.run(llm_complete(
            client, _cfg("claude-opus-4-8"), "sys", "q",
            web_search=True, citations_out=cites))
        urls = [c["url"] for c in cites]
        assert "https://a.com/1" in urls
        assert "https://b.com/2" in urls
        # Deduped: the shared URL appears once.
        assert urls.count("https://a.com/1") == 1

    def test_tool_rejection_strips_and_retries_once(self):
        client = _FakeClient(
            RuntimeError("invalid_request: web_search tool is not supported"),
            _resp([_text_block("plain answer")]))
        out = asyncio.run(llm_complete(
            client, _cfg("claude-opus-4-8"), "sys", "q", web_search=True))
        assert out == "plain answer"
        assert len(client.messages.calls) == 2
        assert "tools" in client.messages.calls[0]
        assert "tools" not in client.messages.calls[1]

    def test_unrelated_error_is_not_swallowed(self):
        client = _FakeClient(RuntimeError("429 rate limited"))
        with pytest.raises(RuntimeError, match="429"):
            asyncio.run(llm_complete(
                client, _cfg("claude-opus-4-8"), "sys", "q", web_search=True))
        assert len(client.messages.calls) == 1

    def test_no_citations_collected_when_search_off(self):
        client = _FakeClient(_resp([_text_block("hi")]))
        cites: list = []
        asyncio.run(llm_complete(
            client, _cfg("claude-opus-4-8"), "sys", "q",
            web_search=False, citations_out=cites))
        assert cites == []


class TestChatGating:
    """The chat layer must only offer web search to admins, and never on the
    public (anonymous) path. Verified by source assertion — the full _llm_chat
    path pulls in the whole engine."""

    def test_admin_only_gate_present_in_source(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        assert "web_search_ok = is_admin and not public" in src
        # The tool is only attached on the operator's Anthropic candidate.
        assert "cfg.provider == LLMProvider.ANTHROPIC" in src
        assert "web_search=_cfor_search" in src
