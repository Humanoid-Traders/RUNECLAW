"""
Phase C: per-setup expectancy nudge.

Proves bot/learning/setup_expectancy.py: a setup's own historical win rate
(symbol + regime + direction) yields a small, bounded, shrinkage-tempered
confidence nudge — zero below min_samples, sign tracks win rate, magnitude never
exceeds max_nudge, and unseen setups are neutral.
"""

from bot.learning.setup_expectancy import SetupExpectancy


def _samples(sym, regime, direction, wins, losses):
    return ([(sym, regime, direction, True)] * wins
            + [(sym, regime, direction, False)] * losses)


def test_zero_nudge_below_min_samples():
    exp = SetupExpectancy(min_samples=10)
    exp.ingest(_samples("SOL", "RANGE", "LONG", wins=3, losses=2))  # 5 < 10
    assert exp.confidence_nudge("SOL", "RANGE", "LONG") == 0.0


def test_unseen_setup_is_neutral():
    exp = SetupExpectancy(min_samples=10).ingest(_samples("SOL", "RANGE", "LONG", 20, 0))
    wr, n = exp.lookup("BTC", "TREND_UP", "SHORT")
    assert (wr, n) == (0.5, 0)
    assert exp.confidence_nudge("BTC", "TREND_UP", "SHORT") == 0.0


def test_winning_setup_nudges_up_losing_down():
    # Lots of samples so shrinkage ~ full strength.
    win = SetupExpectancy(min_samples=10, max_nudge=0.05, shrinkage=10.0)
    win.ingest(_samples("SOL", "RANGE", "LONG", wins=80, losses=20))   # 80% win
    lose = SetupExpectancy(min_samples=10, max_nudge=0.05, shrinkage=10.0)
    lose.ingest(_samples("SOL", "RANGE", "LONG", wins=20, losses=80))  # 20% win
    up = win.confidence_nudge("SOL", "RANGE", "LONG")
    down = lose.confidence_nudge("SOL", "RANGE", "LONG")
    assert up > 0 and down < 0
    assert abs(up) <= 0.05 and abs(down) <= 0.05      # bounded


def test_nudge_is_bounded_at_extremes():
    exp = SetupExpectancy(min_samples=5, max_nudge=0.05, shrinkage=0.0)
    exp.ingest(_samples("X", "RANGE", "LONG", wins=100, losses=0))    # 100% win
    assert exp.confidence_nudge("X", "RANGE", "LONG") <= 0.05 + 1e-9
    exp2 = SetupExpectancy(min_samples=5, max_nudge=0.05, shrinkage=0.0)
    exp2.ingest(_samples("X", "RANGE", "LONG", wins=0, losses=100))   # 0% win
    assert exp2.confidence_nudge("X", "RANGE", "LONG") >= -0.05 - 1e-9


def test_shrinkage_tempers_thin_samples():
    # Same win rate (100%), different sample sizes: more samples -> bigger nudge.
    thin = SetupExpectancy(min_samples=5, max_nudge=0.05, shrinkage=10.0)
    thin.ingest(_samples("X", "RANGE", "LONG", wins=6, losses=0))
    thick = SetupExpectancy(min_samples=5, max_nudge=0.05, shrinkage=10.0)
    thick.ingest(_samples("X", "RANGE", "LONG", wins=200, losses=0))
    assert thick.confidence_nudge("X", "RANGE", "LONG") > thin.confidence_nudge("X", "RANGE", "LONG")


def test_case_and_whitespace_insensitive():
    exp = SetupExpectancy(min_samples=5).ingest(_samples("sol", "range", "long", 10, 0))
    a = exp.confidence_nudge("SOL", "RANGE", "LONG")
    b = exp.confidence_nudge(" sol ", " Range ", " Long ")
    assert a == b and a > 0


def test_samples_from_decisions():
    class D:
        def __init__(self, symbol, regime, direction, pnl):
            self.symbol = symbol
            self.market_regime = regime
            self.direction = direction
            self.pnl_result = pnl
    decisions = [D("SOL", "RANGE", "LONG", 5.0), D("BTC", "TREND_UP", "SHORT", -2.0),
                 D("ETH", "RANGE", "LONG", None)]   # incomplete -> excluded
    samples = SetupExpectancy.samples_from_decisions(decisions)
    assert samples == [("SOL", "RANGE", "LONG", True), ("BTC", "TREND_UP", "SHORT", False)]


def test_not_ready_when_empty():
    exp = SetupExpectancy()
    assert exp.is_ready() is False
    assert exp.confidence_nudge("SOL", "RANGE", "LONG") == 0.0
