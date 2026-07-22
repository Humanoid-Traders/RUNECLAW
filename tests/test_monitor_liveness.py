"""Reciprocal liveness watchdog (reliability audit — monitor loop).

The proactive monitor delivers EVERY internal safety alert, yet nothing
watched it: a dead or hung monitor task silently ended all alerting while
trading continued, and a HUNG engine tick (blocked await, no exception)
was invisible to the failure counters. Now each already-alive loop watches
the other: the monitor heartbeats and the engine's tick checks it (audit
CRITICAL + monitor-independent operator notify + task restart), while the
monitor watches the tick loop's START stamp for hangs. None sentinels
everywhere — the codebase's documented monotonic-epoch trap.
"""
import asyncio
import time
from types import SimpleNamespace


from bot.core.engine import RuneClawEngine
from bot.core.proactive_monitor import ProactiveMonitor


# ── Pure staleness predicates (mirror TestIsIdleStalled) ─────────────────────

class TestMonitorStalePredicate:
    def test_none_last_ran_is_never_stale(self):
        # Startup grace: monotonic epoch is arbitrary — None means "not yet".
        assert RuneClawEngine._is_monitor_stale(None, 1e9, 300.0) is False

    def test_zero_or_negative_timeout_disables(self):
        assert RuneClawEngine._is_monitor_stale(0.0, 1e9, 0.0) is False
        assert RuneClawEngine._is_monitor_stale(0.0, 1e9, -5.0) is False

    def test_within_window_not_stale_beyond_is(self):
        now = 10_000.0
        assert RuneClawEngine._is_monitor_stale(now - 299.0, now, 300.0) is False
        assert RuneClawEngine._is_monitor_stale(now - 300.0, now, 300.0) is False  # boundary
        assert RuneClawEngine._is_monitor_stale(now - 301.0, now, 300.0) is True


class TestTickStallPredicate:
    def test_none_is_never_stalled(self):
        assert ProactiveMonitor._is_tick_stalled(None, 1e9, 600.0) is False

    def test_threshold_clears_the_backoff_cap(self):
        # The tick loop legitimately sleeps up to 300s between failed ticks —
        # a 301s-old stamp must NOT read as a hang.
        now = 10_000.0
        thr = ProactiveMonitor.TICK_STALL_THRESHOLD_S
        assert thr >= 600.0, "threshold must be >= 2x the 300s backoff cap"
        assert ProactiveMonitor._is_tick_stalled(now - 301.0, now, thr) is False
        assert ProactiveMonitor._is_tick_stalled(now - thr - 1, now, thr) is True


# ── Monitor records its own heartbeat ────────────────────────────────────────

def test_monitor_run_records_heartbeat(monkeypatch):
    pm = ProactiveMonitor(SimpleNamespace())
    assert pm.last_loop_ts is None

    async def _stop_sleep(_s):
        pm._running = False

    monkeypatch.setattr(asyncio, "sleep", _stop_sleep)

    async def _send(chat_id, text, buttons=None):
        return None

    # _check_all on a bare SimpleNamespace engine raises inside the loop's
    # try/except — the heartbeat must be stamped BEFORE the checks, so it
    # survives even a fully-broken check pass.
    asyncio.run(pm.run(_send))
    assert isinstance(pm.last_loop_ts, float)
    assert abs(time.monotonic() - pm.last_loop_ts) < 30.0


# ── Engine-side monitor liveness check (mirror TestHealthcheckPing) ──────────

def _engine_stub():
    """Bare object with only the attrs _maybe_check_monitor_liveness touches."""
    e = SimpleNamespace()
    # None sentinel (never checked yet) — a 0.0 here with time.monotonic()
    # would suppress the first check on a freshly-booted host (CI caught
    # exactly this in the implementation).
    e._last_monitor_liveness_check = None
    e._proactive_monitor = None
    e._monitor_stale_callback = None
    e._is_monitor_stale = RuneClawEngine._is_monitor_stale
    return e


def _run_check(e):
    asyncio.run(RuneClawEngine._maybe_check_monitor_liveness(e))


def test_noop_without_monitor_attached():
    e = _engine_stub()
    _run_check(e)                                  # must not raise
    assert e._last_monitor_liveness_check is None


def test_first_check_works_on_a_freshly_booted_host(monkeypatch):
    # THE CI failure this guards: monotonic's epoch is BOOT time, so on a
    # freshly-booted host `monotonic() - 0.0 < timeout` read as "checked
    # recently" and suppressed the first window (passed locally on a
    # long-lived container, failed on CI runners). Simulate 200s of uptime
    # and require the None sentinel to let the very first check through.
    import bot.core.engine as eng_mod
    monkeypatch.setattr(eng_mod.time, "monotonic", lambda: 200.0)
    e = _engine_stub()
    e._proactive_monitor = SimpleNamespace(last_loop_ts=200.0 - 10_000)
    calls = []

    async def _cb(age):
        calls.append(age)

    e._monitor_stale_callback = _cb
    _run_check(e)
    assert len(calls) == 1, "first check must never be throttle-suppressed"


def test_stale_monitor_fires_callback_once_per_window():
    e = _engine_stub()
    e._proactive_monitor = SimpleNamespace(last_loop_ts=time.monotonic() - 10_000)
    calls = []

    async def _cb(age):
        calls.append(age)

    e._monitor_stale_callback = _cb
    _run_check(e)
    assert len(calls) == 1 and calls[0] > 9_000
    # Throttled: an immediate second check does nothing.
    _run_check(e)
    assert len(calls) == 1


def test_fresh_monitor_triggers_nothing():
    e = _engine_stub()
    e._proactive_monitor = SimpleNamespace(last_loop_ts=time.monotonic())
    calls = []

    async def _cb(age):
        calls.append(age)

    e._monitor_stale_callback = _cb
    _run_check(e)
    assert calls == []


def test_raising_callback_is_fail_open():
    e = _engine_stub()
    e._proactive_monitor = SimpleNamespace(last_loop_ts=time.monotonic() - 10_000)

    async def _boom(age):
        raise RuntimeError("telegram down")

    e._monitor_stale_callback = _boom
    _run_check(e)                                  # must not raise


def test_run_loop_wires_the_check_and_tick_stamps():
    import inspect
    assert "_maybe_check_monitor_liveness" in inspect.getsource(RuneClawEngine.run)
    assert "_last_tick_started_ts = time.monotonic()" in inspect.getsource(
        RuneClawEngine._tick)


# ── Monitor watches the engine tick for hangs (mirror test_proactive_alerts) ─

def _pm(last_started):
    pm = ProactiveMonitor(SimpleNamespace(_last_tick_started_ts=last_started))
    return pm


def test_tick_stall_fires_once_then_rearms():
    stale = time.monotonic() - ProactiveMonitor.TICK_STALL_THRESHOLD_S - 60
    pm = _pm(stale)
    first = pm._check_engine_tick_stale()
    assert len(first) == 1
    a = first[0]
    assert a.alert_type == "TICK_STALL" and a.severity == "CRITICAL"
    assert a.dedup_key == "tick_stall"
    assert "NOT" in a.body and "monitored" in a.body
    # Edge-triggered: still stalled → no re-fire.
    assert pm._check_engine_tick_stale() == []
    # Recovery re-arms; a later stall fires again.
    pm.engine._last_tick_started_ts = time.monotonic()
    assert pm._check_engine_tick_stale() == []
    pm.engine._last_tick_started_ts = stale
    assert len(pm._check_engine_tick_stale()) == 1


def test_tick_stall_never_fires_before_engine_starts():
    pm = _pm(None)
    assert pm._check_engine_tick_stale() == []


def test_tick_stall_in_check_all():
    import inspect
    assert "_check_engine_tick_stale" in inspect.getsource(ProactiveMonitor._check_all)


# ── Wiring pins (task-death callback + restart preserves the object) ─────────

def test_handler_wires_death_callback_restart_and_reference():
    import inspect
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler)
    assert "add_done_callback(_monitor_task_died)" in src
    assert "task.cancelled()" in src, "shutdown cancellation must not audit DIED"
    assert "self.engine._proactive_monitor = self.monitor" in src
    assert "self.engine._monitor_stale_callback" in src
    # Restart must reuse the SAME monitor object (dispatch hook + dedup state).
    assert "asyncio.create_task(self.monitor.run(_send_fn))" in src
    assert "ProactiveMonitor(" not in src.split("_on_monitor_stale")[1].split(
        "self.engine._monitor_stale_callback")[0], \
        "restart must never construct a new monitor"


def test_config_knob_exists_with_sane_default():
    from bot.config import MonitoringConfig
    v = MonitoringConfig().monitor_liveness_timeout_sec
    assert v == 300.0
