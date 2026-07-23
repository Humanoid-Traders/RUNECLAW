"""MARKETPLACE Phase 2a — additive preset entry gates in the backtester.

The backtest engine can replay a NAMED marketplace Strategy-Agent with its real
entry semantics (volume-spike / regime / RSI), so a scorecard measures each
agent's actual design — not a near-identical baseline. Every gate is OFF by
default, so an unconfigured run is byte-identical to before (proven here by the
fast-path no-op) and the gates only ever REMOVE entries, never add or reshape
them.
"""
import numpy as np

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.utils.models import MarketSignal


class _Idea:
    def __init__(self, confidence=0.9):
        self.confidence = confidence


class _Regime:
    def __init__(self, value):
        self.value = value


def _engine(**cfg_kw):
    return BacktestEngine(BacktestConfig(symbol="BTC/USDT", **cfg_kw))


def _sig(symbol="BTC/USDT", spike_ratio=0.0, spike=False):
    return MarketSignal(symbol=symbol, price=100.0, change_pct_24h=0.0,
                        volume_usd_24h=1000.0, volume_spike=spike,
                        volume_spike_ratio=spike_ratio)


def test_defaults_are_a_strict_no_op():
    eng = _engine()
    # No gates configured -> the fast path returns False without touching data.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), []) is False
    # The config carries the new fields, all OFF.
    assert eng.config.volume_spike_min is None
    assert eng.config.regime_filter == ""
    assert eng.config.rsi_max is None


def test_volume_spike_gate_filters_below_min():
    eng = _engine(volume_spike_min=3.0)
    # Below the 3x ratio and no boolean spike -> rejected.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=2.0), []) is True
    # At/above the ratio -> passes.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=3.5), []) is False
    # The boolean spike flag also satisfies the gate (mirrors live OR semantics).
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=0.0, spike=True), []) is False


def test_regime_gate_requires_matching_regime():
    eng = _engine(regime_filter="TREND_DOWN")
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_UP")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), []) is True     # wrong regime
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_DOWN")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), []) is False    # matches
    # Case-insensitive on the configured value.
    eng2 = _engine(regime_filter="trend_down")
    eng2.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_DOWN")
    assert eng2._rejected_by_preset_gate(_Idea(), _sig(), []) is False


def _window(closes):
    class _Bar:
        def __init__(self, c):
            self.close = c
    return [_Bar(c) for c in closes]


def test_rsi_gate_only_admits_oversold():
    eng = _engine(rsi_max=35.0)
    # Steadily FALLING closes -> low RSI (oversold) -> passes the rsi_max gate.
    falling = _window(list(np.linspace(100, 60, 30)))
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), falling) is False
    # Steadily RISING closes -> high RSI -> rejected by rsi_max=35.
    rising = _window(list(np.linspace(60, 100, 30)))
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), rising) is True
    # Too few bars -> RSI unknown -> never reject on it.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), _window([100, 101, 102])) is False


def test_gates_compose_any_one_rejects():
    # Momentum-hunter-like: volume spike + TREND_UP. A TREND_DOWN bar is rejected
    # even if the volume passes, because the regime gate also applies.
    eng = _engine(volume_spike_min=3.0, regime_filter="TREND_UP")
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_DOWN")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=5.0), []) is True
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_UP")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=5.0), []) is False


def test_runner_preset_gate_kwargs_default_off():
    import argparse
    from bot.backtest.runner import _preset_gate_kwargs
    ns = argparse.Namespace()
    kw = _preset_gate_kwargs(ns)
    assert kw["volume_spike_min"] is None
    assert kw["regime_filter"] == ""
    assert kw["rsi_max"] is None
    assert kw["confidence_threshold"] == 0.0
    # And they map onto the config as no-ops.
    cfg = BacktestConfig(symbol="BTC/USDT", **kw)
    assert cfg.volume_spike_min is None and cfg.regime_filter == "" and cfg.rsi_max is None
