"""
Runtime venue switching (2026-07-12): persisted override, engine hot-swap,
and the /venue Telegram command.

The feature contract:
  - override file > VENUE env; corrupt/unknown override never trade-blocks
  - engine.switch_venue refuses with open positions, re-wires the four
    executor collaborators, closes the old executor, persists the choice,
    and rolls the override back if the new executor can't be built
  - /venue is admin-only, preflights the target venue before switching,
    and never switches when preflight fails
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.core.venues as venues
from bot.core.venues import get_venue, get_venue_override, set_venue_override


@pytest.fixture(autouse=True)
def _isolated_override(tmp_path, monkeypatch):
    monkeypatch.setattr(venues, "VENUE_OVERRIDE_FILE",
                        str(tmp_path / "venue_override.json"))
    yield


# ── override persistence ─────────────────────────────────────────────
def test_override_round_trip():
    assert get_venue_override() is None
    assert get_venue().id == "bitget"          # env default
    set_venue_override("hyperliquid")
    assert get_venue_override() == "hyperliquid"
    assert get_venue().id == "hyperliquid"     # override wins over env
    set_venue_override(None)
    assert get_venue_override() is None
    assert get_venue().id == "bitget"


def test_override_survives_reload_from_disk():
    set_venue_override("hyperliquid")
    # simulate a fresh process: nothing cached, read straight from disk
    assert get_venue_override() == "hyperliquid"


def test_corrupt_override_file_is_ignored():
    with open(venues.VENUE_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert get_venue_override() is None
    assert get_venue().id == "bitget"


def test_unknown_venue_in_override_file_is_ignored():
    with open(venues.VENUE_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        f.write('{"venue": "binance"}')
    assert get_venue_override() is None


def test_set_override_rejects_unknown_id():
    with pytest.raises(ValueError):
        set_venue_override("binance")


def test_explicit_arg_bypasses_override():
    set_venue_override("hyperliquid")
    assert get_venue("bitget").id == "bitget"


def test_clear_when_no_file_is_noop():
    set_venue_override(None)  # must not raise


# ── engine.switch_venue ──────────────────────────────────────────────
class _FakeExec:
    def __init__(self):
        self._venue = get_venue()
        self.on_position_closed = None
        self._risk_engine = None
        self._ws_feed = None
        self._slippage_tracker = None
        self.open_positions: list = []
        self.closed = False

    async def close(self):
        self.closed = True


def _make_engine(monkeypatch):
    import bot.core.engine as engine_mod
    monkeypatch.setattr(engine_mod, "LiveExecutor", _FakeExec)
    eng = engine_mod.RuneClawEngine.__new__(engine_mod.RuneClawEngine)
    eng.live_executor = _FakeExec()          # venue = current default (bitget)
    eng.risk = object()
    eng.ws_feed = object()
    eng.slippage = object()
    eng._on_live_position_closed = lambda pos: None
    return eng


def test_switch_venue_happy_path(monkeypatch):
    eng = _make_engine(monkeypatch)
    old = eng.live_executor
    res = asyncio.run(eng.switch_venue("hyperliquid"))
    assert res.startswith("switched")
    assert eng.live_executor is not old
    assert eng.live_executor._venue.id == "hyperliquid"
    # the four wiring lines from engine __init__ were re-run
    assert eng.live_executor._risk_engine is eng.risk
    assert eng.live_executor._ws_feed is eng.ws_feed
    assert eng.live_executor._slippage_tracker is eng.slippage
    assert callable(eng.live_executor.on_position_closed)
    assert old.closed is True
    assert get_venue_override() == "hyperliquid"   # survives restart


def test_switch_venue_blocked_with_open_positions(monkeypatch):
    eng = _make_engine(monkeypatch)
    eng.live_executor.open_positions = [
        SimpleNamespace(symbol="BTC/USDT:USDT")]
    old = eng.live_executor
    res = asyncio.run(eng.switch_venue("hyperliquid"))
    assert res.startswith("REFUSED")
    assert eng.live_executor is old
    assert old.closed is False
    assert get_venue_override() is None            # nothing persisted


def test_switch_venue_same_venue_is_noop(monkeypatch):
    eng = _make_engine(monkeypatch)
    res = asyncio.run(eng.switch_venue("bitget"))
    assert "already" in res
    assert get_venue_override() is None


def test_switch_venue_rolls_back_override_on_build_failure(monkeypatch):
    import bot.core.engine as engine_mod
    eng = _make_engine(monkeypatch)
    old = eng.live_executor

    class _Boom:
        def __init__(self):
            raise RuntimeError("no wallet configured")

    monkeypatch.setattr(engine_mod, "LiveExecutor", _Boom)
    res = asyncio.run(eng.switch_venue("hyperliquid"))
    assert res.startswith("FAILED")
    assert eng.live_executor is old                # old executor still active
    assert old.closed is False
    assert get_venue_override() is None            # rolled back to env


# ── /venue command ───────────────────────────────────────────────────
def _make_update(user_id=6307156912, args=None):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = "TestUser"
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _replies(update) -> str:
    out = []
    for call in update.message.reply_text.call_args_list:
        out.append(call[0][0] if call[0] else call.kwargs.get("text", ""))
    return "\n".join(out)


def _make_handler():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(6307156912))
    return handler


@pytest.mark.asyncio
async def test_venue_command_is_admin_only():
    handler = _make_handler()
    update, ctx = _make_update(user_id=999999)   # not admin
    await handler._cmd_venue(update, ctx)
    text = _replies(update)
    assert "🔒" in text or "admin" in text.lower()
    assert "Trading venue" not in text


@pytest.mark.asyncio
async def test_venue_status_lists_both_venues():
    handler = _make_handler()
    update, ctx = _make_update(args=[])
    await handler._cmd_venue(update, ctx)
    text = _replies(update)
    assert "Trading venue" in text
    assert "Bitget" in text and "Hyperliquid" in text
    assert "/venue hyperliquid" in text          # usage line


@pytest.mark.asyncio
async def test_venue_unknown_id_rejected():
    handler = _make_handler()
    update, ctx = _make_update(args=["binance"])
    await handler._cmd_venue(update, ctx)
    assert "unknown venue" in _replies(update).lower()


@pytest.mark.asyncio
async def test_venue_switch_refused_without_credentials(monkeypatch):
    """Target venue with no configured credentials must refuse BEFORE any
    network call or state change."""
    handler = _make_handler()
    hl = venues._VENUES["hyperliquid"]
    monkeypatch.setattr(type(hl), "has_operator_credentials",
                        lambda self, cfg: False)
    update, ctx = _make_update(args=["hyperliquid"])
    await handler._cmd_venue(update, ctx)
    text = _replies(update)
    assert "no credentials" in text.lower()
    assert "HYPERLIQUID_WALLET_ADDRESS" in text
    assert get_venue_override() is None


@pytest.mark.asyncio
async def test_venue_switch_happy_path_with_preflight(monkeypatch):
    handler = _make_handler()
    hl = venues._VENUES["hyperliquid"]
    monkeypatch.setattr(type(hl), "has_operator_credentials",
                        lambda self, cfg: True)

    probe = MagicMock()
    probe.fetch_balance = AsyncMock(return_value={
        "USDC": {"free": 88.0, "total": 109.0}})
    probe.close = AsyncMock()
    monkeypatch.setattr(type(hl), "create_exchange",
                        lambda self, cfg, credentials=None: probe)
    handler.engine.switch_venue = AsyncMock(
        return_value="switched: bitget → hyperliquid")

    update, ctx = _make_update(args=["hyperliquid"])
    await handler._cmd_venue(update, ctx)
    text = _replies(update)
    assert "Venue switched" in text
    assert "109.00 USDC" in text
    handler.engine.switch_venue.assert_awaited_once_with("hyperliquid")
    probe.close.assert_awaited()                 # probe connection cleaned up


@pytest.mark.asyncio
async def test_venue_switch_aborts_on_preflight_failure(monkeypatch):
    """A venue that rejects the credentials must NOT be switched to."""
    handler = _make_handler()
    hl = venues._VENUES["hyperliquid"]
    monkeypatch.setattr(type(hl), "has_operator_credentials",
                        lambda self, cfg: True)

    probe = MagicMock()
    probe.fetch_balance = AsyncMock(side_effect=Exception("invalid signature"))
    probe.close = AsyncMock()
    monkeypatch.setattr(type(hl), "create_exchange",
                        lambda self, cfg, credentials=None: probe)
    handler.engine.switch_venue = AsyncMock()

    update, ctx = _make_update(args=["hyperliquid"])
    await handler._cmd_venue(update, ctx)
    text = _replies(update)
    assert "Preflight failed" in text
    assert "NOT switched" in text
    handler.engine.switch_venue.assert_not_awaited()
    probe.close.assert_awaited()
