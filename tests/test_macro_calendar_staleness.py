"""
Macro calendar staleness fail-safe (Audit Critical Issue #1).

The macro event schedule is hardcoded (2026). Once every event is in the past
the calendar is EXHAUSTED — there is no future event to gate against. Previously
that silently reported NORMAL, so all FOMC/CPI event protection vanished with no
error. The fix routes an exhausted calendar to BLACKOUT (fail-closed) and sets
snapshot.stale=True so the monitor can alert. While future events remain there
is no behaviour change.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.compat import UTC
from bot.macro.calendar import MacroCalendar, build_2026_calendar
from bot.macro.models import MacroEvent, MacroEventType, MacroRiskState


def _clock(dt):
    return lambda: dt


def _cal(now, **kw):
    return MacroCalendar(events=build_2026_calendar(), now_fn=_clock(now), **kw)


# A timestamp comfortably after the last hardcoded 2026 event.
EXHAUSTED_NOW = datetime(2027, 6, 1, 12, 0, tzinfo=UTC)
# A timestamp while 2026 events still lie in the future (quiet window).
LIVE_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


class TestExhaustion:
    def test_exhausted_calendar_fails_closed(self):
        snap = _cal(EXHAUSTED_NOW).evaluate()
        assert snap.state == MacroRiskState.BLACKOUT
        assert snap.stale is True

    def test_is_exhausted_true_when_all_past(self):
        assert _cal(EXHAUSTED_NOW).is_exhausted() is True

    def test_opt_out_reports_normal_but_still_stale(self):
        snap = _cal(EXHAUSTED_NOW, fail_closed_when_stale=False).evaluate()
        assert snap.state == MacroRiskState.NORMAL
        assert snap.stale is True  # flag still set so the alert fires

    def test_empty_calendar_is_not_exhausted(self):
        # An intentionally-empty calendar is an explicit "no calendar" choice,
        # NOT a silently aged-out schedule. (Note: the constructor's
        # `events or build_...` fallback treats [] as "use default", so build a
        # real empty calendar by clearing _events directly.)
        cal = MacroCalendar(now_fn=_clock(EXHAUSTED_NOW))
        cal._events = []
        assert cal.is_exhausted() is False
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.NORMAL
        assert snap.stale is False


class TestNoRegressionWhileLive:
    def test_future_events_not_exhausted(self):
        assert _cal(LIVE_NOW).is_exhausted() is False

    def test_quiet_window_still_normal(self):
        # Mid-2026 with events ahead → NORMAL, never stale, never blacked out.
        snap = _cal(LIVE_NOW).evaluate()
        assert snap.stale is False
        assert snap.state in (
            MacroRiskState.NORMAL,
            MacroRiskState.PRE_EVENT_CAUTION,
            MacroRiskState.EVENT_LOCKDOWN,
            MacroRiskState.POST_EVENT_VOLATILITY,
        )

    def test_lockdown_still_fires_near_event(self):
        # Build a single event and freeze the clock inside its lockdown window.
        ev = MacroEvent(
            event_type=MacroEventType.CPI,
            scheduled_utc=datetime(2026, 6, 10, 12, 30, tzinfo=UTC),
            label="CPI test",
        )
        now = ev.scheduled_utc + timedelta(minutes=5)
        cal = MacroCalendar(events=[ev], now_fn=_clock(now))
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.EVENT_LOCKDOWN
        assert snap.stale is False


class TestMonitorAlert:
    def _mon(self, exhausted: bool):
        from bot.core.proactive_monitor import ProactiveMonitor
        now = EXHAUSTED_NOW if exhausted else LIVE_NOW
        eng = SimpleNamespace(macro_calendar=_cal(now))
        return ProactiveMonitor(eng)

    def test_alert_fires_when_exhausted(self):
        a = self._mon(exhausted=True)._check_macro_calendar_stale()
        assert len(a) == 1
        assert a[0].alert_type == "MACRO_CALENDAR_STALE"
        assert a[0].severity == "CRITICAL"

    def test_no_alert_while_live(self):
        assert self._mon(exhausted=False)._check_macro_calendar_stale() == []

    def test_no_calendar_no_alert(self):
        from bot.core.proactive_monitor import ProactiveMonitor
        mon = ProactiveMonitor(SimpleNamespace())
        assert mon._check_macro_calendar_stale() == []
