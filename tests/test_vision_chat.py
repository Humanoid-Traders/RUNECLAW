"""AI-5: vision — the admin agent reads pasted chart/position screenshots.

Provider contract: images become Anthropic image content blocks on a Claude
model, are ignored on non-Claude providers, and malformed entries are skipped.
Chat contract (source-asserted): a Telegram photo handler exists, is admin-only,
and threads images into the vision-capable chat path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot.llm.provider import (
    LLMConfig,
    LLMProvider,
    llm_complete,
    model_supports_vision,
)


class TestVisionGate:
    def test_claude_models_support_vision(self):
        for m in ("claude-opus-4-8", "claude-sonnet-5", "claude-fable-5",
                  "claude-haiku-4-5-20251001", "claude-3-opus-20240229"):
            assert model_supports_vision(m), m

    def test_non_claude_models_do_not(self):
        for m in ("gpt-4o", "gemini-2.5-pro", "qwen3.6-plus", ""):
            assert not model_supports_vision(m), m


def _resp(text="a chart"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn")


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


class _FakeChat:
    def __init__(self, outcomes):
        self.calls = []
        self._o = list(outcomes)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="text-only"))])


class _FakeOAIClient:
    def __init__(self, *o):
        self.chat = SimpleNamespace(completions=_FakeChat(o))


def _cfg(provider, model):
    return LLMConfig(provider=provider, api_key="k", model=model)


IMG = [{"media_type": "image/jpeg", "data": "QUJD"}]


class TestImageBlocks:
    def test_images_become_image_blocks_on_claude(self):
        client = _FakeClient(_resp())
        asyncio.run(llm_complete(
            client, _cfg(LLMProvider.ANTHROPIC, "claude-opus-4-8"),
            "sys", "what is this?", images=IMG))
        content = client.messages.calls[0]["messages"][-1]["content"]
        assert isinstance(content, list)
        img = [b for b in content if b.get("type") == "image"]
        assert len(img) == 1
        assert img[0]["source"]["type"] == "base64"
        assert img[0]["source"]["media_type"] == "image/jpeg"
        assert img[0]["source"]["data"] == "QUJD"
        # The text prompt still rides after the image(s).
        assert any(b.get("type") == "text" for b in content)

    def test_no_images_keeps_plain_string_content(self):
        client = _FakeClient(_resp())
        asyncio.run(llm_complete(
            client, _cfg(LLMProvider.ANTHROPIC, "claude-opus-4-8"),
            "sys", "hello"))
        assert client.messages.calls[0]["messages"][-1]["content"] == "hello"

    def test_malformed_images_are_skipped_not_raised(self):
        client = _FakeClient(_resp())
        bad = [{"no_data": 1}, {"data": "OK", "media_type": "image/png"}]
        asyncio.run(llm_complete(
            client, _cfg(LLMProvider.ANTHROPIC, "claude-opus-4-8"),
            "sys", "q", images=bad))
        content = client.messages.calls[0]["messages"][-1]["content"]
        imgs = [b for b in content if b.get("type") == "image"]
        assert len(imgs) == 1  # only the valid one survives

    def test_images_ignored_on_non_claude_provider(self):
        client = _FakeOAIClient()
        asyncio.run(llm_complete(
            client, _cfg(LLMProvider.OPENAI, "gpt-4o"),
            "sys", "q", images=IMG))
        # OpenAI branch builds plain string content — no crash, no image block.
        msgs = client.chat.completions.calls[0]["messages"]
        assert msgs[-1]["content"] == "q"


class TestTelegramPhotoHandler:
    def test_photo_handler_registered_and_admin_gated(self):
        src = (Path(__file__).resolve().parent.parent
               / "bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        assert "filters.PHOTO, self._handle_photo" in src
        assert "async def _handle_photo(" in src
        # Admin gate: non-admins are turned away before any LLM spend.
        assert "if not self._is_admin(update):" in src
        # Vision is Anthropic + admin gated at the chat layer.
        assert "_vision_ok = (bool(images) and is_admin and not public" in src
