"""
Global PTB error handler + /version (audit P1: no silent failures).

Before this, no `add_error_handler` was registered anywhere, so an uncaught
exception in any handler produced PTB's default (log-only) → the user got
silence. `_on_error` is the backstop: it logs with update_id correlation and
sends ONE generic reply that never contains the raw exception text (which can
carry secrets). `/version` surfaces the running version (previously absent).
"""

import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Chat, Message, Update, User

from bot.skills.telegram_handler import TelegramHandler


def _handler():
    return TelegramHandler.__new__(TelegramHandler)


def _update(uid=555):
    chat = Chat(id=uid, type="private")
    user = User(id=uid, first_name="T", is_bot=False)
    msg = Message(message_id=1,
                  date=datetime.datetime.now(datetime.timezone.utc),
                  chat=chat, from_user=user)
    return Update(update_id=42, message=msg)


class TestGlobalErrorHandler:
    async def test_logs_and_replies_generically(self):
        h = _handler()
        ctx = MagicMock()
        ctx.error = ValueError("boom API-KEY-SECRET-abc123")
        ctx.bot.send_message = AsyncMock()
        with patch("bot.skills.telegram_handler.system_log") as log:
            await h._on_error(_update(), ctx)
        log.error.assert_called()                       # logged server-side
        ctx.bot.send_message.assert_awaited_once()
        sent = ctx.bot.send_message.call_args.kwargs["text"]
        assert "SECRET" not in sent                     # never leaks exc text
        assert "broke" in sent.lower() or "wrong" in sent.lower()

    async def test_operator_diagnostic_shows_exception_type_not_message(self):
        # For the operator (no allowlist configured in tests → allowlisted), the
        # reply appends the exception CLASS so a systemic "everything breaks"
        # failure is categorisable without server-log access — but never the
        # message, which a forwarded screenshot could leak (F-15).
        h = _handler()
        ctx = MagicMock()
        ctx.error = ConnectionError("cannot reach api.bitget.com KEY-abc123")
        ctx.bot.send_message = AsyncMock()
        with patch("bot.skills.telegram_handler.system_log"):
            await h._on_error(_update(), ctx)
        sent = ctx.bot.send_message.call_args.kwargs["text"]
        assert "ConnectionError" in sent                 # type IS shown
        assert "api.bitget.com" not in sent               # message is NOT
        assert "abc123" not in sent                       # no secret-ish token
        assert "operator diagnostic" in sent

    async def test_non_update_logs_but_does_not_reply(self):
        h = _handler()
        ctx = MagicMock()
        ctx.error = RuntimeError("x")
        ctx.bot.send_message = AsyncMock()
        with patch("bot.skills.telegram_handler.system_log"):
            await h._on_error(None, ctx)                # error not tied to an Update
        ctx.bot.send_message.assert_not_awaited()

    async def test_never_raises_when_send_fails(self):
        h = _handler()
        ctx = MagicMock()
        ctx.error = RuntimeError("x")
        ctx.bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
        with patch("bot.skills.telegram_handler.system_log"):
            await h._on_error(_update(), ctx)           # must swallow, not raise


class TestVersionCommand:
    async def test_replies_with_version(self):
        from bot import __version__
        h = _handler()
        h._limiter = MagicMock()
        h._limiter.allow.return_value = True
        h._send = AsyncMock()
        await h._cmd_version(_update(), MagicMock())
        h._send.assert_awaited_once()
        text = h._send.call_args.args[1]
        assert __version__ in text
        assert "RUNECLAW" in text

    async def test_rate_limited_is_silent(self):
        h = _handler()
        h._limiter = MagicMock()
        h._limiter.allow.return_value = False
        h._send = AsyncMock()
        await h._cmd_version(_update(), MagicMock())
        h._send.assert_not_awaited()


class TestRegistration:
    def test_error_handler_registered_in_build_app(self):
        src = Path(__file__).resolve().parent.parent / "bot/skills/telegram_handler.py"
        assert "add_error_handler(self._on_error)" in src.read_text(encoding="utf-8")

    def test_version_handler_registered(self):
        src = Path(__file__).resolve().parent.parent / "bot/skills/telegram_handler.py"
        assert '("version", self._cmd_version)' in src.read_text(encoding="utf-8")

    def test_version_matches_pyproject(self):
        from bot import __version__
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        assert f'version = "{__version__}"' in pyproject
