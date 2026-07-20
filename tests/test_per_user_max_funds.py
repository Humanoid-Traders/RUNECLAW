"""LIVE-1 — per-linked-account max-funds protection for live testing.

A linked (per-user) account may never deploy more total margin than
PER_USER_MAX_FUNDS_USD, regardless of its balance. The operator executor
is governed by the MICRO_* caps, not this."""

import pytest

from bot.core.live_executor import LiveExecutor


def _ex(tmp_path, user_id=None):
    ex = LiveExecutor(user_id=user_id, state_dir=str(tmp_path))
    ex._hedge_mode = False
    return ex


def test_linked_account_cap_blocks_oversized_deploy(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "50")
    ex = _ex(tmp_path, user_id="12345")
    err = ex._preflight_check(60.0, symbol="BTC/USDT:USDT")
    assert err and "max-funds cap" in err and "50" in err


def test_linked_account_cap_counts_open_positions(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "50")
    ex = _ex(tmp_path, user_id="12345")
    from types import SimpleNamespace
    ex._positions = {"t1": SimpleNamespace(cost_usd=40.0, status="open")}
    assert ex._preflight_check(15.0) is not None, "40 open + 15 new > 50 cap"
    assert ex._preflight_check(5.0) is None, "40 + 5 fits under the cap"


def test_operator_executor_not_governed_by_per_user_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "1")
    ex = _ex(tmp_path, user_id=None)
    assert ex._preflight_check(50.0) is None, (
        "operator account uses MICRO_* caps, not the linked-account cap")


def test_cap_zero_disables_and_default_is_100(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "0")
    ex = _ex(tmp_path, user_id="7")
    assert ex._preflight_check(90.0) is None, "0 disables the extra cap"
    monkeypatch.delenv("PER_USER_MAX_FUNDS_USD", raising=False)
    ex2 = _ex(tmp_path, user_id="7")
    assert ex2._preflight_check(150.0) is not None, "default cap 100 blocks 150"
