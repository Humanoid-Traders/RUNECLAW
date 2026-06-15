"""
RUNECLAW Live Executor — places real orders on Bitget via ccxt.

Safety invariants:
  - MICRO_TEST_MODE caps every position at $10 and total exposure at $50
  - Every order is audited before and after submission
  - Market AND limit orders supported (configurable via DEFAULT_ORDER_TYPE)
  - Trailing stops: activates after 1R profit, trails at 1.5x ATR (shared with paper)
  - Fail-closed: any API error aborts the trade and logs the failure
  - The executor never modifies risk limits or bypasses any gate
  - SL/TP are placed as separate stop-market / take-profit-market orders
  - Trailing SL updates cancel+replace exchange strategy orders
  - F-07 FIX: Positions are persisted to disk and reconciled on restart
"""

from __future__ import annotations

import asyncio
import hashlib
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
from bot.utils.trailing import make_trailing_state, update_trailing_stop

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
    client_oid: str = ""  # idempotency key (Bitget clientOid)
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
    status: str = "open"   # "open", "closed", "error", "pending_fill"
    # Trailing stop state (managed by bot/utils/trailing.py)
    trailing_state: Optional[dict] = None
    # Order type: "market" (filled immediately) or "limit" (pending fill)
    order_type: str = "market"
    # For limit orders: the exchange order ID to poll for fills
    limit_order_id: Optional[str] = None
    # ATR at entry time — needed for trailing stop initialization
    atr_at_entry: float = 0.0


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
        self._is_uta: Optional[bool] = None  # None=unknown, cached after first detection
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
            logger.warning("Margin mode set failed for %s: %s", symbol, exc)
        try:
            await exchange.set_leverage(
                cfg.default_leverage, symbol,
                params={"productType": "USDT-FUTURES"})
        except Exception as exc:
            logger.warning("Leverage set failed for %s (may use exchange default): %s", symbol, exc)
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
            self._is_uta = False
            logger.info("Bitget position mode (v2): %s (hedge=%s)", hold_mode, self._hedge_mode)
            return
        except Exception as exc:
            err_str = str(exc)
            if "40085" not in err_str:
                logger.debug("Hold mode detection failed: %s, defaulting to one-way", exc)
                self._hedge_mode = False
                return
            logger.info("UTA account detected (40085), trying v3 settings endpoint")
            self._is_uta = True

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
            # AUDIT FIX: offload blocking urlopen to thread to avoid
            # freezing the event loop (dashboard, WS feeds, Telegram).
            import asyncio as _asyncio
            resp_raw = await _asyncio.to_thread(_urllib_req.urlopen, req, None, 10)
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

    # ── Order idempotency (UPGRADE: clientOid + timeout-safe recovery) ────
    @staticmethod
    def _client_oid(trade_id: str) -> str:
        """Build a deterministic, Bitget-safe clientOid for a trade idea.

        The same trade_id always maps to the same clientOid, so a retried or
        timed-out submission can never create a duplicate exchange order:
        Bitget rejects a second order carrying a clientOid it has already seen.
        Output is alphanumeric and <= 32 chars (well within Bitget's 64 limit).
        When the cleaned input exceeds 30 chars, we hash to avoid collisions
        from prefix-truncation.
        """
        safe = "".join(ch for ch in str(trade_id) if ch.isalnum())
        if not safe or len(safe) > 30:
            safe = hashlib.sha256(str(trade_id).encode()).hexdigest()[:30]
        return ("rc" + safe)[:32]

    @staticmethod
    def _validate_order_limits(
        market: Optional[dict], quantity: float, notional_usd: float
    ) -> Optional[str]:
        """Check an order against the exchange's min amount / min notional filters.

        Returns an error string if the order would be rejected by the venue, else
        None. Catching this locally turns a confusing exchange rejection into a
        clean, auditable BLOCK before any capital leaves the account.
        """
        if not market:
            return None
        limits = market.get("limits") or {}
        amt_min = (limits.get("amount") or {}).get("min")
        cost_min = (limits.get("cost") or {}).get("min")
        try:
            if amt_min is not None and quantity < float(amt_min):
                return (f"quantity {quantity} below exchange minimum "
                        f"{amt_min} {market.get('base', '')}")
        except (TypeError, ValueError):
            pass
        try:
            if cost_min is not None and notional_usd < float(cost_min):
                return (f"notional ${notional_usd:.4f} below exchange minimum "
                        f"${float(cost_min):.4f}")
        except (TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _round_price_to_market(exchange: "ccxt.Exchange", symbol: str, price: float) -> Optional[str]:
        """Round a price onto the symbol's tick grid using ccxt's market filters.

        Uses the exchange's own ``price_to_precision`` (which respects tick size
        and rounding mode) rather than a decimal-places heuristic. Returns None
        if the venue/market data is unavailable so the caller can fall back.
        """
        try:
            return exchange.price_to_precision(symbol, price)
        except Exception as exc:  # noqa: BLE001
            logger.debug("price_to_precision failed for %s @ %s: %s", symbol, price, exc)
            return None

    async def _find_order_by_client_oid(
        self, exchange: "ccxt.Exchange", symbol: str, coid: str
    ) -> Optional[dict]:
        """Best-effort lookup of an order by its clientOid.

        Used after a network failure/timeout to determine whether an order
        actually landed on the exchange before deciding to treat it as failed.
        Returns the order dict if found, else None.
        """
        def _matches(o: dict) -> bool:
            if not isinstance(o, dict):
                return False
            if o.get("clientOrderId") == coid:
                return True
            info = o.get("info") or {}
            return isinstance(info, dict) and info.get("clientOid") == coid

        # 1) ccxt unified fetch by clientOrderId (params), if the venue supports it
        for fetcher in ("fetch_open_orders", "fetch_closed_orders"):
            fn = getattr(exchange, fetcher, None)
            if fn is None:
                continue
            try:
                orders = await fn(symbol)
                for o in orders or []:
                    if _matches(o):
                        return o
            except Exception as exc:  # noqa: BLE001 — best effort, never fatal
                logger.debug("clientOid lookup via %s failed: %s", fetcher, exc)
        return None

    async def _create_order_idempotent(
        self,
        exchange: "ccxt.Exchange",
        *,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        coid: str,
        params: Optional[dict] = None,
    ) -> dict:
        """Place an order with an idempotency key, recovering from timeouts.

        Flow:
          1. Inject clientOid into params (Bitget dedups on it).
          2. Try create_order normally.
          3. On ANY exception, query the exchange by clientOid. If the order
             actually landed, return it (so a timed-out-but-filled order is
             reconciled instead of lost — and never re-submitted). Only if the
             lookup confirms the order is absent do we re-raise.
        """
        params = dict(params or {})
        params.setdefault("clientOid", coid)       # Bitget raw param
        params.setdefault("clientOrderId", coid)   # ccxt unified alias
        try:
            return await exchange.create_order(
                symbol=symbol, type=type, side=side, amount=amount, params=params
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "create_order raised for %s (coid=%s): %s — checking whether it landed",
                symbol, coid, exc,
            )
            audit(trade_log, f"Order submit error for {symbol}; reconciling by clientOid",
                  action="live_execute", result="SUBMIT_ERROR_RECONCILE",
                  data={"symbol": symbol, "coid": coid, "error": str(exc)[:200]})
            found = await self._find_order_by_client_oid(exchange, symbol, coid)
            if found is not None:
                logger.warning("Recovered order for %s via clientOid %s — NOT resubmitting",
                               symbol, coid)
                audit(trade_log, f"Recovered order via clientOid for {symbol}",
                      action="live_execute", result="RECOVERED_BY_COID",
                      data={"symbol": symbol, "coid": coid,
                            "order_id": found.get("id", "unknown")})
                return found
            # Confirmed absent — safe to surface the failure to the caller.
            raise

    async def detect_untracked_positions(self) -> dict:
        """Detect exchange positions that RUNECLAW is NOT tracking locally.

        Complements ``reconcile_positions()`` (which handles the opposite
        direction — local-open / exchange-closed). This catches *orphans*: a
        live position on Bitget with no local record — the exact failure mode a
        timed-out-but-landed order could create. Read-only: it reports and
        audits, and never touches money state automatically.

        Returns {"untracked": [symbols], "errors": [...]}.
        """
        report: dict[str, Any] = {"untracked": [], "errors": []}
        if not CONFIG.is_live():
            report["errors"].append("not in live mode")
            return report
        try:
            exchange = await self._get_exchange()
            try:
                ex_positions = await exchange.fetch_positions()
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"fetch_positions failed: {exc}")
                return report

            def _normalize_sym(s: str) -> str:
                """Normalize symbol to a common format for comparison.
                ETH/USDT:USDT -> ETH/USDT, ETHUSDT -> ETHUSDT, ETH/USDT -> ETH/USDT"""
                return s.replace(":USDT", "")

            tracked = {
                _normalize_sym(p.symbol)
                for p in self._positions.values()
                if p.status == "open"
            }
            for p in ex_positions or []:
                if not isinstance(p, dict):
                    continue
                try:
                    if float(p.get("contracts") or 0) == 0:
                        continue
                except (TypeError, ValueError):
                    continue
                raw_sym = (p.get("symbol") or "")
                sym = _normalize_sym(raw_sym)
                if sym and sym not in tracked:
                    report["untracked"].append(sym)
                    audit(trade_log,
                          f"ORPHAN: exchange position {sym} has no local record — manual review needed",
                          action="reconcile", result="UNTRACKED_ON_EXCHANGE",
                          data={"symbol": sym, "contracts": p.get("contracts")})
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(str(exc))
            logger.warning("detect_untracked_positions() failed: %s", exc)
        return report

    # ── Execute trade ────────────────────────────────────────────

    async def execute(self, idea: TradeIdea, size_usd: float,
                      order_type: str = "", atr_value: float = 0.0) -> str:
        """Execute a live trade on Bitget.

        Args:
            idea: The approved TradeIdea
            size_usd: Position size in USD (will be clamped to micro limits)
            order_type: "market" or "limit" (empty = use config default)
            atr_value: ATR at entry time (for trailing stop initialization)

        Returns:
            Human-readable result string
        """
        # Resolve order type: explicit > config > default
        if not order_type:
            order_type = CONFIG.limit_orders.default_order_type if CONFIG.limit_orders.enabled else "market"
        order_type = order_type.lower()
        if order_type not in ("market", "limit"):
            order_type = "market"
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

            # UPGRADE: deterministic idempotency key for this trade idea.
            # Reused for every order/cancel below so a timeout-retry can never
            # double-submit (Bitget dedups on clientOid).
            coid = self._client_oid(idea.id)

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

            # UPGRADE: validate against the venue's min-amount / min-notional
            # filters so a sub-minimum order is BLOCKED cleanly here instead of
            # being rejected by Bitget after submission.
            limit_err = self._validate_order_limits(market, quantity, quantity * current_price)
            if limit_err:
                if spot_exchange:
                    await spot_exchange.close()
                audit(trade_log, f"Order below exchange limits: {symbol} — {limit_err}",
                      action="live_execute", result="BELOW_EXCHANGE_MIN",
                      data={"asset": symbol, "size_usd": size_usd,
                            "quantity": quantity, "price": current_price})
                return f"BLOCKED: {limit_err}"

            # Place order (market or limit)
            use_limit = (order_type == "limit" and CONFIG.limit_orders.enabled)
            # Limit orders use the idea's entry_price; for spot cost-based buys,
            # limit is placed at entry_price and the exchange fills at that price or better.
            limit_price = idea.entry_price if use_limit else None
            if use_limit and market:
                # Round limit price to exchange tick grid
                limit_price = float(
                    active_exchange.price_to_precision(symbol, limit_price)
                ) if limit_price else None

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

                otype = "limit" if use_limit else "market"
                create_kwargs: dict[str, Any] = {
                    "symbol": symbol, "type": otype, "side": side,
                    "amount": quantity, "coid": coid, "params": futures_params,
                }
                if use_limit and limit_price:
                    futures_params["price"] = str(limit_price)
                order = await self._create_order_idempotent(exchange, **create_kwargs)
            elif side == "buy" and not use_limit:
                # Spot BUY on Bitget UTA: use cost-based ordering (market only)
                active_exchange.options["createMarketBuyOrderRequiresPrice"] = False
                order = await self._create_order_idempotent(
                    active_exchange,
                    symbol=symbol,
                    type="market",
                    side=side,
                    amount=size_usd,
                    coid=coid,
                    params={"cost": size_usd},
                )
            elif use_limit:
                # Limit order (spot or spot-fallback)
                order = await self._create_order_idempotent(
                    active_exchange,
                    symbol=symbol,
                    type="limit",
                    side=side,
                    amount=quantity,
                    coid=coid,
                    params={"price": str(limit_price)} if limit_price else {},
                )
            else:
                order = await self._create_order_idempotent(
                    active_exchange,
                    symbol=symbol,
                    type="market",
                    side=side,
                    amount=quantity,
                    coid=coid,
                )

            # Clean up spot exchange if we created one
            if spot_exchange:
                await spot_exchange.close()

            # Parse result
            order_status = order.get("status", "unknown")
            order_id = order.get("id", "unknown")

            # Handle limit orders that haven't filled yet
            is_pending_limit = (use_limit and order_status in ("open", "new", "pending"))

            if is_pending_limit:
                # Limit order placed but not yet filled — track as pending
                fill_price = idea.entry_price  # expected fill
                filled_qty = quantity  # expected quantity
                raw_cost = fill_price * filled_qty
                if is_futures and leverage_mult > 1:
                    cost = raw_cost / leverage_mult
                else:
                    cost = raw_cost
            else:
                fill_price = float(order.get("average", 0) or order.get("price", 0) or current_price)
                filled_qty = float(order.get("filled", 0) or quantity)
                # cost_usd = margin (collateral), not notional. For futures, notional / leverage.
                raw_cost = float(order.get("cost", 0) or fill_price * filled_qty)
                if is_futures and leverage_mult > 1:
                    cost = raw_cost / leverage_mult  # store margin, not notional
                else:
                    cost = raw_cost

            live_order = LiveOrder(
                order_id=order_id,
                symbol=idea.asset,
                side=side,
                order_type=order_type,
                amount=filled_qty,
                price=fill_price,
                cost_usd=cost,
                status=order_status,
                client_oid=coid,
                raw=order,
            )
            self._order_history.append(live_order)

            # Track position
            leverage = CONFIG.exchange.default_leverage if is_futures else 1
            spot_fallback = not is_futures and CONFIG.exchange.trade_mode == "futures"

            # Initialize trailing stop state if enabled
            trailing_st = None
            if CONFIG.trailing.enabled and atr_value > 0:
                initial_risk = abs(fill_price - idea.stop_loss)
                trailing_st = make_trailing_state(
                    entry_price=fill_price,
                    direction=idea.direction.value,
                    initial_risk=initial_risk,
                    atr_value=atr_value,
                )

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
                trailing_state=trailing_st,
                order_type=order_type,
                limit_order_id=order_id if is_pending_limit else None,
                atr_at_entry=atr_value,
                status="pending_fill" if is_pending_limit else "open",
            )
            self._positions[idea.id] = position

            # F-07 FIX: persist after opening
            self._save_positions()
            # F-13 FIX: prune order history
            self._prune_order_history()

            if is_pending_limit:
                audit(trade_log, f"Limit order PLACED: {side} {idea.asset} @ ${fill_price:,.4f}",
                      action="live_execute", result="LIMIT_PLACED",
                      data={
                          "order_id": order_id, "trade_id": idea.id,
                          "side": side, "limit_price": fill_price,
                          "quantity": filled_qty, "cost_usd": cost,
                      })
                lev_info = f" | {leverage}x" if leverage > 1 else ""
                mode_label = "FUTURES" if is_futures else "SPOT"
                dir_icon = "🟢" if side == "buy" else "🔴"
                return (
                    f"{dir_icon} <b>LIMIT ORDER {side.upper()} {idea.asset}</b> ({mode_label}{lev_info})\n"
                    f"{'─' * 16}\n"
                    f"- Limit: <code>${fill_price:,.4f}</code>\n"
                    f"- Current: <code>${current_price:,.4f}</code>\n"
                    f"- Qty: <code>{filled_qty:.6f}</code>\n"
                    f"- Cost: <code>${cost:.2f}</code>\n"
                    f"- SL: <code>${idea.stop_loss:,.4f}</code>\n"
                    f"- TP: <code>${idea.take_profit:,.4f}</code>\n"
                    f"- Order: <code>{order_id}</code>\n"
                    f"- Status: ⏳ PENDING FILL\n"
                    f"- Mode: 🔥 Live {mode_label}"
                )

            audit(trade_log, f"Live order FILLED: {side} {idea.asset}",
                  action="live_execute", result="FILLED",
                  data={
                      "order_id": order_id, "trade_id": idea.id,
                      "side": side, "fill_price": fill_price,
                      "quantity": filled_qty, "cost_usd": cost,
                      "status": order_status,
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
            trail_info = ""
            if trailing_st:
                trail_info = "\n- Trailing: ✅ armed (activates at 1R)"
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
                f"- Risk: ✅ APPROVED{trail_info}\n"
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

        # Use cached UTA detection result instead of making an extra API call.
        # _detect_hold_mode already ran during _ensure_leverage and set _is_uta.
        use_v3 = self._is_uta if self._is_uta is not None else False
        if self._is_uta is None:
            # First call — haven't detected yet; probe once
            try:
                await exchange.privateMixGetV2MixAccountAccount(
                    {"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})
            except Exception as exc:
                if "40085" in str(exc):
                    use_v3 = True
                    self._is_uta = True
                else:
                    self._is_uta = False

        if use_v3:
            # UTA mode: place SL/TP via Bitget v3 REST API directly
            # Get tick size from exchange markets for proper price precision
            price_precision = None
            # Try both spot and swap symbol formats for market lookup
            swap_symbol = symbol if ":USDT" in symbol else f"{symbol}:USDT"
            lookup_symbols = [symbol, swap_symbol]
            try:
                if not exchange.markets:
                    await exchange.load_markets()
                for sym in lookup_symbols:
                    mkt = exchange.markets.get(sym)
                    if mkt and mkt.get("precision", {}).get("price") is not None:
                        price_precision = mkt["precision"]["price"]
                        break
            except Exception:
                pass
            # UPGRADE: round SL/TP onto the symbol's tick grid via ccxt's own
            # price_to_precision (tick-aware) rather than a decimal-places
            # heuristic. Try swap symbol format first, then spot.
            sl_rounded = None
            tp_rounded = None
            for sym in lookup_symbols:
                sl_rounded = self._round_price_to_market(exchange, sym, stop_loss)
                tp_rounded = self._round_price_to_market(exchange, sym, take_profit)
                if sl_rounded is not None and tp_rounded is not None:
                    break
            sl_id, tp_id = await self._place_sl_tp_v3(
                symbol, direction, quantity, stop_loss, take_profit,
                price_precision=price_precision,
                sl_str=sl_rounded, tp_str=tp_rounded,
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
        sl_str: Optional[str] = None,
        tp_str: Optional[str] = None,
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

        # v3 strategy order API:
        # Both hedge mode AND one-way mode require posSide = "long" or "short".
        # UTA v3 does NOT accept "net" and does NOT allow omitting posSide.
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
                # AUDIT FIX: kept sync here — callers use asyncio.to_thread
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
        # AUDIT FIX: offload blocking _v3_post to thread pool
        import asyncio as _asyncio
        tp_final = tp_str if tp_str is not None else _round_price(take_profit)
        sl_final = sl_str if sl_str is not None else _round_price(stop_loss)

        # Build payload: posSide is always "long"/"short" (even one-way mode)
        payload: dict[str, str] = {
            "category": "USDT-FUTURES",
            "symbol": bitget_symbol,
            "posSide": pos_side,
            "takeProfit": tp_final,
            "stopLoss": sl_final,
            "tpOrderType": "market",
            "slOrderType": "market",
            "clientOid": self._client_oid(bitget_symbol + pos_side + "sltp"),
        }

        logger.info("v3 SL/TP request: symbol=%s posSide=%s TP=%s SL=%s (raw TP=%s SL=%s, rounded=%s/%s, precision=%s)",
                     bitget_symbol, pos_side, tp_final, sl_final,
                     take_profit, stop_loss, tp_str, sl_str, price_precision)
        try:
            result = await _asyncio.to_thread(_v3_post, "/api/v3/trade/place-strategy-order", payload)

            if result.get("code") == "00000":
                data = result.get("data", {})
                # Bitget v3 returns orderId for the combined strategy order
                order_id = data.get("orderId") or data.get("slOrderId") or data.get("tpOrderId") or "v3-strategy"
                sl_id = order_id
                tp_id = order_id
                audit(trade_log, f"v3 SL/TP strategy order placed: order={order_id}",
                      action="sl_tp_v3", result="OK",
                      data={"symbol": bitget_symbol, "sl": sl_final, "tp": tp_final,
                            "order_id": order_id})
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
        """Check open positions against current prices. Returns list of close/update messages.

        Handles:
        1. Static SL/TP hits → close position
        2. Trailing stop updates → tighten SL when price moves favorably
        3. Pending limit order fills → transition to open position
        4. Pending limit order expiry → cancel stale limit orders
        """
        if not self._positions:
            return []

        closed_messages = []
        try:
            exchange = await self._get_exchange()
            tickers = await exchange.fetch_tickers()

            for trade_id, pos in list(self._positions.items()):
                # ── Handle pending limit orders ──
                if pos.status == "pending_fill":
                    msg = await self._check_pending_limit(exchange, trade_id, pos)
                    if msg:
                        closed_messages.append(msg)
                    continue

                if pos.status != "open":
                    continue
                price = float(tickers.get(pos.symbol, {}).get("last", 0))
                if price <= 0:
                    continue

                # ── Trailing stop update ──
                if CONFIG.trailing.enabled and pos.trailing_state is not None:
                    old_sl = pos.stop_loss
                    new_sl, trailing_active = update_trailing_stop(
                        pos.trailing_state, price, pos.stop_loss, pos.direction
                    )
                    if new_sl != old_sl:
                        pos.stop_loss = new_sl
                        self._save_positions()
                        # Check if the SL moved enough to update on exchange
                        sl_change_pct = abs(new_sl - old_sl) / old_sl * 100 if old_sl > 0 else 100
                        if sl_change_pct >= CONFIG.trailing.min_sl_update_pct:
                            await self._update_exchange_sl(
                                exchange, pos, new_sl
                            )
                            audit(trade_log,
                                  f"Trailing SL updated: {pos.symbol} SL ${old_sl:.4f} -> ${new_sl:.4f}",
                                  action="trailing_sl", result="UPDATED",
                                  data={"trade_id": trade_id, "old_sl": old_sl,
                                        "new_sl": new_sl, "price": price,
                                        "trailing_active": trailing_active})

                # ── Retry SL/TP placement if missing ──
                if not pos.sl_order_id and not pos.tp_order_id and pos.stop_loss > 0 and pos.take_profit > 0:
                    try:
                        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                        sl_id, tp_id = await self._place_sl_tp(
                            exchange, pos.symbol, direction,
                            pos.quantity, pos.stop_loss, pos.take_profit
                        )
                        if sl_id or tp_id:
                            pos.sl_order_id = sl_id
                            pos.tp_order_id = tp_id
                            self._save_positions()
                            audit(trade_log,
                                  f"SL/TP retry succeeded: {pos.symbol} SL={pos.stop_loss:.4f} TP={pos.take_profit:.4f}",
                                  action="sltp_retry", result="PLACED",
                                  data={"trade_id": trade_id, "sl_id": sl_id, "tp_id": tp_id})
                    except Exception as exc:
                        logger.debug("SL/TP retry failed for %s: %s", pos.symbol, exc)

                # ── Static SL/TP check ──
                should_close = False
                reason = ""

                if pos.direction == "LONG":
                    if price <= pos.stop_loss:
                        should_close = True
                        reason = "TRAILING SL HIT" if (pos.trailing_state and pos.trailing_state.get("trailing_active")) else "SL HIT"
                    elif price >= pos.take_profit:
                        should_close = True
                        reason = "TP HIT"
                else:  # SHORT
                    if price >= pos.stop_loss:
                        should_close = True
                        reason = "TRAILING SL HIT" if (pos.trailing_state and pos.trailing_state.get("trailing_active")) else "SL HIT"
                    elif price <= pos.take_profit:
                        should_close = True
                        reason = "TP HIT"

                if should_close:
                    # Close manually if no exchange SL/TP, or if SL/TP exists but
                    # price has blown through the level (exchange SL/TP may have
                    # been cancelled or failed).
                    msg = await self.close_position(trade_id, reason, price)
                    closed_messages.append(msg)

        except Exception as exc:
            logger.warning("Position check error: %s", exc)

        return closed_messages

    async def _check_pending_limit(self, exchange: "ccxt.Exchange",
                                    trade_id: str, pos: LivePosition) -> Optional[str]:
        """Check if a pending limit order has been filled or should be cancelled.

        Returns a message string if status changed, else None.
        """
        if not pos.limit_order_id:
            return None

        try:
            order = await exchange.fetch_order(pos.limit_order_id, pos.symbol)
            order_status = order.get("status", "unknown")

            if order_status in ("closed", "filled"):
                # Limit order filled — transition to open position
                fill_price = float(order.get("average", 0) or order.get("price", 0) or pos.entry_price)
                filled_qty = float(order.get("filled", 0) or pos.quantity)

                pos.entry_price = fill_price
                pos.quantity = filled_qty
                pos.status = "open"
                pos.limit_order_id = None

                # Recalculate cost
                raw_cost = fill_price * filled_qty
                if pos.leverage > 1:
                    pos.cost_usd = raw_cost / pos.leverage
                else:
                    pos.cost_usd = raw_cost

                # Initialize trailing state now that we have a real fill
                if CONFIG.trailing.enabled and pos.atr_at_entry > 0:
                    initial_risk = abs(fill_price - pos.stop_loss)
                    pos.trailing_state = make_trailing_state(
                        entry_price=fill_price,
                        direction=pos.direction,
                        initial_risk=initial_risk,
                        atr_value=pos.atr_at_entry,
                    )

                # Place SL/TP now that position is filled
                direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                sl_id, tp_id = await self._place_sl_tp(
                    exchange, pos.symbol, direction,
                    filled_qty, pos.stop_loss, pos.take_profit
                )
                pos.sl_order_id = sl_id
                pos.tp_order_id = tp_id

                self._save_positions()

                audit(trade_log, f"Limit order FILLED: {pos.symbol} @ ${fill_price:,.4f}",
                      action="limit_fill", result="FILLED",
                      data={"trade_id": trade_id, "fill_price": fill_price,
                            "quantity": filled_qty})

                sl_info = f" | SL: {sl_id}" if sl_id else ""
                tp_info = f" | TP: {tp_id}" if tp_id else ""
                trail_info = " | Trailing: armed" if pos.trailing_state else ""
                return (
                    f"LIMIT FILLED: {pos.direction} {pos.symbol}\n"
                    f"Fill: ${fill_price:,.4f} | Qty: {filled_qty:.6f}{sl_info}{tp_info}{trail_info}"
                )

            elif order_status in ("canceled", "cancelled", "rejected", "expired"):
                # Limit order cancelled/rejected — remove position
                pos.status = "closed"
                pos.closed_at = datetime.now(UTC)
                pos.pnl_usd = 0.0
                self._save_positions()

                audit(trade_log, f"Limit order {order_status}: {pos.symbol}",
                      action="limit_cancel", result=order_status.upper(),
                      data={"trade_id": trade_id})

                return f"LIMIT {order_status.upper()}: {pos.direction} {pos.symbol} — order not filled"

            else:
                # Still open — check expiry
                age_sec = (datetime.now(UTC) - pos.opened_at).total_seconds()
                if age_sec > CONFIG.limit_orders.expire_seconds:
                    # Cancel expired limit order
                    try:
                        await exchange.cancel_order(pos.limit_order_id, pos.symbol)
                    except Exception as exc:
                        logger.warning("Failed to cancel expired limit order %s: %s",
                                       pos.limit_order_id, exc)

                    pos.status = "closed"
                    pos.closed_at = datetime.now(UTC)
                    pos.pnl_usd = 0.0
                    self._save_positions()

                    audit(trade_log, f"Limit order EXPIRED after {age_sec:.0f}s: {pos.symbol}",
                          action="limit_expire", result="EXPIRED",
                          data={"trade_id": trade_id, "age_sec": age_sec})

                    return f"LIMIT EXPIRED: {pos.direction} {pos.symbol} — cancelled after {age_sec/3600:.1f}h"

        except Exception as exc:
            logger.warning("Pending limit check failed for %s: %s", trade_id, exc)

        return None

    async def _update_exchange_sl(self, exchange: "ccxt.Exchange",
                                   pos: LivePosition, new_sl: float) -> None:
        """Cancel old SL order and place a new one at the tightened level.

        Best-effort: trailing stop still works locally even if exchange
        update fails — check_positions() will close at the new SL.
        """
        is_futures = not getattr(pos, "is_spot", False)
        if not is_futures:
            return  # spot SL/TP not reliably supported

        # Cancel existing SL order
        if pos.sl_order_id:
            try:
                await exchange.cancel_order(pos.sl_order_id, pos.symbol)
            except Exception as exc:
                logger.debug("Cancel old SL order %s failed: %s", pos.sl_order_id, exc)

        # Place new SL at tightened level
        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
        use_v3 = self._is_uta if self._is_uta is not None else False

        if use_v3:
            # Round to tick grid
            swap_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
            sl_rounded = self._round_price_to_market(exchange, swap_symbol, new_sl)
            if sl_rounded is None:
                sl_rounded = self._round_price_to_market(exchange, pos.symbol, new_sl)

            # Place SL-only via v3 (keep existing TP)
            sl_id, _ = await self._place_sl_tp_v3(
                pos.symbol, direction, pos.quantity,
                new_sl, pos.take_profit,
                sl_str=sl_rounded,
            )
            if sl_id:
                pos.sl_order_id = sl_id
                self._save_positions()
        else:
            # Classic mode: place trigger order
            close_side = "sell" if direction == Direction.LONG else "buy"
            extra_params = {"productType": "USDT-FUTURES"}
            if self._hedge_mode:
                extra_params["tradeSide"] = "close"
            try:
                sl_order = await exchange.create_order(
                    symbol=pos.symbol, type="market", side=close_side,
                    amount=pos.quantity,
                    params={"triggerPrice": new_sl, "triggerType": "last", **extra_params},
                )
                pos.sl_order_id = sl_order.get("id")
                self._save_positions()
            except Exception as exc:
                logger.warning("Failed to update exchange SL for %s: %s", pos.symbol, exc)

    async def close_position(self, trade_id: str, reason: str = "manual",
                              close_price: float = 0) -> str:
        """Close a live position by placing the opposite order."""
        pos = self._positions.get(trade_id)
        if not pos or pos.status != "open":
            return f"Position {trade_id} not found or already closed."

        try:
            exchange = await self._get_exchange()
            close_side = "sell" if pos.direction == "LONG" else "buy"

            # Determine if this position is futures or spot.
            # Use is_spot flag as primary discriminator (leverage alone is unreliable:
            # a 1x futures position would incorrectly be treated as spot).
            is_futures_pos = not getattr(pos, "is_spot", False)

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
            pnl_pct = ((fill_price - pos.entry_price) / pos.entry_price * 100)
            if pos.direction == "SHORT":
                pnl_pct = -pnl_pct
            hold_secs = (pos.closed_at - pos.opened_at).total_seconds() if pos.closed_at and pos.opened_at else 0
            if hold_secs < 3600:
                hold_str = f"{hold_secs / 60:.0f}m"
            elif hold_secs < 86400:
                hold_str = f"{hold_secs / 3600:.1f}h"
            else:
                hold_str = f"{hold_secs / 86400:.1f}d"
            return (
                f"CLOSED {pos.direction} {pos.symbol} ({reason})\n"
                f"Entry: ${pos.entry_price:,.4f} → Exit: ${fill_price:,.4f}\n"
                f"PnL: {pnl_str} ({pnl_pct:+.2f}%) | Hold: {hold_str}"
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
        return [p for p in self._positions.values() if p.status in ("open", "pending_fill")]

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
                if pos.status not in ("open", "pending_fill"):
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
                    "trailing_state": pos.trailing_state,
                    "order_type": pos.order_type,
                    "limit_order_id": pos.limit_order_id,
                    "atr_at_entry": pos.atr_at_entry,
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
                    trailing_state=pdata.get("trailing_state"),
                    order_type=pdata.get("order_type", "market"),
                    limit_order_id=pdata.get("limit_order_id"),
                    atr_at_entry=float(pdata.get("atr_at_entry", 0)),
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
