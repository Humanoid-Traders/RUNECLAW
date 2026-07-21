"""QC-6: REST CVD-price divergence on the cumulative series (audit-confirmed).

The REST fallback compared raw WINDOW deltas — a magnitude test — while the WS
path already computes divergence correctly on the true CUMULATIVE-CVD series via
_series_divergence. On a market where buyers keep accumulating but each poll's
delta shrinks, the delta-magnitude test fires a FALSE bearish divergence even
though cumulative CVD is still rising and confirming price. The REST fallback
now builds the cumulative series (np.cumsum of the kept deltas) and uses the
same _series_divergence detector.

Live-only path (backtests use pre-recorded OrderFlowSignals via
RecordedOrderFlow), so the frozen benchmark is unaffected.
"""

from __future__ import annotations

import numpy as np

from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig


# Buyers still net-accumulating (cumulative CVD rising to new highs), but each
# poll's delta shrinks. Price makes a higher high.
DELTAS = [30.0, 20.0, 10.0, 8.0, 6.0, 4.0]
PRICES = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_flag_defaults_on():
    assert OrderFlowConfig().rest_cvd_series_divergence is True


def test_raw_delta_detector_gives_a_false_bearish_divergence():
    # The legacy behaviour the fix removes: shrinking delta magnitude reads as
    # "CVD lower high" and — with price higher high — flags distribution.
    assert OrderFlowAnalyzer._detect_cvd_divergence(DELTAS, PRICES) == "bearish_div"


def test_cumulative_series_detector_correctly_sees_no_divergence():
    # Cumulative CVD is still making higher highs (buyers accumulating), so it
    # CONFIRMS the price high — no divergence.
    cum = np.cumsum(DELTAS).tolist()
    assert cum[-1] == 78.0                       # sanity: monotonically rising
    assert OrderFlowAnalyzer._series_divergence(cum, PRICES) == "none"


def test_series_detector_still_catches_a_true_bearish_divergence():
    # Real distribution: cumulative CVD rolls over (lower high) while price makes
    # a higher high.
    deltas = [10.0, 10.0, 10.0, -5.0, -5.0, 3.0]     # cumsum: 10,20,30,25,20,23
    cum = np.cumsum(deltas).tolist()
    assert OrderFlowAnalyzer._series_divergence(cum, PRICES) == "bearish_div"


def test_series_detector_catches_a_true_bullish_divergence():
    # Accumulation: price lower low while cumulative CVD makes a higher low.
    prices = [6.0, 5.0, 4.0, 3.0, 2.0, 1.5]          # lower low in 2nd half
    deltas = [-10.0, -10.0, -10.0, 6.0, 6.0, 6.0]    # cumsum: -10,-20,-30,-24,-18,-12
    cum = np.cumsum(deltas).tolist()
    assert OrderFlowAnalyzer._series_divergence(cum, prices) == "bullish_div"


def test_disabling_the_flag_restores_the_legacy_detector_path():
    # A belt-and-suspenders escape hatch: OF_REST_CVD_SERIES_DIVERGENCE=0 keeps
    # the old raw-delta detector wired in the REST block.
    cfg = OrderFlowConfig(rest_cvd_series_divergence=False)
    assert cfg.rest_cvd_series_divergence is False


# ── squeeze_building graduated vote (default OFF, A/B-gated) ──────────────────

from bot.core.order_flow import OrderFlowSignal  # noqa: E402


def _squeeze(funding):
    return OrderFlowSignal(symbol="BTC/USDT", confidence=0.8,
                           oi_price_divergence="squeeze_building", funding_rate=funding)


def _oi_vote(sig):
    votes, _w, labels = OrderFlowAnalyzer.to_confluence_votes(sig, funding_extreme=0.0005)
    return dict(zip(labels, votes)).get("of_oi_price_div", "absent")


def test_squeeze_graduated_flag_defaults_off():
    assert OrderFlowConfig().squeeze_graduated_vote is False


def test_legacy_squeeze_is_a_full_strength_knife_edge(monkeypatch):
    monkeypatch.delenv("OF_SQUEEZE_GRADUATED_VOTE", raising=False)
    # Just past the 0.01% baseline → full -1.0 (the knife-edge the fix softens).
    assert _oi_vote(_squeeze(0.00011)) == -1.0
    # Missing funding → a diluting 0.0 vote is still emitted (legacy).
    assert _oi_vote(_squeeze(None)) == 0.0


def test_graduated_squeeze_grades_by_distance_from_baseline(monkeypatch):
    monkeypatch.setenv("OF_SQUEEZE_GRADUATED_VOTE", "1")
    v = _oi_vote(_squeeze(0.00011))
    # Just past baseline → a SMALL negative vote, not a full -1.0.
    assert -0.2 < v < 0.0
    # Far past baseline saturates toward -1.
    assert _oi_vote(_squeeze(0.001)) == -1.0


def test_graduated_squeeze_skips_when_funding_unknown(monkeypatch):
    monkeypatch.setenv("OF_SQUEEZE_GRADUATED_VOTE", "1")
    # Direction is undeterminable without funding → SKIP entirely (no diluting
    # 0.0 vote pulling confluence toward neutral).
    assert _oi_vote(_squeeze(None)) == "absent"
