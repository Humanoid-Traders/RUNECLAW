"""
RUNECLAW Performance Tracker -- pushes live performance data to Command Hub API.

Periodically sends portfolio snapshots, trade signals, and trade executions
to the central hub for monitoring and analytics. All push operations are
fire-and-forget: failures are logged but never propagate to the trading bot.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

import aiohttp

from bot.compat import UTC
from bot.core.metrics import MetricsEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.logger import audit
from bot.utils.models import (
    RiskVerdict,
    TradeExecution,
    TradeIdea,
)

logger = logging.getLogger("runeclaw.hub")


class PerformanceTracker:
    """Pushes live performance data to the Command Hub API.

    All public methods catch exceptions internally and return False on
    failure so that hub connectivity issues never affect the trading loop.
    """

    def __init__(
        self,
        hub_url: str,
        api_token: str,
        portfolio: PortfolioTracker,
    ) -> None:
        self._hub_url = hub_url.rstrip("/")
        self._api_token = api_token
        self._portfolio = portfolio
        self._metrics_engine = MetricsEngine()
        self._session: Optional[aiohttp.ClientSession] = None
        self._periodic_task: Optional[asyncio.Task] = None

    # -- Internal helpers ----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def _post(self, path: str, payload: dict[str, Any]) -> bool:
        """POST JSON to hub_url/path. Returns True on 2xx, False otherwise."""
        url = f"{self._hub_url}{path}"
        try:
            session = await self._ensure_session()
            async with session.post(url, json=payload, headers=self._headers()) as resp:
                if 200 <= resp.status < 300:
                    audit(
                        logger,
                        f"Hub push succeeded: {path}",
                        action="hub_push",
                        result="success",
                        data={"url": url, "status": resp.status},
                    )
                    return True
                body = await resp.text()
                audit(
                    logger,
                    f"Hub push failed: {path} -> {resp.status}",
                    action="hub_push",
                    result="failure",
                    data={"url": url, "status": resp.status, "body": body[:500]},
                )
                return False
        except Exception as exc:
            audit(
                logger,
                f"Hub push error: {path} -> {exc}",
                action="hub_push",
                result="error",
                data={"url": url, "error": str(exc)},
            )
            return False

    # -- Public API ----------------------------------------------------------

    async def push_snapshot(self) -> bool:
        """Push current performance to hub API. Returns True on success."""
        try:
            # Gather portfolio state
            state = self._portfolio.snapshot()

            # Compute metrics from closed trade history
            closed_trades = list(self._portfolio._history)
            metrics = self._metrics_engine.compute(closed_trades)

            # Record current equity for the metrics engine's internal curve
            equity = state.equity
            self._metrics_engine.record_equity(equity)

            # Build performance payload
            performance_payload: dict[str, Any] = {
                "portfolio": state.model_dump(mode="json"),
                "metrics": metrics.model_dump(mode="json"),
                "timestamp": datetime.now(UTC).isoformat(),
            }

            # Build health payload
            health_payload: dict[str, Any] = {
                "status": "running",
                "equity": equity,
                "open_positions": len(state.open_positions),
                "total_trades": metrics.total_trades,
                "uptime_ts": datetime.now(UTC).isoformat(),
            }

            # Push both endpoints; both must succeed for overall True
            perf_ok = await self._post("/api/performance", performance_payload)
            health_ok = await self._post("/api/health", health_payload)

            return perf_ok and health_ok

        except Exception as exc:
            audit(
                logger,
                f"push_snapshot failed: {exc}",
                action="push_snapshot",
                result="error",
                data={"error": str(exc)},
            )
            return False

    async def push_signal(self, idea: TradeIdea, verdict: RiskVerdict) -> bool:
        """Push a signal (approved or rejected) to hub API."""
        try:
            payload: dict[str, Any] = {
                "signal": idea.model_dump(mode="json"),
                "verdict": verdict.value,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            return await self._post("/api/signal", payload)
        except Exception as exc:
            audit(
                logger,
                f"push_signal failed: {exc}",
                action="push_signal",
                result="error",
                data={"error": str(exc)},
            )
            return False

    async def push_trade(self, trade: TradeExecution) -> bool:
        """Push trade open/close to hub API."""
        try:
            payload: dict[str, Any] = {
                "trade": trade.model_dump(mode="json"),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            return await self._post("/api/trade", payload)
        except Exception as exc:
            audit(
                logger,
                f"push_trade failed: {exc}",
                action="push_trade",
                result="error",
                data={"error": str(exc)},
            )
            return False

    async def start_periodic_push(self, interval_seconds: int = 30) -> None:
        """Start background task that pushes snapshots every N seconds."""
        if self._periodic_task is not None and not self._periodic_task.done():
            audit(
                logger,
                "Periodic push already running, skipping start",
                action="periodic_push",
                result="skipped",
            )
            return

        async def _loop() -> None:
            audit(
                logger,
                f"Periodic hub push started (interval={interval_seconds}s)",
                action="periodic_push",
                result="started",
            )
            while True:
                try:
                    await asyncio.sleep(interval_seconds)
                    await self.push_snapshot()
                except asyncio.CancelledError:
                    audit(
                        logger,
                        "Periodic hub push cancelled",
                        action="periodic_push",
                        result="cancelled",
                    )
                    break
                except Exception as exc:
                    audit(
                        logger,
                        f"Periodic push iteration error: {exc}",
                        action="periodic_push",
                        result="error",
                        data={"error": str(exc)},
                    )

        self._periodic_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        """Stop the periodic push task and close the HTTP session."""
        if self._periodic_task is not None and not self._periodic_task.done():
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None

        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

        audit(
            logger,
            "Performance tracker stopped",
            action="stop",
            result="success",
        )
