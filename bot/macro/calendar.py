"""
RUNECLAW Macro Calendar -- hardcoded 2026 schedule + risk state machine.

Risk states are computed from proximity to scheduled events:
  NORMAL:                 No event within 24h
  PRE_EVENT_CAUTION:      Within 24h before event
  EVENT_LOCKDOWN:         30min before to 30min after
  POST_EVENT_VOLATILITY:  30min to 4h after
  BLACKOUT:               Calendar evaluation failed (fail-closed)

All event times stored as UTC. Display helpers convert to ET / Amsterdam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from bot.macro.models import (
    MacroEvent,
    MacroEventType,
    MacroRiskState,
    MacroStateSnapshot,
)

ET = ZoneInfo("America/New_York")
AMS = ZoneInfo("Europe/Amsterdam")

# Lockdown window: 30 min before to 30 min after
_LOCKDOWN_BEFORE = timedelta(minutes=30)
_LOCKDOWN_AFTER = timedelta(minutes=30)
# Post-event volatility: 30 min to 4h after
_POST_EVENT_END = timedelta(hours=4)
# Pre-event caution: 24h before
_CAUTION_BEFORE = timedelta(hours=24)


def _et(year: int, month: int, day: int, hour: int = 8, minute: int = 30) -> datetime:
    """Build a UTC datetime from Eastern Time components."""
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(UTC)


def _fomc(year: int, month: int, day: int) -> datetime:
    """FOMC decisions announced at 2:00 PM ET on the second day."""
    return _et(year, month, day, 14, 0)


def build_2026_calendar() -> list[MacroEvent]:
    """Return all known 2026 high-impact macro events."""
    events: list[MacroEvent] = []

    # -- FOMC Rate Decisions (announcement day, 2:00 PM ET) --
    fomc_dates = [
        (1, 29, "January"),
        (3, 19, "March"),
        (5, 7, "May"),
        (6, 17, "June"),
        (7, 29, "July"),
        (9, 16, "September"),
        (10, 28, "October"),
        (12, 9, "December"),
    ]
    for month, day, name in fomc_dates:
        events.append(MacroEvent(
            event_type=MacroEventType.FOMC_DECISION,
            scheduled_utc=_fomc(2026, month, day),
            label=f"FOMC Rate Decision - {name} 2026",
        ))

    # -- CPI Releases (8:30 AM ET) --
    cpi_dates = [
        (1, 14, "January"), (2, 11, "February"), (3, 11, "March"),
        (4, 14, "April"), (5, 12, "May"), (6, 10, "June"),
        (7, 14, "July"), (8, 12, "August"), (9, 11, "September"),
        (10, 14, "October"), (11, 10, "November"), (12, 10, "December"),
    ]
    for month, day, name in cpi_dates:
        events.append(MacroEvent(
            event_type=MacroEventType.CPI,
            scheduled_utc=_et(2026, month, day),
            label=f"CPI Release - {name} 2026",
        ))

    # -- Core PCE / Personal Income & Outlays (8:30 AM ET) --
    pce_dates = [
        (1, 30, "January"), (2, 27, "February"), (3, 27, "March"),
        (4, 30, "April"), (5, 29, "May"), (6, 25, "June"),
        (7, 30, "July"), (8, 26, "August"), (9, 30, "September"),
        (10, 29, "October"), (11, 25, "November"), (12, 23, "December"),
    ]
    for month, day, name in pce_dates:
        events.append(MacroEvent(
            event_type=MacroEventType.CORE_PCE,
            scheduled_utc=_et(2026, month, day),
            label=f"Core PCE - {name} 2026",
        ))

    # -- NFP (first Friday, 8:30 AM ET) --
    nfp_dates = [
        (1, 9, "January"), (2, 6, "February"), (3, 6, "March"),
        (4, 3, "April"), (5, 8, "May"), (6, 5, "June"),
        (7, 2, "July"), (8, 7, "August"), (9, 4, "September"),
        (10, 2, "October"), (11, 6, "November"), (12, 4, "December"),
    ]
    for month, day, name in nfp_dates:
        events.append(MacroEvent(
            event_type=MacroEventType.NFP,
            scheduled_utc=_et(2026, month, day),
            label=f"Nonfarm Payrolls - {name} 2026",
        ))

    events.sort(key=lambda e: e.scheduled_utc)
    return events


class MacroCalendar:
    """
    Macro event calendar with risk-state machine.

    The now_fn parameter is a testability hook: inject a frozen clock in tests
    instead of monkeypatching datetime.now.
    """

    def __init__(
        self,
        events: Optional[list[MacroEvent]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._events: list[MacroEvent] = sorted(
            events or build_2026_calendar(),
            key=lambda e: e.scheduled_utc,
        )
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def evaluate(self) -> MacroStateSnapshot:
        """Compute the current macro risk state. Fail-closed: exceptions → BLACKOUT."""
        try:
            return self._evaluate_inner()
        except Exception:
            return MacroStateSnapshot(state=MacroRiskState.BLACKOUT)

    def _evaluate_inner(self) -> MacroStateSnapshot:
        now = self._now_fn()
        if not self._events:
            return MacroStateSnapshot(state=MacroRiskState.NORMAL)

        # Check ALL events for lockdown/post-event (multiple events same day)
        for ev in self._events:
            delta = (now - ev.scheduled_utc).total_seconds()
            before_sec = -_LOCKDOWN_BEFORE.total_seconds()
            after_sec = _LOCKDOWN_AFTER.total_seconds()
            post_end_sec = _POST_EVENT_END.total_seconds()

            # EVENT_LOCKDOWN: -30min to +30min
            if before_sec <= delta <= after_sec:
                return MacroStateSnapshot(
                    state=MacroRiskState.EVENT_LOCKDOWN,
                    active_event=ev,
                    next_event=ev if delta < 0 else self._next_after(now),
                    time_until_next=self._time_until_next(now),
                )

            # POST_EVENT_VOLATILITY: +30min to +4h
            if after_sec < delta <= post_end_sec:
                return MacroStateSnapshot(
                    state=MacroRiskState.POST_EVENT_VOLATILITY,
                    active_event=ev,
                    next_event=self._next_after(now),
                    time_until_next=self._time_until_next(now),
                )

        # PRE_EVENT_CAUTION: within 24h before any event
        for ev in self._events:
            delta = (ev.scheduled_utc - now).total_seconds()
            if 0 < delta <= _CAUTION_BEFORE.total_seconds():
                return MacroStateSnapshot(
                    state=MacroRiskState.PRE_EVENT_CAUTION,
                    next_event=ev,
                    time_until_next=timedelta(seconds=delta),
                )

        # NORMAL
        nxt = self._next_after(now)
        return MacroStateSnapshot(
            state=MacroRiskState.NORMAL,
            next_event=nxt,
            time_until_next=(nxt.scheduled_utc - now) if nxt else None,
        )

    def _next_after(self, now: datetime) -> Optional[MacroEvent]:
        for ev in self._events:
            if ev.scheduled_utc > now:
                return ev
        return None

    def _time_until_next(self, now: datetime) -> Optional[timedelta]:
        nxt = self._next_after(now)
        return (nxt.scheduled_utc - now) if nxt else None

    def upcoming(self, limit: int = 5) -> list[MacroEvent]:
        """Next N events from now, sorted by date."""
        now = self._now_fn()
        return [ev for ev in self._events if ev.scheduled_utc > now][:limit]

    def add_events(self, events: list[MacroEvent]) -> None:
        """Append events and re-sort. Call before engine starts."""
        self._events.extend(events)
        self._events.sort(key=lambda e: e.scheduled_utc)

    @staticmethod
    def format_event_times(event: MacroEvent) -> dict[str, str]:
        """Return formatted times in UTC, ET, and Amsterdam."""
        fmt = "%Y-%m-%d %H:%M"
        utc_dt = event.scheduled_utc.replace(tzinfo=UTC) if event.scheduled_utc.tzinfo is None else event.scheduled_utc
        return {
            "utc": utc_dt.strftime(fmt) + " UTC",
            "et": utc_dt.astimezone(ET).strftime(fmt) + " ET",
            "ams": utc_dt.astimezone(AMS).strftime(fmt) + " AMS",
        }
