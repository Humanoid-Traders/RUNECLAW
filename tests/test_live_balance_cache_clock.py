"""Live balance cache clock coherence (hotfix).

The cache timestamp `_live_balance_cache_ts` is diffed against
time.monotonic() by BOTH readers:
  - engine.get_live_equity()'s 30s TTL check, and
  - the proactive monitor's 5-min stale-balance alert.

/livebalance (the very command the stale alert recommends) stamped it with
time.time() — a wall-clock epoch that dwarfs any monotonic value. One
/livebalance on the operator account then made the cache read as fresh
FOREVER (epoch-poisoned TTL diff is hugely negative), freezing live position
sizing on that equity snapshot indefinitely AND blinding the staleness alert
that should have caught it. This is the codebase's documented monotonic /
wall-clock mixing failure class.
"""
from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import bot.core.proactive_monitor as mon_mod
from bot.core.proactive_monitor import ProactiveMonitor


# The monitor reads time.monotonic() at check time. Pin it (rather than
# deriving stamps from the real clock) so the tests hold on a freshly booted
# host: on a young CI runner real monotonic() - 400 goes NEGATIVE and trips
# the "never stamped" ts<=0 guard — the exact fresh-boot trap these clocks
# keep springing.
_NOW = 10_000.0


def _stale_alerts(monkeypatch, ts_val):
    monkeypatch.setattr(mon_mod, "CONFIG", SimpleNamespace(is_live=lambda: True))
    monkeypatch.setattr(mon_mod.time, "monotonic", lambda: _NOW)
    stub = SimpleNamespace(engine=SimpleNamespace(_live_balance_cache_ts=ts_val))
    return ProactiveMonitor._check_stale_balance(stub)


def test_livebalance_handler_stamps_monotonic_never_wall_clock():
    import bot.skills.telegram_handler as th

    src = inspect.getsource(th.TelegramHandler._cmd_livebalance)
    assert "_live_balance_cache_ts = time.monotonic()" in src
    assert "_live_balance_cache_ts = time.time()" not in src


def test_stale_alert_fires_on_old_monotonic_stamp(monkeypatch):
    alerts = _stale_alerts(monkeypatch, _NOW - 400.0)
    assert len(alerts) == 1
    assert alerts[0].alert_type == "STALE_BALANCE"


def test_fresh_monotonic_stamp_is_quiet(monkeypatch):
    assert _stale_alerts(monkeypatch, _NOW - 10.0) == []


def test_wall_clock_poisoned_stamp_blinds_the_watchdog(monkeypatch):
    # Documents WHY the handler pin above matters: an epoch stamp dwarfs any
    # monotonic "now", so the age diff goes hugely negative and the watchdog
    # stays silent no matter how stale the balance really is. The engine's
    # TTL check misreads the same stamp as fresh-forever — frozen live
    # sizing with no alert.
    assert time.time() > _NOW + 300, "epoch must dwarf the pinned monotonic now"
    assert _stale_alerts(monkeypatch, time.time()) == []
