"""
Proactive slippage alert (closes the loop on realized-slippage recording).

The monitor warns — live only — when a symbol's mean absolute slippage drifts
above CONFIG.execution.slippage_alert_mean_pct, once it has at least
slippage_alert_min_trades recorded fills. Exercises _check_slippage in isolation.
"""

from types import SimpleNamespace

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor
from bot.core.slippage import SlippageStats


@pytest.fixture()
def live(monkeypatch):
    # CONFIG is frozen; patch is_live on its class (same pattern as the proactive
    # alert suite). Default thresholds: mean 0.20%, min 10 trades.
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


def _stats(symbol, mean, trades):
    return SlippageStats(
        symbol=symbol, total_trades=trades, mean_slippage_pct=mean,
        p95_slippage_pct=mean * 1.5, adverse_count=trades, total_slippage_usd=12.34)


def _mon(stats_map):
    engine = SimpleNamespace(slippage=SimpleNamespace(get_all_stats=lambda: stats_map))
    return ProactiveMonitor(engine)


def test_quiet_when_not_live():
    # No live fixture → is_live() is False → no alert even with bad slippage.
    assert _mon({"BTC/USDT": _stats("BTC/USDT", 0.5, 50)})._check_slippage() == []


def test_alerts_above_threshold_with_enough_trades(live):
    a = _mon({"BTC/USDT": _stats("BTC/USDT", 0.40, 20)})._check_slippage()
    assert len(a) == 1
    assert a[0].alert_type == "SLIPPAGE_HIGH"
    assert "BTC/USDT" in a[0].title


def test_quiet_below_threshold(live):
    assert _mon({"BTC/USDT": _stats("BTC/USDT", 0.05, 50)})._check_slippage() == []


def test_quiet_with_too_few_trades(live):
    # Above the mean threshold but only 3 fills (< min 10) → not enough signal.
    assert _mon({"ETH/USDT": _stats("ETH/USDT", 0.9, 3)})._check_slippage() == []


def test_only_offending_symbols_alert(live):
    a = _mon({
        "BTC/USDT": _stats("BTC/USDT", 0.40, 20),   # alerts
        "ETH/USDT": _stats("ETH/USDT", 0.02, 40),   # clean
    })._check_slippage()
    assert len(a) == 1 and "BTC/USDT" in a[0].title


def test_no_tracker_is_quiet(live):
    mon = ProactiveMonitor(SimpleNamespace())
    assert mon._check_slippage() == []
