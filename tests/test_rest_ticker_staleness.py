"""
REST ticker staleness guard for the live SL/TP monitor (deep-audit HIGH).

The executor's check_positions loop reads `last` from REST fetch_ticker to drive
trailing-stop tightening and local stop-outs. A frozen/old `last` (illiquidity,
partial outage) could drive a false tighten, a premature stop-out, or a missed
breach — the WS staleness guard (#134) does NOT cover this REST path.
_ticker_too_old gates it: when the ticker timestamp is older than the bound, the
symbol's local monitoring is skipped that cycle and the exchange stop protects it.
"""

from bot.core.live_executor import LiveExecutor

_old = LiveExecutor._ticker_too_old
_NOW = 1_000_000.0  # arbitrary epoch seconds


def _ticker(age_sec):
    return {"timestamp": (_NOW - age_sec) * 1000.0, "last": 100.0}


class TestTickerTooOld:
    def test_fresh_is_not_stale(self):
        assert _old(_ticker(5), 120.0, _NOW) is False

    def test_old_is_stale(self):
        assert _old(_ticker(300), 120.0, _NOW) is True

    def test_max_age_zero_disables(self):
        assert _old(_ticker(9999), 0.0, _NOW) is False

    def test_missing_timestamp_not_stale(self):
        # Can't verify freshness → don't disable monitoring.
        assert _old({"last": 100.0}, 120.0, _NOW) is False
        assert _old({"timestamp": None}, 120.0, _NOW) is False
        assert _old({"timestamp": 0}, 120.0, _NOW) is False

    def test_none_ticker_is_safe(self):
        assert _old(None, 120.0, _NOW) is False

    def test_boundary_is_inclusive_not_stale(self):
        assert _old(_ticker(120), 120.0, _NOW) is False        # exactly at bound
        assert _old(_ticker(120.5), 120.0, _NOW) is True       # just over

    def test_bad_timestamp_fails_safe(self):
        assert _old({"timestamp": "garbage"}, 120.0, _NOW) is False
