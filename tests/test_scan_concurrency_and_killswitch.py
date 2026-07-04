"""Telegram bot must not freeze while a scan runs, and enabling concurrent
update processing must not let a position survive the kill switch.

Bug: the PTB app was built WITHOUT concurrent_updates, so a handler that runs
an inline force_scan (the 'Latest Signal' button, /scan, /signals) head-of-line
blocked EVERY other update until the scan finished — commands and buttons got no
reply. The fix enables concurrent_updates(True); this suite locks in the guards
that keep it safe:
  * force_scan is single-flight (two concurrent calls -> one scan),
  * the periodic tick skips scanning while a force_scan holds the lock,
  * a fail-closed kill-switch re-check sits immediately before executor.execute,
    so /halt (now able to interleave a confirm) can't be beaten to the order.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from bot.core.engine import RuneClawEngine


class _FakeScanEngine:
    """Exercises the real force_scan single-flight wrapper over a stub body."""
    force_scan = RuneClawEngine.force_scan

    def __init__(self):
        self._scan_lock = asyncio.Lock()
        self._pending_ideas = {}
        self.body_calls = 0

    async def _force_scan_locked(self):
        self.body_calls += 1
        await asyncio.sleep(0.05)  # hold the lock so a racing caller collides
        return {"signals": 3, "ideas": 1, "auto_confirmed": 0}


@pytest.mark.asyncio
async def test_force_scan_is_single_flight():
    eng = _FakeScanEngine()
    results = await asyncio.gather(eng.force_scan(), eng.force_scan())
    # Exactly one scan body ran; the other returned the in-flight skip summary.
    assert eng.body_calls == 1
    assert sum(r.get("skipped") == "scan_already_running" for r in results) == 1
    assert sum("skipped" not in r for r in results) == 1


@pytest.mark.asyncio
async def test_sequential_force_scans_both_run():
    eng = _FakeScanEngine()
    await eng.force_scan()
    await eng.force_scan()
    assert eng.body_calls == 2  # nothing in flight -> each runs


# ── Source invariants (match the repo's audit-test style) ───────────────

def test_concurrent_updates_enabled_on_the_app():
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler.build_app)
    assert ".concurrent_updates(True)" in src, \
        "PTB must process updates concurrently or a scan freezes all handlers"


def test_killswitch_recheck_precedes_execute():
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    guard_pos = src.index("self._halted or self.risk.circuit_breaker_active")
    # The real order call must follow the guard (an earlier 'executor.execute'
    # mention is only a comment; anchor on the call AFTER the guard).
    exec_pos = src.index("await executor.execute(", guard_pos)
    assert exec_pos > guard_pos, "kill-switch re-check must precede executor.execute()"
    assert "Trade REJECTED" in src[guard_pos:exec_pos]
    # And there is no OTHER live execute call that bypasses the guard.
    assert src.count("await executor.execute(") == 1


def test_emergency_halt_sets_flag_and_resume_clears_it():
    halt = inspect.getsource(RuneClawEngine.emergency_halt_all)
    resume = inspect.getsource(RuneClawEngine.reset_circuit_breaker_all)
    assert "self._halted = True" in halt
    assert "self._halted = False" in resume


def test_tick_yields_scan_while_force_scan_holds_lock():
    src = inspect.getsource(RuneClawEngine._tick)
    assert "self._scan_lock.locked()" in src, \
        "tick must skip its scan branch while a force_scan holds the scan lock"
