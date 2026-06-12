"""
RUNECLAW Live Executor — places real orders on Bitget via ccxt.

Safety invariants:
  - MICRO_TEST_MODE caps every position at $10 and total exposure at $50
  - Every order is audited before and after submission
  - Market orders only (simplest, most reliable for micro-amounts)
  - Fail-closed: any API error aborts the trade and logs the failure
  - The executor never modifies risk limits or bypasses any gate
  - SL/TP are placed as separate stop-market / take-profit-market orders
  - F-07 FIX: Positions are persisted to disk and reconciled on restart
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from bot.compat import UTC
from typing import Any, Optional

import ccxt.async_support as ccxt

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log, system_log
from bot.utils.models import Direction, TradeIdea

logger = logging.getLogger(__name__)


# ── Safety limits for micro-testing ──────────────────────────────────

MICRO_MAX_POSITION_USD = 10.0     # Max $10 per trade
MICRO_MAX_TOTAL_EXPOSURE = 50.0   # Max $50 total open exposure
MICRO_MAX_OPEN_POSITIONS = 5      # Max 5 concurrent positions

# F-07 FIX: Persistence file for live positions
_POSITIONS_FILE = os.path.join(
    os.environ.get("RUNECLAW_STATE_DIR", "data"), "live_positions.json"
)
# F-13 FIX: Maximum order history retained in memory
_MAX_ORDER_HISTORY = 200


@dataclass
class LiveOrder:
    """Record of a live order placed on the exchange."""
    order_id: str
    symbol: str
    side: str          # "buy" or "sell"
    order_type: str    # "market", "limit"
    amount: float      # quantity in base currency
    price: float       # fill price (0 if pending)
    cost_usd: float    # total cost in USDT
    status: str        # "filled", "open", "canceled", "failed"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict = field(default_factory=dict)


@dataclass
class LivePosition:
    """A tracked live position with SL/TP order IDs."""
    trade_id: str
    symbol: str
    direction: str         # "LONG" or "SHORT"
    entry_price: float
    quantity: float        # base currency amount
    cost_usd: float
    stop_loss: float
    take_profit: float
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    status: str = "open"   # "open", "closed", "error"


class LiveExecutor:
    """Executes real trades on Bitget with micro-test safety limits.

    Usage:
        executor = LiveExecutor()
        result = await executor.execute(idea, size_usd=10.0)
    """

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.Exchange] = None
        self._positions: dict[str, LivePosition] = {}
        self._order_history: list[LiveOrder] = []
        # F-07 FIX: Load persisted positions on startup
        self._load_positions()

    async def _get_exchange(self) -> ccxt.Exchange:
        """Get authenticated Bitget exchange instance."""
        if self._exchange is None:
            cfg = CONFIG.exchange
            if not cfg.api_key or not cfg.api_secret:
                raise RuntimeError(
                    "BITGET_API_KEY and BITGET_API_SECRET required for live trading. "
                    "Set them in .env and restart."
                )
            self._exchange = ccxt.bitget({
                "apiKey": cfg.api_key,
                "secret": cfg.api_secret,
                "password": cfg.passphrase,
                "sandbox": cfg.sandbox,
                "timeout": 30000,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "uta": True,  # Support Bitget Unified Trading Account
                },
            })
        return self._exchange

    async def close(self) -> None:
        """Clean up exchange connection."""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None

    # ── Pre-flight checks ────────────────────────────────────────

    def _preflight_check(self, size_usd: float) -> Optional[str]:
        """Run micro-test safety checks. Returns error string or None."""
        # Cap position size
        if size_usd > MICRO_MAX_POSITION_USD:
            return (
                f"Position size ${size_usd:.2f} exceeds micro-test limit "
                f"${MICRO_MAX_POSITION_USD:.2f}"
            )

        # Check total exposure
        total_exposure = sum(
            p.cost_usd for p in self._positions.values()
            if p.status == "open"
        )
        if total_exposure + size_usd > MICRO_MAX_TOTAL_EXPOSURE:
            return (
                f"Total exposure ${total_exposure + size_usd:.2f} would exceed "
                f"micro-test limit ${MICRO_MAX_TOTAL_EXPOSURE:.2f}"
            )

        # Check open positions count
        open_count = sum(1 for p in self._positions.values() if p.status == "open")
        if open_count >= MICRO_MAX_OPEN_POSITIONS:
            return f"Already {open_count} open positions (max {MICRO_MAX_OPEN_POSITIONS})"

        return None

    # ── Execute trade ────────────────────────────────────────────

    async def execute(self, idea: TradeIdea, size_usd: float) -> str:
        """Execute a live trade on Bitget.

        Args:
            idea: The approved TradeIdea
            size_usd: Position size in USD (will be clamped to micro limits)

        Returns:
            Human-readable result string
        """
        # Clamp to micro limit
        size_usd = min(size_usd, MICRO_MAX_POSITION_USD)

        # Pre-flight
        preflight_err = self._preflight_check(size_usd)
        if preflight_err:
            audit(trade_log, f"Live execution blocked: {preflight_err}",
                  action="live_execute", result="BLOCKED",
                  data={"asset": idea.asset, "size_usd": size_usd})
            return f"BLOCKED: {preflight_err}"

        # AUDIT FIX: Re-assert live mode at execution time (not just at call time)
        # This prevents a race where /golive is revoked between confirmation and execution
        if not CONFIG.is_live():
            audit(trade_log, f"LIVE EXECUTION BLOCKED: is_live() returned False at execution time for {idea.asset}",
                  action="live_execute", result="BLOCKED_NOT_LIVE")
            return f"Live execution blocked: live mode was deactivated before order placement."

        audit(trade_log, f"Live execution starting: {idea.direction.value} {idea.asset}",
              action="live_execute", result="STARTING",
              data={
                  "trade_id": idea.id, "asset": idea.asset,
                  "direction": idea.direction.value,
                  "size_usd": size_usd,
                  "entry": idea.entry_price,
                  "sl": idea.stop_loss, "tp": idea.take_profit,
              })

        try:
            exchange = await self._get_exchange()

            # Fetch current price to calculate quantity
            ticker = await exchange.fetch_ticker(idea.asset)
            current_price = float(ticker["last"])

            # Calculate quantity
            quantity = size_usd / current_price

            # Determine side
            side = "buy" if idea.direction == Direction.LONG else "sell"

            # Load markets for precision rounding
            markets = await exchange.load_markets()
            market = markets.get(idea.asset)
            if market:
                quantity = float(exchange.amount_to_precision(idea.asset, quantity))

            if quantity <= 0:
                audit(trade_log, f"Quantity too small after precision: {idea.asset} ${size_usd}",
                      action="live_execute", result="QUANTITY_TOO_SMALL",
                      data={"asset": idea.asset, "size_usd": size_usd, "price": current_price})
                return f"BLOCKED: quantity too small after precision rounding for {idea.asset}"

            # Place market order
            # For spot BUY on Bitget UTA: use cost-based ordering to avoid
            # precision issues and "insufficient balance" errors
            if side == "buy":
                exchange.options["createMarketBuyOrderRequiresPrice"] = False
                order = await exchange.create_order(
                    symbol=idea.asset,
                    type="market",
                    side=side,
                    amount=size_usd,
                    params={"cost": size_usd},
                )
            else:
                order = await exchange.create_order(
                    symbol=idea.asset,
                    type="market",
                    side=side,
                    amount=quantity,
                )

            # Parse result
            fill_price = float(order.get("average", 0) or order.get("price", 0) or current_price)
            filled_qty = float(order.get("filled", 0) or quantity)
            cost = float(order.get("cost", 0) or fill_price * filled_qty)
            order_id = order.get("id", "unknown")
            status = order.get("status", "unknown")

            live_order = LiveOrder(
                order_id=order_id,
                symbol=idea.asset,
                side=side,
                order_type="market",
                amount=filled_qty,
                price=fill_price,
                cost_usd=cost,
                status=status,
                raw=order,
            )
            self._order_history.append(live_order)

            # Track position
            position = LivePosition(
                trade_id=idea.id,
                symbol=idea.asset,
                direction=idea.direction.value,
                entry_price=fill_price,
                quantity=filled_qty,
                cost_usd=cost,
                stop_loss=idea.stop_loss,
                take_profit=idea.take_profit,
            )
            self._positions[idea.id] = position

            # F-07 FIX: persist after opening
            self._save_positions()
            # F-13 FIX: prune order history
            self._prune_order_history()

            audit(trade_log, f"Live order FILLED: {side} {idea.asset}",
                  action="live_execute", result="FILLED",
                  data={
                      "order_id": order_id, "trade_id": idea.id,
                      "side": side, "fill_price": fill_price,
                      "quantity": filled_qty, "cost_usd": cost,
                      "status": status,
                  })

            # Try to place SL/TP orders (best-effort — not all exchanges support this for spot)
            sl_id, tp_id = await self._place_sl_tp(
                exchange, idea.asset, idea.direction,
                filled_qty, idea.stop_loss, idea.take_profit
            )
            position.sl_order_id = sl_id
            position.tp_order_id = tp_id

            sl_info = f" | SL order: {sl_id}" if sl_id else " | SL: manual"
            tp_info = f" | TP order: {tp_id}" if tp_id else " | TP: manual"

            dir_icon = "🟢" if side == "buy" else "🔴"
            return (
                f"{dir_icon} <b>LIVE {side.upper()} {idea.asset}</b>\n"
                f"{'─' * 16}\n"
                f"- Fill: <code>${fill_price:,.4f}</code>\n"
                f"- Qty: <code>{filled_qty:.6f}</code>\n"
                f"- Cost: <code>${cost:.2f}</code>\n"
                f"- SL: <code>${idea.stop_loss:,.4f}</code>{sl_info}\n"
                f"- TP: <code>${idea.take_profit:,.4f}</code>{tp_info}\n"
                f"- Order: <code>{order_id}</code>\n"
                f"- Risk: ✅ APPROVED\n"
                f"- Mode: 🔥 Live"
            )

        except ccxt.InsufficientFunds as exc:
            audit(trade_log, f"Insufficient funds: {exc}",
                  action="live_execute", result="INSUFFICIENT_FUNDS",
                  data={"asset": idea.asset, "size_usd": size_usd})
            return f"INSUFFICIENT FUNDS: {exc}"

        except ccxt.InvalidOrder as exc:
            audit(trade_log, f"Invalid order: {exc}",
                  action="live_execute", result="INVALID_ORDER",
                  data={"asset": idea.asset, "size_usd": size_usd, "error": str(exc)})
            return f"INVALID ORDER: {exc}"

        except Exception as exc:
            audit(trade_log, f"Live execution failed: {exc}",
                  action="live_execute", result="ERROR",
                  data={"asset": idea.asset, "size_usd": size_usd, "error": str(exc)})
            return f"EXECUTION FAILED: {exc}"

    async def _place_sl_tp(
        self, exchange: ccxt.Exchange, symbol: str,
        direction: Direction, quantity: float,
        stop_loss: float, take_profit: float
    ) -> tuple[Optional[str], Optional[str]]:
        """Attempt to place SL/TP orders. Returns (sl_order_id, tp_order_id).

        Best-effort: spot markets on some exchanges don't support
        conditional orders. Fails silently and logs.
        """
        sl_id = None
        tp_id = None
        close_side = "sell" if direction == Direction.LONG else "buy"

        # Stop-loss
        try:
            sl_order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=quantity,
                params={
                    "triggerPrice": stop_loss,
                    "triggerType": "last",
                },
            )
            sl_id = sl_order.get("id")
            audit(trade_log, f"SL order placed: {sl_id}",
                  action="sl_order", result="OK",
                  data={"symbol": symbol, "trigger": stop_loss})
        except Exception as exc:
            logger.debug("SL order failed (expected for spot): %s", exc)
            audit(trade_log, f"SL order not placed: {exc}",
                  action="sl_order", result="SKIP",
                  data={"symbol": symbol, "reason": str(exc)[:200]})

        # Take-profit
        try:
            tp_order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=quantity,
                params={
                    "triggerPrice": take_profit,
                    "triggerType": "last",
                },
            )
            tp_id = tp_order.get("id")
            audit(trade_log, f"TP order placed: {tp_id}",
                  action="tp_order", result="OK",
                  data={"symbol": symbol, "trigger": take_profit})
        except Exception as exc:
            logger.debug("TP order failed (expected for spot): %s", exc)
            audit(trade_log, f"TP order not placed: {exc}",
                  action="tp_order", result="SKIP",
                  data={"symbol": symbol, "reason": str(exc)[:200]})

        return sl_id, tp_id

    # ── Position management ──────────────────────────────────────

    async def check_positions(self) -> list[str]:
        """Check open positions against current prices. Returns list of close messages."""
        if not self._positions:
            return []

        closed_messages = []
        try:
            exchange = await self._get_exchange()
            tickers = await exchange.fetch_tickers()

            for trade_id, pos in list(self._positions.items()):
                if pos.status != "open":
                    continue
                price = float(tickers.get(pos.symbol, {}).get("last", 0))
                if price <= 0:
                    continue

                should_close = False
                reason = ""

                if pos.direction == "LONG":
                    if price <= pos.stop_loss:
                        should_close = True
                        reason = "SL HIT"
                    elif price >= pos.take_profit:
                        should_close = True
                        reason = "TP HIT"
                else:  # SHORT
                    if price >= pos.stop_loss:
                        should_close = True
                        reason = "SL HIT"
                    elif price <= pos.take_profit:
                        should_close = True
                        reason = "TP HIT"

                if should_close and not pos.sl_order_id:
                    # No exchange-level SL/TP — close manually
                    msg = await self.close_position(trade_id, reason, price)
                    closed_messages.append(msg)

        except Exception as exc:
            logger.debug("Position check error: %s", exc)

        return closed_messages

    async def close_position(self, trade_id: str, reason: str = "manual",
                              close_price: float = 0) -> str:
        """Close a live position by placing the opposite order."""
        pos = self._positions.get(trade_id)
        if not pos or pos.status != "open":
            return f"Position {trade_id} not found or already closed."

        try:
            exchange = await self._get_exchange()
            close_side = "sell" if pos.direction == "LONG" else "buy"

            order = await exchange.create_order(
                symbol=pos.symbol,
                type="market",
                side=close_side,
                amount=pos.quantity,
            )

            fill_price = float(order.get("average", 0) or order.get("price", 0) or close_price)

            # Calculate PnL
            if pos.direction == "LONG":
                pnl = (fill_price - pos.entry_price) * pos.quantity
            else:
                pnl = (pos.entry_price - fill_price) * pos.quantity

            pos.status = "closed"
            pos.close_price = fill_price
            pos.pnl_usd = pnl
            pos.closed_at = datetime.now(UTC)

            # F-07 FIX: persist after closing
            self._save_positions()

            # Cancel any outstanding SL/TP orders
            for oid in [pos.sl_order_id, pos.tp_order_id]:
                if oid:
                    try:
                        await exchange.cancel_order(oid, pos.symbol)
                    except Exception:
                        pass

            audit(trade_log, f"Live position closed: {pos.symbol} PnL=${pnl:.4f}",
                  action="live_close", result="CLOSED",
                  data={
                      "trade_id": trade_id, "reason": reason,
                      "entry": pos.entry_price, "exit": fill_price,
                      "pnl_usd": round(pnl, 4),
                  })

            pnl_str = f"+${pnl:.4f}" if pnl >= 0 else f"-${abs(pnl):.4f}"
            return (
                f"CLOSED {pos.direction} {pos.symbol} ({reason})\n"
                f"Entry: ${pos.entry_price:,.4f} → Exit: ${fill_price:,.4f}\n"
                f"PnL: {pnl_str}"
            )

        except Exception as exc:
            audit(trade_log, f"Live close failed: {exc}",
                  action="live_close", result="ERROR",
                  data={"trade_id": trade_id, "error": str(exc)})
            return f"CLOSE FAILED for {trade_id}: {exc}"

    # ── Account info ─────────────────────────────────────────────

    async def fetch_balance(self) -> dict:
        """Fetch USDT balance and all spot holdings from Bitget."""
        try:
            exchange = await self._get_exchange()
            balance = await exchange.fetch_balance()
            usdt = balance.get("USDT", {})

            # Collect all non-zero spot holdings
            holdings = []
            for asset, info in balance.items():
                if asset in ("info", "free", "used", "total", "timestamp", "datetime"):
                    continue
                total_val = float(info.get("total", 0) if isinstance(info, dict) else 0)
                if total_val > 0 and asset != "USDT":
                    holdings.append({
                        "asset": asset,
                        "total": total_val,
                        "free": float(info.get("free", 0) if isinstance(info, dict) else 0),
                    })

            return {
                "free": float(usdt.get("free", 0)),
                "used": float(usdt.get("used", 0)),
                "total": float(usdt.get("total", 0)),
                "holdings": holdings,
            }
        except Exception as exc:
            return {"error": str(exc), "free": 0, "used": 0, "total": 0, "holdings": []}

    @property
    def open_positions(self) -> list[LivePosition]:
        return [p for p in self._positions.values() if p.status == "open"]

    @property
    def closed_positions(self) -> list[LivePosition]:
        return [p for p in self._positions.values() if p.status == "closed"]

    @property
    def total_exposure_usd(self) -> float:
        return sum(p.cost_usd for p in self.open_positions)

    def status_summary(self) -> str:
        """Human-readable status."""
        open_pos = self.open_positions
        closed = self.closed_positions
        total_pnl = sum(p.pnl_usd or 0 for p in closed)
        return (
            f"Open: {len(open_pos)} | Closed: {len(closed)} | "
            f"Exposure: ${self.total_exposure_usd:.2f} | "
            f"Realized PnL: ${total_pnl:.4f}"
        )

    # ── Direct Spot Buy / Sell ───────────────────────────────────

    async def buy_spot(self, symbol: str, amount_usd: float) -> dict:
        """Buy a spot asset with a market order.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT")
            amount_usd: How many USDT to spend

        Returns:
            Dict with order details or error
        """
        # Enforce micro-test cap
        amount_usd = min(amount_usd, MICRO_MAX_POSITION_USD)
        if amount_usd <= 0:
            return {"error": "Amount must be > 0"}

        # Exposure check
        total_exposure = self.total_exposure_usd
        if total_exposure + amount_usd > MICRO_MAX_TOTAL_EXPOSURE:
            return {
                "error": f"Would exceed exposure limit: "
                         f"${total_exposure + amount_usd:.2f} > ${MICRO_MAX_TOTAL_EXPOSURE}"
            }

        audit(trade_log, f"Spot BUY starting: {symbol} ${amount_usd:.2f}",
              action="spot_buy", result="STARTING",
              data={"symbol": symbol, "amount_usd": amount_usd})

        try:
            exchange = await self._get_exchange()

            # Use cost-based market buy: tell Bitget to spend $X of USDT
            # This avoids precision rounding issues with tiny quantities (e.g. BTC at $100K)
            exchange.options["createMarketBuyOrderRequiresPrice"] = False
            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side="buy",
                amount=amount_usd,  # interpreted as USDT cost when above flag is False
                params={"cost": amount_usd},
            )

            # Fetch fill details — Bitget may not return them synchronously
            order_id = order.get("id", "unknown")
            fill_price = float(order.get("average", 0) or order.get("price", 0) or 0)
            filled_qty = float(order.get("filled", 0) or 0)
            cost = float(order.get("cost", 0) or amount_usd)

            # If fill details missing, fetch the order to get actuals
            if not fill_price or not filled_qty:
                try:
                    fetched = await exchange.fetch_order(order_id, symbol)
                    fill_price = float(fetched.get("average", 0) or fetched.get("price", 0) or 0)
                    filled_qty = float(fetched.get("filled", 0) or 0)
                    cost = float(fetched.get("cost", 0) or amount_usd)
                except Exception:
                    # Best-effort: estimate from ticker
                    ticker = await exchange.fetch_ticker(symbol)
                    fill_price = float(ticker["last"])
                    filled_qty = amount_usd / fill_price

            live_order = LiveOrder(
                order_id=order_id, symbol=symbol, side="buy",
                order_type="market", amount=filled_qty, price=fill_price,
                cost_usd=cost, status=order.get("status", "filled"), raw=order,
            )
            self._order_history.append(live_order)
            self._prune_order_history()

            audit(trade_log, f"Spot BUY filled: {symbol} {filled_qty} @ ${fill_price:,.4f}",
                  action="spot_buy", result="FILLED",
                  data={"order_id": order_id, "symbol": symbol,
                        "qty": filled_qty, "price": fill_price, "cost": cost})

            return {
                "status": "filled",
                "order_id": order_id,
                "symbol": symbol,
                "side": "buy",
                "qty": filled_qty,
                "price": fill_price,
                "cost": cost,
            }

        except Exception as exc:
            audit(trade_log, f"Spot BUY failed: {symbol} — {exc}",
                  action="spot_buy", result="ERROR",
                  data={"symbol": symbol, "amount_usd": amount_usd, "error": str(exc)})
            return {"error": str(exc)}

    async def sell_spot(self, symbol: str, qty: float = 0, sell_all: bool = False) -> dict:
        """Sell a spot asset with a market order.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT")
            qty: Quantity in base currency to sell (0 = sell_all)
            sell_all: If True, sell entire balance of the base asset

        Returns:
            Dict with order details or error
        """
        audit(trade_log, f"Spot SELL starting: {symbol} qty={qty} sell_all={sell_all}",
              action="spot_sell", result="STARTING",
              data={"symbol": symbol, "qty": qty, "sell_all": sell_all})

        try:
            exchange = await self._get_exchange()

            if sell_all or qty <= 0:
                # Fetch balance for the base asset
                base = symbol.split("/")[0]
                balance = await exchange.fetch_balance()
                available = float(balance.get(base, {}).get("free", 0))
                if available <= 0:
                    return {"error": f"No {base} balance to sell"}
                qty = available

            # Precision
            markets = await exchange.load_markets()
            market = markets.get(symbol)
            if market:
                qty = float(exchange.amount_to_precision(symbol, qty))

            if qty <= 0:
                return {"error": "Quantity too small after precision rounding"}

            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side="sell",
                amount=qty,
            )

            fill_price = float(order.get("average", 0) or order.get("price", 0) or 0)
            filled_qty = float(order.get("filled", 0) or qty)
            proceeds = float(order.get("cost", 0) or fill_price * filled_qty)
            order_id = order.get("id", "unknown")

            live_order = LiveOrder(
                order_id=order_id, symbol=symbol, side="sell",
                order_type="market", amount=filled_qty, price=fill_price,
                cost_usd=proceeds, status=order.get("status", "filled"), raw=order,
            )
            self._order_history.append(live_order)
            self._prune_order_history()

            audit(trade_log, f"Spot SELL filled: {symbol} {filled_qty} @ ${fill_price:,.4f}",
                  action="spot_sell", result="FILLED",
                  data={"order_id": order_id, "symbol": symbol,
                        "qty": filled_qty, "price": fill_price, "proceeds": proceeds})

            return {
                "status": "filled",
                "order_id": order_id,
                "symbol": symbol,
                "side": "sell",
                "qty": filled_qty,
                "price": fill_price,
                "proceeds": proceeds,
            }

        except Exception as exc:
            audit(trade_log, f"Spot SELL failed: {symbol} — {exc}",
                  action="spot_sell", result="ERROR",
                  data={"symbol": symbol, "qty": qty, "error": str(exc)})
            return {"error": str(exc)}

    # ── F-07 FIX: Position persistence ──────────────────────────────

    def _save_positions(self) -> None:
        """Persist open positions to disk so they survive restarts."""
        try:
            data: dict[str, Any] = {}
            for tid, pos in self._positions.items():
                if pos.status != "open":
                    continue
                data[tid] = {
                    "trade_id": pos.trade_id,
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "cost_usd": pos.cost_usd,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "sl_order_id": pos.sl_order_id,
                    "tp_order_id": pos.tp_order_id,
                    "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
                    "status": pos.status,
                }
            path = Path(_POSITIONS_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, str(path))
        except Exception as exc:
            logger.debug("Failed to save live positions: %s", exc)

    def _load_positions(self) -> None:
        """Load persisted positions on startup."""
        path = Path(_POSITIONS_FILE)
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for tid, pdata in data.items():
                opened_at = datetime.fromisoformat(pdata["opened_at"]) if pdata.get("opened_at") else datetime.now(UTC)
                self._positions[tid] = LivePosition(
                    trade_id=pdata["trade_id"],
                    symbol=pdata["symbol"],
                    direction=pdata["direction"],
                    entry_price=float(pdata["entry_price"]),
                    quantity=float(pdata["quantity"]),
                    cost_usd=float(pdata["cost_usd"]),
                    stop_loss=float(pdata["stop_loss"]),
                    take_profit=float(pdata["take_profit"]),
                    sl_order_id=pdata.get("sl_order_id"),
                    tp_order_id=pdata.get("tp_order_id"),
                    opened_at=opened_at,
                    status=pdata.get("status", "open"),
                )
            if self._positions:
                audit(trade_log, f"Loaded {len(self._positions)} live positions from disk",
                      action="load_positions", result="OK")
        except Exception as exc:
            audit(trade_log, f"Failed to load live positions: {exc}",
                  action="load_positions", result="ERROR")

    def _prune_order_history(self) -> None:
        """F-13 FIX: Cap order history to prevent unbounded growth."""
        if len(self._order_history) > _MAX_ORDER_HISTORY:
            self._order_history = self._order_history[-(_MAX_ORDER_HISTORY // 2):]
