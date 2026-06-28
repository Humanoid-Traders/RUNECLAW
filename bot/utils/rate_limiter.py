"""
RUNECLAW Async Rate Limiter -- token-bucket rate limiter for async code.

Used to throttle LLM and exchange API calls without blocking the event loop.
No external dependencies (no tenacity, no sync sleep).

Usage:
    limiter = AsyncRateLimiter(max_rpm=40)
    async with limiter:
        result = await some_api_call()
"""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Token-bucket rate limiter for async contexts.

    Enforces max_rpm (requests per minute) using a sliding window.
    Uses asyncio.sleep — never blocks the event loop.
    """

    def __init__(self, max_rpm: int = 60, name: str = "default") -> None:
        self._max_rpm = max(1, max_rpm)
        self._interval = 60.0 / self._max_rpm  # seconds between calls
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()
        self._name = name
        self._total_calls: int = 0
        self._total_waits: int = 0
        self._total_wait_seconds: float = 0.0

    async def acquire(self) -> None:
        """Wait until a call is permitted under the rate limit."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._interval:
                wait = self._interval - elapsed
                self._total_waits += 1
                self._total_wait_seconds += wait
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
            self._total_calls += 1

    async def __aenter__(self) -> "AsyncRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {
            "name": self._name,
            "max_rpm": self._max_rpm,
            "total_calls": self._total_calls,
            "total_waits": self._total_waits,
            "total_wait_seconds": round(self._total_wait_seconds, 2),
        }
