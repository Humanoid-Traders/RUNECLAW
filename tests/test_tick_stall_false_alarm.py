"""TICK_STALL false-alarm fix — a planned quiet-market sleep is not a hang.

Root-caused from production: the loop was "parked" at the inter-tick
`await asyncio.sleep(_compute_smart_scan_interval())`, whose smart-scan max
(600s default) meets the 600s stall threshold — so a ~15s tick + 600s
planned sleep = 615s since tick START tripped the alarm on a perfectly
healthy engine. The fix is precision, not a bigger constant: the engine
stamps _next_tick_due_ts before EVERY inter-tick sleep (success and
backoff), and the monitor never counts time inside a declared sleep
(+ grace) as a stall. A genuine hang leaves the stamp in the past, so real
alarms still fire.
"""
from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

from bot.core.proactive_monitor import ProactiveMonitor

THR = ProactiveMonitor.TICK_STALL_THRESHOLD_S
GRACE = ProactiveMonitor.TICK_DUE_GRACE_S


class TestPredicate:
    def test_the_production_incident_no_longer_alarms(self):
        # 615s since tick start, but the engine declared a 600s sleep that
        # ends 30s from now — healthy quiet market.
        now = time.monotonic()
        assert ProactiveMonitor._is_tick_stalled(
            now - 615.0, now, THR, next_due=now + 30.0) is False

    def test_inside_grace_after_due_is_still_healthy(self):
        now = time.monotonic()
        assert ProactiveMonitor._is_tick_stalled(
            now - 700.0, now, THR, next_due=now - GRACE + 1.0) is False

    def test_blown_plan_is_a_real_stall(self):
        # The declared wake-up came and went (plus grace) and no tick started.
        now = time.monotonic()
        assert ProactiveMonitor._is_tick_stalled(
            now - 900.0, now, THR, next_due=now - GRACE - 1.0) is True

    def test_no_stamp_falls_back_to_threshold_only(self):
        now = time.monotonic()
        assert ProactiveMonitor._is_tick_stalled(now - THR - 15, now, THR) is True
        assert ProactiveMonitor._is_tick_stalled(now - THR + 15, now, THR) is False

    def test_a_hang_inside_the_tick_still_fires(self):
        # Tick started long ago and the only stamp is from BEFORE it (the
        # previous sleep) — a parked await inside _tick leaves due stale.
        now = time.monotonic()
        assert ProactiveMonitor._is_tick_stalled(
            now - 800.0, now, THR, next_due=now - 800.0) is True


class TestCheckWiring:
    def test_check_reads_the_engine_stamp(self):
        # Engine says: asleep for another 200s. 615s since start — healthy.
        pm = ProactiveMonitor(SimpleNamespace(
            _last_tick_started_ts=time.monotonic() - 615.0,
            _next_tick_due_ts=time.monotonic() + 200.0))
        assert pm._check_engine_tick_stale() == []

    def test_check_still_fires_on_a_blown_plan(self):
        pm = ProactiveMonitor(SimpleNamespace(
            _last_tick_started_ts=time.monotonic() - 900.0,
            _next_tick_due_ts=time.monotonic() - GRACE - 60.0))
        alerts = pm._check_engine_tick_stale()
        assert len(alerts) == 1 and alerts[0].alert_type == "TICK_STALL"


class TestEngineStamps:
    def test_run_stamps_the_plan_before_both_sleeps(self):
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.run)
        assert src.count("self._next_tick_due_ts = time.monotonic() +") == 2
        # The stamp must come BEFORE the park, on both paths.
        for chunk in ("+ backoff\n", "+ _sleep_s\n"):
            assert chunk in src.replace("        ", "")  # indentation-agnostic

    def test_init_declares_the_stamp_none(self):
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.__init__)
        assert "_next_tick_due_ts: float | None = None" in src
