"""Balance-mismatch fix: get_effective_equity_async must honor the balance
cache TTL. The old `if not cache: refresh` short-circuit returned a populated-
but-EXPIRED cache forever, so the /start status card could show an equity tens
of dollars away from the fresh /portfolio fetch taken seconds later."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from bot.core.engine import RuneClawEngine


def _engine(cached_total: float, cache_age_s: float, fresh_total: float,
            fetch_fails: bool = False) -> RuneClawEngine:
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._live_balance_cache = {"total": cached_total}
    eng._live_balance_cache_ts = time.monotonic() - cache_age_s
    eng._LIVE_BALANCE_TTL = 30.0
    if fetch_fails:
        eng.live_executor = type("X", (), {})()
        eng.live_executor.fetch_balance = AsyncMock(side_effect=RuntimeError("api down"))
    else:
        eng.live_executor = type("X", (), {})()
        eng.live_executor.fetch_balance = AsyncMock(return_value={"total": fresh_total})
    eng.user_portfolios = type("P", (), {"get": lambda self, uid: None})()
    return eng


@pytest.mark.asyncio
async def test_expired_cache_is_refreshed():
    eng = _engine(cached_total=304.55, cache_age_s=600.0, fresh_total=325.84)
    with patch("bot.core.engine.CONFIG") as cfg:
        cfg.is_live.return_value = True
        got = await eng.get_effective_equity_async()
    assert got == 325.84  # fresh fetch, not the 10-minute-old cache


@pytest.mark.asyncio
async def test_fresh_cache_is_served_without_refetch():
    eng = _engine(cached_total=304.55, cache_age_s=5.0, fresh_total=325.84)
    with patch("bot.core.engine.CONFIG") as cfg:
        cfg.is_live.return_value = True
        got = await eng.get_effective_equity_async()
    assert got == 304.55  # within TTL — cache is authoritative
    eng.live_executor.fetch_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_failure_falls_back_to_stale_cache():
    eng = _engine(cached_total=304.55, cache_age_s=600.0, fresh_total=0.0,
                  fetch_fails=True)
    with patch("bot.core.engine.CONFIG") as cfg:
        cfg.is_live.return_value = True
        got = await eng.get_effective_equity_async()
    assert got == 304.55  # fail-open: stale beats nothing
