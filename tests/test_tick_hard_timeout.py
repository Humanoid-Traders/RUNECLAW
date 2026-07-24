"""Tick hang self-heal — the follow-up to the TICK_STALL self-diagnosis.

A parked await inside _tick() used to hang the loop forever until a human
restarted the process (observed in production: 615s stall). Now _tick runs
under a hard cap: on expiry the parked await is CANCELLED and the timeout
re-raised so run()'s failure path counts it (backoff + degraded alerts) and
the loop recovers on its own. Post-tick maintenance awaits get a quieter
cap — cancelled with a warning, loop moves on. Both caps are generous by
design and 0 disables them.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine


def _set_cap(name: str, value: float):
    old = getattr(CONFIG.monitoring, name)
    object.__setattr__(CONFIG.monitoring, name, value)
    return old


class TestConfigKnobs:
    def test_defaults_and_relation_to_stall_threshold(self):
        from bot.core.proactive_monitor import ProactiveMonitor
        cap = CONFIG.monitoring.tick_hard_timeout_sec
        assert cap == 900.0
        # The stall alert (with its stack diagnosis) must fire BEFORE the
        # self-heal cancels the evidence.
        assert cap > ProactiveMonitor.TICK_STALL_THRESHOLD_S
        assert CONFIG.monitoring.tick_maintenance_timeout_sec == 120.0


class TestTickGuarded:
    @pytest.mark.asyncio
    async def test_hung_tick_is_cancelled_and_timeout_reraised(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        cancelled = {"flag": False}

        async def hung_tick():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled["flag"] = True
                raise

        eng._tick = hung_tick
        old = _set_cap("tick_hard_timeout_sec", 0.05)
        try:
            with pytest.raises(asyncio.TimeoutError):
                await eng._tick_guarded()
        finally:
            _set_cap("tick_hard_timeout_sec", old)
        assert cancelled["flag"], "the parked await must actually be cancelled"

    @pytest.mark.asyncio
    async def test_fast_tick_passes_untouched(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        ran = {"flag": False}

        async def quick_tick():
            ran["flag"] = True

        eng._tick = quick_tick
        old = _set_cap("tick_hard_timeout_sec", 0.5)
        try:
            await eng._tick_guarded()
        finally:
            _set_cap("tick_hard_timeout_sec", old)
        assert ran["flag"]

    @pytest.mark.asyncio
    async def test_zero_cap_disables_the_guard(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        ran = {"flag": False}

        async def quick_tick():
            ran["flag"] = True

        eng._tick = quick_tick
        old = _set_cap("tick_hard_timeout_sec", 0.0)
        try:
            await eng._tick_guarded()
        finally:
            _set_cap("tick_hard_timeout_sec", old)
        assert ran["flag"]


class TestMaintenanceCap:
    @pytest.mark.asyncio
    async def test_hung_maintenance_is_swallowed_quietly(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        old = _set_cap("tick_maintenance_timeout_sec", 0.05)
        try:
            # No raise: the loop must simply move on.
            out = await eng._with_maintenance_cap(asyncio.sleep(3600), "test hang")
        finally:
            _set_cap("tick_maintenance_timeout_sec", old)
        assert out is None

    @pytest.mark.asyncio
    async def test_healthy_maintenance_returns_its_value(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)

        async def fine():
            return 42

        old = _set_cap("tick_maintenance_timeout_sec", 1.0)
        try:
            assert await eng._with_maintenance_cap(fine(), "ok") == 42
        finally:
            _set_cap("tick_maintenance_timeout_sec", old)


class TestLoopWiring:
    def test_run_awaits_the_guarded_tick_and_caps_maintenance(self):
        import inspect
        src = inspect.getsource(RuneClawEngine.run)
        assert "await self._tick_guarded()" in src
        assert "await self._tick()" not in src, "the bare tick must be gone from run()"
        assert src.count("_with_maintenance_cap") >= 5
