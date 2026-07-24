"""The decision picture must name WHICH voters drove the call.

The analyzer builds a named per-voter breakdown (_confluence_votes) but the
ExplainabilityEngine.explain() call previously dropped it, leaving
report.factors / top_bullish / top_bearish permanently empty. These tests pin
the wiring: breakdown tuples -> votes/weights/labels -> populated attribution.
"""

from bot.core.explainability import ExplainabilityEngine


def _explain_with(breakdown):
    labels = [b[0] for b in breakdown]
    votes = [float(b[1]) for b in breakdown]
    weights = [float(b[2]) for b in breakdown]
    return ExplainabilityEngine().explain(
        trade_id="t1",
        symbol="BTCUSDT",
        direction="LONG",
        indicators={},
        regime="TRENDING",
        confluence=0.7,
        confidence=0.7,
        votes=votes,
        weights=weights,
        labels=labels,
    )


def test_breakdown_populates_factor_attribution():
    report = _explain_with([
        ("rsi", 0.8, 1.5),
        ("macd", 0.4, 1.0),
        ("stoch", -0.6, 1.2),
    ])
    assert report.factors, "factors must be populated from the voter breakdown"
    names = {f.factor for f in report.factors}
    assert {"rsi", "macd", "stoch"} <= names
    # contributions sum to ~100 and are sorted descending
    contribs = [f.contribution_pct for f in report.factors]
    assert abs(sum(contribs) - 100.0) < 1.0
    assert contribs == sorted(contribs, reverse=True)


def test_top_bullish_and_bearish_are_directional():
    report = _explain_with([
        ("rsi", 0.8, 1.5),
        ("stoch", -0.6, 1.2),
        ("vwap", 0.0, 0.5),    # exactly flat — belongs to neither side
    ])
    bull = set(report.top_bullish)
    bear = set(report.top_bearish)
    assert "rsi" in bull and "rsi" not in bear
    assert "stoch" in bear and "stoch" not in bull
    assert "vwap" not in bull | bear


def test_empty_breakdown_stays_honestly_empty():
    report = _explain_with([])
    assert report.factors == []
