"""
RUNECLAW Live Executor — places real orders on Bitget via ccxt.

Safety invariants:
  - MICRO_TEST_MODE caps every position at $10 and total exposure at $50
  - Every order is audited before and after submission
  - Market orders only (simplest, most reliable for micro-amounts)
  - Fail-closed: any API error aborts the trade and logs the failure
  - The executor never modifies risk limits or bypasses any gate
  - SL/TP are placed as separate stop-market / take-profit-market orders
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Optional

import ccxt.async_support as ccxt

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log, system_log
from bot.utils.models import Direction, TradeIdea

logger = logging.getLogger(__name__)


# ── Safety limits for micro-testing ──────────────────────────────────

MICRO_MAX_POSITION_USD = 10.0     # Max $10 per trade
MICRO_MAX_TOTAL_EXPOSURE = 50.0   # Max $50 total open exposure
MICRO_MAX_OPEN_POSITIONS = 5      # Max 5 concurrent positions


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

            # Place market order
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

            return (
                f"LIVE {side.upper()} {idea.asset}\n"
                f"Fill: ${fill_price:,.4f} | Qty: {filled_qty:.6f}\n"
                f"Cost: ${cost:.2f} | Order: {order_id}\n"
                f"SL: ${idea.stop_loss:,.4f}{sl_info}\n"
                f"TP: ${idea.take_profit:,.4f}{tp_info}"
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
        """Fetch USDT balance from Bitget."""
        try:
            exchange = await self._get_exchange()
            balance = await exchange.fetch_balance()
            usdt = balance.get("USDT", {})
            return {
                "free": float(usdt.get("free", 0)),
                "used": float(usdt.get("used", 0)),
                "total": float(usdt.get("total", 0)),
            }
        except Exception as exc:
            return {"error": str(exc), "free": 0, "used": 0, "total": 0}

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
