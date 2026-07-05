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

from bot.config import CONFIG
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

    def test_trend_up_applies_the_configured_mult(self):
        # TREND_UP no longer boosts by default -- see TestTrendUpSizeMultOverride
        # below for the frozen-benchmark A/B behind the 0.7 default.
        p = _cfg(enabled=True)
        try:
            risk = _risk()
            _engine({"ETH/USDT": Regime.TREND_UP})._apply_regime_to(risk, "ETH/USDT")
            assert risk._current_regime == "TREND_UP"
            assert _mult(risk) == CONFIG.risk.trend_up_size_mult
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


class TestTrendUpSizeMultOverride:
    """`TREND_UP_SIZE_MULT` lets the TREND_UP entry be A/B'd independently of
    the hardcoded table — frozen-benchmark attribution showed TREND_UP as the
    weakest/most inconsistent regime bucket, unlike TREND_DOWN which stays
    untouched here. Default is now 0.7 (down from the static table's 1.2x
    boost): combined-universe A/B win (+1.12% vs +1.00% baseline, PF 1.67 vs
    1.55), majors-only a wash on return but better PF/worst-fold — no
    universe got worse. See docs/FROZEN_BENCHMARK.md."""

    def test_default_is_the_frozen_benchmark_winner(self):
        risk = _risk()
        risk.set_regime("TREND_UP", "NORMAL")
        assert _mult(risk) == 0.7

    def test_override_applies_to_trend_up_only(self):
        from unittest.mock import patch as _patch
        with _patch("bot.risk.risk_engine.CONFIG") as m:
            m.risk.trend_up_size_mult = 0.7
            risk = _risk()
            risk.set_regime("TREND_UP", "NORMAL")
            assert _mult(risk) == 0.7
            # TREND_DOWN keeps its static-table value, unaffected by the override.
            risk.set_regime("TREND_DOWN", "NORMAL")
            assert _mult(risk) == 1.2


class TestRegimeCapTightening:
    """The fixed-fractional pre-cap position_usd routinely exceeds the
    notional cap ("binds on ~every crypto trade" — see vol_target_sizing's
    docstring), so multiplying that already-oversized value by a sub-1.0
    regime mult was previously a no-op: still clamped to the same cap. This
    silently neutered EVERY regime reduction (CHOP 0.5x, RANGE 0.7x, and
    TREND_UP once A/B'd down to 0.7x) in both live and backtest. Frozen-
    benchmark A/B'd for all three with no downside on either universe
    (docs/FROZEN_BENCHMARK.md), so ANY regime_mult<1.0 now tightens
    max_notional_usd itself, mirroring vol_target_sizing's tighten-only
    pattern — not scoped to a single regime name."""

    def test_any_reduce_regime_tightens_the_cap(self):
        import inspect
        src = inspect.getsource(RiskEngine._evaluate_locked)
        assert "if regime_mult < 1.0:" in src
        mult = src.index("max_notional_usd *= regime_mult")
        clamp = src.index("position_usd = max_notional_usd")
        assert mult < clamp

    def test_boost_regimes_stay_cap_only_not_exceeding(self):
        # regime_mult>=1.0 (TREND_DOWN/EXPANSION boosts) must never widen the
        # cap itself -- only the pre-cap position_usd -- per C2-29.
        import inspect
        src = inspect.getsource(RiskEngine._evaluate_locked)
        segment = src[src.index("if regime_mult < 1.0:\n            max_notional_usd"):
                       src.index("if max_notional_usd > 0 and position_usd > max_notional_usd")]
        assert ">= 1.0" not in segment
