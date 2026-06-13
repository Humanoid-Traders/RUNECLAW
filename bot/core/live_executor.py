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
# F-14 FIX: Separate persistence for closed trades (survives restarts)
_CLOSED_TRADES_FILE = os.path.join(
    os.environ.get("RUNECLAW_STATE_DIR", "data"), "closed_trades.json"
)
_MAX_CLOSED_TRADES = 500  # Cap closed trade history
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
    leverage: int = 1      # leverage multiplier (1 = no leverage)
    is_spot: bool = False   # True if opened as spot fallback (no futures market)
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
        self._closed_trades: list[LivePosition] = []  # F-14: persisted closed trades
        self._order_history: list[LiveOrder] = []
        self._hedge_mode: Optional[bool] = None  # None=unknown, True=hedge, False=one-way
        # F-07 FIX: Load persisted positions on startup
        self._load_positions()
        # F-14 FIX: Load persisted closed trades on startup
        self._load_closed_trades()

    async def _get_exchange(self) -> ccxt.Exchange:
        """Get authenticated Bitget exchange instance."""
        if self._exchange is None:
            cfg = CONFIG.exchange
            if not cfg.api_key or not cfg.api_secret:
                raise RuntimeError(
                    "BITGET_API_KEY and BITGET_API_SECRET required for live trading. "
                    "Set them in .env and restart."
                )
            is_futures = cfg.trade_mode == "futures"
            self._exchange = ccxt.bitget({
                "apiKey": cfg.api_key,
                "secret": cfg.api_secret,
                "password": cfg.passphrase,
                "sandbox": cfg.sandbox,
                "timeout": 30000,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap" if is_futures else "spot",
                    "uta": True,  # Support Bitget Unified Trading Account
                },
            })
            # Set leverage and margin mode for futures
            if is_futures:
                logger.info("Futures mode: leverage=%dx, margin=%s",
                            cfg.default_leverage, cfg.margin_mode)
        return self._exchange

    async def _ensure_leverage(self, symbol: str) -> None:
        """Set leverage and margin mode for a symbol (futures only)."""
        cfg = CONFIG.exchange
        if cfg.trade_mode != "futures":
            return
        exchange = await self._get_exchange()
        try:
            await exchange.set_margin_mode(
                cfg.margin_mode, symbol,
                params={"productType": "USDT-FUTURES"})
        except Exception as exc:
            logger.debug("Margin mode set info for %s: %s", symbol, exc)
        try:
            await exchange.set_leverage(
                cfg.default_leverage, symbol,
                params={"productType": "USDT-FUTURES"})
        except Exception as exc:
            logger.debug("Leverage set info for %s: %s", symbol, exc)
        # Detect hold mode (one-way vs hedge) on first call
        if self._hedge_mode is None:
            await self._detect_hold_mode()

    async def _detect_hold_mode(self) -> None:
        """Detect Bitget account position hold mode (one-way vs hedge).

        One-way mode: tradeSide/posSide must NOT be sent.
        Hedge mode: tradeSide (v2) or posSide (v3/UTA) is required.

        Tries v2 API first (classic accounts), falls back to v3 settings
        endpoint for UTA accounts.
        """
        exchange = await self._get_exchange()

        # ── Attempt 1: v2 API (classic accounts) ──
        try:
            resp = await exchange.privateMixGetV2MixAccountAccount(
                {"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})
            data = resp.get("data", {})
            if isinstance(data, list) and data:
                data = data[0]
            hold_mode = data.get("holdMode", "") if isinstance(data, dict) else ""
            self._hedge_mode = (hold_mode == "double_hold")
            logger.info("Bitget position mode (v2): %s (hedge=%s)", hold_mode, self._hedge_mode)
            return
        except Exception as exc:
            err_str = str(exc)
            if "40085" not in err_str:
                logger.debug("Hold mode detection failed: %s, defaulting to one-way", exc)
                self._hedge_mode = False
                return
            logger.info("UTA account detected (40085), trying v3 settings endpoint")

        # ── Attempt 2: v3 /api/v3/account/settings (UTA accounts) ──
        try:
            import urllib.request as _urllib_req
            import urllib.parse as _urllib_parse
            import hmac as _hmac
            import hashlib as _hashlib
            import base64 as _base64
            import time as _time
            import json as _json

            cfg = CONFIG.exchange
            ts = str(int(_time.time() * 1000))
            path = "/api/v3/account/settings"
            pre_sign = ts + "GET" + path
            sig = _base64.b64encode(
                _hmac.new(cfg.api_secret.encode(), pre_sign.encode(), _hashlib.sha256).digest()
            ).decode()
            url = "https://api.bitget.com" + path
            req = _urllib_req.Request(url)
            req.add_header("ACCESS-KEY", cfg.api_key)
            req.add_header("ACCESS-SIGN", sig)
            req.add_header("ACCESS-TIMESTAMP", ts)
            req.add_header("ACCESS-PASSPHRASE", cfg.passphrase)
            req.add_header("Content-Type", "application/json")
            req.add_header("locale", "en-US")
            resp_raw = _urllib_req.urlopen(req, timeout=10)
            resp_data = _json.loads(resp_raw.read())

            if resp_data.get("code") == "00000":
                hold_mode = resp_data.get("data", {}).get("holdMode", "")
                self._hedge_mode = (hold_mode == "hedge_mode")
                logger.info("Bitget position mode (v3 settings): %s (hedge=%s)",
                            hold_mode, self._hedge_mode)
                return
        except Exception as exc2:
            logger.debug("v3 settings detection failed: %s", exc2)

        # Default to one-way (most common)
        self._hedge_mode = False
        logger.info("Hold mode detection exhausted, defaulting to one-way")

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
            is_futures = CONFIG.exchange.trade_mode == "futures"

            # Check if futures market exists for this symbol
            # Some tokens (e.g., SNEK) are spot-only on Bitget
            symbol = idea.asset
            if is_futures:
                markets = await exchange.load_markets()
                # ccxt uses "SYMBOL:USDT" for swap markets
                swap_symbol = symbol.replace("/USDT", "/USDT:USDT")
                has_futures = swap_symbol in markets or any(
                    m.get("swap") and m.get("symbol") == swap_symbol
                    for m in markets.values()
                    if isinstance(m, dict)
                )
                if not has_futures:
                    # Fallback to spot for this symbol
                    is_futures = False
                    logger.info("No futures market for %s, falling back to spot", symbol)
                    audit(trade_log, f"Futures unavailable for {symbol}, using spot",
                          action="live_execute", result="SPOT_FALLBACK",
                          data={"asset": symbol})

            # Set leverage for this symbol (futures only)
            if is_futures:
                await self._ensure_leverage(idea.asset)

            # Convert symbol for futures if needed
            symbol = idea.asset

            # For spot orders on a futures-configured exchange, we need a spot
            # exchange instance since the swap instance can't find spot markets
            spot_exchange = None
            if not is_futures and CONFIG.exchange.trade_mode == "futures":
                # We're falling back to spot but exchange is swap-mode
                cfg = CONFIG.exchange
                spot_exchange = ccxt.bitget({
                    "apiKey": cfg.api_key,
                    "secret": cfg.api_secret,
                    "password": cfg.passphrase,
                    "sandbox": cfg.sandbox,
                    "timeout": 30000,
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": "spot",
                        "uta": True,
                    },
                })
                active_exchange = spot_exchange
            else:
                active_exchange = exchange

            # Fetch current price to calculate quantity
            try:
                ticker = await active_exchange.fetch_ticker(symbol)
            except Exception:
                # Spot exchange may need markets loaded first
                await active_exchange.load_markets()
                ticker = await active_exchange.fetch_ticker(symbol)
            current_price = float(ticker["last"])

            # Calculate quantity
            # For futures with leverage: size_usd is the margin (collateral).
            # Notional exposure = margin * leverage, so qty = (size_usd * leverage) / price.
            leverage_mult = CONFIG.exchange.default_leverage if is_futures else 1
            quantity = (size_usd * leverage_mult) / current_price

            # Determine side
            side = "buy" if idea.direction == Direction.LONG else "sell"

            # Load markets for precision rounding
            markets = await active_exchange.load_markets()
            market = markets.get(symbol)
            if market:
                quantity = float(active_exchange.amount_to_precision(symbol, quantity))

            if quantity <= 0:
                if spot_exchange:
                    await spot_exchange.close()
                audit(trade_log, f"Quantity too small after precision: {symbol} ${size_usd}",
                      action="live_execute", result="QUANTITY_TOO_SMALL",
                      data={"asset": symbol, "size_usd": size_usd, "price": current_price})
                return f"BLOCKED: quantity too small after precision rounding for {symbol}"

            # Place market order
            if is_futures:
                # Futures: use USDT-FUTURES product type
                # tradeSide only required in hedge (double_hold) mode
                leverage = CONFIG.exchange.default_leverage

                # Pre-check: verify balance is accessible
                # UTA accounts pool all margin — try swap first, fall back to default
                try:
                    bal_free = 0.0
                    try:
                        fut_bal = await exchange.fetch_balance({"type": "swap"})
                        fut_usdt = fut_bal.get("USDT", {})
                        bal_free = float(fut_usdt.get("free", 0) if isinstance(fut_usdt, dict) else 0)
                    except Exception:
                        # UTA mode: fetch_balance without type returns unified balance
                        uni_bal = await exchange.fetch_balance()
                        uni_usdt = uni_bal.get("USDT", {})
                        bal_free = float(uni_usdt.get("free", 0) if isinstance(uni_usdt, dict) else 0)
                    logger.info("Balance pre-check: free=%.2f USDT for %s", bal_free, symbol)
                    if bal_free < size_usd / leverage:
                        audit(trade_log,
                              f"Low balance warning: ${bal_free:.2f} available, need ~${size_usd/leverage:.2f} margin for {symbol}",
                              action="live_execute", result="BALANCE_WARN",
                              data={"balance_free": bal_free, "margin_needed": size_usd/leverage})
                except Exception as exc:
                    logger.debug("Balance pre-check failed: %s", exc)

                futures_params = {
                    "productType": "USDT-FUTURES",
                    "marginMode": CONFIG.exchange.margin_mode,
                    "leverage": str(leverage),
                }
                if self._hedge_mode:
                    futures_params["tradeSide"] = "open"
                order = await exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=side,
                    amount=quantity,
                    params=futures_params,
                )
            elif side == "buy":
                # Spot BUY on Bitget UTA: use cost-based ordering
                active_exchange.options["createMarketBuyOrderRequiresPrice"] = False
                order = await active_exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=side,
                    amount=size_usd,
                    params={"cost": size_usd},
                )
            else:
                order = await active_exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=side,
                    amount=quantity,
                )

            # Clean up spot exchange if we created one
            if spot_exchange:
                await spot_exchange.close()

            # Parse result
            fill_price = float(order.get("average", 0) or order.get("price", 0) or current_price)
            filled_qty = float(order.get("filled", 0) or quantity)
            # cost_usd = margin (collateral), not notional. For futures, notional / leverage.
            raw_cost = float(order.get("cost", 0) or fill_price * filled_qty)
            if is_futures and leverage_mult > 1:
                cost = raw_cost / leverage_mult  # store margin, not notional
            else:
                cost = raw_cost
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
            leverage = CONFIG.exchange.default_leverage if is_futures else 1
            spot_fallback = not is_futures and CONFIG.exchange.trade_mode == "futures"
            position = LivePosition(
                trade_id=idea.id,
                symbol=idea.asset,
                direction=idea.direction.value,
                entry_price=fill_price,
                quantity=filled_qty,
                cost_usd=cost,
                stop_loss=idea.stop_loss,
                take_profit=idea.take_profit,
                leverage=leverage,
                is_spot=spot_fallback,
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

            lev_info = f" | {leverage}x" if leverage > 1 else ""
            mode_label = "FUTURES" if is_futures else "SPOT"
            dir_icon = "🟢" if side == "buy" else "🔴"
            return (
                f"{dir_icon} <b>LIVE {side.upper()} {idea.asset}</b> ({mode_label}{lev_info})\n"
                f"{'─' * 16}\n"
                f"- Fill: <code>${fill_price:,.4f}</code>\n"
                f"- Qty: <code>{filled_qty:.6f}</code>\n"
                f"- Cost: <code>${cost:.2f}</code>\n"
                f"- Notional: <code>${fill_price * filled_qty:.2f}</code>\n"
                f"- Leverage: <code>{leverage}x</code>\n"
                f"- SL: <code>${idea.stop_loss:,.4f}</code>{sl_info}\n"
                f"- TP: <code>${idea.take_profit:,.4f}</code>{tp_info}\n"
                f"- Order: <code>{order_id}</code>\n"
                f"- Risk: ✅ APPROVED\n"
                f"- Mode: 🔥 Live {mode_label}"
            )

        except ccxt.InsufficientFunds as exc:
            audit(trade_log, f"Insufficient funds: {exc}",
                  action="live_execute", result="INSUFFICIENT_FUNDS",
                  data={"asset": idea.asset, "size_usd": size_usd,
                        "is_futures": CONFIG.exchange.trade_mode == "futures"})
            hint = ""
            if CONFIG.exchange.trade_mode == "futures":
                hint = ("\n\n💡 <i>Tip: Your Bitget UTA may need funds transferred "
                        "to the futures account. Check your Bitget app → Assets → Transfer.</i>")
            return f"INSUFFICIENT FUNDS: {exc}{hint}"

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

        For UTA futures accounts: uses Bitget v3 REST API directly because
        ccxt's triggerPrice param executes immediately as a market order in
        UTA mode instead of creating a pending trigger order.

        For non-UTA futures: falls back to ccxt trigger orders.
        For spot: best-effort (may not be supported).
        """
        sl_id = None
        tp_id = None
        close_side = "sell" if direction == Direction.LONG else "buy"
        is_futures = CONFIG.exchange.trade_mode == "futures"

        if not is_futures:
            # Spot: skip SL/TP (not reliably supported)
            return sl_id, tp_id

        # Detect if UTA mode by checking if v2 API would fail
        # We already know from _detect_hold_mode if this is UTA
        use_v3 = False
        try:
            await exchange.privateMixGetV2MixAccountAccount(
                {"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})
        except Exception as exc:
            if "40085" in str(exc):
                use_v3 = True

        if use_v3:
            # UTA mode: place SL/TP via Bitget v3 REST API directly
            # Get tick size from exchange markets for proper price precision
            price_precision = None
            try:
                if not exchange.markets:
                    await exchange.load_markets()
                mkt = exchange.markets.get(symbol)
                if mkt and mkt.get("precision", {}).get("price") is not None:
                    price_precision = mkt["precision"]["price"]
            except Exception:
                pass
            sl_id, tp_id = await self._place_sl_tp_v3(
                symbol, direction, quantity, stop_loss, take_profit,
                price_precision=price_precision,
            )
        else:
            # Classic mode: use ccxt trigger orders
            extra_params = {"productType": "USDT-FUTURES"}
            if self._hedge_mode:
                extra_params["tradeSide"] = "close"

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
                        **extra_params,
                    },
                )
                sl_id = sl_order.get("id")
                audit(trade_log, f"SL order placed: {sl_id}",
                      action="sl_order", result="OK",
                      data={"symbol": symbol, "trigger": stop_loss, "futures": True})
            except Exception as exc:
                logger.warning("SL order failed for %s: %s", symbol, exc)
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
                        **extra_params,
                    },
                )
                tp_id = tp_order.get("id")
                audit(trade_log, f"TP order placed: {tp_id}",
                      action="tp_order", result="OK",
                      data={"symbol": symbol, "trigger": take_profit, "futures": True})
            except Exception as exc:
                logger.warning("TP order failed for %s: %s", symbol, exc)
                audit(trade_log, f"TP order not placed: {exc}",
                      action="tp_order", result="SKIP",
                      data={"symbol": symbol, "reason": str(exc)[:200]})

        return sl_id, tp_id

    async def _place_sl_tp_v3(
        self, symbol: str, direction: Direction, quantity: float,
        stop_loss: float, take_profit: float,
        price_precision: object = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Place SL/TP via Bitget v3 REST API for UTA accounts.

        Uses /api/v3/trade/place-strategy-order which creates pending
        TP/SL orders attached to the position (not immediate market orders).
        """
        import urllib.request as _urllib_req
        import hmac as _hmac
        import hashlib as _hashlib
        import base64 as _base64
        import time as _time
        import json as _json

        cfg = CONFIG.exchange
        sl_id = None
        tp_id = None

        # Strip "/USDT" from ccxt symbol format to get Bitget symbol
        bitget_symbol = symbol.replace("/USDT", "USDT").replace(":USDT", "")

        # In one-way mode, posSide should be "long" for long positions
        pos_side = "long" if direction == Direction.LONG else "short"

        def _v3_post(path: str, body_dict: dict) -> dict:
            body = _json.dumps(body_dict)
            ts = str(int(_time.time() * 1000))
            pre_sign = ts + "POST" + path + body
            sig = _base64.b64encode(
                _hmac.new(cfg.api_secret.encode(), pre_sign.encode(), _hashlib.sha256).digest()
            ).decode()
            url = "https://api.bitget.com" + path
            req = _urllib_req.Request(url, data=body.encode(), method="POST")
            req.add_header("ACCESS-KEY", cfg.api_key)
            req.add_header("ACCESS-SIGN", sig)
            req.add_header("ACCESS-TIMESTAMP", ts)
            req.add_header("ACCESS-PASSPHRASE", cfg.passphrase)
            req.add_header("Content-Type", "application/json")
            req.add_header("locale", "en-US")
            try:
                resp = _urllib_req.urlopen(req, timeout=10)
                return _json.loads(resp.read())
            except Exception as e:
                if hasattr(e, 'read'):
                    return _json.loads(e.read().decode())
                return {"code": "ERROR", "msg": str(e)}

        # Round SL/TP prices to the symbol's tick precision.
        # Bitget ccxt precision is typically the number of decimal places.
        def _round_price(price: float) -> str:
            """Round price to exchange-allowed precision."""
            if price_precision is not None:
                # ccxt returns precision as decimal places (int) for Bitget
                if isinstance(price_precision, int):
                    dp = price_precision
                elif isinstance(price_precision, float) and price_precision < 1:
                    # tick-size format (e.g. 0.0001 → 4 decimals)
                    import math
                    dp = max(0, -int(math.floor(math.log10(price_precision))))
                else:
                    dp = int(price_precision)
                return f"{price:.{dp}f}"
            # Fallback: conservative rounding by magnitude
            if price >= 1000:
                return f"{price:.1f}"
            elif price >= 10:
                return f"{price:.2f}"
            elif price >= 1:
                return f"{price:.3f}"
            elif price >= 0.1:
                return f"{price:.4f}"
            elif price >= 0.01:
                return f"{price:.5f}"
            elif price >= 0.001:
                return f"{price:.6f}"
            else:
                return f"{price:.8f}"

        # Place combined TP/SL strategy order
        try:
            result = _v3_post("/api/v3/trade/place-strategy-order", {
                "category": "USDT-FUTURES",
                "symbol": bitget_symbol,
                "posSide": pos_side,
                "takeProfit": _round_price(take_profit),
                "stopLoss": _round_price(stop_loss),
                "tpOrderType": "market",
                "slOrderType": "market",
            })

            if result.get("code") == "00000":
                data = result.get("data", {})
                sl_id = data.get("slOrderId") or "v3-strategy"
                tp_id = data.get("tpOrderId") or "v3-strategy"
                audit(trade_log, f"v3 SL/TP strategy order placed: SL={sl_id} TP={tp_id}",
                      action="sl_tp_v3", result="OK",
                      data={"symbol": bitget_symbol, "sl": stop_loss, "tp": take_profit})
            else:
                error_msg = result.get("msg", str(result))
                logger.warning("v3 strategy order failed: %s", error_msg)
                audit(trade_log, f"v3 SL/TP failed: {error_msg}",
                      action="sl_tp_v3", result="SKIP",
                      data={"symbol": bitget_symbol, "response": str(result)[:300]})
        except Exception as exc:
            logger.warning("v3 SL/TP placement error for %s: %s", bitget_symbol, exc)
            audit(trade_log, f"v3 SL/TP error: {exc}",
                  action="sl_tp_v3", result="ERROR",
                  data={"symbol": bitget_symbol, "error": str(exc)[:200]})

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

            # Determine if this position is futures or spot based on is_spot flag
            # (leverage alone is unreliable: spot fallback has leverage=1 but is_spot=True)
            is_futures_pos = not getattr(pos, "is_spot", False) and (pos.leverage or 1) > 1

            if is_futures_pos:
                close_params = {"productType": "USDT-FUTURES"}
                if self._hedge_mode:
                    close_params["tradeSide"] = "close"
                order = await exchange.create_order(
                    symbol=pos.symbol,
                    type="market",
                    side=close_side,
                    amount=pos.quantity,
                    params=close_params,
                )
            else:
                # Spot close on UTA — use a dedicated spot exchange instance
                # The main exchange is defaultType=swap and can't route spot
                # orders reliably on Bitget UTA. Create a one-off spot instance.
                cfg = CONFIG.exchange
                spot_exchange = ccxt.bitget({
                    "apiKey": cfg.api_key,
                    "secret": cfg.api_secret,
                    "password": cfg.passphrase,
                    "sandbox": cfg.sandbox,
                    "timeout": 30000,
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": "spot",
                        "uta": True,
                    },
                })
                try:
                    await spot_exchange.load_markets()
                    # Use sell_all approach: fetch actual balance for precision
                    base = pos.symbol.split("/")[0]
                    balance = await spot_exchange.fetch_balance()
                    available = float(balance.get(base, {}).get("free", 0))
                    sell_qty = available if available > 0 else pos.quantity
                    # Apply exchange precision
                    mkt = spot_exchange.markets.get(pos.symbol)
                    if mkt:
                        sell_qty = float(spot_exchange.amount_to_precision(
                            pos.symbol, sell_qty))
                    order = await spot_exchange.create_order(
                        symbol=pos.symbol,
                        type="market",
                        side="sell",
                        amount=sell_qty,
                    )
                finally:
                    await spot_exchange.close()

            # Extract fill price — Bitget spot responses sometimes omit average/price
            fill_price = float(order.get("average", 0) or order.get("price", 0) or 0)
            if fill_price == 0:
                # Derive from cost/filled (proceeds / qty sold)
                cost_val = float(order.get("cost", 0) or 0)
                filled_val = float(order.get("filled", 0) or 0)
                if cost_val > 0 and filled_val > 0:
                    fill_price = cost_val / filled_val
            if fill_price == 0:
                # Last resort: fetch ticker for current price
                try:
                    main_exchange = await self._get_exchange()
                    ticker = await main_exchange.fetch_ticker(pos.symbol)
                    fill_price = float(ticker.get("last", 0) or 0)
                except Exception:
                    pass
            if fill_price == 0:
                fill_price = pos.entry_price  # absolute fallback — no phantom PnL

            # Calculate PnL
            if pos.direction == "LONG":
                pnl = (fill_price - pos.entry_price) * pos.quantity
            else:
                pnl = (pos.entry_price - fill_price) * pos.quantity

            pos.status = "closed"
            pos.close_price = fill_price
            pos.pnl_usd = pnl
            pos.closed_at = datetime.now(UTC)

            # F-07 FIX: persist after closing (removes from open positions file)
            self._save_positions()
            # F-14 FIX: persist closed trade to closed_trades.json
            self._append_closed_trade(pos)

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
        """All closed trades: in-memory + persisted from disk."""
        in_mem = [p for p in self._positions.values() if p.status == "closed"]
        # Merge: persisted closed trades + any in-memory closures not yet persisted
        seen_ids = {p.trade_id for p in self._closed_trades}
        merged = list(self._closed_trades)
        for p in in_mem:
            if p.trade_id not in seen_ids:
                merged.append(p)
        return merged

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
                    "leverage": pos.leverage,
                    "is_spot": pos.is_spot,
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
                    leverage=int(pdata.get("leverage", 1)),
                    is_spot=bool(pdata.get("is_spot", False)),
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

    # ── F-14 FIX: Closed trades persistence ───────────────────────

    def _append_closed_trade(self, pos: LivePosition) -> None:
        """Append a closed trade to the persisted closed trades file."""
        self._closed_trades.append(pos)
        # Cap to prevent unbounded growth
        if len(self._closed_trades) > _MAX_CLOSED_TRADES:
            self._closed_trades = self._closed_trades[-_MAX_CLOSED_TRADES:]
        self._save_closed_trades()

    def _save_closed_trades(self) -> None:
        """Persist all closed trades to disk."""
        try:
            data = []
            for pos in self._closed_trades:
                data.append({
                    "trade_id": pos.trade_id,
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "cost_usd": pos.cost_usd,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "leverage": pos.leverage,
                    "close_price": pos.close_price,
                    "pnl_usd": pos.pnl_usd,
                    "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
                    "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
                    "status": "closed",
                })
            path = Path(_CLOSED_TRADES_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, str(path))
        except Exception as exc:
            logger.debug("Failed to save closed trades: %s", exc)

    def _load_closed_trades(self) -> None:
        """Load persisted closed trades on startup."""
        path = Path(_CLOSED_TRADES_FILE)
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for item in data:
                opened_at = datetime.fromisoformat(item["opened_at"]) if item.get("opened_at") else datetime.now(UTC)
                closed_at = datetime.fromisoformat(item["closed_at"]) if item.get("closed_at") else datetime.now(UTC)
                pos = LivePosition(
                    trade_id=item["trade_id"],
                    symbol=item["symbol"],
                    direction=item["direction"],
                    entry_price=float(item["entry_price"]),
                    quantity=float(item["quantity"]),
                    cost_usd=float(item["cost_usd"]),
                    stop_loss=float(item.get("stop_loss", 0)),
                    take_profit=float(item.get("take_profit", 0)),
                    leverage=int(item.get("leverage", 1)),
                    close_price=float(item.get("close_price", 0)),
                    pnl_usd=float(item.get("pnl_usd", 0)),
                    opened_at=opened_at,
                    closed_at=closed_at,
                    status="closed",
                )
                self._closed_trades.append(pos)
            if self._closed_trades:
                total_pnl = sum(p.pnl_usd or 0 for p in self._closed_trades)
                audit(trade_log,
                      f"Loaded {len(self._closed_trades)} closed trades from disk (total PnL: ${total_pnl:.4f})",
                      action="load_closed_trades", result="OK")
        except Exception as exc:
            audit(trade_log, f"Failed to load closed trades: {exc}",
                  action="load_closed_trades", result="ERROR")

    # ── Exchange reconciliation ───────────────────────────────────

    async def reconcile_positions(self) -> list[str]:
        """Check tracked open positions against exchange. Close any that no longer exist.

        This catches positions closed by exchange-side SL/TP triggers that the bot
        didn't process (e.g., during downtime or missed webhook).
        Returns list of reconciliation messages.
        """
        open_pos = self.open_positions
        if not open_pos:
            return []

        messages = []
        try:
            exchange = await self._get_exchange()

            for pos in open_pos:
                try:
                    # Check if position still exists on exchange
                    ccxt_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
                    positions = await exchange.fetch_positions([ccxt_symbol])
                    has_position = any(
                        float(p.get("contracts", 0)) > 0 for p in positions
                    )

                    if not has_position:
                        # Position no longer on exchange — closed by SL/TP or liquidation
                        # Try to get the last trade price for PnL calculation
                        try:
                            ticker = await exchange.fetch_ticker(ccxt_symbol)
                            close_price = float(ticker.get("last", 0) or 0)
                        except Exception:
                            close_price = 0

                        if close_price <= 0:
                            close_price = pos.entry_price  # fallback

                        # Estimate PnL based on SL/TP proximity
                        if pos.direction == "LONG":
                            dist_tp = abs(close_price - pos.take_profit) if pos.take_profit else float("inf")
                            dist_sl = abs(close_price - pos.stop_loss) if pos.stop_loss else float("inf")
                            if dist_tp < dist_sl and pos.take_profit:
                                est_exit = pos.take_profit
                                reason = "TP HIT (exchange)"
                            elif pos.stop_loss:
                                est_exit = pos.stop_loss
                                reason = "SL HIT (exchange)"
                            else:
                                est_exit = close_price
                                reason = "closed (exchange)"
                            pnl = (est_exit - pos.entry_price) * pos.quantity
                        else:  # SHORT
                            dist_tp = abs(close_price - pos.take_profit) if pos.take_profit else float("inf")
                            dist_sl = abs(close_price - pos.stop_loss) if pos.stop_loss else float("inf")
                            if dist_tp < dist_sl and pos.take_profit:
                                est_exit = pos.take_profit
                                reason = "TP HIT (exchange)"
                            elif pos.stop_loss:
                                est_exit = pos.stop_loss
                                reason = "SL HIT (exchange)"
                            else:
                                est_exit = close_price
                                reason = "closed (exchange)"
                            pnl = (pos.entry_price - est_exit) * pos.quantity

                        pos.status = "closed"
                        pos.close_price = est_exit
                        pos.pnl_usd = pnl
                        pos.closed_at = datetime.now(UTC)

                        self._save_positions()
                        self._append_closed_trade(pos)

                        pnl_str = f"+${pnl:.4f}" if pnl >= 0 else f"-${abs(pnl):.4f}"
                        msg = (
                            f"RECONCILED {pos.direction} {pos.symbol} ({reason})\n"
                            f"Entry: ${pos.entry_price:,.4f} -> Exit: ~${est_exit:,.4f}\n"
                            f"PnL: {pnl_str}"
                        )
                        messages.append(msg)

                        audit(trade_log,
                              f"Position reconciled (closed on exchange): {pos.symbol} PnL=${pnl:.4f}",
                              action="reconcile_close", result="CLOSED",
                              data={
                                  "trade_id": pos.trade_id, "reason": reason,
                                  "entry": pos.entry_price, "exit": est_exit,
                                  "pnl_usd": round(pnl, 4),
                              })

                except Exception as exc:
                    logger.debug("Reconciliation error for %s: %s", pos.trade_id, exc)

        except Exception as exc:
            logger.debug("Reconciliation error: %s", exc)

        return messages

    def _prune_order_history(self) -> None:
        """F-13 FIX: Cap order history to prevent unbounded growth."""
        if len(self._order_history) > _MAX_ORDER_HISTORY:
            self._order_history = self._order_history[-(_MAX_ORDER_HISTORY // 2):]
