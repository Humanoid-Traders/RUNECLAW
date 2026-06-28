"""
The backtest honors CONFIG.trailing.trail_rule, so 'playbook' vs 'multistage'
can be A/B-tested on history BEFORE the rule is ever flipped on the live account.

The discriminating behaviour: the multistage rule has a 1R activation gate (no
trailing until +1R of profit); the playbook rule trails playbook_atr_mult·ATR
behind the mark with NO gate. A sub-1R favorable bar therefore leaves the SL
frozen under 'multistage' but ratchets it under 'playbook'.
"""

import dataclasses
from datetime import datetime

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.utils.models import Direction, TradeIdea
from bot.utils.trailing import make_trailing_state


def _engine():
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=10_000.0,
        slippage_pct=0.0, commission_pct=0.0))


def _bar(o, h, l, c):
    from bot.backtest.models import BacktestBar
    return BacktestBar(timestamp=datetime(2025, 1, 1, 5, tzinfo=UTC),
                       open=o, high=h, low=l, close=c, volume=1000.0, symbol="BTC/USDT")


def _open_long(eng, entry, sl, tp, atr):
    """Open a LONG and seed a real multistage trailing state (with 'stage')."""
    idea = TradeIdea(
        id="TI-TRAIL", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        confidence=0.7, reasoning="x", source="t",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )
    trade = eng.portfolio.open_position(idea, size_usd=100.0)
    initial_risk = abs(entry - sl)
    meta = make_trailing_state(entry, "LONG", initial_risk, atr)
    meta["entry_price"] = entry
    eng._open_bt_positions[trade.trade_id] = meta
    return trade


def _patch_rule(monkeypatch, rule):
    """Swap CONFIG.trailing.trail_rule on the engine module (frozen → replace)."""
    new_trailing = dataclasses.replace(CONFIG.trailing, trail_rule=rule)
    new_config = dataclasses.replace(CONFIG, trailing=new_trailing)
    monkeypatch.setattr("bot.backtest.engine.CONFIG", new_config)


# Sub-1R favorable bar: high 101 is +0.5R (risk = 2). low stays above the 98 SL,
# high stays below the 110 TP, so neither fires — only the trail can move the SL.
_FAV_BAR = dict(o=100.0, h=101.0, l=99.5, c=100.5)


class TestBacktestHonorsTrailRule:
    def test_multistage_keeps_sl_frozen_below_1r(self, monkeypatch):
        _patch_rule(monkeypatch, "multistage")
        eng = _engine()
        try:
            t = _open_long(eng, entry=100.0, sl=98.0, tp=110.0, atr=0.25)
            eng._check_stops_intrabar(_bar(**_FAV_BAR))
            pos = eng.portfolio._positions[t.trade_id]
            # 1R gate not cleared → SL untouched, trail inactive.
            assert pos.stop_loss == pytest.approx(98.0)
            assert eng._open_bt_positions[t.trade_id]["trailing_active"] is False
        finally:
            eng.cleanup()

    def test_playbook_ratchets_sl_below_1r(self, monkeypatch):
        _patch_rule(monkeypatch, "playbook")
        eng = _engine()
        try:
            t = _open_long(eng, entry=100.0, sl=98.0, tp=110.0, atr=0.25)
            eng._check_stops_intrabar(_bar(**_FAV_BAR))
            pos = eng.portfolio._positions[t.trade_id]
            # No gate: candidate = 101 − 2·0.25 = 100.5 > 98 → ratchet up.
            assert pos.stop_loss == pytest.approx(100.5)
            assert eng._open_bt_positions[t.trade_id]["trailing_active"] is True
        finally:
            eng.cleanup()

    def test_default_config_rule_is_multistage(self):
        # The live default must remain the conservative multistage rule.
        assert CONFIG.trailing.trail_rule == "multistage"
