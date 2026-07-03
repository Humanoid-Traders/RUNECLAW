"""Two overlapping auto-confirm cycles for the SAME symbol must place ONE order.

Reported: a GRASS LONG setup was auto-confirmed twice and placed as two limit
orders (doubled exposure). The duplicate guard runs at analysis time, so two
concurrent cycles both clear it before either order lands (TOCTOU). confirm_trade
now serializes per symbol and re-checks live open/pending orders under the lock.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot.core.engine import RuneClawEngine


class _FakeExec:
    def __init__(self):
        self._pos = []

    @property
    def open_positions(self):
        return list(self._pos)


class _FakeEngine:
    confirm_trade = RuneClawEngine.confirm_trade  # exercise the real wrapper

    def __init__(self, ideas):
        self._pending_ideas = ideas
        self._pending_pyramid = {}
        self._pending_atr = {}
        self._symbol_entry_locks = {}
        self.live_executor = _FakeExec()
        self.inner_calls = []

    async def _confirm_trade_inner(self, trade_id, user_id=""):
        idea = self._pending_ideas.get(trade_id)
        self.inner_calls.append(trade_id)
        await asyncio.sleep(0)  # yield so a racing caller can interleave
        # Simulate the placed order landing as a pending_fill position.
        self.live_executor._pos.append(
            SimpleNamespace(symbol=idea.asset, status="pending_fill"))
        return f"LIMIT ORDER placed {idea.asset}"


def _idea(asset="GRASS/USDT"):
    return SimpleNamespace(asset=asset,
                           direction=SimpleNamespace(value="LONG"))


@pytest.fixture
def _live(monkeypatch):
    from bot.core import engine as eng_mod
    monkeypatch.setattr(eng_mod, "CONFIG", SimpleNamespace(is_live=lambda: True))


def test_concurrent_same_symbol_places_one(_live):
    eng = _FakeEngine({"A": _idea(), "B": _idea()})

    async def run():
        return await asyncio.gather(eng.confirm_trade("A"),
                                    eng.confirm_trade("B"))

    results = asyncio.get_event_loop().run_until_complete(run())
    # Exactly one order placed; the other suppressed as a duplicate.
    assert len(eng.inner_calls) == 1
    assert sum("duplicate suppressed" in r for r in results) == 1
    assert len(eng.live_executor._pos) == 1


def test_different_symbols_both_place(_live):
    eng = _FakeEngine({"A": _idea("GRASS/USDT"), "B": _idea("ONDO/USDT")})

    async def run():
        return await asyncio.gather(eng.confirm_trade("A"),
                                    eng.confirm_trade("B"))

    asyncio.get_event_loop().run_until_complete(run())
    assert len(eng.inner_calls) == 2  # distinct symbols never collide


def test_flagged_pyramid_add_is_allowed(_live):
    eng = _FakeEngine({"A": _idea(), "B": _idea()})
    eng._pending_pyramid["B"] = True  # deliberate pyramid add
    asyncio.get_event_loop().run_until_complete(eng.confirm_trade("A"))
    asyncio.get_event_loop().run_until_complete(eng.confirm_trade("B"))
    # A places; B is a flagged pyramid so it is NOT suppressed by the guard.
    assert eng.inner_calls == ["A", "B"]
