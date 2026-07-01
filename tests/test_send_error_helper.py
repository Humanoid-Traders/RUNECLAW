"""
_send_error() must never leak raw exception text to the user, and must always
log the real exception server-side.

Real incident: several admin commands (/equitycurve, /crossasset, /slippage,
/journal, /flags, /strategy) sent f"Error: {exc}" directly via
context.bot.send_message(), bypassing this class's _send() chokepoint --
which is where secret-redaction (Audit F-15) lives -- and never logging the
exception anywhere, so a failure was invisible to the operator once the raw
(unredacted) text reached Telegram.
"""

import asyncio
from unittest.mock import patch

from bot.skills.telegram_handler import TelegramHandler as H


class _StubSelf:
    def __init__(self):
        self.sent = []

    async def _send(self, update, text, **kwargs):
        self.sent.append((update, text))


class TestSendError:
    def test_sends_friendly_message_without_raw_exception_text(self):
        stub = _StubSelf()
        exc = Exception("api_key=SECRET_TOKEN_123 invalid")

        with patch("bot.skills.telegram_handler.system_log") as mock_log:
            asyncio.run(H._send_error(stub, "update", "the equity curve report", exc))

        assert len(stub.sent) == 1
        _, text = stub.sent[0]
        assert "SECRET_TOKEN_123" not in text
        assert "api_key" not in text
        assert "the equity curve report" in text

    def test_logs_the_real_exception_server_side(self):
        stub = _StubSelf()
        exc = ValueError("boom")

        with patch("bot.skills.telegram_handler.system_log") as mock_log:
            asyncio.run(H._send_error(stub, "update", "the trade journal", exc))

        assert mock_log.error.called
        args, kwargs = mock_log.error.call_args
        assert "the trade journal" in args
        assert exc in args
        assert kwargs.get("exc_info") is True

    def test_different_command_names_are_reflected_in_the_message(self):
        stub = _StubSelf()
        asyncio.run(H._send_error(stub, "update", "the feature flags", Exception("x")))
        assert "the feature flags" in stub.sent[0][1]
