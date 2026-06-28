"""
Phase B: VoterWeightLearner.

Proves the learner's safety-critical properties: identity below thresholds,
bounded multipliers, sign of the adjustment tracks a voter's agree-win-rate vs
the base rate, shrinkage tempers thin voters, the decision/outcome join, and
persistence.
"""

from bot.learning.voter_weights import VoterWeightLearner


def _votes(*names):
    # Each named voter casts a bullish vote (+1) with weight 1.0.
    return [(n, 1.0, 1.0) for n in names]


def test_identity_below_min_samples():
    lw = VoterWeightLearner(min_samples=20)
    lw.fit([(_votes("rsi"), "LONG", True)] * 5)   # 5 < 20
    assert lw.is_ready() is False
    assert lw.multiplier("rsi") == 1.0


def test_unseen_voter_is_identity():
    lw = VoterWeightLearner(min_samples=2, min_voter_samples=1)
    lw.fit([(_votes("rsi"), "LONG", True), (_votes("rsi"), "LONG", True)])
    assert lw.multiplier("does_not_exist") == 1.0


def test_good_voter_boosted_bad_voter_cut():
    # "good" agrees with LONG on trades that win; "bad" agrees on trades that lose.
    # Base rate is 50% (half win, half lose).
    samples = []
    for i in range(40):
        won = i % 2 == 0
        # good fires only on winners; bad fires only on losers.
        names = ["good"] if won else ["bad"]
        samples.append((_votes(*names), "LONG", won))
    lw = VoterWeightLearner(min_samples=20, min_voter_samples=5, shrinkage=0.0).fit(samples)
    assert lw.multiplier("good") > 1.0
    assert lw.multiplier("bad") < 1.0
    # Bounded.
    assert 0.5 <= lw.multiplier("good") <= 1.5
    assert 0.5 <= lw.multiplier("bad") <= 1.5


def test_multiplier_bounded_at_extremes():
    # A voter that always agrees and always wins, base rate well below 1.
    samples = [(_votes("x"), "LONG", True) for _ in range(30)]
    samples += [(_votes("y"), "LONG", False) for _ in range(30)]
    lw = VoterWeightLearner(min_samples=20, min_voter_samples=5, gain=100.0, shrinkage=0.0).fit(samples)
    assert lw.multiplier("x") <= 1.5
    assert lw.multiplier("y") >= 0.5


def test_only_counts_agreeing_votes():
    # 'rsi' votes bullish but every trade is SHORT and loses. It never agrees,
    # so it has < min_voter_samples agreements and stays identity.
    samples = [([("rsi", 1.0, 1.0)], "SHORT", False) for _ in range(30)]
    lw = VoterWeightLearner(min_samples=20, min_voter_samples=5).fit(samples)
    assert lw.multiplier("rsi") == 1.0


def _balanced(k):
    # 'v' agrees-and-wins k times; k balancing losers (no 'v') keep the overall
    # base rate at exactly 0.5, so only 'v's sample count differs between cases.
    s = [(_votes("filler"), "LONG", i % 2 == 0) for i in range(40)]   # 50% filler
    s += [(_votes("v"), "LONG", True) for _ in range(k)]              # v: +k wins
    s += [(_votes("other"), "LONG", False) for _ in range(k)]        # +k losers, no v
    return s


def test_shrinkage_tempers_thin_voter():
    lw_thin = VoterWeightLearner(min_samples=20, min_voter_samples=5, shrinkage=10.0).fit(_balanced(6))
    lw_thick = VoterWeightLearner(min_samples=20, min_voter_samples=5, shrinkage=10.0).fit(_balanced(200))
    # Same win rate (1.0) and base rate (0.5); more samples -> closer to full edge.
    assert lw_thick.multiplier("v") > lw_thin.multiplier("v") > 1.0


def test_samples_from_decisions_joins_outcome():
    class D:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    decisions = [
        # decision record: has votes + trade id, pnl None
        D(confluence_votes=_votes("rsi"), direction="LONG", paper_trade_id="T1", pnl_result=None),
        # outcome record for T1: pnl present, no votes
        D(confluence_votes=[], direction="LONG", paper_trade_id="T1", pnl_result=5.0),
        # decision with no matching outcome -> dropped
        D(confluence_votes=_votes("macd"), direction="LONG", paper_trade_id="T2", pnl_result=None),
    ]
    samples = VoterWeightLearner.samples_from_decisions(decisions)
    assert len(samples) == 1
    votes, direction, won = samples[0]
    assert direction == "LONG" and won is True
    assert votes == _votes("rsi")


def test_persistence_round_trip(tmp_path):
    samples = []
    for i in range(40):
        won = i % 2 == 0
        samples.append((_votes("good" if won else "bad"), "LONG", won))
    lw = VoterWeightLearner(min_samples=20, min_voter_samples=5, shrinkage=0.0).fit(samples)
    p = tmp_path / "vw.json"
    lw.save(str(p))
    loaded = VoterWeightLearner.load(str(p))
    assert loaded is not None and loaded.is_ready()
    assert loaded.multiplier("good") == lw.multiplier("good")


def test_load_missing_returns_none(tmp_path):
    assert VoterWeightLearner.load(str(tmp_path / "nope.json")) is None
