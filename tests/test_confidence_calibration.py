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

from bot.learning.confidence_calibration import ConfidenceCalibrator, _pav


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


def test_samples_from_decisions():
    class D:
        def __init__(self, confidence, pnl_result):
            self.confidence = confidence
            self.pnl_result = pnl_result
    decisions = [D(0.8, 5.0), D(0.6, -2.0), D(0.7, None), D(None, 1.0)]
    samples = ConfidenceCalibrator.samples_from_decisions(decisions)
    # Only the two completed trades with a confidence are extracted.
    assert samples == [(0.8, True), (0.6, False)]
