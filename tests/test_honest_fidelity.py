"""--honest must measure the SAME time-stop and fee model live actually runs.

Live runs the time-stop (TIME_STOP_ENABLED default True) and models the taker
fee at CONFIG.risk.taker_fee_pct (0.06%), but the backtest defaulted the
time-stop off (BACKTEST_TIME_STOP) and charged a stale 0.1% commission. A 4-arm
A/B on the frozen benchmark showed both fixes independently improve the result
and stack cleanly (+0.31% -> +0.49% OOS, PF 1.14 -> 1.24), so --honest now
aligns both to live — respecting explicit operator overrides for either.

`_apply_honest_fidelity` must be called by every `_run_*` entry point, not just
`main()` — a caller that invokes `_run_backtest`/`_run_portfolio`/
`_run_walk_forward` directly (as tests do) bypassing `main()` previously hit a
pydantic ValidationError because `args.commission` stayed the unresolved `None`
sentinel (a real regression this suite caught in CI).
"""
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.backtest import runner


def _args(honest=False, commission=None):
    return SimpleNamespace(honest=honest, commission=commission,
                           strict_data=False, fill_mode="close")


def test_commission_default_is_a_sentinel_resolved_per_mode():
    ns = runner.build_parser().parse_args(["--dataset", "x"])
    assert ns.commission is None  # unresolved until _apply_honest_fidelity runs


def test_explicit_commission_overrides_honest_default():
    ns = runner.build_parser().parse_args(["--dataset", "x", "--honest", "--commission", "0.25"])
    runner._apply_honest_fidelity(ns)
    assert ns.commission == 0.25


def test_non_honest_resolves_to_point_one_percent():
    args = _args(honest=False)
    runner._apply_honest_fidelity(args)
    assert args.commission == 0.1
    assert args.fill_mode == "close"  # untouched


def test_honest_resolves_commission_to_live_taker_fee():
    from bot.config import CONFIG
    args = _args(honest=True)
    runner._apply_honest_fidelity(args)
    assert args.commission == CONFIG.risk.taker_fee_pct
    assert args.strict_data is True
    assert args.fill_mode == "next_open"


def test_honest_enables_partial_tp_and_time_stop_via_env(monkeypatch):
    monkeypatch.delenv("BACKTEST_PARTIAL_TP", raising=False)
    monkeypatch.delenv("BACKTEST_TIME_STOP", raising=False)
    runner._apply_honest_fidelity(_args(honest=True))
    import os
    assert os.environ["BACKTEST_PARTIAL_TP"] == "1"
    assert os.environ["BACKTEST_TIME_STOP"] == "1"


def test_honest_respects_explicit_env_override(monkeypatch):
    monkeypatch.setenv("BACKTEST_TIME_STOP", "0")
    runner._apply_honest_fidelity(_args(honest=True))
    import os
    assert os.environ["BACKTEST_TIME_STOP"] == "0"  # setdefault does not clobber


def test_idempotent_double_call_is_a_no_op():
    args = _args(honest=True)
    runner._apply_honest_fidelity(args)
    first = args.commission
    runner._apply_honest_fidelity(args)
    assert args.commission == first


@pytest.mark.parametrize("fn_name", ["_run_backtest", "_run_portfolio", "_run_walk_forward"])
def test_every_entry_point_calls_the_resolver(fn_name):
    # Regression guard: each _run_* function must call _apply_honest_fidelity
    # itself, since tests (and any future embedding) invoke them directly
    # without going through main() first.
    src = inspect.getsource(getattr(runner, fn_name))
    assert "_apply_honest_fidelity(args)" in src


def test_run_backtest_direct_call_does_not_raise_on_unresolved_commission():
    # The exact regression: calling _run_backtest directly with a freshly
    # parsed (unresolved-commission) Namespace must not raise a pydantic
    # ValidationError before ever reaching the data-loading code.
    args = runner.build_parser().parse_args(["--synthetic", "--bars", "50"])
    assert args.commission is None
    with patch.object(runner, "_load_bars", side_effect=RuntimeError("stop-here")):
        import asyncio
        with pytest.raises(RuntimeError, match="stop-here"):
            asyncio.run(runner._run_backtest(args))
    assert args.commission == 0.1  # resolved as a side effect, no ValidationError
