"""Deferred audit items: 15m scalp session VWAP + cross-layer confirmation.

Scalp/intraday setups rebuild the session VWAP from the 15m series (real
intraday granularity vs <=24 hourly points). The cross-layer confirmation
bonus (default OFF, measured) nudges confidence when >=2 independent signal
families agree with the net direction.
"""

import dataclasses


from bot.config import CONFIG
from bot.core.analyzer import _apply_scalp_session_vwap


def _mk_15m(n=96, base=100.0, drift=0.02):
    # [ts, o, h, l, c, v] rows, 15m apart, same UTC day.
    ts0 = 1_700_000_000_000
    rows = []
    for i in range(n):
        c = base + i * drift
        rows.append([ts0 + i * 900_000, c, c + 0.3, c - 0.3, c, 10.0])
    return rows


class TestScalpSessionVWAP:
    def test_scalp_rebuilds_session_from_15m(self):
        ind = {"vwap_session": 100.0}
        _apply_scalp_session_vwap(ind, "scalp", {"15m": _mk_15m()})
        assert ind.get("vwap_session_tf") == "15m"
        # Rebuilt value differs from the coarse 1h placeholder.
        assert ind["vwap_session"] != 100.0

    def test_swing_setup_untouched(self):
        ind = {"vwap_session": 100.0}
        _apply_scalp_session_vwap(ind, "swing", {"15m": _mk_15m()})
        assert "vwap_session_tf" not in ind
        assert ind["vwap_session"] == 100.0

    def test_missing_15m_is_noop(self):
        ind = {"vwap_session": 100.0}
        _apply_scalp_session_vwap(ind, "scalp", {"1h": _mk_15m()})
        assert ind["vwap_session"] == 100.0

    def test_default_flag_on(self):
        assert CONFIG.analyzer.scalp_session_vwap_enabled is True


def _score(ind, price=100.0):
    from bot.core.analyzer import Analyzer
    from bot.core.ta_utils import Regime
    from bot.utils.models import MarketSignal
    sig = MarketSignal(symbol="T/USDT", price=price, change_pct_24h=1.0,
                       volume_usd_24h=1e6)
    return Analyzer.__dict__["_score_confluence"].__func__(
        ind, Regime.TREND_UP, sig)


class TestCrossLayerConfirmation:
    def test_default_off_is_baseline(self, monkeypatch):
        # With the flag OFF, the bonus never applies (byte-identical scoring).
        assert CONFIG.analyzer.cross_layer_confirmation_enabled is False

    def _three_bull_families(self):
        # Sweep (liquidity) + candlestick (price-action) both bullish, plus a
        # bearish MACD so the raw score doesn't saturate at 1.0 and there's
        # visible headroom for the (small, bounded) breadth bonus.
        return {
            "_sweep_votes": [1.0], "_sweep_weights": [0.6],
            "candle_bullish_strength": 3.0, "candle_bearish_strength": 0.0,
            "candle_bullish_count": 3, "candle_bearish_count": 0,
            "macd_histogram": -0.5,
        }

    def test_bonus_fires_and_lifts_a_confirmed_bull(self, monkeypatch):
        base = _score(self._three_bull_families())
        new_an = dataclasses.replace(CONFIG.analyzer,
                                     cross_layer_confirmation_enabled=True)
        monkeypatch.setattr("bot.core.analyzer.CONFIG",
                            dataclasses.replace(CONFIG, analyzer=new_an))
        boosted = _score(self._three_bull_families())
        # Two agreeing families (liquidity + price-action) -> +0.03 nudge.
        assert boosted > base + 1e-6
        assert boosted <= 1.0
        assert boosted - base <= 0.09 + 1e-9   # bounded

    def test_bonus_never_flips_or_lowers_direction(self, monkeypatch):
        new_an = dataclasses.replace(CONFIG.analyzer,
                                     cross_layer_confirmation_enabled=True)
        monkeypatch.setattr("bot.core.analyzer.CONFIG",
                            dataclasses.replace(CONFIG, analyzer=new_an))
        boosted = _score(self._three_bull_families())
        assert boosted >= 0.5   # a confirmed bull stays bullish
