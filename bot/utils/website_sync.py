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
from typing import Optional

log = logging.getLogger(__name__)

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://pmvc58g2.mule.page")
SYNC_SECRET = os.getenv("BOT_SYNC_SECRET", "")

if not SYNC_SECRET:
    log.warning("BOT_SYNC_SECRET not set — website sync will be rejected by the server.")


def _attr(obj, key, default=None):
    """Safely get attribute from Pydantic model or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    val = getattr(obj, key, default)
    return val if val is not None else default


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
            "symbol": _attr(p, "asset", ""),
            "direction": str(_attr(p, "direction", "")).split(".")[-1],
            "entry_price": float(_attr(p, "entry_price", 0)),
            "size_usd": float(_attr(p, "quantity", 0)) * float(_attr(p, "entry_price", 0)),
            "fees": float(_attr(p, "commission", 0)),
            "pattern": _attr(p, "pattern"),
            "stop_loss": float(_attr(p, "stop_loss", 0)),
            "take_profit": float(_attr(p, "take_profit", 0)),
            "opened_at": str(_attr(p, "opened_at", "")),
        })

    closed_list = []
    for t in closed_trades:
        closed_list.append({
            "symbol": _attr(t, "asset", ""),
            "direction": str(_attr(t, "direction", "")).split(".")[-1],
            "entry_price": float(_attr(t, "entry_price", 0)),
            "exit_price": float(_attr(t, "exit_price", 0)),
            "size_usd": float(_attr(t, "quantity", 0)) * float(_attr(t, "entry_price", 0)),
            "pnl": float(_attr(t, "pnl", 0)),
            "fees": float(_attr(t, "commission", 0)),
            "pattern": _attr(t, "pattern"),
            "opened_at": str(_attr(t, "opened_at", "")),
            "closed_at": str(_attr(t, "closed_at", "")),
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
        "symbol": _attr(trade, "asset", ""),
        "direction": str(_attr(trade, "direction", "")).split(".")[-1],
        "entry_price": float(_attr(trade, "entry_price", 0)),
        "size_usd": float(_attr(trade, "quantity", 0)) * float(_attr(trade, "entry_price", 0)),
        "fees": float(_attr(trade, "commission", 0)),
        "pattern": _attr(trade, "pattern"),
        "stop_loss": float(_attr(trade, "stop_loss", 0)),
        "take_profit": float(_attr(trade, "take_profit", 0)),
    }

    if event == "close":
        trade_data["exit_price"] = float(_attr(trade, "exit_price", 0))
        trade_data["pnl"] = float(_attr(trade, "pnl", 0))
        trade_data["opened_at"] = str(_attr(trade, "opened_at", ""))
        trade_data["closed_at"] = str(_attr(trade, "closed_at", ""))

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


def sync_scan_data(scan_payload: dict) -> bool:
    """Push scan results to the website dashboard.

    scan_payload should match the dashboard's expected schema:
    {
        regime: { label, score, gate, long_short, funding },
        circuit_breaker: { rules: [{ label, active }] },
        symbols: { 'ADAUSDT': { book_ratio, book_side, status, status_label } },
        entry_cards: [{ symbol, direction, score, entry, stop_loss, tp1, tp2,
                        margin, rr, book_ratio, trigger, thesis }],
        key_call: "HTML narrative string",
        timestamp: "2026-06-18 11:28 CST"
    }
    """
    result = _post("/api/bot/sync/scan", scan_payload)
    if result and result.get("ok"):
        log.info("Scan data synced to website dashboard")
        return True
    log.warning("Scan data sync failed")
    return False


def sync_scan_in_background(scan_payload: dict) -> None:
    """Non-blocking scan data sync."""
    t = threading.Thread(
        target=sync_scan_data,
        args=(scan_payload,),
        daemon=True,
    )
    t.start()


def build_signal_payload(signal_key: str, idea, *, score: float = 0.0,
                         regime: str = "", status: str = "NEW",
                         pnl: Optional[float] = None,
                         created_at: str = "", resolved_at: str = "") -> dict:
    """Shape one signal-stream row from a TradeIdea-like object (dict or model).

    ``signal_key`` is a STABLE per-signal id so re-syncing the same signal updates
    its outcome (status/pnl) instead of duplicating. Every generated signal —
    taken or not — belongs in the stream; the dashboard joins a user's own trades
    to it. Pure shaping (no I/O); returns a JSON-ready dict.
    """
    direction = str(_attr(idea, "direction", "")).split(".")[-1]
    entry = float(_attr(idea, "entry_price", 0) or 0)
    sl = float(_attr(idea, "stop_loss", 0) or 0)
    tp = float(_attr(idea, "take_profit", 0) or 0)
    rr = _attr(idea, "risk_reward_ratio", None)
    if rr is None:
        risk = abs(entry - sl)
        rr = (abs(tp - entry) / risk) if risk > 0 else 0.0
    return {
        "signal_key": str(signal_key),
        "symbol": _attr(idea, "asset", "") or _attr(idea, "symbol", ""),
        "direction": direction,
        "confidence": float(_attr(idea, "confidence", 0) or 0),
        "score": float(score or 0),
        "pattern": _attr(idea, "pattern"),
        "regime": regime or "",
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "rr": float(rr or 0),
        "thesis": _attr(idea, "reasoning", "") or _attr(idea, "thesis", ""),
        "status": status,
        "pnl": pnl,
        "created_at": created_at or "",
        "resolved_at": resolved_at or "",
    }


def sync_signals(signals: list[dict]) -> bool:
    """Push a batch of signal-stream rows to the website (UPSERT by signal_key)."""
    if not signals:
        return True
    result = _post("/api/bot/sync/signals", {"signals": signals})
    if result and result.get("ok"):
        log.info(f"Synced {result.get('upserted', 0)} signal(s) to website")
        return True
    log.warning("Signal stream sync failed")
    return False


def sync_signals_in_background(signals: list[dict]) -> None:
    """Non-blocking signal-stream sync."""
    if not signals:
        return
    t = threading.Thread(target=sync_signals, args=(list(signals),), daemon=True)
    t.start()
