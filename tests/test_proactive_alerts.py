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


def test_stale_balance_alerts_when_old(live, monkeypatch):
    # Pin the monotonic clock to a large fixed value. On a freshly-booted CI
    # runner time.monotonic() (seconds since an arbitrary epoch) can be < 400,
    # so `time.monotonic() - 400` goes NEGATIVE and trips the `ts <= 0`
    # "never fetched" guard in the check — a false negative unrelated to
    # staleness. A stable large base removes that environment dependence.
    monkeypatch.setattr(pm.time, "monotonic", lambda: 1_000_000.0)
    fresh = _mon(_engine(bal_ts=1_000_000.0))
    assert fresh._check_stale_balance() == []
    stale = _mon(_engine(bal_ts=1_000_000.0 - 400))
    a = stale._check_stale_balance()
    assert len(a) == 1 and a[0].alert_type == "STALE_BALANCE"


def test_stale_balance_quiet_when_never_fetched(live):
    assert _mon(_engine(bal_ts=0.0))._check_stale_balance() == []


# ── trade-signal Telegram gate (confidence >= signal_display_min_confidence) ──

def _idea(conf, direction="LONG"):
    return types.SimpleNamespace(
        asset="BTC/USDT",
        direction=types.SimpleNamespace(value=direction),
        confidence=conf,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
    )


def test_trade_signal_only_alerts_above_display_threshold():
    # Default risk.signal_display_min_confidence is 0.70: a 0.62 idea still
    # queues/trades but must NOT ping Telegram; a 0.75 idea does.
    m = _mon(_engine())
    m.engine._pending_ideas = {"lo": _idea(0.62), "hi": _idea(0.75)}
    a = m._check_trade_signals()
    assert len(a) == 1 and a[0].alert_type == "TRADE_SIGNAL"
    assert "75%" in a[0].body            # the high-conviction idea messaged
    # Both ideas are now marked seen -> a second tick is silent (no re-eval).
    assert m._check_trade_signals() == []


def test_trade_signal_at_threshold_alerts():
    m = _mon(_engine())
    m.engine._pending_ideas = {"edge": _idea(0.70)}   # exactly at the gate
    assert len(m._check_trade_signals()) == 1


# ── idle-cash stake nudge ─────────────────────────────────────────────────

def _idle_engine(free):
    eng = types.SimpleNamespace()
    eng._live_balance_cache = {"free": free, "equity": free}
    return eng


def test_idle_cash_nudges_once_after_hours_with_guarded_button(monkeypatch):
    monkeypatch.setenv("IDLE_CASH_NUDGE_USD", "25")
    monkeypatch.setenv("IDLE_CASH_NUDGE_HOURS", "6")
    m = _mon(_idle_engine(free=100.0))   # stakeable 70 >= 25
    assert m._check_idle_cash() == []    # timer only just started
    m._idle_since -= 6 * 3600 + 1        # simulate 6h of idleness
    alerts = m._check_idle_cash()
    assert len(alerts) == 1 and alerts[0].alert_type == "IDLE_CASH"
    # The button must route to the guarded stake callback and carry NO amount.
    assert ("✅ Stake idle USDT", "yld:s:USDT") in alerts[0].buttons
    assert all("70" not in cb for _lbl, cb in alerts[0].buttons)
    # Cooldown: no immediate re-nudge even though still idle.
    m._idle_since -= 6 * 3600 + 1
    assert m._check_idle_cash() == []


def test_idle_cash_rearms_when_cash_gets_used(monkeypatch):
    monkeypatch.setenv("IDLE_CASH_NUDGE_USD", "25")
    m = _mon(_idle_engine(free=100.0))
    m._check_idle_cash()
    assert m._idle_since > 0
    m.engine._live_balance_cache = {"free": 10.0}   # engine deployed the cash
    assert m._check_idle_cash() == []
    assert m._idle_since == 0.0                     # timer reset


def test_idle_cash_disabled_by_env(monkeypatch):
    monkeypatch.setenv("IDLE_CASH_NUDGE_ENABLED", "false")
    m = _mon(_idle_engine(free=1000.0))
    m._idle_since = 1.0
    assert m._check_idle_cash() == []


# ── daily digest (morning brief / evening wrap) ───────────────────────────

def test_daily_digest_fires_each_kind_once_per_day(monkeypatch):
    monkeypatch.setenv("DAILY_BRIEF_HOUR_UTC", "0")
    monkeypatch.setenv("DAILY_WRAP_HOUR_UTC", "0")   # any hour qualifies
    eng = _idle_engine(free=50.0)
    eng.portfolio = types.SimpleNamespace(open_positions=[])
    m = _mon(eng)
    kinds = {a.alert_type for a in m._check_daily_digest()}
    assert kinds == {"DAILY_BRIEF", "DAILY_WRAP"}
    assert m._check_daily_digest() == []             # once per day only


def test_daily_digest_respects_hour_gate(monkeypatch):
    monkeypatch.setenv("DAILY_BRIEF_HOUR_UTC", "23")
    monkeypatch.setenv("DAILY_WRAP_HOUR_UTC", "23")
    from datetime import datetime as _dt
    if _dt.now(pm.UTC).hour >= 23:
        pytest.skip("wall clock past the gate hour")
    m = _mon(_idle_engine(free=50.0))
    assert m._check_daily_digest() == []


# ── button pass-through in dispatch ───────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_uses_3arg_send_only_for_button_alerts():
    m = _mon(types.SimpleNamespace())
    m._enabled_chats = {"1"}
    calls = []

    async def send_fn(chat_id, text, buttons=None):
        calls.append((chat_id, text, buttons))

    await m._dispatch(pm.Alert(alert_type="T", severity="INFO", title="t",
                               body="plain"), send_fn)
    await m._dispatch(pm.Alert(alert_type="T", severity="INFO", title="t",
                               body="btn", buttons=[("Go", "yld:x")]), send_fn)
    assert calls[0][2] is None                     # legacy 2-arg path
    assert calls[1][2] == [("Go", "yld:x")]        # buttons passed through


# ── weekly parity digest ──────────────────────────────────────────────────

def test_parity_digest_fires_once_per_week(monkeypatch, tmp_path):
    import json
    from datetime import datetime as _dt
    now = _dt.now(pm.UTC)
    monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
    monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
    f = tmp_path / "closed.json"
    f.write_text(json.dumps([
        {"symbol": "BTC/USDT", "pnl_usd": 10.0, "fees_usd": 0.5,
         "size_usd": 100.0, "close_reason": "take_profit"},
        {"symbol": "ETH/USDT", "pnl_usd": -4.0, "fees_usd": 0.5,
         "size_usd": 100.0, "close_reason": "stop_loss"},
    ]))
    eng = types.SimpleNamespace(
        live_executor=types.SimpleNamespace(_closed_trades_file=str(f)))
    m = _mon(eng)
    alerts = m._check_parity_digest()
    assert len(alerts) == 1 and alerts[0].alert_type == "PARITY_DIGEST"
    assert "PF" in alerts[0].body and "parity" in alerts[0].body.lower()
    assert m._check_parity_digest() == []          # once per ISO week


def test_parity_digest_silent_without_trades(monkeypatch, tmp_path):
    from datetime import datetime as _dt
    now = _dt.now(pm.UTC)
    monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
    monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
    eng = types.SimpleNamespace(
        live_executor=types.SimpleNamespace(
            _closed_trades_file=str(tmp_path / "missing.json")))
    assert _mon(eng)._check_parity_digest() == []
