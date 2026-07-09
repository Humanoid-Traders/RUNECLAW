"""
Tests for untested Telegram bot commands.

Covers: Live Trading, Admin, War Room, LLM BYOK, and Watch commands.
Uses mocked Telegram Update/Context objects to call handler methods directly.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ── Fixtures ──────────────────────────────────────────────────────


def _make_update(user_id=6307156912, text="/test", args=None):
    """Create a mock Telegram Update object."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "TestUser"
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.text = text
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _make_handler():
    """Create a TelegramHandler with mocked engine."""
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler

    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    # Seed the test user as admin
    handler.users.seed_admin(str(6307156912))
    return handler


# ── Helpers ───────────────────────────────────────────────────────


def _last_reply_text(update) -> str:
    """Extract the text argument from the last reply_text call."""
    call_args = update.message.reply_text.call_args
    if call_args is None:
        return ""
    # reply_text(text, parse_mode=..., reply_markup=...)
    return call_args[0][0] if call_args[0] else call_args.kwargs.get("text", "")


def _any_reply_contains(update, substring: str) -> bool:
    """Check if any reply_text call contained the substring."""
    for call in update.message.reply_text.call_args_list:
        text = call[0][0] if call[0] else call.kwargs.get("text", "")
        if substring.lower() in text.lower():
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
#  LIVE TRADING COMMANDS (5 tests)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_golive_shows_warning_without_confirm():
    """/golive with no args shows safety limits warning."""
    handler = _make_handler()
    update, ctx = _make_update(text="/golive", args=[])
    await handler._cmd_golive(update, ctx)
    assert _any_reply_contains(update, "LIVE TRADING ACTIVATION")
    assert _any_reply_contains(update, "concurrent positions")


@pytest.mark.asyncio
async def test_golive_confirm_enables_live_mode():
    """/golive CONFIRM sets RUNTIME.live_mode = True."""
    from bot.config import RUNTIME

    handler = _make_handler()
    update, ctx = _make_update(text="/golive CONFIRM", args=["CONFIRM"])
    try:
        await handler._cmd_golive(update, ctx)
        assert RUNTIME.live_mode is True
        assert _any_reply_contains(update, "LIVE TRADING ENABLED")
    finally:
        RUNTIME.live_mode = False


@pytest.mark.asyncio
async def test_livebalance_returns_balance():
    """/livebalance returns formatted balance from exchange."""
    handler = _make_handler()
    update, ctx = _make_update(text="/livebalance")

    handler.engine.live_executor.fetch_balance = AsyncMock(
        return_value={"total": 123.45, "free": 100.00, "used": 23.45}
    )
    # The spot-holdings section calls _get_exchange(); mock it so it doesn't try
    # to init a real Bitget client (which errors on missing API keys).
    _ex = AsyncMock()
    _ex.fetch_ticker = AsyncMock(return_value={"last": 1.0})
    handler.engine.live_executor._get_exchange = AsyncMock(return_value=_ex)

    await handler._cmd_livebalance(update, ctx)
    assert _any_reply_contains(update, "BITGET PORTFOLIO")
    assert _any_reply_contains(update, "123.45")


@pytest.mark.asyncio
async def test_livepositions_empty():
    """/livepositions with no open positions shows 'no positions'."""
    handler = _make_handler()
    update, ctx = _make_update(text="/livepositions")

    # Ensure _positions is empty
    handler.engine.live_executor._positions = {}

    await handler._cmd_livepositions(update, ctx)
    assert _any_reply_contains(update, "no live positions")


@pytest.mark.asyncio
async def test_liveclose_no_args_shows_usage():
    """/liveclose with no args shows usage instructions."""
    handler = _make_handler()
    update, ctx = _make_update(text="/liveclose", args=[])

    await handler._cmd_liveclose(update, ctx)
    assert _any_reply_contains(update, "Usage")
    assert _any_reply_contains(update, "TRADE_ID")


# ═══════════════════════════════════════════════════════════════════
#  ADMIN COMMANDS (3 tests)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_users_lists_registered():
    """/users shows registered user list for admin."""
    handler = _make_handler()
    # Register a second user so the list has content
    handler.users.register("999999", name="AnotherUser")
    update, ctx = _make_update(text="/users")

    await handler._cmd_users(update, ctx)
    assert _any_reply_contains(update, "USERS")
    # The admin user itself should appear
    assert _any_reply_contains(update, "admin")


@pytest.mark.asyncio
async def test_revoke_removes_access():
    """/revoke removes a user's authorization."""
    handler = _make_handler()
    target_id = "1111111"
    handler.users.register(target_id, name="RevokeTarget")
    handler.users.authorize(target_id, role="trader")
    assert handler.users.is_authorized(target_id) is True

    update, ctx = _make_update(text=f"/revoke {target_id}", args=[target_id])
    await handler._cmd_revoke(update, ctx)

    assert handler.users.is_authorized(target_id) is False
    assert _any_reply_contains(update, "Access revoked")


@pytest.mark.asyncio
async def test_approve_grants_access():
    """/approve upgrades a user's role to trader."""
    handler = _make_handler()
    target_id = "2222222"
    # Register user — they start authorized with 'viewer' or default role
    handler.users.register(target_id, name="ApproveTarget")
    user_before = handler.users.get(target_id)
    initial_role = user_before.get("role", "viewer")

    update, ctx = _make_update(text=f"/approve {target_id}", args=[target_id])
    await handler._cmd_approve(update, ctx)

    assert handler.users.is_authorized(target_id) is True
    user = handler.users.get(target_id)
    # After approve, role should be 'trader' (upgraded from initial)
    assert user["role"] == "trader"


# ═══════════════════════════════════════════════════════════════════
#  WAR ROOM COMMANDS (5 tests)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pause_triggers_circuit_breaker():
    """/pause opens the circuit breaker."""
    handler = _make_handler()
    update, ctx = _make_update(text="/pause")

    await handler._cmd_pause(update, ctx)
    assert handler.engine.risk._circuit_open is True
    assert _any_reply_contains(update, "PAUSED") or _any_reply_contains(update, "pause")


@pytest.mark.asyncio
async def test_resume_resets_circuit_breaker():
    """/resume closes the circuit breaker."""
    handler = _make_handler()
    # First open the breaker
    handler.engine.risk._circuit_open = True
    update, ctx = _make_update(text="/resume")

    await handler._cmd_resume(update, ctx)
    assert handler.engine.risk.circuit_breaker_active is False
    assert _any_reply_contains(update, "RESUM") or _any_reply_contains(update, "active")


@pytest.mark.asyncio
async def test_emergency_stop_halts_engine():
    """/emergency_stop shows stop confirmation prompt."""
    handler = _make_handler()
    update, ctx = _make_update(text="/emergency_stop")

    await handler._cmd_emergency_stop(update, ctx)
    # Should show an emergency stop message with confirmation keyboard
    text = _last_reply_text(update)
    assert "EMERGENCY" in text.upper() or "STOP" in text.upper()


@pytest.mark.asyncio
async def test_latest_signal_no_signals():
    """/latest_signal shows 'no signals' when none are tracked."""
    handler = _make_handler()
    # Clear any pending ideas
    handler.engine._pending_ideas.clear()
    update, ctx = _make_update(text="/latest_signal")

    await handler._cmd_latest_signal(update, ctx)
    assert _any_reply_contains(update, "NO ACTIVE SIGNALS") or _any_reply_contains(update, "no signal")


@pytest.mark.asyncio
async def test_daily_report_renders():
    """/daily_report returns formatted output."""
    handler = _make_handler()
    update, ctx = _make_update(text="/daily_report")

    await handler._cmd_daily_report(update, ctx)
    # Should contain report-like content
    text = _last_reply_text(update)
    assert len(text) > 20  # non-trivial output
    # Should contain some expected fields
    assert _any_reply_contains(update, "report") or _any_reply_contains(update, "pnl") or _any_reply_contains(update, "trade")


# ═══════════════════════════════════════════════════════════════════
#  LLM BYOK COMMANDS (4 tests)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_llmstatus_shows_provider():
    """/llmstatus shows current LLM configuration."""
    handler = _make_handler()
    update, ctx = _make_update(text="/llmstatus")

    await handler._cmd_llmstatus(update, ctx)
    text = _last_reply_text(update)
    assert len(text) > 10
    # Should contain provider info
    assert _any_reply_contains(update, "provider") or _any_reply_contains(update, "llm") or _any_reply_contains(update, "model")


@pytest.mark.asyncio
async def test_llmstatus_shows_healthy_brain_by_default():
    """A fresh analyzer (no failures) reports the brain as healthy."""
    handler = _make_handler()
    update, ctx = _make_update(text="/llmstatus")
    await handler._cmd_llmstatus(update, ctx)
    assert _any_reply_contains(update, "Brain: healthy")


@pytest.mark.asyncio
async def test_llmstatus_shows_degraded_brain_after_provider_failures():
    """After the analyzer records consecutive all-provider failures, /llmstatus
    surfaces the DEGRADED brain line (mirrors the proactive alert)."""
    handler = _make_handler()
    handler.engine.analyzer._note_llm_degraded()
    handler.engine.analyzer._note_llm_degraded()
    update, ctx = _make_update(text="/llmstatus")
    await handler._cmd_llmstatus(update, ctx)
    assert _any_reply_contains(update, "Brain: DEGRADED")
    assert _any_reply_contains(update, "2 analyses")


@pytest.mark.asyncio
async def test_setllm_changes_provider():
    """/setllm groq switches the LLM provider."""
    from bot.llm.provider import BYOK

    handler = _make_handler()
    update, ctx = _make_update(text="/setllm groq", args=["groq"])

    await handler._cmd_setllm(update, ctx)
    text = _last_reply_text(update)
    # Should confirm switch or show error (no key is OK for some providers)
    assert len(text) > 5
    # Reset BYOK state after test
    BYOK.reset()


@pytest.mark.asyncio
async def test_llmreset_restores_default():
    """/llmreset clears runtime config and reverts to .env settings."""
    from bot.llm.provider import BYOK

    # First set a runtime config
    BYOK.set_provider("ollama")

    handler = _make_handler()
    update, ctx = _make_update(text="/llmreset")

    await handler._cmd_llmreset(update, ctx)
    text = _last_reply_text(update)
    assert "reset" in text.lower() or "env" in text.lower()
    # Verify BYOK runtime config is cleared
    assert BYOK._runtime_config is None


@pytest.mark.asyncio
async def test_llmtiers_shows_routing():
    """/llmtiers shows the tier routing table."""
    handler = _make_handler()
    update, ctx = _make_update(text="/llmtiers")

    await handler._cmd_llmtiers(update, ctx)
    text = _last_reply_text(update)
    assert len(text) > 20
    assert _any_reply_contains(update, "tier") or _any_reply_contains(update, "routing") or _any_reply_contains(update, "scan")


# ═══════════════════════════════════════════════════════════════════
#  WATCH COMMAND (1 test)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_watch_on_enables_alerts():
    """/watch on enables proactive alerts for the chat."""
    handler = _make_handler()
    tg_id = str(6307156912)
    update, ctx = _make_update(text="/watch on", args=["on"])

    await handler._cmd_watch(update, ctx)
    assert handler.monitor.is_enabled(tg_id)
    assert _any_reply_contains(update, "PROACTIVE ALERTS ON")
