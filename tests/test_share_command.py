"""NEWS-3b: the Telegram /share command — parity for share-to-agent.

/share <text> (or replying to a message with /share) saves a PRIVATE note to
the same encrypted per-user ingest store the web panel uses. User-supplied only
— the bot never fetches anything.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

ADMIN = 6307156912


@pytest.fixture()
def models(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
    import bot.db.models as m
    monkeypatch.setattr(m, "DB_PATH", Path(tmp_path / "t.db"))
    monkeypatch.setattr(m, "_LLM_CIPHER", None)
    m.init_db()
    return m


def _update(text="/share", args=None, reply_text=None, reply_source=None):
    up = MagicMock()
    up.effective_user = MagicMock(id=ADMIN, first_name="T")
    up.effective_chat = MagicMock(id=ADMIN)
    up.message = MagicMock()
    up.message.reply_text = AsyncMock()
    up.message.text = text
    up.callback_query = None
    if reply_text is None:
        up.message.reply_to_message = None
    else:
        r = MagicMock()
        r.text = reply_text
        r.caption = None
        r.forward_origin = None
        r.forward_from_chat = (MagicMock(title=reply_source) if reply_source else None)
        r.forward_from = None
        up.message.reply_to_message = r
    ctx = MagicMock()
    ctx.args = args or []
    return up, ctx


def _handler():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler(RuneClawEngine())
    h.users.seed_admin(str(ADMIN))
    return h


def _replies(up):
    return " ".join(
        (c[0][0] if c[0] else c.kwargs.get("text", ""))
        for c in up.message.reply_text.call_args_list)


def test_share_command_is_registered():
    import bot.skills.telegram_handler as th
    # the command name is wired in the handler map (source-level pin)
    src = Path(th.__file__).read_text()
    assert '("share", self._cmd_share)' in src


@pytest.mark.asyncio
async def test_share_text_saves_a_private_note(models):
    h = _handler()
    up, ctx = _update(args=["BTC", "ETF", "inflows", "hit", "record"])
    await h._cmd_share(up, ctx)
    assert "Saved" in _replies(up)
    uid = models.settings_user_id(str(ADMIN))
    notes = models.list_user_ingest_notes(uid)
    assert notes and notes[0]["body"] == "BTC ETF inflows hit record"


@pytest.mark.asyncio
async def test_bare_share_shows_help_and_saves_nothing(models):
    h = _handler()
    up, ctx = _update(args=[])
    await h._cmd_share(up, ctx)
    assert "Share with your agent" in _replies(up)
    uid = models.settings_user_id(str(ADMIN))
    assert models.list_user_ingest_notes(uid) == []


@pytest.mark.asyncio
async def test_reply_to_message_saves_that_text_with_source(models):
    h = _handler()
    up, ctx = _update(args=[], reply_text="Pendle shipped v3 today", reply_source="Bankless")
    await h._cmd_share(up, ctx)
    assert "Saved" in _replies(up)
    uid = models.settings_user_id(str(ADMIN))
    notes = models.list_user_ingest_notes(uid)
    assert notes[0]["body"] == "Pendle shipped v3 today"
    assert notes[0]["source"] == "Bankless"


@pytest.mark.asyncio
async def test_unauthorized_user_is_asked_to_register(models):
    h = _handler()
    up, ctx = _update(args=["x"])
    up.effective_user.id = 999          # not seeded → not authorized
    up.effective_chat.id = 999
    await h._cmd_share(up, ctx)
    assert "/start" in _replies(up)
    assert models.list_user_ingest_notes(models.settings_user_id("999")) == []
