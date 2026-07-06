"""
Regression tests for LiveExecutor.check_degradation WS-freshness sourcing.

Root cause (fixed here): the executor's _ws_last_seen shadow clock is refreshed
only once per engine scan tick, and smart-scan stretches that tick to ~90s in a
calm market — longer than ws_disconnect_pause_sec (60s). So check_degradation
would falsely return "paused" ("real-time price feed disconnected") during the
tail of every quiet cycle even though the WS socket was streaming a tick every
second. The fix reads the feed's real last-message age (seconds_since_last_msg)
when a feed handle is wired, falling back to the shadow clock otherwise.
"""

from __future__ import annotations

import time

from bot.core.live_executor import LiveExecutor


class _FakeFeed:
    """Minimal stand-in exposing the freshness accessor check_degradation uses."""

    def __init__(self, age, *, raises: bool = False):
        self._age = age
        self._raises = raises

    def seconds_since_last_msg(self):
        if self._raises:
            raise RuntimeError("boom")
        return self._age

    def is_connected(self):
        return True


def _stale_executor() -> LiveExecutor:
    """Executor whose shadow clock is well past the pause threshold (>60s)."""
    ex = LiveExecutor()
    ex._ws_last_seen = time.time() - 200.0  # shadow clock says "disconnected"
    ex._degraded_mode = False
    return ex


def test_fresh_feed_overrides_stale_shadow_clock():
    """The bug: a healthy, streaming feed must NOT be reported as paused just
    because the per-tick shadow clock aged past the threshold between scans."""
    ex = _stale_executor()
    ex._ws_feed = _FakeFeed(age=1.0)  # real data is 1s old — feed is healthy
    assert ex.check_degradation() == "normal"
    assert ex._degraded_mode is False


def test_genuinely_stale_feed_still_pauses():
    """A real disconnect (feed's last message is old) must still pause."""
    ex = _stale_executor()
    ex._ws_feed = _FakeFeed(age=200.0)  # no fresh data for 200s
    assert ex.check_degradation() == "paused"
    assert ex._degraded_mode is True


def test_no_feed_ref_falls_back_to_shadow_clock():
    """Paper/tests with no feed wired keep the prior behaviour: a stale shadow
    clock pauses. Guarantees the fix doesn't change the no-feed path."""
    ex = _stale_executor()
    assert ex._ws_feed is None
    assert ex.check_degradation() == "paused"


def test_feed_none_age_falls_back_to_shadow_clock():
    """Before the first message, seconds_since_last_msg() returns None; the
    check must fall back to the shadow clock rather than crash or mis-read."""
    ex = LiveExecutor()
    ex._ws_last_seen = time.time()  # shadow clock fresh
    ex._ws_feed = _FakeFeed(age=None)
    assert ex.check_degradation() == "normal"


def test_feed_error_is_failsafe_to_shadow_clock():
    """Any error reading the feed must not raise; it falls back to the shadow
    clock (here fresh → normal), never blocking on an unexpected feed fault."""
    ex = LiveExecutor()
    ex._ws_last_seen = time.time()  # shadow clock fresh
    ex._ws_feed = _FakeFeed(age=None, raises=True)
    assert ex.check_degradation() == "normal"
