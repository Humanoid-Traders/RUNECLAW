"""
Closed-trade outcomes are tagged with the REAL detected regime (deep-audit medium).

Outcomes were tagged with the risk engine's _current_regime, which stays
"UNKNOWN" unless REGIME_SIZING_ENABLED (the regime→sizing bridge is gated OFF by
default). Setup-expectancy looks up by the analyzer's real regime (regime.value),
so the keys never matched and the per-setup nudge was permanently zero. The
engine now tags outcomes via _outcome_regime, which prefers the analyzer's
actual per-symbol regime and tolerates symbol-format differences.
"""

import inspect
from types import SimpleNamespace

from bot.core.engine import RuneClawEngine
from bot.core.ta_utils import Regime


def _self(regimes=None, current_regime="UNKNOWN", analyzer=True):
    an = SimpleNamespace(_current_regimes=regimes if regimes is not None else {}) if analyzer else None
    return SimpleNamespace(analyzer=an, risk=SimpleNamespace(_current_regime=current_regime))


_regime = RuneClawEngine._outcome_regime


class TestOutcomeRegime:
    def test_uses_analyzer_real_regime(self):
        s = _self(regimes={"BTC/USDT": Regime.TREND_UP})
        assert _regime(s, "BTC/USDT") == "TREND_UP"

    def test_tolerates_symbol_format_difference(self):
        # Position symbol carries the :USDT suffix; regime is keyed by "BTC/USDT".
        s = _self(regimes={"BTC/USDT": Regime.RANGE})
        assert _regime(s, "BTC/USDT:USDT") == "RANGE"

    def test_falls_back_to_current_regime_when_no_analyzer_entry(self):
        s = _self(regimes={"ETH/USDT": Regime.CHOP}, current_regime="EXPANSION")
        assert _regime(s, "BTC/USDT") == "EXPANSION"

    def test_falls_back_when_analyzer_absent(self):
        s = _self(analyzer=False, current_regime="RANGE")
        assert _regime(s, "BTC/USDT") == "RANGE"

    def test_empty_analyzer_and_unknown_current(self):
        s = _self(regimes={}, current_regime="UNKNOWN")
        assert _regime(s, "BTC/USDT") == "UNKNOWN"

    def test_not_the_gated_unknown_when_real_regime_present(self):
        # The whole point: a real regime is used instead of the gated "UNKNOWN".
        s = _self(regimes={"SOL/USDT": Regime.TREND_DOWN}, current_regime="UNKNOWN")
        assert _regime(s, "SOL/USDT") == "TREND_DOWN"
        assert _regime(s, "SOL/USDT") != "UNKNOWN"


class TestWiring:
    def test_live_close_records_with_outcome_regime(self):
        src = inspect.getsource(RuneClawEngine._on_live_position_closed)
        assert "market_regime=self._outcome_regime(" in src
        # The gated _current_regime is no longer the outcome's regime source here.
        assert 'market_regime=str(getattr(self.risk, "_current_regime"' not in src
