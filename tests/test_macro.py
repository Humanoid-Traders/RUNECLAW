"""
Tests for the RUNECLAW macro calendar system.
Covers all 5 risk states, edge cases, and integration with the risk engine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


from bot.macro.calendar import MacroCalendar, build_2026_calendar
from bot.macro.models import (
    MacroEvent,
    MacroEventType,
    MacroRiskState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _isolated_state_file() -> str:
    """Unique temp path for a RiskEngine's state. Never "/dev/null":
    RiskEngine._save_state does os.replace(tmp, state_file), which as root
    clobbers the /dev/null device with a regular file and leaks circuit-breaker
    state into later tests (env-dependent flake)."""
    import os
    import tempfile
    return os.path.join(tempfile.mkdtemp(prefix="rc-risk-"), "risk_state.json")


def _make_event(
    dt: datetime,
    event_type: MacroEventType = MacroEventType.FOMC_DECISION,
    label: str = "Test Event",
) -> MacroEvent:
    return MacroEvent(event_type=event_type, scheduled_utc=dt, label=label)


def _cal(events: list[MacroEvent], now: datetime) -> MacroCalendar:
    return MacroCalendar(events=events, now_fn=lambda: now)


# ---------------------------------------------------------------------------
# build_2026_calendar
# ---------------------------------------------------------------------------

class TestBuild2026Calendar:
    def test_returns_nonempty(self):
        events = build_2026_calendar()
        assert len(events) > 0

    def test_event_types_present(self):
        events = build_2026_calendar()
        types = {e.event_type for e in events}
        assert MacroEventType.FOMC_DECISION in types
        assert MacroEventType.CPI in types
        assert MacroEventType.CORE_PCE in types
        assert MacroEventType.NFP in types

    def test_sorted_by_date(self):
        events = build_2026_calendar()
        for i in range(len(events) - 1):
            assert events[i].scheduled_utc <= events[i + 1].scheduled_utc

    def test_fomc_count(self):
        events = build_2026_calendar()
        fomc = [e for e in events if e.event_type == MacroEventType.FOMC_DECISION]
        assert len(fomc) == 8

    def test_cpi_count(self):
        events = build_2026_calendar()
        cpi = [e for e in events if e.event_type == MacroEventType.CPI]
        assert len(cpi) == 12

    def test_all_events_utc(self):
        events = build_2026_calendar()
        for e in events:
            assert e.scheduled_utc.tzinfo is not None


# ---------------------------------------------------------------------------
# MacroCalendar risk state machine
# ---------------------------------------------------------------------------

class TestMacroRiskStates:
    """Test all 5 risk states with frozen time."""

    def test_normal_no_events_nearby(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt - timedelta(days=3)  # 3 days before
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.NORMAL

    def test_pre_event_caution_23h_before(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt - timedelta(hours=23)  # within 24h
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.PRE_EVENT_CAUTION

    def test_pre_event_caution_boundary_24h(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt - timedelta(hours=24, seconds=1)  # just outside
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.NORMAL

    def test_event_lockdown_at_event_time(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt  # exactly at event
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.EVENT_LOCKDOWN
        assert snap.active_event is not None

    def test_event_lockdown_29min_before(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt - timedelta(minutes=29)
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.EVENT_LOCKDOWN

    def test_event_lockdown_30min_after(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt + timedelta(minutes=30)
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.EVENT_LOCKDOWN

    def test_post_event_volatility_31min_after(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt + timedelta(minutes=31)
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.POST_EVENT_VOLATILITY
        assert snap.active_event is not None

    def test_post_event_volatility_3h_after(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt + timedelta(hours=3)
        cal = _cal([_make_event(event_dt)], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.POST_EVENT_VOLATILITY

    def test_post_event_ends_after_4h(self):
        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt + timedelta(hours=4, minutes=1)
        # Include a far-future event so the calendar is NOT exhausted (which would
        # fail closed). This isolates the post-event-window-ends behaviour.
        future = _make_event(datetime(2026, 8, 1, 14, 0, tzinfo=UTC))
        cal = _cal([_make_event(event_dt), future], now)
        snap = cal.evaluate()
        # Post-event window has elapsed and the next event is >24h away → NORMAL.
        assert snap.state == MacroRiskState.NORMAL
        assert snap.stale is False

    def test_blackout_on_exception(self):
        """Fail-closed: if evaluate raises, return BLACKOUT."""
        cal = MacroCalendar(events=[])
        # Inject a now_fn that raises
        cal._now_fn = lambda: (_ for _ in ()).throw(RuntimeError("clock broken"))
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.BLACKOUT

    def test_empty_calendar_is_normal(self):
        cal = MacroCalendar(events=[], now_fn=lambda: datetime(2026, 6, 1, tzinfo=UTC))
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.NORMAL


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestMacroEdgeCases:
    def test_multiple_events_same_day(self):
        """Two events on same day: lockdown should trigger for the one we're near."""
        ev1 = _make_event(datetime(2026, 6, 1, 13, 0, tzinfo=UTC), label="CPI")
        ev2 = _make_event(datetime(2026, 6, 1, 18, 0, tzinfo=UTC), label="FOMC")
        now = datetime(2026, 6, 1, 13, 0, tzinfo=UTC)  # right at ev1
        cal = _cal([ev1, ev2], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.EVENT_LOCKDOWN

    def test_pre_event_picks_nearest(self):
        """Pre-event caution should reference the nearest upcoming event."""
        ev1 = _make_event(datetime(2026, 6, 1, 14, 0, tzinfo=UTC), label="CPI")
        ev2 = _make_event(datetime(2026, 6, 3, 14, 0, tzinfo=UTC), label="NFP")
        now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)  # 14h before ev1
        cal = _cal([ev1, ev2], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.PRE_EVENT_CAUTION
        assert snap.next_event is not None
        assert snap.next_event.label == "CPI"

    def test_all_events_in_past(self):
        """All events in the past → EXHAUSTED → fail closed (BLACKOUT) + stale.

        Previously this returned NORMAL, silently dropping all macro event
        protection once the hardcoded schedule aged out. The fail-safe now routes
        an exhausted calendar to BLACKOUT (blocks new entries) and flags it stale
        so the monitor alerts.
        """
        ev = _make_event(datetime(2025, 1, 1, 14, 0, tzinfo=UTC))
        now = datetime(2026, 12, 31, tzinfo=UTC)
        cal = _cal([ev], now)
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.BLACKOUT
        assert snap.stale is True

    def test_all_events_in_past_opt_out_reports_normal(self):
        """With the fail-safe disabled, an exhausted calendar reports NORMAL but
        is still flagged stale (so the staleness alert still fires)."""
        ev = _make_event(datetime(2025, 1, 1, 14, 0, tzinfo=UTC))
        now = datetime(2026, 12, 31, tzinfo=UTC)
        cal = _cal([ev], now)
        cal._fail_closed_when_stale = False
        snap = cal.evaluate()
        assert snap.state == MacroRiskState.NORMAL
        assert snap.stale is True


# ---------------------------------------------------------------------------
# Helpers: upcoming, add_events, format_event_times
# ---------------------------------------------------------------------------

class TestMacroHelpers:
    def test_upcoming_returns_future_events(self):
        events = build_2026_calendar()
        now = datetime(2026, 3, 1, tzinfo=UTC)
        cal = MacroCalendar(events=events, now_fn=lambda: now)
        upcoming = cal.upcoming(limit=3)
        assert len(upcoming) == 3
        for ev in upcoming:
            assert ev.scheduled_utc > now

    def test_add_events_sorts(self):
        ev1 = _make_event(datetime(2026, 12, 1, tzinfo=UTC))
        ev2 = _make_event(datetime(2026, 1, 1, tzinfo=UTC))
        cal = MacroCalendar(events=[ev1])
        cal.add_events([ev2])
        assert cal._events[0].scheduled_utc < cal._events[1].scheduled_utc

    def test_format_event_times(self):
        ev = _make_event(datetime(2026, 6, 1, 18, 0, tzinfo=UTC))
        times = MacroCalendar.format_event_times(ev)
        assert "UTC" in times["utc"]
        assert "ET" in times["et"]
        assert "AMS" in times["ams"]


# ---------------------------------------------------------------------------
# Integration: macro check in RiskEngine
# ---------------------------------------------------------------------------

class TestMacroRiskIntegration:
    """Verify that risk_engine check #18 uses the macro calendar."""

    def test_lockdown_rejects_trade(self):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt  # lockdown
        cal = MacroCalendar(
            events=[_make_event(event_dt)],
            now_fn=lambda: now,
        )
        portfolio = PortfolioTracker()
        risk = RiskEngine(portfolio, macro_calendar=cal, state_file=_isolated_state_file())

        idea = TradeIdea(
            asset="BTC/USDT",
            direction=Direction.LONG,
            entry_price=50000,
            stop_loss=49000,
            take_profit=52000,
            confidence=0.7,
            reasoning="test",
            signals_used=["test"],
        )
        result = risk.evaluate(idea)
        assert result.verdict.value == "REJECTED"
        assert any("MACRO_EVENT" in c for c in result.checks_failed)

    def test_normal_passes_macro_check(self):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        event_dt = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
        now = event_dt - timedelta(days=5)  # far from event
        cal = MacroCalendar(
            events=[_make_event(event_dt)],
            now_fn=lambda: now,
        )
        portfolio = PortfolioTracker()
        risk = RiskEngine(portfolio, macro_calendar=cal, state_file=_isolated_state_file())

        idea = TradeIdea(
            asset="BTC/USDT",
            direction=Direction.LONG,
            entry_price=50000,
            stop_loss=49000,
            take_profit=52000,
            confidence=0.7,
            reasoning="test",
            signals_used=["test"],
        )
        result = risk.evaluate(idea)
        assert any("MACRO_EVENT: NORMAL" in c for c in result.checks_passed)

    def test_blackout_rejects_trade(self):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        cal = MacroCalendar(events=[])
        cal._now_fn = lambda: (_ for _ in ()).throw(RuntimeError("broken"))
        portfolio = PortfolioTracker()
        risk = RiskEngine(portfolio, macro_calendar=cal, state_file=_isolated_state_file())

        idea = TradeIdea(
            asset="BTC/USDT",
            direction=Direction.LONG,
            entry_price=50000,
            stop_loss=49000,
            take_profit=52000,
            confidence=0.7,
            reasoning="test",
            signals_used=["test"],
        )
        result = risk.evaluate(idea)
        assert result.verdict.value == "REJECTED"
        assert any("BLACKOUT" in c for c in result.checks_failed)

    def test_no_calendar_skips_check(self):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        portfolio = PortfolioTracker()
        risk = RiskEngine(portfolio, macro_calendar=None, state_file=_isolated_state_file())

        idea = TradeIdea(
            asset="BTC/USDT",
            direction=Direction.LONG,
            entry_price=50000,
            stop_loss=49000,
            take_profit=52000,
            confidence=0.7,
            reasoning="test",
            signals_used=["test"],
        )
        result = risk.evaluate(idea)
        assert any("no calendar" in c for c in result.checks_passed)
