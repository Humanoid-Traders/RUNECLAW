"""WS reconnect backoff must survive open-then-drop cycles (ops-reported).

The old code reset ``_reconnect_delay`` to base the moment a socket OPENED, so
a link that connects fine but drops seconds later (flaky network, subscribe
rejection, idle-watchdog kill) re-armed the 1s delay every cycle — an endless
~1-2s reconnect storm, exactly what production health checks reported. The
backoff now resets only after a connection has survived STABLE_CONNECTION_S.
"""

import asyncio
import inspect
import time

import pytest

from bot.core import ws_feed
from bot.core.ws_feed import BitgetWSFeed, RECONNECT_BASE_S, STABLE_CONNECTION_S


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


def test_connect_no_longer_resets_backoff_immediately():
    src = inspect.getsource(BitgetWSFeed._connect_and_listen)
    assert "_reconnect_delay = RECONNECT_BASE_S" not in src
    assert "_conn_started_at = time.time()" in src


def test_outer_loop_resets_only_after_stable_connection():
    src = inspect.getsource(BitgetWSFeed._run_loop)
    # Reset is conditional on the connection having survived the window...
    assert "STABLE_CONNECTION_S" in src
    assert "_reconnect_delay = RECONNECT_BASE_S" in src
    # ...and a failed-before-connect attempt (started_at None) never resets.
    assert "self._conn_started_at = None" in src
    assert "self._conn_started_at is not None" in src


def test_unstable_cycle_keeps_doubling():
    # Simulate the outer loop's decision inline: a 5s-lived connection must
    # NOT reset the delay; a 60s+ one must.
    feed = BitgetWSFeed(["BTC/USDT:USDT"])
    feed._reconnect_delay = 16

    feed._conn_started_at = time.time() - 5  # dropped after 5s
    stable = (feed._conn_started_at is not None
              and time.time() - feed._conn_started_at >= STABLE_CONNECTION_S)
    assert stable is False

    feed._conn_started_at = time.time() - (STABLE_CONNECTION_S + 1)
    stable = (feed._conn_started_at is not None
              and time.time() - feed._conn_started_at >= STABLE_CONNECTION_S)
    assert stable is True


def test_stable_window_constant_sane():
    # Long enough to prove stability, far below the idle watchdog horizon x10.
    assert 10 <= STABLE_CONNECTION_S <= 600
    assert RECONNECT_BASE_S >= 1
    assert ws_feed.RECONNECT_MAX_S >= 30


# ── App-level heartbeat (Bitget "ping"/"pong") ──────────────────────

def test_keepalive_and_pong_handler_are_both_wired():
    # The pong handler existed before this change but nothing ever sent the
    # "ping" it answers. Assert the loop now completes the pair.
    run_src = inspect.getsource(BitgetWSFeed._connect_and_listen)
    assert "self._keepalive()" in run_src           # started alongside the read loop
    ka_src = inspect.getsource(BitgetWSFeed._keepalive)
    assert '"ping"' in ka_src or "'ping'" in ka_src  # sends Bitget's text ping
    handle_src = inspect.getsource(BitgetWSFeed._handle_message)
    assert "pong" in handle_src                       # still consumes the reply


@pytest.mark.asyncio
async def test_keepalive_sends_ping_on_interval(monkeypatch):
    monkeypatch.setattr(ws_feed, "PING_INTERVAL_S", 0.01)
    feed = BitgetWSFeed(["BTC/USDT:USDT"])
    feed._connected = True
    feed._ws = _FakeWS()
    task = asyncio.create_task(feed._keepalive())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert feed._ws.sent and all(m == "ping" for m in feed._ws.sent)


@pytest.mark.asyncio
async def test_keepalive_stops_promptly_on_stop_event(monkeypatch):
    monkeypatch.setattr(ws_feed, "PING_INTERVAL_S", 100)  # would block if not for stop
    feed = BitgetWSFeed(["BTC/USDT:USDT"])
    feed._connected = True
    feed._ws = _FakeWS()
    feed._stop_event.set()
    # Must return quickly (not wait the full 100s) and send nothing.
    await asyncio.wait_for(feed._keepalive(), timeout=1.0)
    assert feed._ws.sent == []


@pytest.mark.asyncio
async def test_keepalive_returns_quietly_when_socket_send_fails(monkeypatch):
    monkeypatch.setattr(ws_feed, "PING_INTERVAL_S", 0.01)
    feed = BitgetWSFeed(["BTC/USDT:USDT"])
    feed._connected = True

    class _DeadWS:
        async def send(self, msg):
            raise ConnectionError("socket dead")

    feed._ws = _DeadWS()
    # A dead socket must not raise out of the keepalive — the read loop handles
    # reconnection; the heartbeat just exits.
    await asyncio.wait_for(feed._keepalive(), timeout=1.0)
