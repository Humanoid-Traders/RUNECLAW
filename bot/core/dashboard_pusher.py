"""
Dashboard snapshot pusher for RUNECLAW.

Periodically collects portfolio state from all users and pushes
a JSON snapshot to the live dashboard endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp

log = logging.getLogger("runeclaw.dashboard_pusher")

PUSH_INTERVAL = 30  # seconds
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:9090")
DASHBOARD_KEY = os.environ.get("DASHBOARD_API_KEY", "runeclaw-beta-2026")


class DashboardPusher:
    """Pushes portfolio snapshots to the live dashboard."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if not DASHBOARD_URL:
            log.info("DASHBOARD_URL not set — dashboard pusher disabled")
            return
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._loop())
        log.info("Dashboard pusher started → %s (every %ds)", DASHBOARD_URL, PUSH_INTERVAL)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._session:
            await self._session.close()

    def _build_snapshot(self) -> dict:
        """Build a full dashboard snapshot from all user portfolios."""
        # Load user names
        users_file = "data/users.json"
        user_names = {}
        try:
            with open(users_file) as f:
                users_data = json.load(f)
            for uid, u in users_data.items():
                user_names[uid] = u.get("name", f"User {uid[-4:]}")
        except Exception:
            pass

        multi = self.engine.user_portfolios
        traders = []

        for user_id, portfolio in multi.all_portfolios().items():
            snap = portfolio.snapshot()
            # Open positions
            positions = []
            for pos in portfolio.open_positions:
                last_px = portfolio._last_prices.get(pos.asset, pos.entry_price)
                if pos.direction.value == "LONG":
                    pnl_pct = ((last_px - pos.entry_price) / pos.entry_price) * 100
                else:
                    pnl_pct = ((pos.entry_price - last_px) / pos.entry_price) * 100
                sz = pos.quantity * pos.entry_price
                hold_h = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600

                positions.append({
                    "asset": pos.asset,
                    "direction": pos.direction.value,
                    "entry": round(pos.entry_price, 6),
                    "current": round(last_px, 6),
                    "quantity": round(pos.quantity, 4),
                    "size_usd": round(sz, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usd": round(sz * pnl_pct / 100, 2),
                    "sl": round(pos.stop_loss, 6),
                    "tp": round(pos.take_profit, 6),
                    "opened_at": pos.opened_at.isoformat(),
                    "hold_hours": round(hold_h, 1),
                })

            # Recent closed trades (last 20)
            recent_trades = []
            for t in portfolio.trade_history[-20:]:
                recent_trades.append({
                    "asset": t.asset,
                    "direction": t.direction.value,
                    "entry": round(t.entry_price, 6),
                    "exit": round(t.exit_price, 6) if t.exit_price else None,
                    "quantity": round(t.quantity, 4),
                    "gross_pnl": round(t.gross_pnl, 2),
                    "commission": round(t.commission, 2),
                    "pnl": round(t.pnl, 2),
                    "opened_at": t.opened_at.isoformat(),
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                })

            display_name = user_names.get(user_id, f"Trader {user_id[-4:]}")

            traders.append({
                "user_id": user_id,
                "name": display_name,
                "balance": round(snap.balance_usd, 2),
                "equity": round(snap.equity_usd, 2),
                "open_count": snap.open_positions,
                "total_trades": snap.total_trades,
                "win_rate": round(snap.win_rate * 100, 1),
                "total_pnl": round(snap.total_pnl, 2),
                "daily_pnl": round(snap.daily_pnl, 2),
                "max_drawdown": round(snap.max_drawdown_pct, 2),
                "total_commission": round(snap.total_commission, 2),
                "positions": positions,
                "recent_trades": recent_trades,
            })

        # System-level stats
        combined = multi.combined_snapshot() if multi.all_portfolios() else None

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bot_version": "4.0-beta",
            "mode": "PAPER",
            "total_traders": len(traders),
            "total_open_positions": multi.total_open_positions() if multi.all_portfolios() else 0,
            "system": {
                "equity": round(combined.equity_usd, 2) if combined else 0,
                "total_pnl": round(combined.total_pnl, 2) if combined else 0,
                "total_trades": combined.total_trades if combined else 0,
                "win_rate": round(combined.win_rate * 100, 1) if combined else 0,
            } if combined else {},
            "traders": traders,
        }

    async def _loop(self) -> None:
        while True:
            try:
                snapshot = self._build_snapshot()
                headers = {"Content-Type": "application/json"}
                if DASHBOARD_KEY:
                    headers["X-API-Key"] = DASHBOARD_KEY
                async with self._session.post(
                    f"{DASHBOARD_URL}/api/snapshot",
                    json=snapshot,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        log.debug("Dashboard snapshot pushed (%d traders)", len(snapshot["traders"]))
                    else:
                        log.warning("Dashboard push failed: %d", resp.status)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("Dashboard push error: %s", e)

            await asyncio.sleep(PUSH_INTERVAL)
