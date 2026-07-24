"""TICK_STALL self-diagnosis — when the engine loop hangs, the alert now
carries WHERE it is parked.

The monitor shares the engine's event loop, so a fired TICK_STALL proves the
loop is alive and the hang is a suspended await; Task.get_stack() can read
the parked frames in place. The engine task is identified by its OUTERMOST
frame being Engine.run in engine.py (so a stuck post-tick maintenance await
still matches). Everything is fail-open: no diagnosis ever blocks the alert.
"""

import time
from types import SimpleNamespace

from bot.core import proactive_monitor as pm_mod
from bot.core.proactive_monitor import ProactiveMonitor


def _frame(filename, lineno, name):
    return SimpleNamespace(
        f_code=SimpleNamespace(co_filename=filename, co_name=name),
        f_lineno=lineno)


def _stalled_pm():
    stale = time.monotonic() - ProactiveMonitor.TICK_STALL_THRESHOLD_S - 60
    return ProactiveMonitor(SimpleNamespace(_last_tick_started_ts=stale))


class TestFrameSummaries:
    def test_formats_basename_line_and_name(self):
        frames = [
            _frame("/srv/bot/core/engine.py", 2182, "run"),
            _frame("/srv/bot/core/live_executor.py", 440, "_sync"),
        ]
        assert ProactiveMonitor._frame_summaries(frames) == [
            "engine.py:2182 in run",
            "live_executor.py:440 in _sync",
        ]

    def test_broken_frames_are_skipped_not_fatal(self):
        frames = [object(), _frame("/x/engine.py", 1, "run")]
        assert ProactiveMonitor._frame_summaries(frames) == ["engine.py:1 in run"]


class TestStallDiagnosis:
    def test_finds_the_engine_run_task_and_reads_its_parked_frames(self, monkeypatch):
        engine_task = SimpleNamespace(get_stack=lambda: [
            _frame("/srv/bot/core/engine.py", 2093, "run"),
            _frame("/srv/bot/core/engine.py", 2629, "_tick"),
            _frame("/usr/lib/aiohttp/client.py", 512, "_request"),
        ])
        other_task = SimpleNamespace(get_stack=lambda: [
            _frame("/srv/tests/test_x.py", 9, "run"),   # right name, wrong file
        ])
        monkeypatch.setattr(pm_mod.asyncio, "all_tasks",
                            lambda: [other_task, engine_task])
        lines = _stalled_pm()._stall_diagnosis()
        assert lines[0] == "engine.py:2093 in run"
        assert lines[-1] == "client.py:512 in _request"   # innermost = the culprit

    def test_no_running_loop_is_fail_open_empty(self):
        # Outside any event loop asyncio.all_tasks() raises — diagnosis
        # swallows it and returns nothing.
        assert _stalled_pm()._stall_diagnosis() == []


class TestAlertCarriesTheDiagnosis:
    def test_hung_await_lands_in_the_alert_body(self):
        pm = _stalled_pm()
        pm._stall_diagnosis = lambda: [
            "engine.py:2093 in run", "client.py:512 in _request"]
        alerts = pm._check_engine_tick_stale()
        assert len(alerts) == 1
        assert "Hung awaiting: client.py:512 in _request" in alerts[0].body
        # The original operator guidance is intact.
        assert "consider a restart" in alerts[0].body

    def test_without_diagnosis_the_body_is_unchanged(self):
        pm = _stalled_pm()
        pm._stall_diagnosis = lambda: []
        alerts = pm._check_engine_tick_stale()
        assert len(alerts) == 1
        assert "Hung awaiting" not in alerts[0].body
