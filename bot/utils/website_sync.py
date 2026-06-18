"""
RUNECLAW -- Website sync module.
Pushes portfolio state and trade events to the website API
so the dashboard shows real, live data.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://deryrgeb.mule.page")
SYNC_SECRET = os.getenv("BOT_SYNC_SECRET", "runeclaw-sync-2026")


def _post(path: str, data: dict) -> Optional[dict]:
    """POST JSON to the website API. Returns response dict or None on error."""
    url = f"{WEBSITE_URL}{path}"
    payload = json.dumps(data, default=str).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RUNECLAW-Bot/1.0",
            "X-Bot-Secret": SYNC_SECRET,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except Exception:
            pass
        log.error(f"Sync HTTP error {e.code}: {body}")
        return None
    except Exception as exc:
        log.error(f"Sync error: {exc}")
        return None


def sync_portfolio(user_id: int, equity: float,
                   positions: list, closed_trades: list) -> bool:
    """Full sync: replace all website data for a user with current bot state."""
    open_list = []
    for p in positions:
        open_list.append({
            "symbol": getattr(p, "asset", None) or p.get("asset", ""),
            "direction": str(getattr(p, "direction", None) or p.get("direction", "")).split(".")[-1],
            "entry_price": float(getattr(p, "entry_price", 0) or p.get("entry_price", 0)),
            "size_usd": float(getattr(p, "quantity", 0) or p.get("quantity", 0)) * float(getattr(p, "entry_price", 0) or p.get("entry_price", 0)),
            "fees": float(getattr(p, "commission", 0) or p.get("commission", 0)),
            "pattern": getattr(p, "pattern", None) or p.get("pattern"),
            "stop_loss": float(getattr(p, "stop_loss", 0) or p.get("stop_loss", 0)),
            "take_profit": float(getattr(p, "take_profit", 0) or p.get("take_profit", 0)),
            "opened_at": str(getattr(p, "opened_at", None) or p.get("opened_at", "")),
        })

    closed_list = []
    for t in closed_trades:
        closed_list.append({
            "symbol": getattr(t, "asset", None) or t.get("asset", ""),
            "direction": str(getattr(t, "direction", None) or t.get("direction", "")).split(".")[-1],
            "entry_price": float(getattr(t, "entry_price", 0) or t.get("entry_price", 0)),
            "exit_price": float(getattr(t, "exit_price", 0) or t.get("exit_price", 0)),
            "size_usd": float(getattr(t, "quantity", 0) or t.get("quantity", 0)) * float(getattr(t, "entry_price", 0) or t.get("entry_price", 0)),
            "pnl": float(getattr(t, "pnl", 0) or t.get("pnl", 0)),
            "fees": float(getattr(t, "commission", 0) or t.get("commission", 0)),
            "pattern": getattr(t, "pattern", None) or t.get("pattern"),
            "opened_at": str(getattr(t, "opened_at", None) or t.get("opened_at", "")),
            "closed_at": str(getattr(t, "closed_at", None) or t.get("closed_at", "")),
        })

    result = _post("/api/bot/sync", {
        "user_id": user_id,
        "equity": equity,
        "positions": open_list,
        "closed_trades": closed_list,
    })

    if result and result.get("ok"):
        log.info(f"Synced to website: user={user_id} equity={equity} "
                 f"open={len(open_list)} closed={len(closed_list)}")
        return True
    return False


def sync_trade_event(user_id: int, event: str, trade, equity: float) -> bool:
    """Push a single trade event (open/close) to the website."""
    trade_data = {
        "symbol": getattr(trade, "asset", None) or trade.get("asset", ""),
        "direction": str(getattr(trade, "direction", None) or trade.get("direction", "")).split(".")[-1],
        "entry_price": float(getattr(trade, "entry_price", 0) or trade.get("entry_price", 0)),
        "size_usd": float(getattr(trade, "quantity", 0) or trade.get("quantity", 0)) * float(getattr(trade, "entry_price", 0) or trade.get("entry_price", 0)),
        "fees": float(getattr(trade, "commission", 0) or trade.get("commission", 0)),
        "pattern": getattr(trade, "pattern", None) or trade.get("pattern"),
        "stop_loss": float(getattr(trade, "stop_loss", 0) or trade.get("stop_loss", 0)),
        "take_profit": float(getattr(trade, "take_profit", 0) or trade.get("take_profit", 0)),
    }

    if event == "close":
        trade_data["exit_price"] = float(getattr(trade, "exit_price", 0) or trade.get("exit_price", 0))
        trade_data["pnl"] = float(getattr(trade, "pnl", 0) or trade.get("pnl", 0))
        trade_data["opened_at"] = str(getattr(trade, "opened_at", None) or trade.get("opened_at", ""))
        trade_data["closed_at"] = str(getattr(trade, "closed_at", None) or trade.get("closed_at", ""))

    result = _post("/api/bot/sync/trade-event", {
        "user_id": user_id,
        "event": event,
        "trade": trade_data,
        "equity": equity,
    })

    if result and result.get("ok"):
        log.info(f"Trade event synced: user={user_id} event={event} "
                 f"symbol={trade_data['symbol']}")
        return True
    return False


def sync_in_background(user_id: int, equity: float,
                       positions: list, closed_trades: list) -> None:
    """Non-blocking sync: runs in a background thread."""
    t = threading.Thread(
        target=sync_portfolio,
        args=(user_id, equity, positions, closed_trades),
        daemon=True,
    )
    t.start()


def sync_event_in_background(user_id: int, event: str, trade, equity: float) -> None:
    """Non-blocking trade event sync."""
    t = threading.Thread(
        target=sync_trade_event,
        args=(user_id, event, trade, equity),
        daemon=True,
    )
    t.start()
