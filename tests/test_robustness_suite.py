"""Parsers for scripts/robustness_suite.py — pinned against real runner
output shapes so report extraction can't silently drift."""

import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "robustness_suite",
    os.path.join(os.path.dirname(__file__), "..", "scripts",
                 "robustness_suite.py"))
suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suite)


FULL_RUN = """
  Portfolio backtest: 10 symbols, fetching 6000 bars each...
  fetched BTC/USDT:USDT: 5994 REAL bars
  SOL/USDT:USDT: fetch failed (bitget GET ...) — skipped
  Total Return:           +0.07%
  Total Trades:     24
  Winners:          15  (62%)
  Max Drawdown:     2.52%  ($258.34)
  Profit Factor:    1.02
"""

WF_RUN = """
  PORTFOLIO 6-fold walk-forward (10 symbols, fill=next_open):
  fold 0:  58 trades  ret  -3.21%  win 62%  maxDD  3.83%  PF 0.59
  fold 1:  16 trades  ret  -0.70%  win 56%  maxDD  1.98%  PF 0.65
  => profitable folds 0/6 | mean OOS ret -1.14% | worst -3.21%
"""


class TestParseFullRun:
    def test_extracts_metrics(self):
        r = suite.parse_full_run(FULL_RUN)
        assert r["ok"]
        assert r["return_pct"] == 0.07
        assert r["trades"] == 24
        assert r["win_pct"] == 62.0
        assert r["profit_factor"] == 1.02
        assert r["max_dd_pct"] == 2.52
        assert r["skipped_symbols"] == ["SOL/USDT:USDT"]

    def test_crash_output_flags_not_ok(self):
        r = suite.parse_full_run("Traceback (most recent call last):\nboom")
        assert not r["ok"]


class TestParseWalkForward:
    def test_extracts_folds_and_summary(self):
        r = suite.parse_walk_forward(WF_RUN)
        assert r["ok"] and len(r["folds"]) == 2
        assert r["folds"][0] == {"fold": 0, "trades": 58, "return_pct": -3.21,
                                 "win_pct": 62.0, "max_dd_pct": 3.83,
                                 "profit_factor": 0.59}
        assert r["profitable_folds"] == 0 and r["total_folds"] == 6
        assert r["mean_oos_pct"] == -1.14
        assert r["worst_fold_pct"] == -3.21


class TestArmsMatrix:
    def test_dark_flags_have_revalidation_arms(self):
        # Every measured-dark default must stay re-measurable by name.
        assert "arm_floor" in suite.ARMS
        assert "arm_trail" in suite.ARMS
        assert "arm_voters" in suite.ARMS

    def test_all_arms_use_honest_universe_symbols(self):
        for name, (symbols, _extra, _env, _purpose) in suite.ARMS.items():
            assert symbols in (suite.STD_SYMBOLS, suite.HOLDOUT_SYMBOLS), name
