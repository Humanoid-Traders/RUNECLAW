"""
Phase B (instrument-first): _score_confluence named per-voter breakdown.

Two guarantees:
  1. The instrumentation is BYTE-IDENTICAL — the confluence value is unchanged
     (locked to fixed reference values + breakdown does not alter the score).
  2. The breakdown is well-formed: aligned with the score's voters, every entry
     is (name, vote, weight), and names are populated.
"""

from bot.core.analyzer import Analyzer, MarketSignal
from bot.core.ta_utils import Regime


def _sig(sym="SOL/USDT", price=100.0, chg=5.0, spike=True):
    return MarketSignal(symbol=sym, price=price, change_pct_24h=chg, volume_usd_24h=1e6,
                        volume_spike=spike, volume_spike_ratio=2.0, momentum_score=0.5)


# Reference confluence values locked to the current confluence math (family
# caps + dilution guard defaults ON since the 2026-07 audit fixes).
# If a future edit changes the confluence math, these break — by design.
_CASES = [
    ({"rsi": 25, "macd_histogram": 0.5, "bb_pct_b": 0.1, "adx": 35, "plus_di": 30,
      "minus_di": 10, "stoch_k": 15, "stoch_d": 18, "ema_9": 105, "ema_21": 100,
      "taker_buy_ratio": 0.6}, Regime.TREND_UP, "swing", 0.9591),
    ({"rsi": 75, "macd_histogram": -0.5, "bb_pct_b": 0.9, "adx": 35, "plus_di": 10,
      "minus_di": 30, "stoch_k": 85, "stoch_d": 82, "ema_9": 95, "ema_21": 100},
     Regime.TREND_DOWN, "scalp", 0.18),
    ({"rsi": 50, "macd_histogram": 0.0, "bb_pct_b": 0.5, "adx": 15, "vwap": 100.0},
     Regime.RANGE, "intraday", 0.5727),
]


def test_confluence_locked_to_reference():
    for ind, regime, strat, expected in _CASES:
        got = Analyzer._score_confluence(ind, regime, _sig(), strategy_type=strat)
        assert round(got, 4) == expected, f"{regime} {strat}: {got} != {expected}"


def test_breakdown_does_not_change_score():
    for ind, regime, strat, _ in _CASES:
        without = Analyzer._score_confluence(ind, regime, _sig(), strategy_type=strat)
        bd = []
        with_bd = Analyzer._score_confluence(ind, regime, _sig(), strategy_type=strat, breakdown=bd)
        assert without == with_bd            # opt-in capture is side-effect-free on the value
        assert len(bd) > 0


def test_breakdown_is_wellformed():
    ind = _CASES[0][0]
    bd = []
    Analyzer._score_confluence(ind, Regime.TREND_UP, _sig(), strategy_type="swing", breakdown=bd)
    for entry in bd:
        assert isinstance(entry, tuple) and len(entry) == 3
        name, vote, weight = entry
        assert isinstance(name, str) and name
        assert -1.0 <= vote <= 1.0
        assert weight >= 0.0
    names = {n for n, _, _ in bd}
    # The strongly-firing voters in this setup must be present and named.
    assert {"rsi", "macd", "adx"} <= names
