"""
WS idle-stall watchdog (deep-audit medium).

The WS read loop blocks on `async for raw in ws`, and ping/pong keepalive only
detects a truly dead socket. A feed that stays pong-alive while Bitget stops
pushing ticker data (subscription dropped server-side, half-open stall) would
freeze the price cache forever with no reconnect.

When CONFIG.execution.ws_idle_timeout_sec > 0, a watchdog forces a reconnect +
resubscribe (and alerts) if no WS message has arrived for that long while
connected. 0 disables → the read loop is byte-identical.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot.core.ws_feed as ws_feed_mod
from bot.core.ws_feed import BitgetWSFeed

_stalled = BitgetWSFeed._is_idle_stalled
_NOW = 1_000_000.0


class TestIsIdleStalled:
    def test_timeout_zero_never_stalled(self):
        assert _stalled(_NOW - 999, _NOW, 0.0) is False

    def test_not_seeded_not_stalled(self):
        assert _stalled(0.0, _NOW, 60.0) is False

    def test_within_window_not_stalled(self):
        assert _stalled(_NOW - 30, _NOW, 60.0) is False

    def test_exceeded_is_stalled(self):
        assert _stalled(_NOW - 90, _NOW, 60.0) is True

    def test_boundary_is_inclusive_not_stalled(self):
        assert _stalled(_NOW - 60, _NOW, 60.0) is False     # exactly at bound
        assert _stalled(_NOW - 60.5, _NOW, 60.0) is True    # just over


def _feed(monkeypatch, timeout):
    monkeypatch.setattr(ws_feed_mod, "CONFIG", SimpleNamespace(
        execution=SimpleNamespace(ws_idle_timeout_sec=timeout)))
    feed = BitgetWSFeed()
    feed._connected = True
    feed._ws = AsyncMock()  # .close() is awaitable
    return feed


class TestIdleWatchdog:
    def test_disabled_returns_without_closing(self, monkeypatch):
        feed = _feed(monkeypatch, 0.0)
        feed._last_msg_ts = time.time() - 9999  # very stale, but watchdog is OFF
        asyncio.run(feed._idle_watchdog())
        feed._ws.close.assert_not_called()

    def test_stalled_closes_socket(self, monkeypatch):
        feed = _feed(monkeypatch, 0.05)
        feed._last_msg_ts = time.time() - 100  # already stale
        asyncio.run(feed._idle_watchdog())
        feed._ws.close.assert_awaited_once()

    def test_stop_event_returns_without_closing(self, monkeypatch):
        feed = _feed(monkeypatch, 0.05)
        feed._last_msg_ts = time.time() - 100  # stale, but we ask it to stop
        feed._stop_event.set()
        asyncio.run(feed._idle_watchdog())
        feed._ws.close.assert_not_called()

    def test_fresh_feed_not_closed(self, monkeypatch):
        # A feed receiving data (fresh _last_msg_ts) is never force-closed.
        feed = _feed(monkeypatch, 5.0)

        async def _run():
            feed._last_msg_ts = time.time()
            task = asyncio.create_task(feed._idle_watchdog())
            await asyncio.sleep(0.05)
            feed._stop_event.set()
            await task

        asyncio.run(_run())
        feed._ws.close.assert_not_called()


class TestDefaultOn:
    def test_flag_defaults_on(self, monkeypatch):
        # Enabled by default (operator-requested activation); explicit env still wins.
        monkeypatch.delenv("WS_IDLE_TIMEOUT_SEC", raising=False)
        from bot.config import ExecutionConfig
        assert ExecutionConfig().ws_idle_timeout_sec == 90.0
