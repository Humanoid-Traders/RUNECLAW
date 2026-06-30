"""
Activation flag comparison harness (scripts/flag_compare.py).

Pins that the legacy-vs-new comparison tooling runs end-to-end and toggles the
full activation flag set both ways. The harness mutates process env + the frozen
CONFIG singletons, so every test here snapshots and restores that global state to
avoid polluting later tests (which assert the flags default ON).
"""

import importlib
import os

import pytest

from bot.config import CONFIG

fc = importlib.import_module("scripts.flag_compare")


@pytest.fixture(autouse=True)
def _restore_flag_state():
    """Snapshot env + CONFIG attrs the harness mutates, restore them afterwards."""
    env_snap = {k: os.environ.get(k) for k in fc.ENV_FLAGS}
    cfg_snap = {(s, n): getattr(getattr(CONFIG, s), n) for s, n in fc.CFG_FLAGS}
    try:
        yield
    finally:
        for k, v in env_snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for (s, n), v in cfg_snap.items():
            object.__setattr__(getattr(CONFIG, s), n, v)


class TestApplyFlags:
    def test_off_then_on(self):
        fc.apply_flags(False)
        for k in fc.ENV_FLAGS:
            assert os.environ[k] == "0"
        for s, n in fc.CFG_FLAGS:
            assert getattr(getattr(CONFIG, s), n) is False

        fc.apply_flags(True)
        for k in fc.ENV_FLAGS:
            assert os.environ[k] == "1"
        for s, n in fc.CFG_FLAGS:
            assert getattr(getattr(CONFIG, s), n) is True


class TestCompareBacktest:
    async def test_runs_and_reports_both_configs(self):
        report = await fc.compare_backtest(seeds=[1], bars_n=300)
        assert report["mode"] == "backtest"
        assert report["seeds"] == [1]
        assert len(report["per_seed"]) == 1
        # Every catalogued metric is aggregated with the legacy/new/delta shape.
        for m in fc.METRICS:
            agg = report["aggregate"][m]
            assert {"legacy_mean", "new_mean", "delta", "new_ge_legacy", "n"} <= agg.keys()
            assert agg["n"] == 1
        # Same bars + same trade-gating → identical synthetic data drives both runs;
        # the comparison is meaningful (both legs actually ran).
        row = report["per_seed"][0]
        assert "legacy_notional" in row and "new_notional" in row


class TestCompareWalkForward:
    async def test_oos_comparison_shape(self):
        report = await fc.compare_walk_forward(seeds=[1], bars_n=600, n_folds=3)
        assert report["mode"] == "walk_forward"
        assert report["n_folds"] == 3
        for k in ("mean_oos_return", "pct_profitable_folds", "worst_oos_return"):
            assert k in report["aggregate"]
            assert {"legacy_mean", "new_mean", "delta"} <= report["aggregate"][k].keys()
