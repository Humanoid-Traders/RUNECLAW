"""
Regression: a blocked live execution must never be announced as "EXECUTED".

Real incident: /fullscan -> tap-to-confirm on a signal while the WebSocket was
disconnected. live_executor.execute() correctly refused (returning
"EXECUTION BLOCKED: system is in degraded mode (paused) -- WebSocket
disconnected") BEFORE placing any order. But scan_skill.callback_confirm_reject
classified success/failure with its own local prefix list that only checked
for a bare "BLOCKED:" prefix -- "EXECUTION BLOCKED:" never matched it, so the
handler rendered "(check) DYDX/USDT LONG EXECUTED" followed by the block
reason, telling the user a live position was opened when none was.

The fix routes classification through bot.core.live_executor's canonical
execution_indicates_failure() (already used by engine.confirm_trade and
already covered by test_audit_v7_fixes.py) instead of a second, drifted list.
"""

import types
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from telegram.ext import ContextTypes

import bot.skills.scan_skill as scan_skill
from bot.utils.models import RiskVerdict


def _make_update_and_context(result_text):
    query = MagicMock()
    query.data = "scan_confirm:DYDX/USDT:LONG:0.20462"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = types.SimpleNamespace(id=999)

    ohlcv = [[0, 0, 1.0, 0.9, 1.0, 100.0] for _ in range(30)]
    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv)
    exchange.fetch_ticker = AsyncMock(return_value={"last": 0.20462})

    scanner = MagicMock()
    scanner._get_exchange = AsyncMock(return_value=exchange)

    risk_result = types.SimpleNamespace(verdict=RiskVerdict.APPROVED, reason="OK")
    risk = MagicMock()
    risk.evaluate = MagicMock(return_value=risk_result)

    engine = MagicMock()
    engine.scanner = scanner
    engine.risk = risk
    engine._pending_ideas = {}
    engine._pending_atr = {}
    engine.confirm_trade = AsyncMock(return_value=result_text)

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"engine": engine}
    return update, context


@pytest.mark.asyncio
async def test_degraded_mode_block_is_not_announced_as_executed(monkeypatch):
    monkeypatch.setattr(np, "array", np.array)  # sanity no-op, keeps numpy real
    update, context = _make_update_and_context(
        "EXECUTION BLOCKED: system is in degraded mode (paused) — WebSocket disconnected"
    )

    await scan_skill.callback_confirm_reject(update, context)

    sent = update.callback_query.message.reply_text.call_args_list[-1]
    text = sent.args[0] if sent.args else sent.kwargs.get("text", "")
    assert "EXECUTED" not in text
    assert "Execution failed" in text
    assert "degraded mode" in text


@pytest.mark.asyncio
async def test_genuine_fill_is_still_announced_as_executed():
    update, context = _make_update_and_context("Filled at $0.20462, qty 48.7")

    await scan_skill.callback_confirm_reject(update, context)

    sent = update.callback_query.message.reply_text.call_args_list[-1]
    text = sent.args[0] if sent.args else sent.kwargs.get("text", "")
    assert "EXECUTED" in text


@pytest.mark.asyncio
async def test_confirm_trade_exception_does_not_leak_raw_text():
    """Audit F-15: a caught exception (e.g. a ccxt/auth error whose str()
    can contain the raw API key) must never reach the user verbatim -- only
    a friendly generic message, with the real exception logged server-side."""
    update, context = _make_update_and_context("Filled at $0.20462, qty 48.7")
    secret_bearing_error = RuntimeError("auth failed: api_key=SUPER_SECRET_TOKEN_123")
    context.bot_data["engine"].confirm_trade = AsyncMock(side_effect=secret_bearing_error)

    await scan_skill.callback_confirm_reject(update, context)

    sent = update.callback_query.message.reply_text.call_args_list[-1]
    text = sent.args[0] if sent.args else sent.kwargs.get("text", "")
    assert "SUPER_SECRET_TOKEN_123" not in text
    assert "api_key" not in text
    assert "Something went wrong" in text
