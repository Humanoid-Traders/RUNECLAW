"""
Talk-to-your-agent stance controls.

Pins: risk-preference phrases classify to stance_* intents (and don't get
swallowed by scan/analyze rules), the proposal NEVER flips the mode itself
(only the permission-gated mode_ callback does), same-stance requests are a
friendly no-op, and /agent renders the posture with preset buttons.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.nlp.intent_router import IntentRouter


# ── intent classification ─────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("be more careful please", "stance_defensive"),
    ("reduce risk", "stance_defensive"),
    ("play it safe for a while", "stance_defensive"),
    ("use smaller positions", "stance_defensive"),
    ("be more aggressive", "stance_aggressive"),
    ("push harder", "stance_aggressive"),
    ("risk on", "stance_aggressive"),
    ("back to normal", "stance_balanced"),
    ("reset the risk", "stance_balanced"),
])
def test_stance_phrases_classify(text, expected):
    intent = IntentRouter().classify_rules(text)
    assert intent.skill == expected, f"{text!r} -> {intent.skill!r}"
    assert intent.confidence >= 0.8


def test_stance_rules_do_not_hijack_market_questions():
    r = IntentRouter()
    assert not r.classify_rules("risk check").skill.startswith("stance_")
    assert not r.classify_rules("scan the market").skill.startswith("stance_")
    assert not r.classify_rules("how's my risk level").skill.startswith("stance_")


# ── proposal flow ─────────────────────────────────────────────────────


def _handler():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    handler = TelegramHandler(RuneClawEngine())
    handler.users.seed_admin(str(6307156912))
    return handler


def _update(user_id=6307156912):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def _reply_text(update):
    call = update.message.reply_text.call_args
    return (call[0][0] if call and call[0] else
            call.kwargs.get("text", "")) if call else ""


def test_propose_stance_never_flips_the_mode():
    from bot.config import RUNTIME
    handler = _handler()
    update = _update()
    RUNTIME.strategy_mode = "balanced"
    try:
        asyncio.run(handler._propose_stance(update, "defensive"))
        # Mode untouched — the proposal only shows a confirm button.
        assert RUNTIME.strategy_mode == "balanced"
        text = _reply_text(update)
        assert "Proposed" in text and "Defensive" in text
        markup = update.message.reply_text.call_args.kwargs.get("reply_markup")
        flat = [b for row in markup.inline_keyboard for b in row]
        assert any(b.callback_data == "mode_defensive" for b in flat)
        assert any(b.callback_data == "stance_keep" for b in flat)
    finally:
        RUNTIME.strategy_mode = "balanced"


def test_propose_same_stance_is_a_friendly_noop():
    from bot.config import RUNTIME
    handler = _handler()
    update = _update()
    RUNTIME.strategy_mode = "balanced"
    asyncio.run(handler._propose_stance(update, "balanced"))
    text = _reply_text(update)
    assert "already" in text.lower()
    assert update.message.reply_text.call_args.kwargs.get("reply_markup") is None


def test_agent_command_shows_posture_and_presets():
    handler = _handler()
    update = _update()
    asyncio.run(handler._cmd_agent(update, MagicMock()))
    text = _reply_text(update)
    assert "posture" in text.lower()
    assert "be more careful" in text
    markup = update.message.reply_text.call_args.kwargs.get("reply_markup")
    flat = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert set(flat) == {"mode_defensive", "mode_balanced",
                         "mode_aggressive", "mode_manual"}


def test_mode_callback_still_permission_gated():
    """The stance buttons route to mode_*, which _handle_callback gates on
    the 'mode' permission — pin that mapping so a refactor can't silently
    open stance switching to unprivileged users."""
    import inspect
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._handle_callback)
    assert 'data.startswith("mode_")' in src
    assert '"mode"' in src
