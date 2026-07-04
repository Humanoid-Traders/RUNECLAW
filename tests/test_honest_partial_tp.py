"""The honest benchmark must measure the exit strategy LIVE actually runs.

Live uses the partial-TP ladder (`PARTIAL_TP_ENABLED` default True); the backtest
gates it behind `BACKTEST_PARTIAL_TP`, default False for single-exit
reproducibility. A single-exit benchmark badly misreads live (−0.72% / PF 0.72 vs
the real +0.31% / PF 1.14 on majors_1h), so `--honest` enables the ladder —
respecting an explicit operator override so single-exit A/Bs stay reproducible.
"""
import inspect

from bot.backtest import runner


def test_honest_enables_partial_tp_ladder():
    # Moved into _apply_honest_fidelity (tests/test_honest_fidelity.py covers
    # the full behavior, incl. every _run_* entry point calling it); this
    # source check stays as a locator for the specific env var.
    src = inspect.getsource(runner._apply_honest_fidelity)
    honest_ix = src.index("honest")
    assert "BACKTEST_PARTIAL_TP" in src[honest_ix:]
    # setdefault (not hard-set) so `BACKTEST_PARTIAL_TP=0` still yields a
    # single-exit run for controlled comparisons.
    assert "os.environ.setdefault(\"BACKTEST_PARTIAL_TP\"" in src


def test_backtest_engine_default_is_still_single_exit():
    # Non-honest backtests remain byte-identical single-exit (reproducibility) —
    # only --honest opts into the live ladder.
    src = inspect.getsource(runner)
    # The engine's own default must stay False; --honest is the only opt-in.
    from bot.backtest import engine
    esrc = inspect.getsource(engine)
    assert '_env_bool("BACKTEST_PARTIAL_TP", False)' in esrc
