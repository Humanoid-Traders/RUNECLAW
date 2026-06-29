"""
Tier 1a: proactive early-warning alerts (drawdown tiers, tick failures,
warning-rate breaker, WS health, stale balance).

Each check is exercised directly against a lightweight fake engine — no full
engine, no network. CONFIG.is_live() is patched at the class level (CONFIG is a
frozen instance) for the live-gated checks.
"""

import time
import types

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor


def _engine(**risk):
    eng = types.SimpleNamespace()
    eng.risk = types.SimpleNamespace(
        current_drawdown_pct=risk.get("dd"),
        warning_rate_breaker_active=risk.get("warn", False),
        _warning_rate_trip_key=risk.get("warn_key", ""),
    )
    eng.ws_feed = types.SimpleNamespace(is_connected=lambda: risk.get("ws", True))
    eng._tick_consecutive_failures = risk.get("fails", 0)
    eng._live_balance_cache_ts = risk.get("bal_ts", 0.0)
    return eng


def _mon(eng):
    return ProactiveMonitor(eng)


@pytest.fixture
def live(monkeypatch):
    # CONFIG is frozen; patch is_live on its class.
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


# ── drawdown tiers ────────────────────────────────────────────────────────

def test_drawdown_tiers_fire_once_and_escalate():
    # MAX_DRAWDOWN_PCT defaults to 10.0 -> 50%/75%/85% at dd 5.0/7.5/8.5.
    m = _mon(_engine(dd=5.0))
    a = m._check_drawdown_tiers()
    assert len(a) == 1 and a[0].alert_type == "DRAWDOWN_TIER" and "50%" in a[0].title
    # Same tier again -> no repeat.
    assert m._check_drawdown_tiers() == []
    # Escalate to 75% then 85%.
    m.engine.risk.current_drawdown_pct = 7.5
    a = m._check_drawdown_tiers()
    assert len(a) == 1 and "75%" in a[0].title and a[0].severity == "WARNING"
    m.engine.risk.current_drawdown_pct = 8.5
    a = m._check_drawdown_tiers()
    assert len(a) == 1 and "85%" in a[0].title and a[0].severity == "CRITICAL"


def test_drawdown_tier_rearms_after_recovery():
    m = _mon(_engine(dd=7.5))
    assert m._check_drawdown_tiers()           # 75% fires
    m.engine.risk.current_drawdown_pct = 2.0   # recover below 50%
    assert m._check_drawdown_tiers() == []     # re-arm, no alert at tier 0
    m.engine.risk.current_drawdown_pct = 7.5   # back up
    assert m._check_drawdown_tiers()           # fires again


def test_drawdown_no_alert_below_50pct():
    m = _mon(_engine(dd=3.0))                   # 30% of limit
    assert m._check_drawdown_tiers() == []


def test_drawdown_handles_missing_data():
    assert _mon(_engine(dd=None))._check_drawdown_tiers() == []


# ── tick failures ─────────────────────────────────────────────────────────

def test_tick_failures_fire_once_at_threshold():
    m = _mon(_engine(fails=3))
    a = m._check_tick_failures()
    assert len(a) == 1 and a[0].severity == "CRITICAL"
    assert m._check_tick_failures() == []          # already alerted
    m.engine._tick_consecutive_failures = 0        # recovered
    assert m._check_tick_failures() == []
    m.engine._tick_consecutive_failures = 3        # degraded again
    assert m._check_tick_failures()


def test_tick_failures_below_threshold_quiet():
    assert _mon(_engine(fails=2))._check_tick_failures() == []


# ── warning-rate breaker ──────────────────────────────────────────────────

def test_warning_rate_breaker_alerts_on_trip():
    m = _mon(_engine(warn=True, warn_key="exchange_api"))
    a = m._check_warning_rate_breaker()
    assert len(a) == 1 and "exchange_api" in a[0].body
    assert m._check_warning_rate_breaker() == []   # once per trip
    m.engine.risk.warning_rate_breaker_active = False
    assert m._check_warning_rate_breaker() == []


# ── WS health (live-gated) ────────────────────────────────────────────────

def test_ws_health_quiet_when_not_live():
    # Not live -> no WS alerts regardless of state.
    assert _mon(_engine(ws=False))._check_ws_health() == []


def test_ws_down_alerts_after_sustained_outage(live):
    m = _mon(_engine(ws=False))
    # First call arms _ws_down_since; not yet >5min -> quiet.
    assert m._check_ws_health() == []
    m._ws_down_since = time.monotonic() - 400      # pretend down >5 min
    a = m._check_ws_health()
    assert len(a) == 1 and a[0].alert_type == "WS_DOWN"
    # Reconnect -> recovery INFO.
    m.engine.ws_feed.is_connected = lambda: True
    a = m._check_ws_health()
    assert len(a) == 1 and a[0].alert_type == "WS_UP"


# ── stale balance (live-gated) ────────────────────────────────────────────

def test_stale_balance_quiet_when_not_live():
    assert _mon(_engine(bal_ts=time.monotonic() - 999))._check_stale_balance() == []


def test_stale_balance_alerts_when_old(live):
    fresh = _mon(_engine(bal_ts=time.monotonic()))
    assert fresh._check_stale_balance() == []
    stale = _mon(_engine(bal_ts=time.monotonic() - 400))
    a = stale._check_stale_balance()
    assert len(a) == 1 and a[0].alert_type == "STALE_BALANCE"


def test_stale_balance_quiet_when_never_fetched(live):
    assert _mon(_engine(bal_ts=0.0))._check_stale_balance() == []
