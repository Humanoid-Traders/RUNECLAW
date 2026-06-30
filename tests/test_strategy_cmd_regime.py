"""
/strategy shows a real regime, not the gated "UNKNOWN" (deep-audit low #56).

The command read self.engine.risk._current_regime, which stays "UNKNOWN" unless
REGIME_SIZING_ENABLED (the regime→sizing bridge is gated). The analyzer detects
a regime per symbol regardless, so /strategy now displays the most common real
regime from analyzer._current_regimes, falling back to the risk value.
"""

import inspect
from types import SimpleNamespace

from bot.core.ta_utils import Regime
from bot.skills.telegram_handler import TelegramHandler

_rep = TelegramHandler._representative_regime


def _self(regimes=None, risk_regime="UNKNOWN", analyzer=True):
    an = SimpleNamespace(_current_regimes=regimes if regimes is not None else {}) if analyzer else None
    return SimpleNamespace(engine=SimpleNamespace(
        analyzer=an, risk=SimpleNamespace(_current_regime=risk_regime)))


class TestRepresentativeRegime:
    def test_most_common_real_regime(self):
        s = _self(regimes={"BTC/USDT": Regime.TREND_UP, "ETH/USDT": Regime.TREND_UP,
                           "SOL/USDT": Regime.RANGE})
        assert _rep(s) == "TREND_UP"

    def test_ignores_unknown_entries(self):
        s = _self(regimes={"BTC/USDT": Regime.UNKNOWN, "ETH/USDT": Regime.RANGE})
        assert _rep(s) == "RANGE"

    def test_falls_back_when_all_unknown(self):
        s = _self(regimes={"BTC/USDT": Regime.UNKNOWN}, risk_regime="CHOP")
        assert _rep(s) == "CHOP"

    def test_falls_back_when_no_analyzer_regimes(self):
        s = _self(regimes={}, risk_regime="EXPANSION")
        assert _rep(s) == "EXPANSION"

    def test_falls_back_when_analyzer_absent(self):
        s = _self(analyzer=False, risk_regime="RANGE")
        assert _rep(s) == "RANGE"

    def test_real_regime_beats_gated_unknown(self):
        # The whole point: a real regime is shown even though risk is "UNKNOWN".
        s = _self(regimes={"BTC/USDT": Regime.TREND_DOWN}, risk_regime="UNKNOWN")
        assert _rep(s) == "TREND_DOWN" != "UNKNOWN"


class TestWiring:
    def test_cmd_strategy_uses_helper(self):
        src = inspect.getsource(TelegramHandler._cmd_strategy)
        assert "regime = self._representative_regime()" in src
        assert "regime = self.engine.risk._current_regime" not in src
