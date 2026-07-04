"""The wide-universe scan analyzes signals with BOUNDED concurrency.

The scanner now emits ~200 volume-filtered symbols per cycle. Analysing them
with an unbounded ``asyncio.gather`` would fan out hundreds of simultaneous
OHLCV/order-flow/MTF fetches and overwhelm the exchange rate limiter.
``RuneClawEngine._analyze_signals_batched`` caps in-flight analyses at
``CONFIG.scan_analysis_concurrency`` while preserving order and isolating
per-signal failures.
"""
import asyncio
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine


def _set_concurrency(value: int):
    old = CONFIG.scan_analysis_concurrency
    object.__setattr__(CONFIG, "scan_analysis_concurrency", value)
    return old


@pytest.mark.asyncio
async def test_concurrency_is_bounded_by_config():
    limit = 5
    old = _set_concurrency(limit)
    try:
        state = {"cur": 0, "peak": 0}

        async def fake_analyze(sig, *, timeframe="1h", **kw):
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            await asyncio.sleep(0.01)  # hold the slot so overlap is observable
            state["cur"] -= 1
            return f"idea:{sig}"

        stub = SimpleNamespace(_analyze_signal=fake_analyze)
        signals = [f"S{i}" for i in range(40)]
        out = await RuneClawEngine._analyze_signals_batched(stub, signals)

        assert len(out) == 40
        assert state["peak"] <= limit, state["peak"]
        # Order preserved.
        assert out == [f"idea:S{i}" for i in range(40)]
    finally:
        object.__setattr__(CONFIG, "scan_analysis_concurrency", old)


@pytest.mark.asyncio
async def test_one_failure_does_not_sink_the_batch():
    old = _set_concurrency(8)
    try:
        async def fake_analyze(sig, *, timeframe="1h", **kw):
            if sig == "BOOM":
                raise RuntimeError("analysis blew up")
            return f"idea:{sig}"

        stub = SimpleNamespace(_analyze_signal=fake_analyze)
        signals = ["A", "BOOM", "B"]
        out = await RuneClawEngine._analyze_signals_batched(stub, signals)

        assert out == ["idea:A", None, "idea:B"]
    finally:
        object.__setattr__(CONFIG, "scan_analysis_concurrency", old)


@pytest.mark.asyncio
async def test_empty_input_short_circuits():
    stub = SimpleNamespace(_analyze_signal=None)
    assert await RuneClawEngine._analyze_signals_batched(stub, []) == []


@pytest.mark.asyncio
async def test_timeframe_is_threaded_through():
    old = _set_concurrency(4)
    try:
        seen = []

        async def fake_analyze(sig, *, timeframe="1h", **kw):
            seen.append(timeframe)
            return sig

        stub = SimpleNamespace(_analyze_signal=fake_analyze)
        await RuneClawEngine._analyze_signals_batched(stub, ["X"], timeframe="4h")
        assert seen == ["4h"]
    finally:
        object.__setattr__(CONFIG, "scan_analysis_concurrency", old)
