"""
Tier 2a: learning auto-refit cadence.

Counts closed outcomes and refits the three learners every N trades. The refit
calls are stubbed (monkeypatched module functions) so we test the cadence + the
fail-open behaviour without touching the real learners or disk.
"""

from bot.learning.auto_refit import LearningAutoRefit


def _stub_refits(monkeypatch):
    calls = {"cal": 0, "vw": 0, "exp": 0}
    monkeypatch.setattr("bot.learning.confidence_calibration.refit_and_save",
                        lambda *a, **k: calls.__setitem__("cal", calls["cal"] + 1))
    monkeypatch.setattr("bot.learning.voter_weights.refit_and_save",
                        lambda *a, **k: calls.__setitem__("vw", calls["vw"] + 1))
    monkeypatch.setattr("bot.learning.setup_expectancy.get_setup_expectancy",
                        lambda *a, **k: calls.__setitem__("exp", calls["exp"] + 1))
    return calls


def test_refits_every_interval(monkeypatch):
    calls = _stub_refits(monkeypatch)
    r = LearningAutoRefit(interval=5)
    fired = [r.note_closed_trade() for _ in range(12)]
    # Fires on the 5th and 10th close only.
    assert sum(fired) == 2
    assert fired[4] is True and fired[9] is True
    assert calls == {"cal": 2, "vw": 2, "exp": 2}


def test_interval_one_fires_every_time(monkeypatch):
    _stub_refits(monkeypatch)
    r = LearningAutoRefit(interval=1)
    assert all(r.note_closed_trade() for _ in range(3))


def test_below_interval_no_refit(monkeypatch):
    calls = _stub_refits(monkeypatch)
    r = LearningAutoRefit(interval=10)
    for _ in range(9):
        assert r.note_closed_trade() is False
    assert calls == {"cal": 0, "vw": 0, "exp": 0}


def test_refresh_calibrator_called_on_analyzer(monkeypatch):
    _stub_refits(monkeypatch)
    seen = {"refresh": 0}

    class FakeAnalyzer:
        def refresh_calibrator(self):
            seen["refresh"] += 1

    r = LearningAutoRefit(interval=1)
    r.note_closed_trade(FakeAnalyzer())
    assert seen["refresh"] == 1


def test_fail_open_per_learner(monkeypatch):
    # Calibration refit blows up; voter + expectancy must still run.
    calls = {"vw": 0, "exp": 0}
    monkeypatch.setattr("bot.learning.confidence_calibration.refit_and_save",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("bot.learning.voter_weights.refit_and_save",
                        lambda *a, **k: calls.__setitem__("vw", calls["vw"] + 1))
    monkeypatch.setattr("bot.learning.setup_expectancy.get_setup_expectancy",
                        lambda *a, **k: calls.__setitem__("exp", calls["exp"] + 1))
    r = LearningAutoRefit(interval=1)
    assert r.note_closed_trade() is True       # did not raise
    assert calls == {"vw": 1, "exp": 1}


def test_interval_floor(monkeypatch):
    _stub_refits(monkeypatch)
    assert LearningAutoRefit(interval=0).interval == 1
    assert LearningAutoRefit(interval=-3).interval == 1


def test_summary(monkeypatch):
    _stub_refits(monkeypatch)
    r = LearningAutoRefit(interval=2)
    r.note_closed_trade(); r.note_closed_trade()
    s = r.summary()
    assert "auto-refit" in s and "2 closed trades" in s
