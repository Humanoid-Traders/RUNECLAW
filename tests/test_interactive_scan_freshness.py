"""Interactive-scan responsiveness gate (live "bot seems slow" fix).

When a "Latest Signal" tap finds nothing queued, the handler used to always
run a fresh 40-symbol scan on a 45s deadline — which, under exchange
throttling, just re-confirmed "nothing" slowly. The freshness gate serves an
instant honest status when the continuous background sweep is recent, and only
falls back to a live re-scan when that data is genuinely stale.

_background_scan_is_fresh is pure, so the decision is tested without the engine
or Telegram.
"""

from __future__ import annotations

from bot.skills.telegram_handler import _background_scan_is_fresh
from bot.config import CONFIG


class TestFreshnessGate:
    def test_recent_sweep_is_fresh_and_reports_next_sweep(self):
        # Swept 10s ago, 60s interval, 30s grace, now=1000.
        fresh, next_in = _background_scan_is_fresh(
            last_scan_time=990.0, interval=60.0, grace=30.0, now=1000.0)
        assert fresh is True
        assert next_in == 50          # 60 - 10s age

    def test_within_interval_plus_grace_is_still_fresh(self):
        # Age 85s, interval 60 + grace 30 = 90 window → still fresh.
        fresh, next_in = _background_scan_is_fresh(990.0, 60.0, 30.0, 1075.0)
        assert fresh is True
        assert next_in == 0           # past the interval, next sweep imminent

    def test_stale_sweep_falls_through_to_rescan(self):
        # Age 120s > 90s window → stale, must re-scan.
        fresh, next_in = _background_scan_is_fresh(990.0, 60.0, 30.0, 1110.0)
        assert fresh is False
        assert next_in == 0

    def test_grace_zero_disables_the_gate(self):
        # grace=0 restores always-rescan behavior even for a just-run sweep.
        fresh, _ = _background_scan_is_fresh(999.0, 60.0, 0.0, 1000.0)
        assert fresh is False

    def test_never_scanned_is_not_fresh(self):
        # last_scan_time=0 (fresh boot, loop hasn't run) → must re-scan.
        fresh, _ = _background_scan_is_fresh(0.0, 60.0, 30.0, 1000.0)
        assert fresh is False


class TestFreshnessConfigDefault:
    def test_grace_default_present_and_reversible(self):
        # Default enables the gate; env INTERACTIVE_SCAN_FRESH_GRACE_SEC=0
        # restores the old always-rescan path (documented reversibility).
        assert CONFIG.interactive_scan_fresh_grace_sec == 30
