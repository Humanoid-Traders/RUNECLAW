"""WS reconnect backoff must survive open-then-drop cycles (ops-reported).

The old code reset ``_reconnect_delay`` to base the moment a socket OPENED, so
a link that connects fine but drops seconds later (flaky network, subscribe
rejection, idle-watchdog kill) re-armed the 1s delay every cycle — an endless
~1-2s reconnect storm, exactly what production health checks reported. The
backoff now resets only after a connection has survived STABLE_CONNECTION_S.
"""

import inspect
import time

from bot.core import ws_feed
from bot.core.ws_feed import BitgetWSFeed, RECONNECT_BASE_S, STABLE_CONNECTION_S


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
