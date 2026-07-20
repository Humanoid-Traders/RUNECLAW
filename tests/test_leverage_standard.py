"""Leverage standardization (operator directive 2026-07-20).

Live incident: a BCH position ran 20x while every other position and the
config said 5x — Bitget's sticky per-symbol default survived a failed
set+verify, and the order proceeded on a warning. The new contract:

1. One STANDARD leverage everywhere: dynamic vol scaling is opt-in
   (default OFF), so every new position targets exactly the standard.
2. The standard is runtime-adjustable via RUNTIME.leverage_override
   (/leverage set), hard-clamped 1..20.
3. Fail CLOSED: if the exchange will not confirm the target leverage,
   the order ABORTS instead of trading at the exchange's sticky default.
   LEVERAGE_FAIL_OPEN=1 is the explicit escape hatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import CONFIG, RUNTIME


@pytest.fixture(autouse=True)
def _clean_override():
    RUNTIME.leverage_override = None
    yield
    RUNTIME.leverage_override = None


def _executor(tmp_path):
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._hedge_mode = False
    return ex


def _mock_exchange(set_leverage, fetch_leverage):
    exchange = MagicMock()
    exchange.set_margin_mode = AsyncMock()
    exchange.privateMixGetV2MixAccountAccount = AsyncMock(
        return_value={"data": {"marginMode": "isolated"}})
    exchange.set_leverage = set_leverage
    exchange.fetch_leverage = fetch_leverage
    return exchange


# ── 1. uniform standard ──────────────────────────────────────────────────────

def test_dynamic_scaling_is_opt_in_default_off():
    assert CONFIG.exchange.dynamic_leverage_enabled is False, (
        "uniform standard leverage everywhere — vol scaling is an explicit "
        "DYNAMIC_LEVERAGE_ENABLED=1 opt-in")


def test_target_is_exactly_the_standard_when_scaling_off(tmp_path):
    ex = _executor(tmp_path)
    for sym in ("BCH/USDT:USDT", "BTC/USDT:USDT", "RKLB/USDT:USDT"):
        assert ex._compute_target_leverage(sym) == CONFIG.exchange.default_leverage


# ── 2. runtime override ──────────────────────────────────────────────────────

def test_runtime_override_feeds_the_target_and_is_clamped(tmp_path):
    ex = _executor(tmp_path)
    RUNTIME.leverage_override = 3
    assert ex._compute_target_leverage("BTC/USDT:USDT") == 3
    RUNTIME.leverage_override = 100          # clamped to the 20x backstop
    assert RUNTIME.leverage_override == 20
    RUNTIME.leverage_override = 0            # clamped to 1x floor
    assert RUNTIME.leverage_override == 1
    RUNTIME.leverage_override = None
    assert ex._compute_target_leverage("BTC/USDT:USDT") == CONFIG.exchange.default_leverage


# ── 3. fail-closed on unconfirmed leverage ───────────────────────────────────

@pytest.mark.asyncio
async def test_unverified_leverage_aborts_the_order(tmp_path, monkeypatch):
    monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
    ex = _executor(tmp_path)
    set_lev = AsyncMock(side_effect=Exception("40019 holdSide cannot be empty"))
    fetch_lev = AsyncMock(side_effect=Exception("fetch_leverage unavailable"))
    ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
    with pytest.raises(RuntimeError, match="Cannot confirm"):
        await ex._ensure_leverage("BCH/USDT:USDT")


@pytest.mark.asyncio
async def test_fail_open_env_restores_proceed_with_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("LEVERAGE_FAIL_OPEN", "1")
    ex = _executor(tmp_path)
    set_lev = AsyncMock(side_effect=Exception("40019 holdSide cannot be empty"))
    fetch_lev = AsyncMock(side_effect=Exception("fetch_leverage unavailable"))
    ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
    await ex._ensure_leverage("BCH/USDT:USDT")   # no raise
    assert "BCH/USDT:USDT" in ex._lev_unverified_warned


@pytest.mark.asyncio
async def test_verified_target_still_opens_normally(tmp_path, monkeypatch):
    monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
    ex = _executor(tmp_path)
    target = CONFIG.exchange.default_leverage
    set_lev = AsyncMock()
    fetch_lev = AsyncMock(return_value={"longLeverage": target})
    ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
    await ex._ensure_leverage("BTC/USDT:USDT")   # no raise, no warning
    assert ex._lev_unverified_warned == set()


# ── command registration ─────────────────────────────────────────────────────

def test_leverage_command_registered():
    import inspect
    from bot.skills import telegram_handler as th
    src = inspect.getsource(th)
    assert '("leverage", self._cmd_leverage)' in src
    assert "LEVERAGE_OVERRIDE_MAX = 20" in inspect.getsource(
        __import__("bot.config", fromlist=["RuntimeState"]).RuntimeState)
