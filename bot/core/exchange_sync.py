"""
RUNECLAW Exchange Sync — makes Bitget the single source of truth.

Two public async functions:

  sync_portfolio_with_exchange(engine)
      Reconciles local portfolio and live_executor state against real
      exchange positions.  Closes ghost positions in the portfolio and
      adopts orphaned exchange positions into the live executor.

  get_exchange_position_count(engine) -> int
      Returns the actual number of open positions on the exchange.
      Use this instead of local state counts for risk gating.

Designed to run on every startup and periodically thereafter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bot.config import CONFIG
from bot.core.live_executor import normalize_symbol
from bot.utils.logger import audit, trade_log, system_log
from bot.utils.models import Direction

logger = logging.getLogger(__name__)

# ── Position count cache ────────────────────────────────────────────
# Avoids hammering the exchange API on every risk check during a scan.
_position_count_cache: dict[str, Any] = {
    "count": 0,
    "timestamp": 0.0,
}
_POSITION_COUNT_TTL = 30.0  # seconds — refresh at most every 30s

# ── Helpers ──────────────────────────────────────────────────────────

_PRODUCT_TYPE_PARAMS = {"productType": "USDT-FUTURES"}


async def _fetch_exchange_positions(engine) -> list[dict[str, Any]]:
    """Fetch all open futures positions from Bitget and return only those
    with a non-zero contract count.

    For UTA accounts, ccxt's fetch_positions may not return all positions.
    Falls back to querying the v3 REST API directly and merging results.
    """
    exchange = await engine.live_executor._get_exchange()
    raw = await exchange.fetch_positions(params=_PRODUCT_TYPE_PARAMS)
    ccxt_positions = [
        p for p in raw
        if abs(float(p.get("contracts", 0) or 0)) > 0
    ]

    # UTA fallback: query v3 position endpoint directly to catch any
    # positions missed by ccxt (observed with some symbols like INTC).
    is_uta = getattr(engine.live_executor, "_is_uta", None)
    if is_uta:
        try:
            v3_positions = await asyncio.to_thread(
                _fetch_v3_positions_direct)
            if v3_positions:
                # Merge: add any v3 positions not already in ccxt results
                ccxt_syms = {
                    (p.get("symbol", ""), (p.get("side", "")).lower())
                    for p in ccxt_positions
                }
                added = 0
                for v3p in v3_positions:
                    key = (v3p.get("symbol", ""), (v3p.get("side", "")).lower())
                    if key not in ccxt_syms:
                        ccxt_positions.append(v3p)
                        added += 1
                if added > 0:
                    audit(system_log,
                          f"v3 position fallback found {added} extra position(s) missed by ccxt",
                          action="v3_position_fallback", result="OK")
        except Exception as exc:
            logger.debug("v3 position fallback failed: %s", exc)

    return ccxt_positions


def _fetch_v3_positions_direct() -> list[dict[str, Any]]:
    """Query Bitget v3 /api/v3/position/current-position directly.

    Returns positions in ccxt-compatible dict format so they can be merged
    with ccxt's fetch_positions output.

    This is a synchronous call — callers must wrap in asyncio.to_thread.
    """
    cfg = CONFIG.exchange
    if not cfg.api_key or not cfg.api_secret:
        return []

    # v3 uses "category" not "productType"
    query = "category=USDT-FUTURES"
    path = f"/api/v3/position/current-position?{query}"

    from bot.core.bitget_v3_client import BitgetV3Client
    try:
        resp_data = BitgetV3Client.from_config().get(path)
    except Exception:
        return []

    if resp_data.get("code") != "00000":
        return []

    data_list = resp_data.get("data", [])
    if not isinstance(data_list, list):
        return []

    positions = []
    for item in data_list:
        qty = float(item.get("totalQty") or item.get("available") or 0)
        if qty <= 0:
            continue

        # Convert Bitget raw symbol to ccxt format
        raw_sym = item.get("symbol", "")  # e.g. "INTCUSDT"
        # Build ccxt-style symbol: INTC/USDT:USDT
        base = raw_sym
        for quote in ("USDT", "USDC"):
            if raw_sym.endswith(quote) and len(raw_sym) > len(quote):
                base = raw_sym[:-len(quote)]
                break
        ccxt_symbol = f"{base}/USDT:USDT"

        side = (item.get("holdSide") or item.get("posSide") or "long").lower()
        if side == "net":
            side = "long"  # one-way mode defaults to long

        entry_price = float(item.get("openPriceAvg") or 0)
        margin = float(item.get("margin") or item.get("im") or 0)
        leverage = int(float(item.get("leverage") or 1))
        mark_price = float(item.get("markPrice") or 0)
        unrealized_pnl = float(item.get("unrealizedPL") or 0)
        ts_val = item.get("ctime") or item.get("utime")

        positions.append({
            "symbol": ccxt_symbol,
            "side": side,
            "contracts": qty,
            "entryPrice": entry_price,
            "initialMargin": margin,
            "leverage": str(leverage),
            "markPrice": mark_price,
            "unrealizedPnl": unrealized_pnl,
            "timestamp": int(ts_val) if ts_val else None,
            "info": item,  # raw v3 data for adopt_exchange_positions
        })

    return positions


def _exchange_key(pos: dict[str, Any]) -> tuple[str, str]:
    """Return (normalized_symbol, direction) for an exchange position."""
    sym = normalize_symbol(pos.get("symbol", ""))
    side = pos.get("side", "").lower()  # "long" / "short"
    return (sym, side)


def _portfolio_key(trade) -> tuple[str, str]:
    """Return (normalized_symbol, direction_str) for a portfolio TradeExecution."""
    sym = normalize_symbol(trade.asset)
    side = "long" if trade.direction == Direction.LONG else "short"
    return (sym, side)


# ── Public API ───────────────────────────────────────────────────────


def _calc_pnl(trade, exit_price: float) -> float:
    """Calculate raw PnL for a portfolio trade at the given exit price."""
    if trade.direction == Direction.LONG:
        return (exit_price - trade.entry_price) * trade.quantity
    return (trade.entry_price - exit_price) * trade.quantity


async def _get_actual_close_price(
    engine, exchange, trade, trade_id: str,
) -> tuple[float, str, str]:
    """Look up the actual close price from exchange trade history.

    Mirrors the 3-step fallback in live_executor.reconcile_positions():
      1. fetchMyTrades — match by SL/TP order IDs
      2. fetchClosedOrders — match by SL/TP order IDs
      3. Ticker estimate — proximity to SL/TP levels
      4. Last resort — entry price (PnL = 0)

    Returns (close_price, reason, source).
    """
    ccxt_symbol = trade.asset if ":USDT" in trade.asset else f"{trade.asset}:USDT"

    # Find matching live_executor position for SL/TP order IDs
    sl_order_id = None
    tp_order_id = None
    sl_price = getattr(trade, "stop_loss", 0) or 0
    tp_price = getattr(trade, "take_profit", 0) or 0

    if hasattr(engine.live_executor, "_positions"):
        positions_dict = engine.live_executor._positions
        if isinstance(positions_dict, dict):
            for pos in positions_dict.values():
                pos_sym = normalize_symbol(getattr(pos, "symbol", "") or "")
                trade_sym = normalize_symbol(trade.asset)
                if pos_sym == trade_sym:
                    sl_order_id = getattr(pos, "sl_order_id", None)
                    tp_order_id = getattr(pos, "tp_order_id", None)
                    sl_price = sl_price or getattr(pos, "stop_loss", 0) or 0
                    tp_price = tp_price or getattr(pos, "take_profit", 0) or 0
                    break

    # ── 1. fetchMyTrades — actual fill price + exchange PnL ──────────
    if sl_order_id or tp_order_id:
        try:
            trades = await exchange.fetch_my_trades(ccxt_symbol, limit=50)
            relevant = [
                t for t in trades
                if t.get("order") in (sl_order_id, tp_order_id)
            ]
            if relevant:
                fill_price = float(relevant[-1].get("price", 0) or 0)
                if fill_price > 0:
                    matched_order = relevant[-1].get("order")
                    if matched_order == tp_order_id:
                        reason = "TP HIT (exchange)"
                    elif matched_order == sl_order_id:
                        reason = "SL HIT (exchange)"
                    else:
                        reason = "closed (exchange)"
                    return fill_price, reason, "exchange_fill"
        except Exception as e:
            logger.debug("Ghost close fetchMyTrades failed for %s: %s", ccxt_symbol, e)

    # ── 2. fetchClosedOrders ─────────────────────────────────────────
    if sl_order_id or tp_order_id:
        try:
            closed_orders = await exchange.fetch_closed_orders(ccxt_symbol, limit=20)
            for o in closed_orders:
                if o.get("id") in (sl_order_id, tp_order_id):
                    avg = o.get("average") or o.get("price")
                    if avg:
                        fill_price = float(avg)
                        if fill_price > 0:
                            if o["id"] == tp_order_id:
                                reason = "TP HIT (exchange)"
                            else:
                                reason = "SL HIT (exchange)"
                            return fill_price, reason, "closed_order"
        except Exception as e:
            logger.debug("Ghost close fetchClosedOrders failed for %s: %s", ccxt_symbol, e)

    # ── 3. Ticker + SL/TP proximity estimate ─────────────────────────
    try:
        ticker = await exchange.fetch_ticker(ccxt_symbol)
        current_price = float(ticker.get("last", 0) or 0)
    except Exception:
        current_price = 0

    if current_price > 0 and (sl_price > 0 or tp_price > 0):
        # Only attribute to SL/TP if price is very close to the level
        # (within 0.3%). Otherwise it was a manual close — use ticker price.
        proximity_threshold = trade.entry_price * 0.003  # 0.3%
        dist_tp = abs(current_price - tp_price) if tp_price > 0 else float("inf")
        dist_sl = abs(current_price - sl_price) if sl_price > 0 else float("inf")
        if dist_tp <= proximity_threshold and tp_price > 0:
            return tp_price, "TP HIT (estimated)", "estimated"
        elif dist_sl <= proximity_threshold and sl_price > 0:
            return sl_price, "SL HIT (estimated)", "estimated"
        else:
            return current_price, "manually closed", "ticker"

    if current_price > 0:
        return current_price, "manually closed", "ticker"

    # ── 4. Last resort: entry price ──────────────────────────────────
    logger.warning(
        "Ghost close for %s: no fill data available — using entry price (PnL=0)",
        trade.asset)
    return trade.entry_price, "no matching exchange position", "fallback"

async def sync_portfolio_with_exchange(engine) -> list[str]:
    """Reconcile local state with the exchange.

    1. Close ghost portfolio positions (exist locally but not on exchange).
    2. Adopt orphaned exchange positions (exist on exchange but not locally).

    Returns a list of human-readable messages describing every action taken.
    """
    messages: list[str] = []

    # ── Fetch exchange positions ─────────────────────────────────────
    try:
        exchange_positions = await _fetch_exchange_positions(engine)
    except Exception as exc:
        msg = f"Exchange sync aborted — failed to fetch positions: {exc}"
        audit(system_log, msg, action="exchange_sync", result="ERROR")
        return [msg]

    exchange_keys: set[tuple[str, str]] = set()
    for ep in exchange_positions:
        exchange_keys.add(_exchange_key(ep))

    audit(system_log,
          f"Exchange sync: {len(exchange_positions)} open position(s) on exchange",
          action="exchange_sync", result="OK")

    # ── Phase 1: close ghost portfolio positions ─────────────────────
    portfolio_positions: dict[str, Any] = dict(engine.portfolio._positions)
    exchange = await engine.live_executor._get_exchange()

    for trade_id, trade in portfolio_positions.items():
        key = _portfolio_key(trade)
        if key not in exchange_keys:
            # Ghost position — look up actual fill price from exchange
            close_price, close_reason, close_source = await _get_actual_close_price(
                engine, exchange, trade, trade_id,
            )
            try:
                engine.portfolio.close_position(trade_id, close_price)
                pnl = _calc_pnl(trade, close_price)
                msg = (
                    f"Ghost closed: {trade_id} "
                    f"({key[0]} {key[1]}) — {close_reason} | "
                    f"exit=${close_price:,.4f} | PnL=${pnl:+.4f} ({close_source})"
                )
                audit(trade_log, msg, action="ghost_close", result="OK",
                      data={"trade_id": trade_id, "symbol": key[0],
                            "direction": key[1], "exit_price": close_price,
                            "pnl": round(pnl, 4), "source": close_source})
            except Exception as exc:
                msg = f"Failed to close ghost {trade_id}: {exc}"
                audit(trade_log, msg, action="ghost_close", result="ERROR",
                      data={"trade_id": trade_id})
            messages.append(msg)

    # ── Phase 2: adopt orphaned exchange positions ───────────────────
    # Build set of keys tracked by EITHER portfolio or live_executor
    tracked_keys: set[tuple[str, str]] = set()

    # Re-read portfolio after ghost cleanup (use thread-safe property)
    for trade in engine.portfolio.open_positions:
        tracked_keys.add(_portfolio_key(trade))

    # Live executor positions (open_positions is a list of LivePosition objects)
    if hasattr(engine.live_executor, "open_positions"):
        for pos in engine.live_executor.open_positions:
            sym = normalize_symbol(getattr(pos, "symbol", "") or "")
            side = (getattr(pos, "direction", "") or "").lower()
            tracked_keys.add((sym, side))

    orphans_exist = False
    for ep in exchange_positions:
        key = _exchange_key(ep)
        if key not in tracked_keys:
            orphans_exist = True
            msg = (
                f"Orphan detected on exchange: {key[0]} {key[1]} "
                f"({ep.get('contracts')} contracts @ {ep.get('entryPrice')})"
            )
            audit(trade_log, msg, action="orphan_detect", result="WARN",
                  data={"symbol": key[0], "direction": key[1],
                        "contracts": ep.get("contracts")})
            messages.append(msg)

    if orphans_exist:
        try:
            adopted = await engine.live_executor.adopt_exchange_positions()
            for a in adopted:
                msg = f"Adopted orphan: {a}"
                messages.append(msg)
            audit(system_log,
                  f"Adopted {len(adopted)} orphaned exchange position(s)",
                  action="exchange_sync_adopt", result="OK")
        except Exception as exc:
            msg = f"Failed to adopt orphaned positions: {exc}"
            audit(system_log, msg, action="exchange_sync_adopt", result="ERROR")
            messages.append(msg)

    # Also adopt orphaned limit orders not tracked locally
    try:
        adopted_orders = await engine.live_executor.adopt_exchange_limit_orders()
        for a in adopted_orders:
            msg = f"Adopted orphan limit order: {a}"
            messages.append(msg)
        if adopted_orders:
            audit(system_log,
                  f"Adopted {len(adopted_orders)} orphaned limit order(s)",
                  action="exchange_sync_adopt_limit", result="OK")
    except Exception as exc:
        logger.debug("Failed to adopt orphaned limit orders: %s", exc)

    if not messages:
        messages.append("Exchange sync complete — all positions in sync")
        audit(system_log, messages[0], action="exchange_sync", result="OK")

    return messages


async def get_exchange_position_count(engine) -> int:
    """Return the number of actually open positions on the exchange.

    This is the authoritative count the risk engine should use for
    position-limit checks instead of local state.

    Cached for 30 seconds to avoid hammering the exchange API during
    multi-symbol scans (a fullscan checks 10+ symbols in rapid succession).
    """
    global _position_count_cache
    now = time.monotonic()

    # Return cached value if fresh enough
    if (now - _position_count_cache["timestamp"]) < _POSITION_COUNT_TTL:
        return _position_count_cache["count"]

    try:
        positions = await _fetch_exchange_positions(engine)
        count = len(positions)
        _position_count_cache["count"] = count
        _position_count_cache["timestamp"] = now
        return count
    except Exception as exc:
        audit(system_log,
              f"Could not fetch exchange position count: {exc}",
              action="exchange_position_count", result="ERROR")
        # Fall back to local state maximum so we never accidentally
        # exceed limits when the exchange API is unreachable.
        local_live = len(getattr(engine.live_executor, "open_positions", {}))
        local_port = len(getattr(engine.portfolio, "_positions", {}))
        fallback = max(local_live, local_port)
        # Cache fallback too to prevent repeated failed API calls
        _position_count_cache["count"] = fallback
        _position_count_cache["timestamp"] = now
        return fallback


def invalidate_position_count_cache():
    """Call after opening/closing a position to force a fresh exchange query."""
    global _position_count_cache
    _position_count_cache["timestamp"] = 0.0
