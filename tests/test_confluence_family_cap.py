"""
Confluence mean-reversion oscillator de-correlation (roadmap alpha item).

RSI, Bollinger %B, Stochastic and Fibonacci all read "price is low/high in its
recent range", so on an oversold/overbought bar they co-fire and inflate the
confluence score with what is really ONE piece of information. When
CONFIG.confluence.family_cap_enabled is set, their COMBINED (actively-voting)
weight is scaled down to mr_oscillator_weight_cap so the family counts as ~one
strong voter.

Default OFF — these tests pin the *opt-in* behaviour and that the flag gates it.
"""

from unittest.mock import patch

from bot.core.analyzer import Analyzer
from bot.core.ta_utils import Regime
from bot.utils.models import MarketSignal


def _signal():
    return MarketSignal(symbol="BTC/USDT", price=100.0, change_pct_24h=0.0,
                        volume_usd_24h=1_000_000.0)


def _score(indicators, *, enabled=False, cap=2.0):
    if not enabled:
        # Real CONFIG (family_cap_enabled defaults False) — the baseline.
        return Analyzer._score_confluence(indicators, Regime.RANGE, _signal())
    with patch("bot.core.analyzer.CONFIG") as cfg:
        cfg.confluence.family_cap_enabled = True
        cfg.confluence.mr_oscillator_weight_cap = cap
        return Analyzer._score_confluence(indicators, Regime.RANGE, _signal())


# Four oscillators all screaming the same direction.
_BULL_CLUSTER = {"rsi": 25, "bb_pct_b": 0.1, "stoch_k": 15, "stoch_d": 15, "fib_zone": "below_786"}
_BEAR_CLUSTER = {"rsi": 78, "bb_pct_b": 0.92, "stoch_k": 88, "stoch_d": 88, "fib_zone": "above_236"}


class TestCapReducesCoFiring:
    def test_bullish_cluster_is_pulled_toward_neutral(self):
        base = _score(_BULL_CLUSTER)
        capped = _score(_BULL_CLUSTER, enabled=True)
        assert base > 0.5            # the cluster reads bullish
        assert capped < base         # capping de-weights the co-firing family
        assert capped > 0.5          # still bullish, just less inflated

    def test_bearish_cluster_is_pulled_toward_neutral(self):
        base = _score(_BEAR_CLUSTER)
        capped = _score(_BEAR_CLUSTER, enabled=True)
        assert base < 0.5            # the cluster reads bearish
        assert capped > base         # capping pulls it back toward 0.5
        assert capped < 0.5

    def test_lower_cap_pulls_harder(self):
        c_loose = _score(_BULL_CLUSTER, enabled=True, cap=3.0)
        c_tight = _score(_BULL_CLUSTER, enabled=True, cap=1.0)
        # A tighter cap removes more of the redundant oscillator weight.
        assert c_tight < c_loose


class TestNoFalsePenalty:
    def test_lone_oscillator_is_unchanged(self):
        # Only RSI casts a directional vote; %B defaults to neutral. A single
        # signalling oscillator over-counts nothing, so the cap must not touch it.
        ind = {"rsi": 25}
        assert _score(ind, enabled=True) == _score(ind)

    def test_two_oscillators_disagreeing_then_one_neutral(self):
        # RSI bullish, the rest neutral/absent -> only one active member -> no-op.
        ind = {"rsi": 28, "stoch_k": 50, "stoch_d": 50}
        assert _score(ind, enabled=True) == _score(ind)


class TestFlagGating:
    def test_disabled_equals_baseline(self):
        # Explicitly patch the flag OFF; must match the un-patched baseline.
        with patch("bot.core.analyzer.CONFIG") as cfg:
            cfg.confluence.family_cap_enabled = False
            disabled = Analyzer._score_confluence(_BULL_CLUSTER, Regime.RANGE, _signal())
        assert disabled == _score(_BULL_CLUSTER)

    def test_default_config_is_off(self):
        from bot.config import CONFIG
        assert CONFIG.confluence.family_cap_enabled is False


class TestWiring:
    def test_cap_logic_present_in_source(self):
        import inspect
        src = inspect.getsource(Analyzer._score_confluence)
        assert "family_cap_enabled" in src
        assert "mr_oscillator_weight_cap" in src
        assert "_mark_mr_osc" in src
