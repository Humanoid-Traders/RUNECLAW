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
async def test_unverified_leverage_proceeds_by_default(tmp_path, monkeypatch):
    # Operator directive 2026-07-21 ("I can't open trades"): default fail-OPEN
    # so trades open; the STOP-LOSS is the risk backstop. Even when set fails
    # AND the read-back fails, the order PROCEEDS with a warning (not aborts).
    monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
    monkeypatch.delenv("LEVERAGE_FAIL_CLOSED", raising=False)
    ex = _executor(tmp_path)
    set_lev = AsyncMock(side_effect=Exception("40019 holdSide cannot be empty"))
    fetch_lev = AsyncMock(side_effect=Exception("fetch_leverage unavailable"))
    ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
    await ex._ensure_leverage("BCH/USDT:USDT")   # no raise — proceeds
    assert "BCH/USDT:USDT" in ex._lev_unverified_warned


@pytest.mark.asyncio
async def test_strict_mode_still_aborts_unverified_leverage(tmp_path, monkeypatch):
    # The strict standard is opt-in: LEVERAGE_FAIL_CLOSED=1 restores the abort.
    monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
    monkeypatch.setenv("LEVERAGE_FAIL_CLOSED", "1")
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


@pytest.mark.asyncio
async def test_successful_set_opens_even_when_readback_unparseable(tmp_path, monkeypatch):
    """Live regression (2026-07-21, BTC/ETHFI "trades can not open").

    A brand-new position has nothing to read leverage back FROM, and some
    Bitget symbols return a fetch_leverage shape the parser can't decode — so
    the fail-closed guard was aborting EVERY trade on those symbols even though
    set_leverage had SUCCEEDED. A successful set is authoritative confirmation:
    the order proceeds (with an audit warning), it does NOT abort.
    """
    monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
    ex = _executor(tmp_path)
    set_lev = AsyncMock()                                    # exchange ACCEPTS the target
    fetch_lev = AsyncMock(return_value={"longLeverage": None,  # …but won't echo it back
                                        "leverage": None, "info": {}})
    ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
    await ex._ensure_leverage("BTC/USDT:USDT")              # MUST NOT raise
    set_lev.assert_awaited()                                 # the set was actually attempted
    assert "BTC/USDT:USDT" in ex._lev_unverified_warned      # surfaced, not silent


@pytest.mark.asyncio
async def test_failed_set_and_unparseable_readback_aborts_in_strict_mode(tmp_path, monkeypatch):
    """In opt-in strict mode, the genuine 20x danger still fails closed: set
    fails AND read-back can't confirm → abort rather than trade at the sticky
    default. (The default posture is fail-open — covered above.)"""
    monkeypatch.delenv("LEVERAGE_FAIL_OPEN", raising=False)
    monkeypatch.setenv("LEVERAGE_FAIL_CLOSED", "1")
    ex = _executor(tmp_path)
    set_lev = AsyncMock(side_effect=Exception("40019 holdSide cannot be empty"))
    fetch_lev = AsyncMock(return_value={"leverage": None, "info": {}})
    ex._get_exchange = AsyncMock(return_value=_mock_exchange(set_lev, fetch_lev))
    with pytest.raises(RuntimeError, match="Cannot confirm"):
        await ex._ensure_leverage("BCH/USDT:USDT")


# ── command registration ─────────────────────────────────────────────────────

def test_leverage_command_registered():
    import inspect
    from bot.skills import telegram_handler as th
    src = inspect.getsource(th)
    assert '("leverage", self._cmd_leverage)' in src
    assert "LEVERAGE_OVERRIDE_MAX = 20" in inspect.getsource(
        __import__("bot.config", fromlist=["RuntimeState"]).RuntimeState)
