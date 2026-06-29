"""
Regime-aware sizing bridge (P0.1 from the product audit).

The analyzer classifies a per-symbol market regime, but it was never bridged into
the risk engine, so _current_regime stayed "UNKNOWN" and the per-regime size
multiplier was always 1.0×. _apply_regime_to() wires it, gated by
REGIME_SIZING_ENABLED (default OFF → byte-identical). The analyzer's Regime values
are a subset of the risk engine's _REGIME_MULTIPLIERS keys, so regime.value maps
straight through.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from bot.core.engine import RuneClawEngine
from bot.core.analyzer import Regime
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker


def _risk():
    state = os.path.join(tempfile.mkdtemp(), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _engine(regimes):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.analyzer = SimpleNamespace(_current_regimes=regimes)
    return eng


def _cfg(enabled):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.risk.regime_sizing_enabled = enabled
    return p


def _mult(risk):
    return risk.get_regime_adjusted_params(
        risk._current_regime, risk._current_vol_state)["position_size_mult"]


class TestRegimeBridge:
    def test_flag_off_leaves_regime_unknown(self):
        p = _cfg(enabled=False)
        try:
            risk = _risk()
            _engine({"BTC/USDT": Regime.CHOP})._apply_regime_to(risk, "BTC/USDT")
            assert risk._current_regime == "UNKNOWN"
            assert _mult(risk) == 1.0   # byte-identical: no multiplier
        finally:
            p.stop()

    def test_chop_reduces_size(self):
        p = _cfg(enabled=True)
        try:
            risk = _risk()
            _engine({"BTC/USDT": Regime.CHOP})._apply_regime_to(risk, "BTC/USDT")
            assert risk._current_regime == "CHOP"
            assert _mult(risk) == 0.5
        finally:
            p.stop()

    def test_trend_increases_size(self):
        p = _cfg(enabled=True)
        try:
            risk = _risk()
            _engine({"ETH/USDT": Regime.TREND_UP})._apply_regime_to(risk, "ETH/USDT")
            assert risk._current_regime == "TREND_UP"
            assert _mult(risk) == 1.2
        finally:
            p.stop()

    def test_expansion_maps(self):
        p = _cfg(enabled=True)
        try:
            risk = _risk()
            _engine({"SOL/USDT": Regime.EXPANSION})._apply_regime_to(risk, "SOL/USDT")
            assert risk._current_regime == "EXPANSION"
            assert _mult(risk) == 1.3
        finally:
            p.stop()

    def test_every_analyzer_regime_is_a_known_key(self):
        # The "no translation layer needed" claim: each analyzer Regime value is a
        # key in the risk engine's multiplier table (UNKNOWN → default 1.0×).
        p = _cfg(enabled=True)
        try:
            for reg in Regime:
                risk = _risk()
                _engine({"X": reg})._apply_regime_to(risk, "X")
                assert risk._current_regime == reg.value
                # No KeyError; UNKNOWN falls back to the 1.0× default.
                _mult(risk)
        finally:
            p.stop()

    def test_no_regime_for_symbol_is_noop(self):
        p = _cfg(enabled=True)
        try:
            risk = _risk()
            _engine({"BTC/USDT": Regime.CHOP})._apply_regime_to(risk, "ETH/USDT")
            assert risk._current_regime == "UNKNOWN"
        finally:
            p.stop()

    def test_missing_analyzer_map_is_noop(self):
        p = _cfg(enabled=True)
        try:
            risk = _risk()
            _engine(None)._apply_regime_to(risk, "BTC/USDT")
            assert risk._current_regime == "UNKNOWN"
        finally:
            p.stop()

    def test_fail_open_on_lookup_error(self):
        p = _cfg(enabled=True)
        try:
            risk = _risk()

            class _Boom:
                def get(self, _):
                    raise RuntimeError("regime boom")

            _engine(_Boom())._apply_regime_to(risk, "BTC/USDT")
            assert risk._current_regime == "UNKNOWN"   # untouched, no raise
        finally:
            p.stop()
