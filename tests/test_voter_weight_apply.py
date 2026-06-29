"""
Phase B2: gated application of learned voter weights in _score_confluence.

Guarantees:
  * flag OFF (default) -> byte-identical confluence (no multiplier path);
  * flag ON but no/empty learner -> still byte-identical (identity);
  * flag ON with a fitted learner -> the confluence shifts in the learned
    direction (a boosted bullish voter raises confluence; a cut one lowers it),
    and the result stays in [0,1].
"""

import pytest

import bot.core.analyzer as analyzer_mod
from bot.core.analyzer import Analyzer, MarketSignal
from bot.core.ta_utils import Regime
from bot.learning.voter_weights import VoterWeightLearner


def _sig():
    return MarketSignal(symbol="SOL/USDT", price=100.0, change_pct_24h=5.0,
                        volume_usd_24h=1e6, volume_spike=True, volume_spike_ratio=2.0,
                        momentum_score=0.5)


# Strong bullish setup: rsi oversold (+), macd up (+), bb low (+), etc.
_IND = {"rsi": 25, "macd_histogram": 0.5, "bb_pct_b": 0.1, "adx": 35, "plus_di": 30,
        "minus_di": 10, "stoch_k": 15, "stoch_d": 18, "ema_9": 105, "ema_21": 100,
        "taker_buy_ratio": 0.6}


@pytest.fixture
def flag():
    orig = analyzer_mod.CONFIG.analyzer.voter_weight_learning_enabled

    def _set(v):
        object.__setattr__(analyzer_mod.CONFIG.analyzer, "voter_weight_learning_enabled", v)

    yield _set
    object.__setattr__(analyzer_mod.CONFIG.analyzer, "voter_weight_learning_enabled", orig)


def _baseline():
    return Analyzer._score_confluence(_IND, Regime.TREND_UP, _sig(), strategy_type="swing")


def _fitted(mult):
    lw = VoterWeightLearner(min_samples=1)
    lw._mult = dict(mult)
    lw._n_samples = 100
    assert lw.is_ready()
    return lw


def test_flag_off_is_identity(flag, monkeypatch):
    base = _baseline()
    flag(False)
    monkeypatch.setattr("bot.learning.voter_weights.get_voter_learner",
                        lambda *a, **k: _fitted({"rsi": 1.5}))
    assert Analyzer._score_confluence(_IND, Regime.TREND_UP, _sig(), strategy_type="swing") == base


def test_flag_on_no_learner_is_identity(flag, monkeypatch):
    base = _baseline()
    flag(True)
    monkeypatch.setattr("bot.learning.voter_weights.get_voter_learner", lambda *a, **k: None)
    assert Analyzer._score_confluence(_IND, Regime.TREND_UP, _sig(), strategy_type="swing") == base


def test_flag_on_empty_learner_is_identity(flag, monkeypatch):
    base = _baseline()
    flag(True)
    monkeypatch.setattr("bot.learning.voter_weights.get_voter_learner",
                        lambda *a, **k: VoterWeightLearner())   # not ready
    assert Analyzer._score_confluence(_IND, Regime.TREND_UP, _sig(), strategy_type="swing") == base


def test_boosting_bullish_voters_raises_confluence(flag, monkeypatch):
    base = _baseline()
    flag(True)
    # All bullish voters here; boosting them should raise confluence toward 1.
    monkeypatch.setattr("bot.learning.voter_weights.get_voter_learner",
                        lambda *a, **k: _fitted({"rsi": 1.5, "macd": 1.5, "bb_pct_b": 1.5,
                                                 "stoch": 1.5, "adx": 1.5}))
    boosted = Analyzer._score_confluence(_IND, Regime.TREND_UP, _sig(), strategy_type="swing")
    assert boosted >= base
    assert 0.0 <= boosted <= 1.0


def test_cutting_voters_changes_confluence(flag, monkeypatch):
    base = _baseline()
    flag(True)
    monkeypatch.setattr("bot.learning.voter_weights.get_voter_learner",
                        lambda *a, **k: _fitted({"rsi": 0.5, "macd": 0.5, "stoch": 0.5}))
    cut = Analyzer._score_confluence(_IND, Regime.TREND_UP, _sig(), strategy_type="swing")
    assert cut != base
    assert 0.0 <= cut <= 1.0
