"""The multi-stage trailing ATR multipliers are env-overridable, defaulting to
the original table so baseline behaviour is byte-identical.

The default table tightens the trail as profit grows (2.0 -> 1.5 -> 1.0 xATR),
which can choke a runner at stage 3; the knobs let the late-stage distance be
widened for A/B tuning without touching the default.
"""
import bot.utils.trailing as trailing

from tests._env_subprocess import run_py


def test_stage_mult_defaults():
    assert trailing._stage_mult(1, 2.0) == 2.0
    assert trailing._stage_mult(2, 1.5) == 1.5
    assert trailing._stage_mult(3, 1.0) == 1.0


def test_default_stage_table_unchanged():
    # Baseline must reproduce the historical multipliers exactly.
    assert trailing._STAGES[1]["atr_mult"] == 2.0
    assert trailing._STAGES[2]["atr_mult"] == 1.5
    assert trailing._STAGES[3]["atr_mult"] == 1.0


def test_env_override_widens_late_stage(monkeypatch):
    monkeypatch.setenv("TRAIL_STAGE3_ATR_MULT", "2.5")
    assert trailing._stage_mult(3, 1.0) == 2.5
    # And a fresh import builds the table with the override. Checked in a
    # subprocess — reloading trailing in-process replaces its function objects
    # and breaks later tests that imported them at collection time.
    out = run_py(
        "import bot.utils.trailing as t\n"
        "print(t._STAGES[3]['atr_mult'])",
        env_overrides={"TRAIL_STAGE3_ATR_MULT": "2.5"})
    assert out == "2.5"


def test_bad_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TRAIL_STAGE3_ATR_MULT", "not-a-number")
    assert trailing._stage_mult(3, 1.0) == 1.0
