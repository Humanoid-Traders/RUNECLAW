"""
Phase A: confidence calibration engine.

Proves the safety-critical properties of bot/learning/confidence_calibration.py:
  * identity below min_samples (and when unfitted) — calibration can only refine
    a confidence once there is enough evidence, never fabricate one;
  * monotonic output (isotonic / PAV) — a higher raw confidence never maps to a
    lower calibrated win rate, so trade ordering is preserved;
  * the curve actually tracks realized win rate when the signal is strong;
  * shrinkage keeps thin bins near identity;
  * round-trip persistence;
  * sample extraction from DecisionMemory-like records.
"""

from types import SimpleNamespace

from bot.learning.confidence_calibration import ConfidenceCalibrator, _pav


def _decision(confidence, tid):
    """A decision-time record: carries confidence + paper_trade_id; trade still open."""
    return SimpleNamespace(confidence=confidence, paper_trade_id=tid, pnl_result=None)


def _outcome(pnl, tid):
    """A realized-outcome record: pnl_result + paper_trade_id; confidence at 0.0 default."""
    return SimpleNamespace(confidence=0.0, paper_trade_id=tid, pnl_result=pnl)


def test_pav_is_monotonic():
    out = _pav([0.9, 0.1, 0.5, 0.2, 0.8], [1, 1, 1, 1, 1])
    assert out == sorted(out), out
    # Mean is preserved by PAV.
    assert abs(sum(out) - (0.9 + 0.1 + 0.5 + 0.2 + 0.8)) < 1e-9


def test_identity_below_min_samples():
    cal = ConfidenceCalibrator(min_samples=30)
    cal.fit([(0.8, True)] * 10)            # only 10 < 30
    assert cal.is_ready() is False
    for x in (0.0, 0.3, 0.55, 0.85, 1.0):
        assert cal.calibrate(x) == x        # exact identity


def test_unfitted_is_identity():
    cal = ConfidenceCalibrator()
    assert cal.is_ready() is False
    assert cal.calibrate(0.73) == 0.73


def _synthetic(n_per_bin=40):
    """Build samples where TRUE win rate rises with confidence: at confidence c,
    win with probability ~c. Deterministic (no RNG): first round(c*k) of k win."""
    samples = []
    for b in range(10):
        c = (b + 0.5) / 10.0
        wins = round(c * n_per_bin)
        for i in range(n_per_bin):
            samples.append((c, i < wins))
    return samples


def test_curve_is_monotonic_and_tracks_winrate():
    cal = ConfidenceCalibrator(min_samples=30).fit(_synthetic())
    assert cal.is_ready() is True
    # Monotonic non-decreasing across the domain.
    xs = [i / 100 for i in range(101)]
    ys = [cal.calibrate(x) for x in xs]
    assert all(b >= a - 1e-9 for a, b in zip(ys, ys[1:])), "calibration must be monotonic"
    # At a high-confidence point the calibrated win rate should be high; at a
    # low-confidence point, low. (Win prob ~ confidence by construction.)
    assert cal.calibrate(0.85) > 0.6
    assert cal.calibrate(0.15) < 0.4
    assert 0.0 <= min(ys) and max(ys) <= 1.0


def test_overconfident_model_is_pulled_down():
    # Model says 0.9 but those trades only win ~50% of the time.
    samples = [(0.9, i < 20) for i in range(40)]          # 50% win at conf 0.9
    samples += [(0.5, i < 20) for i in range(40)]          # 50% win at conf 0.5
    cal = ConfidenceCalibrator(min_samples=30, shrinkage=0.0).fit(samples)
    assert cal.calibrate(0.9) < 0.9        # over-confidence corrected downward


def test_shrinkage_keeps_thin_bins_near_identity():
    # One bin, very few samples, all wins. With strong shrinkage the calibrated
    # value stays closer to the raw confidence than to the raw 100% empirical.
    samples = [(0.85, True)] * 3 + [(0.15, False)] * 40
    strong = ConfidenceCalibrator(min_samples=5, shrinkage=20.0).fit(samples)
    weak = ConfidenceCalibrator(min_samples=5, shrinkage=0.0).fit(samples)
    # The thin 0.85 bin: strong shrinkage pulls it down toward ~0.85, weak leaves
    # it near 1.0. So strong <= weak for that bin.
    assert strong.calibrate(0.85) <= weak.calibrate(0.85)


def test_persistence_round_trip(tmp_path):
    cal = ConfidenceCalibrator(min_samples=30).fit(_synthetic())
    p = tmp_path / "cal.json"
    cal.save(str(p))
    loaded = ConfidenceCalibrator.load(str(p))
    assert loaded is not None
    assert loaded.is_ready()
    for x in (0.2, 0.5, 0.85):
        assert abs(loaded.calibrate(x) - cal.calibrate(x)) < 1e-9


def test_load_missing_returns_none(tmp_path):
    assert ConfidenceCalibrator.load(str(tmp_path / "nope.json")) is None


def test_samples_join_confidence_to_outcome_by_trade_id():
    # Confidence lives on the decision record; the realized pnl lives on a SEPARATE
    # outcome record. samples_from_decisions must JOIN them by paper_trade_id.
    decisions = [
        _decision(0.8, "t1"), _outcome(5.0, "t1"),    # win
        _decision(0.6, "t2"), _outcome(-2.0, "t2"),   # loss
    ]
    samples = ConfidenceCalibrator.samples_from_decisions(decisions)
    assert sorted(samples) == [(0.6, False), (0.8, True)]


def test_zero_confidence_outcome_rows_are_not_samples():
    # The bug this fixes: outcome rows carry confidence=0.0; they must NOT become
    # (0.0, won) samples (which made the calibrator train entirely on 0.0).
    decisions = [_decision(0.9, "t1"), _outcome(3.0, "t1")]
    samples = ConfidenceCalibrator.samples_from_decisions(decisions)
    assert samples == [(0.9, True)]
    assert all(c > 0.0 for c, _ in samples)


def test_decision_without_outcome_is_excluded():
    # An open trade (no matching outcome record yet) yields no sample.
    assert ConfidenceCalibrator.samples_from_decisions([_decision(0.7, "open")]) == []


def test_curve_is_non_degenerate_after_join():
    # Regression for the "calibrator trains on confidence=0.0" bug: with the join,
    # low-confidence losers and high-confidence winners fit a curve that actually
    # DISCRIMINATES. Under the old single-record read this was impossible — every
    # sample was (0.0, won), so the curve was one flat bin.
    decisions = []
    for i in range(20):                      # 0.55 conf, mostly LOSE (4/20 win)
        tid = f"lo{i}"
        decisions += [_decision(0.55, tid), _outcome(1.0 if i < 4 else -1.0, tid)]
    for i in range(20):                      # 0.90 conf, mostly WIN (16/20 win)
        tid = f"hi{i}"
        decisions += [_decision(0.90, tid), _outcome(1.0 if i < 16 else -1.0, tid)]
    samples = ConfidenceCalibrator.samples_from_decisions(decisions)
    assert len(samples) == 40
    assert all(c in (0.55, 0.90) for c, _ in samples)   # never trained on 0.0
    cal = ConfidenceCalibrator(min_samples=10).fit(samples)
    assert cal.is_ready()
    assert cal.calibrate(0.90) > cal.calibrate(0.55)    # the curve discriminates
