"""
Leverage drift round (2026-07-13) — from a live portfolio paste:

XPT SHORT ran 20x (margin $7.82) while every other position and every
order card said 10x. The bot never chose 20x: the pending-order card for
the SAME symbol showed "10x leverage" — Bitget applied its own
per-symbol default (20x on never-configured symbols) because:

  1. the bare set_leverage call can fail on isolated margin (holdSide
     required) and the per-side retry was only reachable when the
     fetch_leverage VERIFICATION succeeded — the one case that failed
     together with the set call;
  2. an unverifiable leverage was a debug-level log and the order
     proceeded silently at whatever the exchange had sticky.

Fixes pinned here: immediate holdSide retry on set failure, an audit
WARNING when leverage cannot be verified (once per symbol), and a
post-fill sync so cards show exchange truth without waiting for a
restart.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest


def _executor(tmp_path):
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._hedge_mode = False          # skip hold-mode detection probe
    return ex


def _mock_exchange(set_leverage, fetch_leverage):
    exchange = MagicMock()
    exchange.set_margin_mode = AsyncMock()
    # Margin-mode verification read: benign empty payload
    exchange.privateMixGetV2MixAccountAccount = AsyncMock(
        return_value={"data": {"marginMode": "isolated"}})
    exchange.set_leverage = set_leverage
    exchange.fetch_leverage = fetch_leverage
    return exchange


@pytest.mark.asyncio
async def test_holdside_retry_fires_immediately_on_set_failure(tmp_path):
    """The per-side retry must NOT depend on the verification read
    working — that was exactly the silent-20x shape."""
    ex = _executor(tmp_path)
    set_lev = AsyncMock(side_effect=Exception("40019 holdSide cannot be empty"))
    fetch_lev = AsyncMock(side_effect=Exception("fetch_leverage unavailable"))
    exchange = _mock_exchange(set_lev, fetch_lev)
    ex._get_exchange = AsyncMock(return_value=exchange)

    await ex._ensure_leverage("XPT/USDT:USDT")

    # bare call + explicit long + explicit short
    assert set_lev.await_count == 3
    sides = [c.kwargs.get("params", {}).get("holdSide")
             for c in set_lev.await_args_list]
    assert sides[0] is None and set(sides[1:]) == {"long", "short"}


@pytest.mark.asyncio
async def test_unverifiable_leverage_is_warned_once(tmp_path):
    ex = _executor(tmp_path)
    set_lev = AsyncMock(side_effect=Exception("40019"))
    fetch_lev = AsyncMock(side_effect=Exception("unavailable"))
    exchange = _mock_exchange(set_lev, fetch_lev)
    ex._get_exchange = AsyncMock(return_value=exchange)

    await ex._ensure_leverage("XPT/USDT:USDT")
    assert "XPT/USDT:USDT" in ex._lev_unverified_warned
    # Second call: no growth, no error (warned once per process).
    await ex._ensure_leverage("XPT/USDT:USDT")
    assert len(ex._lev_unverified_warned) == 1


@pytest.mark.asyncio
async def test_verified_match_stays_quiet(tmp_path):
    ex = _executor(tmp_path)
    target = ex._compute_target_leverage("PENGU/USDT:USDT")
    set_lev = AsyncMock()
    fetch_lev = AsyncMock(return_value={"longLeverage": str(target)})
    exchange = _mock_exchange(set_lev, fetch_lev)
    ex._get_exchange = AsyncMock(return_value=exchange)

    await ex._ensure_leverage("PENGU/USDT:USDT")
    assert set_lev.await_count == 1          # no retries needed
    assert ex._lev_unverified_warned == set()


@pytest.mark.asyncio
async def test_verified_mismatch_still_aborts_after_retry(tmp_path):
    """Existing C2-04 abort must survive the refactor: exchange stuck on
    the wrong leverage after the per-side retry -> order aborted."""
    ex = _executor(tmp_path)
    set_lev = AsyncMock()                    # succeeds but doesn't stick
    fetch_lev = AsyncMock(return_value={"longLeverage": "20"})
    exchange = _mock_exchange(set_lev, fetch_lev)
    ex._get_exchange = AsyncMock(return_value=exchange)

    target = ex._compute_target_leverage("XPT/USDT:USDT")
    assert target != 20                      # config default is not 20
    with pytest.raises(RuntimeError, match="Cannot set leverage"):
        await ex._ensure_leverage("XPT/USDT:USDT")


# ── post-fill true-up wiring ─────────────────────────────────────────
def test_limit_fill_paths_sync_leverage_from_exchange():
    from bot.core.live_executor import LiveExecutor
    src = inspect.getsource(LiveExecutor._check_pending_limit)
    assert "sync_positions_from_exchange" in src
    src2 = inspect.getsource(LiveExecutor.execute)
    assert "sync_positions_from_exchange" in src2
