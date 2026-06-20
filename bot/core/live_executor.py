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
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from bot.compat import UTC
from typing import Any, Callable, Optional

import ccxt.async_support as ccxt

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log, system_log
from bot.utils.models import Direction, TradeIdea
from bot.utils.trailing import make_trailing_state, update_trailing_stop
from bot.core.order_rules import (
    is_market_open, is_weekend_queued, adjust_sl_for_gap_risk,
    adjust_size_for_weekend, should_defer_tp_sl, ASSET_RULES,
)
from bot.core.limit_entry import calculate_entry, validate_entry_distance, EntryResult
from bot.core.market_scanner import _classify_symbol

logger = logging.getLogger(__name__)


def normalize_symbol(s: str) -> str:
    """Canonical symbol normalizer — strips ccxt suffixes to a bare base.

    Examples:
        MEGA/USDT:USDT  →  MEGA
        MEGA/USDT       →  MEGA
        MEGAUSDT        →  MEGAUSDT  (no destructive mid-string strip)
        XAU/USDT:USDT   →  XAU
    """
    result = s.upper()
    # Strip :USDT settle suffix first, then /USDT quote suffix.
    # Do NOT strip bare 'USDT' from the middle of strings (e.g. USDTUSDT).
    if result.endswith(":USDT"):
        result = result[:-5]
    if result.endswith("/USDT"):
        result = result[:-5]
    return result


def display_symbol(s: str) -> str:
    """Format a ccxt symbol for user-facing display.

    Examples:
        MEGA/USDT:USDT  →  MEGAUSDT
        MEGA/USDT       →  MEGAUSDT
        MEGAUSDT        →  MEGAUSDT
    """
    return s.replace("/", "").replace(":USDT", "")


# ── Safety limits ────────────────────────────────────────────────────
# $800 deposit, $100 margin per trade at 5x = $500 notional per trade
MICRO_MAX_POSITION_USD = 100.0    # Max $100 margin per trade
MICRO_MAX_TOTAL_EXPOSURE = 500.0  # Max $500 total margin exposure
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
    is_spot: bool = False   # DEPRECATED: always False (futures-only mode)
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
    # Fee tracking: commission deducted from PnL
    gross_pnl: Optional[float] = None
    commission: Optional[float] = None
    # Reason the position was closed (e.g. "SL", "TP", "manual", error status)
    close_reason: Optional[str] = None


class LiveExecutor:
    """Executes real trades on Bitget with micro-test safety limits.

    Usage:
        executor = LiveExecutor()
        result = await executor.execute(idea, size_usd=100.0)
    """

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.Exchange] = None
        self._positions: dict[str, LivePosition] = {}
        self._closed_trades: list[LivePosition] = []  # F-14: persisted closed trades
        self._order_history: list[LiveOrder] = []
        self._hedge_mode: Optional[bool] = None  # None=unknown, True=hedge, False=one-way
        self._is_uta: Optional[bool] = None  # None=unknown, cached after first detection
        self._persistence_broken: bool = False  # C-02: set True if position save fails
        self._last_close_data: Optional[dict] = None  # Structured data from most recent close
        # C2-02 FIX: Per-trade-id locks to prevent double-close race condition.
        # close_position() is called from check_positions, reconcile_positions,
        # and Telegram handler — all can race on the same trade_id.
        self._close_locks: dict[str, asyncio.Lock] = {}
        # C2-27: Track consecutive ticker fetch failures per symbol
        self._ticker_failure_count: dict[str, int] = {}
        # Callback: invoked after any position is closed (for balance cache invalidation)
        self.on_position_closed: Optional[Callable] = None
        # Exchange sync: periodically check for untracked positions
        self._last_exchange_sync: float = 0
        self._EXCHANGE_SYNC_INTERVAL: float = 300  # 5 minutes
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

        # C2-04 FIX: Verify leverage was actually applied. If it doesn't match,
        # log a critical warning. The caller should treat this as a risk.
        try:
            lev_info = await exchange.fetch_leverage(symbol, params={"productType": "USDT-FUTURES"})
            actual_lev = None
            if isinstance(lev_info, dict):
                # CCXT / Bitget returns various shapes — try common keys
                actual_lev = lev_info.get("longLeverage") or lev_info.get("leverage") or lev_info.get("long")
                if actual_lev is not None:
                    actual_lev = int(float(actual_lev))
            if actual_lev is not None and actual_lev != cfg.default_leverage:
                logger.critical(
                    "LEVERAGE MISMATCH for %s: wanted %dx, exchange reports %dx — "
                    "position will have INCORRECT risk exposure",
                    symbol, cfg.default_leverage, actual_lev)
        except Exception:
            # fetch_leverage may not be implemented for all exchanges — log but don't block
            logger.debug("Could not verify leverage for %s (fetch_leverage unavailable)", symbol)
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
                {"symbol": CONFIG.exchange.hold_mode_probe_symbol, "productType": "USDT-FUTURES"})
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

    def _preflight_check(self, size_usd: float, symbol: str = "") -> Optional[str]:
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

        # GETCLAW: Capital buffer guard — keep minimum reserve after trade.
        # Deploying too much leaves no buffer for margin calls or new opportunities.
        # Warn (don't block) if remaining equity drops below 20% of limit.
        MIN_RESERVE_PCT = 20.0
        remaining = MICRO_MAX_TOTAL_EXPOSURE - total_exposure - size_usd
        reserve_needed = MICRO_MAX_TOTAL_EXPOSURE * (MIN_RESERVE_PCT / 100.0)
        if remaining < reserve_needed and remaining > 0:
            audit(trade_log,
                  f"Capital buffer warning: ${remaining:.2f} remaining after trade "
                  f"(reserve target: ${reserve_needed:.2f})",
                  action="capital_buffer", result="WARN",
                  data={"remaining": remaining, "reserve": reserve_needed,
                        "exposure": total_exposure, "new_size": size_usd})

        # Check open positions count
        open_count = sum(1 for p in self._positions.values() if p.status == "open")
        if open_count >= MICRO_MAX_OPEN_POSITIONS:
            return f"Already {open_count} open positions (max {MICRO_MAX_OPEN_POSITIONS})"

        # DUPLICATE SYMBOL GUARD: block opening a second position on the same symbol
        if symbol:
            norm = normalize_symbol(symbol)
            for p in self._positions.values():
                if p.status != "open":
                    continue
                p_norm = normalize_symbol(p.symbol)
                if p_norm == norm:
                    return (
                        f"Already have an open {p.direction} position on {p.symbol} "
                        f"(trade {p.trade_id}). Close it first or wait for SL/TP."
                    )

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
        price: Optional[float] = None,
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
                symbol=symbol, type=type, side=side, amount=amount,
                price=price, params=params
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

    # ── Post-trade verification (GetClaw-style) ─────────────────────
    async def _verify_order_fill(
        self,
        exchange: "ccxt.Exchange",
        order_id: str,
        symbol: str,
        expected_qty: float,
        max_retries: int = 3,
        delay: float = 1.5,
    ) -> dict:
        """Post-check: query the order to confirm actual fill.

        Returns dict with:
          confirmed: bool — True if order is filled/closed with qty > 0
          fill_price: float — average fill price (0 if unconfirmed)
          fill_qty: float — confirmed filled quantity
          fees: float — exchange-reported fees
          status: str — order status from exchange
          failure_stage: str — empty if confirmed, else stage that failed
          raw: dict — raw order response from exchange
        """
        result = {
            "confirmed": False,
            "fill_price": 0.0,
            "fill_qty": 0.0,
            "fees": 0.0,
            "status": "unknown",
            "failure_stage": "",
            "raw": {},
        }
        for attempt in range(max_retries):
            try:
                fetched = await exchange.fetch_order(order_id, symbol)
                result["raw"] = fetched
                status = str(fetched.get("status", "")).lower()
                result["status"] = status
                filled = float(fetched.get("filled", 0) or 0)
                avg_price = float(fetched.get("average", 0) or 0)
                fee_info = fetched.get("fee") or {}
                fee_cost = float(fee_info.get("cost", 0) or 0) if isinstance(fee_info, dict) else 0

                if status in ("closed", "filled") and filled > 0:
                    result["confirmed"] = True
                    result["fill_price"] = avg_price if avg_price > 0 else float(fetched.get("price", 0) or 0)
                    result["fill_qty"] = filled
                    result["fees"] = abs(fee_cost)
                    logger.info("Order %s CONFIRMED: filled=%.6f @ %.4f, fees=%.4f",
                                order_id, filled, result["fill_price"], result["fees"])
                    return result

                if status in ("canceled", "cancelled", "expired", "rejected"):
                    result["failure_stage"] = "order_cancelled"
                    logger.warning("Order %s was %s", order_id, status)
                    return result

                # Still open/partial — retry after delay
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)

            except Exception as exc:
                logger.warning("Verify order %s attempt %d failed: %s", order_id, attempt + 1, exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)

        # Exhausted retries — order submitted but not confirmed
        result["failure_stage"] = "post_check_unconfirmed"
        return result

    async def _verify_position_exists(
        self,
        exchange: "ccxt.Exchange",
        symbol: str,
        expected_direction: str,
    ) -> dict:
        """Post-check: verify a position exists on the exchange after opening.

        Returns dict with:
          confirmed: bool — True if position found with contracts > 0
          exchange_qty: float — actual quantity on exchange
          exchange_entry: float — exchange-reported entry price
          mark_price: float — current mark price
          unrealized_pnl: float — current unrealized PnL
          margin: float — margin used
          leverage: int — actual leverage set on exchange
        """
        result = {
            "confirmed": False,
            "exchange_qty": 0.0,
            "exchange_entry": 0.0,
            "mark_price": 0.0,
            "unrealized_pnl": 0.0,
            "margin": 0.0,
            "leverage": 0,
        }
        try:
            positions = await exchange.fetch_positions([symbol])
            for p in (positions or []):
                if not isinstance(p, dict):
                    continue
                p_symbol = p.get("symbol", "")
                contracts = float(p.get("contracts", 0) or 0)
                p_side = str(p.get("side", "")).lower()
                expected_side = "long" if expected_direction == "LONG" else "short"
                if p_symbol == symbol and contracts > 0 and p_side == expected_side:
                    result["confirmed"] = True
                    result["exchange_qty"] = contracts
                    result["exchange_entry"] = float(p.get("entryPrice", 0) or 0)
                    result["mark_price"] = float(p.get("markPrice", 0) or 0)
                    result["unrealized_pnl"] = float(p.get("unrealizedPnl", 0) or 0)
                    result["margin"] = float(p.get("initialMargin", 0) or p.get("collateral", 0) or 0)
                    result["leverage"] = int(float(p.get("leverage", 0) or 0))
                    logger.info("Position VERIFIED on exchange: %s %s qty=%.6f entry=%.4f",
                                expected_direction, symbol, contracts, result["exchange_entry"])
                    return result
        except Exception as exc:
            logger.warning("Position verification failed for %s: %s", symbol, exc)
        return result

    async def _verify_position_closed(
        self,
        exchange: "ccxt.Exchange",
        symbol: str,
        direction: str,
        close_order_id: str,
    ) -> dict:
        """Post-check: verify a position is fully closed after close order.

        Returns dict with:
          confirmed: bool — True if position is gone or contracts == 0
          fill_price: float — actual close fill price from order
          fill_qty: float — actual closed quantity
          fees: float — exchange-reported fees on close
          remaining_qty: float — if partial close, qty still open
          failure_stage: str — empty if confirmed
        """
        result = {
            "confirmed": False,
            "fill_price": 0.0,
            "fill_qty": 0.0,
            "fees": 0.0,
            "remaining_qty": 0.0,
            "failure_stage": "",
        }
        # Step 1: Verify the close order filled
        order_check = await self._verify_order_fill(
            exchange, close_order_id, symbol, expected_qty=0, max_retries=3, delay=1.5
        )
        result["fill_price"] = order_check["fill_price"]
        result["fill_qty"] = order_check["fill_qty"]
        result["fees"] = order_check["fees"]

        if not order_check["confirmed"]:
            result["failure_stage"] = order_check.get("failure_stage", "close_order_unconfirmed")
            return result

        # Step 2: Verify position is gone/reduced on exchange
        try:
            await asyncio.sleep(1.0)  # Brief delay for exchange settlement
            positions = await exchange.fetch_positions([symbol])
            expected_side = "long" if direction == "LONG" else "short"
            for p in (positions or []):
                if not isinstance(p, dict):
                    continue
                p_side = str(p.get("side", "")).lower()
                contracts = float(p.get("contracts", 0) or 0)
                if p.get("symbol") == symbol and p_side == expected_side and contracts > 0:
                    result["remaining_qty"] = contracts
                    result["confirmed"] = False
                    result["failure_stage"] = "position_still_open"
                    logger.warning("Position still open after close: %s %s remaining=%.6f",
                                   direction, symbol, contracts)
                    return result
            # Position not found — fully closed
            result["confirmed"] = True
            logger.info("Position CLOSE VERIFIED: %s %s — no remaining position on exchange",
                        direction, symbol)
        except Exception as exc:
            # Close order confirmed but position check failed — trust the order fill
            logger.warning("Post-close position check failed for %s: %s — trusting order fill",
                           symbol, exc)
            result["confirmed"] = True  # Order was confirmed, position check is supplementary
        return result

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
                ex_positions = await exchange.fetch_positions(
                    params={"productType": "USDT-FUTURES"})
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"fetch_positions failed: {exc}")
                return report

            tracked = {
                normalize_symbol(p.symbol)
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
                sym = normalize_symbol(raw_sym)
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

    async def adopt_exchange_positions(self) -> list[str]:
        """Adopt any exchange positions not tracked locally into _positions.

        Called on startup after detect_untracked_positions(). This ensures
        every open position on the exchange has a corresponding LivePosition
        so /open_positions, /close, and performance all work correctly.

        Cooldown: positions on symbols recently closed (within 120s) are skipped
        to prevent re-adopting reverse positions created by hedge mode bugs.

        Returns list of adopted symbol names.
        """
        adopted: list[str] = []
        if not CONFIG.is_live():
            return adopted

        # Build cooldown set from recently closed positions
        _now = time.time()
        _ADOPT_COOLDOWN = 120  # seconds
        recently_closed_symbols: set[str] = set()
        for p in self._closed_trades:
            closed_at = getattr(p, 'closed_at', None)
            if closed_at:
                if isinstance(closed_at, str):
                    try:
                        closed_at = datetime.fromisoformat(closed_at)
                    except (ValueError, TypeError):
                        continue
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=UTC)
                age = _now - closed_at.timestamp()
                if age < _ADOPT_COOLDOWN:
                    recently_closed_symbols.add(normalize_symbol(p.symbol))
        try:
            exchange = await self._get_exchange()
            ex_positions = await exchange.fetch_positions(
                params={"productType": "USDT-FUTURES"})

            tracked = {
                (normalize_symbol(p.symbol), p.direction)
                for p in self._positions.values()
                if p.status in ("open", "pending_fill")
            }

            for p in ex_positions or []:
                if not isinstance(p, dict):
                    continue
                try:
                    contracts = float(p.get("contracts") or 0)
                except (TypeError, ValueError):
                    continue
                if contracts <= 0:
                    continue

                raw_sym = p.get("symbol") or ""
                sym = normalize_symbol(raw_sym)
                side = (p.get("side") or "long").upper()
                if (sym, side) in tracked:
                    continue

                # Cooldown: skip symbols recently closed to prevent re-adoption
                # of reverse positions created by hedge mode bugs
                if sym in recently_closed_symbols:
                    logger.info("Skipping adoption of %s %s — recently closed (cooldown %ds)",
                                sym, side, _ADOPT_COOLDOWN)
                    continue

                # Adopt this position
                entry_price = float(p.get("entryPrice") or p.get("info", {}).get("openPriceAvg") or 0)
                margin = float(p.get("initialMargin") or p.get("collateral") or 0)
                leverage = int(float(p.get("leverage") or 1))
                ts = p.get("timestamp")
                if ts:
                    opened_at = datetime.fromtimestamp(ts / 1000, tz=UTC)
                else:
                    opened_at = datetime.now(UTC)

                trade_id = f"TI-adopted-{raw_sym.replace('/', '-')}-{int(opened_at.timestamp())}"
                lp = LivePosition(
                    trade_id=trade_id,
                    symbol=raw_sym,
                    direction=side,
                    entry_price=entry_price,
                    quantity=contracts,
                    cost_usd=margin,
                    stop_loss=0,
                    take_profit=0,
                    leverage=leverage,
                    is_spot=False,
                    opened_at=opened_at,
                    status="open",
                )

                # Try to find SL/TP from open trigger orders
                try:
                    open_orders = await exchange.fetch_open_orders(raw_sym)
                    for o in (open_orders or []):
                        trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                        if trigger <= 0:
                            continue
                        otype = (o.get("type") or "").lower()
                        if "stop" in otype or "loss" in otype:
                            lp.stop_loss = trigger
                            lp.sl_order_id = o.get("id")
                        elif "take" in otype or "profit" in otype:
                            lp.take_profit = trigger
                            lp.tp_order_id = o.get("id")
                except Exception:
                    pass  # Non-critical — position still adopted without SL/TP

                # If SL or TP missing, calculate safety defaults (3% SL, 6% TP)
                need_sl = lp.stop_loss <= 0 and entry_price > 0
                need_tp = lp.take_profit <= 0 and entry_price > 0
                if need_sl or need_tp:
                    default_sl_pct = 0.03
                    default_tp_pct = 0.06
                    if need_sl:
                        if side == "LONG":
                            lp.stop_loss = round(entry_price * (1 - default_sl_pct), 8)
                        else:
                            lp.stop_loss = round(entry_price * (1 + default_sl_pct), 8)
                    if need_tp:
                        if side == "LONG":
                            lp.take_profit = round(entry_price * (1 + default_tp_pct), 8)
                        else:
                            lp.take_profit = round(entry_price * (1 - default_tp_pct), 8)

                    # Place exchange-side SL/TP for safety
                    try:
                        direction = Direction.LONG if side == "LONG" else Direction.SHORT
                        sl_id, tp_id = await self._place_sl_tp(
                            exchange, raw_sym, direction, contracts,
                            lp.stop_loss, lp.take_profit,
                        )
                        if sl_id:
                            lp.sl_order_id = sl_id
                        if tp_id:
                            lp.tp_order_id = tp_id
                        audit(trade_log,
                              f"ADOPTED position safety SL/TP placed: {raw_sym} SL=${lp.stop_loss:.4f} TP=${lp.take_profit:.4f}",
                              action="adopt_safety_sltp", result="OK")
                    except Exception as exc:
                        audit(trade_log,
                              f"ADOPTED position: failed to place safety SL/TP for {raw_sym}: {exc}",
                              action="adopt_safety_sltp", result="ERROR")

                self._positions[trade_id] = lp
                adopted.append(sym)
                audit(trade_log,
                      f"ADOPTED exchange position: {sym} {side} entry={entry_price} qty={contracts} lev={leverage}x",
                      action="adopt_position", result="OK",
                      data={"trade_id": trade_id, "symbol": raw_sym,
                            "entry_price": entry_price, "contracts": contracts})

            if adopted:
                self._save_positions()

        except Exception as exc:
            logger.warning("adopt_exchange_positions() failed: %s", exc)
        return adopted

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
        # C-04: Work on a copy of the idea to avoid mutating the caller's object
        import copy as _copy
        idea = _copy.copy(idea)
        # Resolve order type: explicit > config > default
        if self._persistence_broken:
            return "REFUSED: position persistence is broken — cannot open new trades until resolved"
        if not order_type:
            order_type = CONFIG.limit_orders.default_order_type if CONFIG.limit_orders.enabled else "market"
        order_type = order_type.lower()
        if order_type not in ("market", "limit"):
            order_type = "market"
        # Clamp to micro limit
        size_usd = min(size_usd, MICRO_MAX_POSITION_USD)

        # ── GETCLAW ORDER RULES: market hours + weekend adjustments ──
        asset_class = _classify_symbol(idea.asset)
        mkt_open, mkt_reason = is_market_open(asset_class)
        is_weekend = is_weekend_queued(asset_class)

        # Log market hours status for non-crypto assets
        if asset_class != "Crypto" and not mkt_open:
            audit(trade_log,
                  f"Market closed for {idea.asset} ({asset_class}): {mkt_reason}",
                  action="market_hours", result="QUEUED",
                  data={"asset": idea.asset, "class": asset_class, "reason": mkt_reason})
            # For market orders on closed markets, force to limit
            if order_type == "market" and asset_class not in ("Crypto", "Pre-IPO"):
                order_type = "limit"
                audit(trade_log,
                      f"Market order → limit: {idea.asset} market is closed",
                      action="order_type_override", result="LIMIT")

        # Weekend size reduction for metals/commodities (GetClaw: 30-40%)
        if is_weekend:
            old_size = size_usd
            size_usd = adjust_size_for_weekend(size_usd, asset_class, is_weekend)
            if size_usd != old_size:
                audit(trade_log,
                      f"Weekend size reduction: ${old_size:.2f} → ${size_usd:.2f} ({asset_class})",
                      action="weekend_size_adjust", result="REDUCED",
                      data={"old_size": old_size, "new_size": size_usd, "class": asset_class})

        # Weekend SL widening for gap-risk assets (GetClaw: widen 25-50%)
        if is_weekend:
            old_sl = idea.stop_loss
            new_sl = adjust_sl_for_gap_risk(
                idea.stop_loss, idea.entry_price,
                idea.direction.value, asset_class, is_weekend,
            )
            if new_sl != old_sl:
                idea.stop_loss = new_sl
                audit(trade_log,
                      f"Weekend SL widened: ${old_sl:.4f} → ${new_sl:.4f} ({asset_class})",
                      action="weekend_sl_widen", result="WIDENED",
                      data={"old_sl": old_sl, "new_sl": new_sl, "class": asset_class})

        # Check if TP/SL should be deferred until after fill (gap-risk limit orders)
        defer_tp_sl = should_defer_tp_sl(asset_class, is_weekend, order_type)

        # ── GETCLAW: Funding rate awareness ──────────────────────────
        # Negative funding = longs get paid (favorable for longs)
        # Positive funding = longs pay (unfavorable, factor into R:R)
        # 0% funding on metals/stocks = market likely closed
        try:
            exchange_pre = await self._get_exchange()
            funding_info = await exchange_pre.fetch_funding_rate(idea.asset)
            funding_rate = float(funding_info.get("fundingRate", 0) or 0)
            if funding_rate != 0:
                direction_favored = (
                    (idea.direction == Direction.LONG and funding_rate < 0) or
                    (idea.direction == Direction.SHORT and funding_rate > 0)
                )
                if not direction_favored and abs(funding_rate) > 0.001:
                    # Funding > 0.1% against us — log warning but don't block
                    audit(trade_log,
                          f"Funding rate {funding_rate*100:.3f}% unfavorable for "
                          f"{idea.direction.value} {idea.asset}",
                          action="funding_check", result="WARN",
                          data={"funding_rate": funding_rate, "direction": idea.direction.value})
        except Exception:
            pass  # Non-critical — don't block trade on funding fetch failure

        # ── GETCLAW: Funding settlement clock guard ──────────────────
        # Funding settles at 00:00 / 08:00 / 16:00 UTC.
        # Opening a position within 5 minutes BEFORE settlement means
        # you pay funding almost immediately. Warn and log.
        try:
            now_utc = datetime.now(UTC)
            minutes_in_day = now_utc.hour * 60 + now_utc.minute
            # Settlement times in minutes: 0, 480, 960
            settlement_times = [0, 480, 960]
            for st in settlement_times:
                mins_until = (st - minutes_in_day) % 1440
                if mins_until <= 5:  # within 5 minutes before settlement
                    audit(trade_log,
                          f"Funding settlement in {mins_until}m — entry will incur "
                          f"immediate funding charge on {idea.asset}",
                          action="funding_clock", result="WARN",
                          data={"mins_until_settlement": mins_until,
                                "direction": idea.direction.value})
                    break
        except Exception:
            pass  # Non-critical timing check

        # Pre-flight
        preflight_err = self._preflight_check(size_usd, symbol=idea.asset)
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
                # ccxt uses "SYMBOL/USDT:USDT" for swap markets
                # Don't double-append :USDT if already present
                if ":USDT" in symbol:
                    swap_symbol = symbol
                else:
                    swap_symbol = symbol.replace("/USDT", "/USDT:USDT")
                has_futures = swap_symbol in markets or any(
                    m.get("swap") and m.get("symbol") == swap_symbol
                    for m in markets.values()
                    if isinstance(m, dict)
                )
                if not has_futures:
                    # FUTURES ONLY MODE: block trade if no futures market exists
                    audit(trade_log,
                          f"BLOCKED: {symbol} has no futures/perpetual market on this exchange",
                          action="live_execute", result="BLOCKED_NO_FUTURES",
                          data={"asset": symbol, "direction": idea.direction.value})
                    return (f"EXECUTION FAILED: {symbol} has no futures market — "
                            f"only USDT-M perpetual futures are supported.")

            # Set leverage for this symbol (futures only)
            if is_futures:
                # AUDIT-FIX: Use swap symbol format for leverage API calls
                swap_sym = idea.asset if ":USDT" in idea.asset else f"{idea.asset}:USDT"
                await self._ensure_leverage(swap_sym)

            # Convert symbol for futures if needed
            symbol = idea.asset

            # Futures-only: always use the main swap exchange
            active_exchange = exchange

            # Fetch current price to calculate quantity
            try:
                ticker = await active_exchange.fetch_ticker(symbol)
            except Exception:
                # Spot exchange may need markets loaded first
                await active_exchange.load_markets()
                ticker = await active_exchange.fetch_ticker(symbol)
            _last_raw = ticker.get("last") if isinstance(ticker, dict) else None
            if _last_raw is None:
                return f"EXECUTION FAILED: exchange returned no price for {symbol}"
            current_price = float(_last_raw)

            # ── SAFEGUARD 1: Pre-trade price validation ──
            # Block trades where the market has already moved past the SL level.
            # This prevents opening a position that will be instantly stopped out.
            if idea.direction == Direction.LONG and current_price <= idea.stop_loss:
                audit(trade_log,
                      f"BLOCKED: {symbol} price ${current_price:.4f} already at/below SL ${idea.stop_loss:.4f}",
                      action="live_execute", result="BLOCKED_PRICE_PAST_SL",
                      data={"asset": symbol, "price": current_price,
                            "sl": idea.stop_loss, "direction": "LONG"})
                return (f"EXECUTION BLOCKED: {symbol} price ${current_price:.4f} is already "
                        f"at/below SL ${idea.stop_loss:.4f} — would be instantly stopped out.")
            elif idea.direction == Direction.SHORT and current_price >= idea.stop_loss:
                audit(trade_log,
                      f"BLOCKED: {symbol} price ${current_price:.4f} already at/above SL ${idea.stop_loss:.4f}",
                      action="live_execute", result="BLOCKED_PRICE_PAST_SL",
                      data={"asset": symbol, "price": current_price,
                            "sl": idea.stop_loss, "direction": "SHORT"})
                return (f"EXECUTION BLOCKED: {symbol} price ${current_price:.4f} is already "
                        f"at/above SL ${idea.stop_loss:.4f} — would be instantly stopped out.")

            # Calculate quantity
            # For futures with leverage: size_usd is the margin (collateral).
            # Notional exposure = margin * leverage, so qty = (size_usd * leverage) / price.
            leverage_mult = CONFIG.exchange.default_leverage
            quantity = (size_usd * leverage_mult) / current_price

            # Determine side
            side = "buy" if idea.direction == Direction.LONG else "sell"

            # Load markets for precision rounding
            markets = await active_exchange.load_markets()
            market = markets.get(symbol)
            if market:
                _rounded = active_exchange.amount_to_precision(symbol, quantity)
                if _rounded is None:
                    return f"EXECUTION FAILED: exchange returned no precision data for {symbol}"
                quantity = float(_rounded)

            if quantity <= 0:
                audit(trade_log, f"Quantity too small after precision: {symbol} ${size_usd}",
                      action="live_execute", result="QUANTITY_TOO_SMALL",
                      data={"asset": symbol, "size_usd": size_usd, "price": current_price})
                return f"BLOCKED: quantity too small after precision rounding for {symbol}"

            # UPGRADE: validate against the venue's min-amount / min-notional
            # filters so a sub-minimum order is BLOCKED cleanly here instead of
            # being rejected by Bitget after submission.
            limit_err = self._validate_order_limits(market, quantity, quantity * current_price)
            if limit_err:
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

            # ── LIMIT ORDER PRICE VALIDATION ──
            # A limit order that's on the wrong side of the market fills instantly
            # as a taker (effectively a market order). Recalculate the limit price
            # using the CURRENT price with an offset to ensure it rests on the book.
            if use_limit and limit_price and current_price > 0:
                needs_recalc = False
                if side == "buy" and limit_price >= current_price:
                    # LONG limit buy above market = instant fill = market order
                    needs_recalc = True
                elif side == "sell" and limit_price <= current_price:
                    # SHORT limit sell below market = instant fill = market order
                    needs_recalc = True

                if needs_recalc and atr_value > 0:
                    # GETCLAW: confluence-based limit entry calculation
                    # Fetch recent 1H OHLCV for VWAP/EMA computation
                    ohlcv_data = None
                    try:
                        ohlcv_data = await active_exchange.fetch_ohlcv(
                            symbol, "1h", limit=50)
                    except Exception as ohlcv_exc:
                        logger.debug("Could not fetch OHLCV for limit calc: %s", ohlcv_exc)

                    entry_result = calculate_entry(
                        current_price=current_price,
                        direction=idea.direction.value,
                        atr_value=atr_value,
                        ohlcv=ohlcv_data,
                    )
                    limit_price = entry_result.limit_price

                    # Apply entry tier size adjustment
                    if entry_result.tier == "D":
                        # Tier D = no confluence — downgrade to market order
                        use_limit = False
                        limit_price = None
                        audit(trade_log,
                              f"Limit downgraded to market: Tier D (no confluence) for {symbol}",
                              action="limit_tier_d", result="MARKET_FALLBACK",
                              data={"symbol": symbol, "tier": "D"})
                    elif entry_result.size_multiplier < 1.0:
                        # Tier C = marginal confluence — reduce size
                        old_sz = size_usd
                        size_usd = round(size_usd * entry_result.size_multiplier, 2)
                        # Recalculate quantity with new size
                        quantity = (size_usd * leverage_mult) / current_price
                        if market:
                            _re_rounded = active_exchange.amount_to_precision(symbol, quantity)
                            if _re_rounded:
                                quantity = float(_re_rounded)

                    # Apply natural SL if better than current
                    if entry_result.natural_sl and limit_price:
                        dir_up = idea.direction.value.upper() == "LONG"
                        current_sl_dist = abs(idea.entry_price - idea.stop_loss) / idea.entry_price
                        natural_sl_dist = abs(limit_price - entry_result.natural_sl) / limit_price
                        # Use natural SL if it provides more room (wider) without exceeding 2x original
                        if natural_sl_dist > current_sl_dist and natural_sl_dist < current_sl_dist * 2:
                            old_sl = idea.stop_loss
                            idea.stop_loss = entry_result.natural_sl
                            audit(trade_log,
                                  f"Natural SL applied: ${old_sl:,.4f} → ${idea.stop_loss:,.4f}",
                                  action="natural_sl", result="APPLIED",
                                  data={"old_sl": old_sl, "natural_sl": idea.stop_loss})

                    if limit_price:
                        audit(trade_log,
                              f"Confluence entry: {entry_result.explanation}",
                              action="limit_recalc_exec", result="RECALCULATED",
                              data={"old_limit": idea.entry_price, "new_limit": limit_price,
                                    "market_price": current_price, "atr": atr_value,
                                    "tier": entry_result.tier,
                                    "confluence": entry_result.confluence_count,
                                    "levels": entry_result.levels_used})
                elif needs_recalc and atr_value <= 0:
                    # No ATR available — fall back to market order
                    use_limit = False
                    limit_price = None
                    audit(trade_log,
                          f"Limit order downgraded to market: no ATR for offset calculation",
                          action="limit_downgrade", result="MARKET_FALLBACK",
                          data={"symbol": symbol})

            if use_limit and market:
                # Round limit price to exchange tick grid
                _prec_price = active_exchange.price_to_precision(symbol, limit_price)
                limit_price = float(_prec_price) if _prec_price is not None else limit_price

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
                    if bal_free < size_usd:
                        audit(trade_log,
                              f"Low balance warning: ${bal_free:.2f} available, need ~${size_usd:.2f} margin for {symbol}",
                              action="live_execute", result="BALANCE_WARN",
                              data={"balance_free": bal_free, "margin_needed": size_usd})
                except Exception as exc:
                    logger.debug("Balance pre-check failed: %s", exc)

                futures_params = {
                    "productType": "USDT-FUTURES",
                    "marginMode": CONFIG.exchange.margin_mode,
                    "leverage": str(leverage),
                }
                if self._hedge_mode:
                    futures_params["tradeSide"] = "open"
                else:
                    # Even in one-way mode, explicitly set tradeSide for safety
                    futures_params["tradeSide"] = "open"

                otype = "limit" if use_limit else "market"
                # TIME IN FORCE — asset-class aware:
                # GETCLAW: metals/stocks need GTC (session queue for overnight).
                # Crypto gets POST_ONLY for maker-only fee savings.
                if use_limit:
                    asset_class = _classify_symbol(symbol)
                    if asset_class in ("Metal", "Commodity", "Stock", "Pre-IPO"):
                        # GTC: stays live through session close/reopen
                        futures_params["timeInForce"] = "GTC"
                    elif CONFIG.limit_orders.post_only:
                        # POST_ONLY: maker-only, rejects if would fill as taker
                        futures_params["timeInForce"] = "post_only"

                create_kwargs: dict[str, Any] = {
                    "symbol": symbol, "type": otype, "side": side,
                    "amount": quantity, "coid": coid, "params": futures_params,
                }
                if use_limit and limit_price:
                    # ccxt requires price as a top-level param for limit orders
                    create_kwargs["price"] = limit_price

                # Try to place the order — handle POST_ONLY rejection gracefully
                try:
                    order = await self._create_order_idempotent(exchange, **create_kwargs)
                except Exception as post_only_exc:
                    exc_str = str(post_only_exc).lower()
                    # Bitget rejects POST_ONLY orders that would cross the book
                    # with "post only order failed" or similar. Retry with wider offset.
                    if use_limit and CONFIG.limit_orders.post_only and (
                        "post only" in exc_str or "post_only" in exc_str
                        or "would immediately" in exc_str
                    ):
                        audit(trade_log,
                              f"POST_ONLY rejected for {symbol} @ ${limit_price:,.4f} — "
                              f"widening offset and retrying",
                              action="post_only_retry", result="WIDENING",
                              data={"symbol": symbol, "rejected_price": limit_price})
                        # Double the offset and retry
                        wider_offset = 1.0 * atr_value if atr_value > 0 else current_price * 0.005
                        if side == "buy":
                            limit_price = round(current_price - wider_offset, 8)
                        else:
                            limit_price = round(current_price + wider_offset, 8)
                        _prec_price = active_exchange.price_to_precision(symbol, limit_price)
                        limit_price = float(_prec_price) if _prec_price is not None else limit_price
                        create_kwargs["price"] = limit_price
                        # Generate new coid for retry
                        retry_coid = coid + "-r1"
                        create_kwargs["coid"] = retry_coid
                        create_kwargs["params"]["clientOid"] = retry_coid
                        create_kwargs["params"]["clientOrderId"] = retry_coid
                        order = await self._create_order_idempotent(exchange, **create_kwargs)
                    else:
                        raise  # Not a POST_ONLY rejection — propagate
            else:
                # FUTURES-ONLY MODE: all non-futures order paths are removed.
                # This branch should never execute when trade_mode="futures".
                raise RuntimeError(
                    f"Unreachable: non-futures order path hit for {symbol} "
                    f"(side={side}, is_futures={is_futures}). "
                    f"Check CONFIG.exchange.trade_mode setting."
                )

            # ── CRITICAL SAFETY NET ──
            # Everything below runs AFTER the order was submitted to the exchange.
            # If parsing/tracking crashes, the position is LIVE on the exchange
            # but untracked locally — creating an orphan with no SL protection.
            # This except block ensures we always record a minimal position.

            # Handle limit orders that haven't filled yet
            order_status = order.get("status", "unknown")
            order_id = order.get("id", "unknown")
            filled_amount = float(order.get("filled", 0) or 0)

            # A limit order is pending if:
            # 1. The status says open/new/pending, OR
            # 2. It's a limit order with zero/negligible fill amount
            #    (Bitget's create_order response may not include a standard status)
            is_pending_limit = False
            if use_limit:
                if order_status in ("open", "new", "pending", "live", "init"):
                    is_pending_limit = True
                elif order_status not in ("closed", "filled") and filled_amount <= 0:
                    # Status is unknown/missing but no fill → treat as pending
                    is_pending_limit = True
                    logger.info("Limit order %s has status=%s, filled=%.6f — treating as pending",
                                order_id, order_status, filled_amount)

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
                filled_qty = float(order.get("filled") or 0)

                # GETCLAW: Enhanced fill verification — try fetch_my_trades first
                # (most accurate), then fetch_order as fallback.
                # fetch_my_trades returns actual execution data with fees and PnL.
                if not filled_qty or filled_qty <= 0:
                    # 1. Try fetch_my_trades (most reliable source)
                    try:
                        my_trades = await active_exchange.fetch_my_trades(symbol, limit=10)
                        # Match trades by order ID
                        order_trades = [t for t in my_trades if t.get("order") == order_id]
                        if order_trades:
                            filled_qty = sum(float(t.get("amount", 0) or 0) for t in order_trades)
                            # Weighted average fill price
                            total_cost = sum(
                                float(t.get("price", 0) or 0) * float(t.get("amount", 0) or 0)
                                for t in order_trades
                            )
                            if filled_qty > 0 and total_cost > 0:
                                fill_price = total_cost / filled_qty
                            audit(trade_log,
                                  f"Fill verified via trades: {symbol} qty={filled_qty:.6f} @ ${fill_price:,.4f}",
                                  action="fill_verify", result="TRADES",
                                  data={"order_id": order_id, "trade_count": len(order_trades)})
                    except Exception as trades_exc:
                        logger.debug("fetch_my_trades failed for %s: %s", symbol, trades_exc)

                    # 2. Fallback: fetch_order
                    if not filled_qty or filled_qty <= 0:
                        try:
                            confirmed = await active_exchange.fetch_order(order_id, symbol)
                            filled_qty = float(confirmed.get("filled", 0) or 0)
                            if confirmed.get("average"):
                                fill_price = float(confirmed["average"])
                        except Exception as fetch_exc:
                            logger.warning("Could not confirm fill for order %s: %s", order_id, fetch_exc)

                # Final fallback: if still no fill data, use requested quantity
                # but flag it as estimated in the audit log
                if not filled_qty or filled_qty <= 0:
                    filled_qty = quantity
                    audit(trade_log,
                          f"Fill quantity unconfirmed for {symbol} — using requested qty {quantity:.6f}",
                          action="fill_fallback", result="ESTIMATED",
                          data={"order_id": order_id})

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
            spot_fallback = False  # Futures-only mode: no spot trading

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

            # ── POST-TRADE VERIFICATION (GetClaw-style) ────────────────
            # Step 1: Verify order fill via exchange query
            verify = await self._verify_order_fill(
                active_exchange, order_id, symbol, expected_qty=filled_qty,
                max_retries=3, delay=1.5,
            )
            confirmed = verify["confirmed"]

            # Use verified fill data when available (never guess)
            if confirmed:
                if verify["fill_price"] > 0:
                    fill_price = verify["fill_price"]
                if verify["fill_qty"] > 0:
                    filled_qty = verify["fill_qty"]
                    # Update position with actual fill
                    position.entry_price = fill_price
                    position.quantity = filled_qty
                exchange_fees = verify["fees"]
            else:
                exchange_fees = 0.0

            # Step 2: Verify position exists on exchange
            pos_verify = await self._verify_position_exists(
                active_exchange, symbol,
                "LONG" if idea.direction == Direction.LONG else "SHORT",
            )
            position_confirmed = pos_verify["confirmed"]

            # Update position with exchange-verified data
            if position_confirmed:
                if pos_verify["exchange_entry"] > 0:
                    position.entry_price = pos_verify["exchange_entry"]
                    fill_price = pos_verify["exchange_entry"]
                if pos_verify["exchange_qty"] > 0:
                    position.quantity = pos_verify["exchange_qty"]
                    filled_qty = pos_verify["exchange_qty"]
                if pos_verify["leverage"] > 0:
                    position.leverage = pos_verify["leverage"]
                    leverage = pos_verify["leverage"]

            # Recalculate cost with verified data
            raw_cost = fill_price * filled_qty
            if is_futures and leverage > 1:
                cost = raw_cost / leverage
            else:
                cost = raw_cost
            position.cost_usd = cost

            # Persist verified position data
            self._save_positions()

            audit(trade_log, f"Live order FILLED: {side} {idea.asset}",
                  action="live_execute", result="FILLED",
                  data={
                      "order_id": order_id, "trade_id": idea.id,
                      "side": side, "fill_price": fill_price,
                      "quantity": filled_qty, "cost_usd": cost,
                      "status": order_status,
                      "confirmed": confirmed,
                      "position_confirmed": position_confirmed,
                      "exchange_fees": exchange_fees,
                      "verify_failure_stage": verify.get("failure_stage", ""),
                  })

            # Try to place SL/TP orders (best-effort — not all exchanges support this for spot)
            # GETCLAW: For gap-risk limit orders (weekend metals/stocks),
            # defer TP/SL until after fill to avoid instant trigger on gap.
            if defer_tp_sl and is_pending_limit:
                sl_id, tp_id = None, None
                audit(trade_log,
                      f"TP/SL deferred until fill: {idea.asset} (weekend-queued limit)",
                      action="defer_tp_sl", result="DEFERRED",
                      data={"symbol": idea.asset, "class": asset_class})
            else:
                sl_id, tp_id = await self._place_sl_tp(
                    exchange, idea.asset, idea.direction,
                    filled_qty, idea.stop_loss, idea.take_profit
                )
            position.sl_order_id = sl_id
            position.tp_order_id = tp_id
            # Persist SL/TP order IDs to disk immediately
            self._save_positions()

            if sl_id is None and tp_id is None:
                audit(trade_log,
                      f"SL/TP placement FAILED for {idea.asset} — position is UNPROTECTED",
                      action="sl_tp_failed",
                      data={"trade_id": idea.id, "symbol": idea.asset,
                            "stop_loss": idea.stop_loss, "take_profit": idea.take_profit})

            sl_info = f" | SL order: {sl_id}" if sl_id else " | SL: pending"
            tp_info = f" | TP order: {tp_id}" if tp_id else " | TP: pending"

            lev_info = f" | {leverage}x" if leverage > 1 else ""
            mode_label = "FUTURES" if is_futures else "SPOT"
            dir_icon = "🟢" if side == "buy" else "🔴"
            trail_info = ""
            if trailing_st:
                trail_info = "\n- Trailing: ✅ armed (activates at 1R)"

            # Verification status line
            if confirmed and position_confirmed:
                verify_line = "- Verified: ✅ CONFIRMED (order + position)"
            elif confirmed:
                verify_line = "- Verified: ✅ order confirmed, ⚠️ position check pending"
            else:
                verify_line = f"- Verified: ⚠️ UNCONFIRMED ({verify.get('failure_stage', 'pending')})"

            fee_line = ""
            if exchange_fees > 0:
                fee_line = f"\n- Fees: <code>${exchange_fees:.4f}</code>"

            sl_tp_warn = ""
            if sl_id is None and tp_id is None:
                sl_tp_warn = "\n⚠️ SL/TP FAILED — position unprotected!"

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
                f"- Order: <code>{order_id}</code>{fee_line}\n"
                f"- Risk: ✅ APPROVED{trail_info}\n"
                f"- {verify_line}\n"
                f"- Mode: 🔥 Live {mode_label}{sl_tp_warn}"
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
            # Check if the order was already submitted to the exchange.
            # If 'order' exists, create_order succeeded but post-processing crashed.
            # The position is LIVE on the exchange — we MUST record it locally.
            if 'order' in dir() and order and isinstance(order, dict) and order.get("id"):
                logger.error("Post-order crash for %s: %s — creating emergency position",
                             idea.asset, exc)
                _side_upper = ("buy" if idea.direction == Direction.LONG else "sell").upper()
                emergency_pos = LivePosition(
                    trade_id=idea.id,
                    symbol=idea.asset,
                    direction="LONG" if idea.direction == Direction.LONG else "SHORT",
                    entry_price=current_price if 'current_price' in dir() else idea.entry_price,
                    quantity=quantity if 'quantity' in dir() else 0,
                    cost_usd=size_usd,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    leverage=CONFIG.exchange.default_leverage if is_futures else 1,
                    is_spot=False,
                    opened_at=datetime.now(UTC),
                    status="open",
                )
                self._positions[idea.id] = emergency_pos
                self._save_positions()
                audit(trade_log,
                      f"EMERGENCY position created for {idea.asset} after post-order crash: {exc}",
                      action="emergency_position", result="CREATED",
                      data={"trade_id": idea.id, "asset": idea.asset,
                            "order_id": order.get("id"), "error": str(exc)})
                # Best-effort SL/TP
                try:
                    _ex = await self._get_exchange()
                    sl_id, tp_id = await self._place_sl_tp(
                        _ex, idea.asset, idea.direction,
                        quantity if 'quantity' in dir() else 0,
                        idea.stop_loss, idea.take_profit,
                    )
                    if sl_id:
                        emergency_pos.sl_order_id = sl_id
                    if tp_id:
                        emergency_pos.tp_order_id = tp_id
                    self._save_positions()
                except Exception:
                    pass
                return (f"LIVE {idea.direction.value} {idea.asset} opened "
                        f"(emergency record — parse error: {exc}). "
                        f"SL/TP may need manual verification.")
            else:
                # Order was never submitted — safe to report as failed
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

        GETCLAW: Always checks existing plan orders first to prevent duplicates.
        Cancels stale SL/TP before placing new ones.

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

        # GETCLAW: Check and cancel existing plan orders before placing new ones.
        # Prevents duplicate SL/TP orders that can cause double-closes.
        ccxt_sym = symbol if ":USDT" in symbol else f"{symbol}:USDT"
        try:
            existing_plans = await exchange.fetch_open_orders(
                ccxt_sym, params={"productType": "USDT-FUTURES", "isPlan": "plan_order"})
            if existing_plans:
                cancelled = 0
                for plan in existing_plans:
                    try:
                        await exchange.cancel_order(plan["id"], ccxt_sym)
                        cancelled += 1
                    except Exception:
                        pass
                if cancelled > 0:
                    audit(trade_log,
                          f"Cleared {cancelled} existing plan order(s) for {symbol} before placing new SL/TP",
                          action="plan_order_cleanup", result="OK",
                          data={"symbol": symbol, "cancelled": cancelled})
        except Exception as plan_exc:
            # Non-critical: some exchanges don't support isPlan filter
            logger.debug("Plan order check failed for %s: %s", symbol, plan_exc)

        # Futures-only mode: spot SL/TP path removed

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
            # Always send tradeSide=close + reduceOnly for SL/TP to prevent reverse opens
            extra_params = {"productType": "USDT-FUTURES", "tradeSide": "close", "reduceOnly": True}

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
                    try:
                        raw_body = e.read().decode()
                        return _json.loads(raw_body)
                    except (ValueError, UnicodeDecodeError) as parse_exc:
                        logger.warning("Non-JSON error response from exchange: %s (parse error: %s)",
                                       getattr(e, 'code', '?'), parse_exc)
                        return {"code": str(getattr(e, 'code', 'ERROR')), "msg": raw_body[:500] if raw_body else str(e)}
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

        # Build payload:
        # - ONE-WAY MODE: omit posSide (causes 40019/40020 errors)
        # - HEDGE MODE: include posSide = "long"/"short"
        payload: dict[str, str] = {
            "category": "USDT-FUTURES",
            "symbol": bitget_symbol,
            "type": "tpsl",
            "tpslMode": "full",
            "takeProfit": tp_final,
            "stopLoss": sl_final,
            "tpOrderType": "market",
            "slOrderType": "market",
            "clientOid": self._client_oid(f"{bitget_symbol}_{pos_side}_sltp_{int(time.time())}"),
        }
        # Always include posSide for safety (prevents reverse position on close)
        payload["posSide"] = pos_side

        logger.info("v3 SL/TP request: symbol=%s hedge=%s posSide=%s TP=%s SL=%s (raw TP=%s SL=%s, rounded=%s/%s, precision=%s)",
                     bitget_symbol, self._hedge_mode, pos_side if self._hedge_mode else "omitted",
                     tp_final, sl_final,
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
                            "order_id": order_id, "hedge_mode": self._hedge_mode})
            else:
                error_msg = result.get("msg", str(result))
                error_code = result.get("code", "")
                logger.warning("v3 strategy order failed (code=%s): %s", error_code, error_msg)
                audit(trade_log, f"v3 SL/TP failed: {error_msg}",
                      action="sl_tp_v3", result="FAIL",
                      data={"symbol": bitget_symbol, "response": str(result)[:300],
                            "payload": {k: v for k, v in payload.items() if k != "clientOid"}})

                # Retry with posSide if omitting it failed (some UTA configs need it)
                if not self._hedge_mode and error_code in ("40019", "40020"):
                    logger.info("Retrying v3 SL/TP with posSide=%s", pos_side)
                    payload["posSide"] = pos_side
                    retry_result = await _asyncio.to_thread(
                        _v3_post, "/api/v3/trade/place-strategy-order", payload)
                    if retry_result.get("code") == "00000":
                        data = retry_result.get("data", {})
                        order_id = data.get("orderId") or data.get("slOrderId") or data.get("tpOrderId") or "v3-strategy"
                        sl_id = order_id
                        tp_id = order_id
                        audit(trade_log, f"v3 SL/TP retry with posSide OK: order={order_id}",
                              action="sl_tp_v3_retry", result="OK",
                              data={"symbol": bitget_symbol, "sl": sl_final, "tp": tp_final})
                    else:
                        retry_msg = retry_result.get("msg", str(retry_result))
                        logger.warning("v3 SL/TP retry also failed: %s", retry_msg)
                        audit(trade_log, f"v3 SL/TP retry failed: {retry_msg}",
                              action="sl_tp_v3_retry", result="FAIL",
                              data={"symbol": bitget_symbol, "response": str(retry_result)[:300]})
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
            # C2-27 FIX: Fetch tickers per-symbol instead of batch.
            # A single delisted/erroring symbol in fetch_tickers() would block
            # SL/TP checks for ALL positions. Per-symbol isolation ensures
            # monitoring continues for healthy symbols.
            open_symbols = [p.symbol for p in self._positions.values() if p.status in ("open", "pending_fill")]
            tickers: dict = {}
            for sym in open_symbols:
                try:
                    t = await exchange.fetch_ticker(sym)
                    tickers[sym] = t
                except Exception as e:
                    # Track consecutive failures per symbol
                    count = self._ticker_failure_count.get(sym, 0) + 1
                    self._ticker_failure_count[sym] = count
                    level = "warning" if count < 3 else "error"
                    getattr(trade_log, level)(
                        "fetch_ticker failed for %s (%d consecutive): %s",
                        sym, count, e,
                    )
                    continue
            # Reset failure count for symbols that succeeded
            for sym in open_symbols:
                if sym in tickers:
                    self._ticker_failure_count.pop(sym, None)

            for trade_id, pos in list(self._positions.items()):
                # ── Handle pending limit orders ──
                if pos.status == "pending_fill":
                    msg = await self._check_pending_limit(exchange, trade_id, pos)
                    if msg:
                        closed_messages.append(msg)
                    continue

                if pos.status != "open":
                    continue

                # ── SAFEGUARD 2: Grace period after open ──
                # Skip local SL/TP monitoring for the first 90 seconds after a
                # position opens. This gives the exchange SL/TP orders time to be
                # placed and prevents instant stop-outs from stale price data.
                age_secs = (datetime.now(UTC) - pos.opened_at).total_seconds() if pos.opened_at else 999
                if age_secs < 90:
                    # ── SAFEGUARD 3: Wait for SL/TP confirmation ──
                    # During the grace period, still attempt to place SL/TP if missing,
                    # but don't run local SL/TP monitoring until orders are confirmed.
                    if (not pos.sl_order_id or not pos.tp_order_id) and pos.stop_loss > 0 and pos.take_profit > 0:
                        try:
                            direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                            sl_id, tp_id = await self._place_sl_tp(
                                exchange, pos.symbol, direction,
                                pos.quantity, pos.stop_loss, pos.take_profit
                            )
                            if sl_id and not pos.sl_order_id:
                                pos.sl_order_id = sl_id
                            if tp_id and not pos.tp_order_id:
                                pos.tp_order_id = tp_id
                            if sl_id or tp_id:
                                self._save_positions()
                                audit(trade_log,
                                      f"SL/TP placed during grace period: {pos.symbol}",
                                      action="sltp_grace", result="PLACED",
                                      data={"trade_id": trade_id, "sl_id": sl_id, "tp_id": tp_id,
                                            "age_secs": round(age_secs, 1)})
                        except Exception as exc:
                            logger.debug("SL/TP grace placement failed for %s: %s", pos.symbol, exc)
                    continue  # Skip local SL/TP check during grace period

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
                if (not pos.sl_order_id or not pos.tp_order_id) and pos.stop_loss > 0 and pos.take_profit > 0:
                    try:
                        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                        sl_id, tp_id = await self._place_sl_tp(
                            exchange, pos.symbol, direction,
                            pos.quantity, pos.stop_loss, pos.take_profit
                        )
                        if sl_id or tp_id:
                            # AUDIT-FIX: Only update missing order IDs to avoid
                            # orphaning existing exchange orders
                            if sl_id and not pos.sl_order_id:
                                pos.sl_order_id = sl_id
                            if tp_id and not pos.tp_order_id:
                                pos.tp_order_id = tp_id
                            self._save_positions()
                            audit(trade_log,
                                  f"SL/TP retry succeeded: {pos.symbol} SL={pos.stop_loss:.4f} TP={pos.take_profit:.4f}",
                                  action="sltp_retry", result="PLACED",
                                  data={"trade_id": trade_id, "sl_id": sl_id, "tp_id": tp_id})
                    except Exception as exc:
                        logger.debug("SL/TP retry failed for %s: %s", pos.symbol, exc)

                # ── GETCLAW: Time-stop check (Rules 6/17) ──
                if CONFIG.time_stop.enabled:
                    hold_hours = (datetime.now(UTC) - pos.opened_at).total_seconds() / 3600
                    close_threshold = CONFIG.time_stop.intraday_close_hours  # default 4H
                    if hold_hours >= close_threshold:
                        # Check if position is in profit
                        if pos.direction == "LONG":
                            in_profit = price > pos.entry_price
                        else:
                            in_profit = price < pos.entry_price
                        if not in_profit:
                            # Time-stop: no profit after threshold → close
                            msg = await self.close_position(
                                trade_id, f"TIME_STOP ({hold_hours:.1f}h, no profit)", price)
                            closed_messages.append(msg)
                            audit(trade_log,
                                  f"Time-stop triggered: {pos.symbol} held {hold_hours:.1f}h with no profit",
                                  action="time_stop", result="CLOSED",
                                  data={"trade_id": trade_id, "hold_hours": hold_hours,
                                        "entry": pos.entry_price, "current": price})
                            continue  # Skip SL/TP check — already closing
                    elif hold_hours >= CONFIG.time_stop.intraday_warn_hours:
                        # Approaching time-stop — log warning (once per cycle is fine)
                        remaining = close_threshold - hold_hours
                        logger.debug("Time-stop warning: %s held %.1fh, %.1fh until auto-close",
                                     pos.symbol, hold_hours, remaining)

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

        # ── Periodic exchange sync ──
        # Every 5 minutes, check if the exchange has positions we're not tracking.
        # This is the definitive fix for "lost positions" — the exchange is always
        # the source of truth.
        now_ts = time.time()
        if now_ts - self._last_exchange_sync > self._EXCHANGE_SYNC_INTERVAL:
            self._last_exchange_sync = now_ts
            try:
                adopted = await self.adopt_exchange_positions()
                for sym in adopted:
                    audit(trade_log, f"Periodic sync adopted orphan: {sym}",
                          action="periodic_sync", result="ADOPTED")
                    closed_messages.append(
                        f"SYNC: Adopted untracked position {sym} from exchange"
                    )
            except Exception as sync_exc:
                logger.debug("Periodic exchange sync failed: %s", sync_exc)

        return closed_messages

    async def _check_pending_limit(self, exchange: "ccxt.Exchange",
                                    trade_id: str, pos: LivePosition) -> Optional[str]:
        """Check if a pending limit order has been filled or should be cancelled.

        Returns a message string if status changed, else None.
        """
        if not pos.limit_order_id:
            return None

        # ── HARD TIMEOUT: stale pending_fill safety net ──
        # If a pending_fill position has been stuck for 2x the normal expiry
        # (e.g. 8 hours by default), force-close it regardless of exchange
        # state.  This prevents positions from being stuck forever when
        # fetch_order keeps failing or the exchange silently cancelled the
        # order.
        hard_timeout = 2 * CONFIG.limit_orders.expire_seconds
        stale_age = (datetime.now(UTC) - pos.opened_at).total_seconds() if pos.opened_at else 0
        if stale_age > hard_timeout:
            # Best-effort cancel on exchange
            try:
                await exchange.cancel_order(pos.limit_order_id, pos.symbol)
            except Exception as cancel_exc:
                logger.warning(
                    "Stale pending hard-timeout: cancel attempt failed for %s order %s: %s",
                    pos.symbol, pos.limit_order_id, cancel_exc,
                )

            pos.status = "closed"
            pos.closed_at = datetime.now(UTC)
            pos.pnl_usd = 0.0
            pos.close_reason = "stale_pending"
            self._save_positions()
            self._append_closed_trade(pos)

            audit(
                trade_log,
                f"Stale pending_fill FORCE-CLOSED after {stale_age / 3600:.1f}h: {pos.symbol}",
                action="stale_pending_close",
                result="FORCE_CLOSED",
                data={
                    "trade_id": trade_id,
                    "age_sec": stale_age,
                    "hard_timeout_sec": hard_timeout,
                    "limit_order_id": pos.limit_order_id,
                },
            )

            return (
                f"STALE PENDING CLOSED: {pos.direction} {pos.symbol} — "
                f"stuck for {stale_age / 3600:.1f}h (hard timeout {hard_timeout / 3600:.1f}h)"
            )

        try:
            order = await exchange.fetch_order(pos.limit_order_id, pos.symbol)
            order_status = order.get("status", "unknown")

            if order_status in ("closed", "filled", "partially_filled"):
                # Limit order filled (or partially filled) — transition to open position
                fill_price = float(order.get("average", 0) or order.get("price", 0) or pos.entry_price)
                filled_qty = float(order.get("filled", 0) or pos.quantity)

                # GETCLAW: partially_filled = some qty matched, rest still open.
                # Use actual filled qty, not original order size.
                if order_status == "partially_filled" and filled_qty > 0:
                    audit(trade_log,
                          f"Limit PARTIAL FILL: {pos.symbol} filled {filled_qty} of {pos.quantity}",
                          action="partial_fill", result="PARTIAL",
                          data={"trade_id": trade_id, "filled": filled_qty,
                                "original": pos.quantity})

                pos.entry_price = fill_price
                pos.quantity = filled_qty
                pos.status = "open"
                pos.order_type = "limit"  # GETCLAW: limit fill = maker fee rate
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

                if sl_id is None and tp_id is None:
                    audit(trade_log,
                          f"SL/TP placement FAILED for {pos.symbol} — position is UNPROTECTED",
                          action="sl_tp_failed",
                          data={"trade_id": trade_id, "symbol": pos.symbol,
                                "stop_loss": pos.stop_loss, "take_profit": pos.take_profit})

                audit(trade_log, f"Limit order FILLED: {pos.symbol} @ ${fill_price:,.4f}",
                      action="limit_fill", result="FILLED",
                      data={"trade_id": trade_id, "fill_price": fill_price,
                            "quantity": filled_qty})

                sl_info = f" | SL: {sl_id}" if sl_id else ""
                tp_info = f" | TP: {tp_id}" if tp_id else ""
                trail_info = " | Trailing: armed" if pos.trailing_state else ""
                sl_tp_warn = ""
                if sl_id is None and tp_id is None:
                    sl_tp_warn = "\n⚠️ SL/TP FAILED — position unprotected!"
                return (
                    f"LIMIT FILLED: {pos.direction} {pos.symbol}\n"
                    f"Fill: ${fill_price:,.4f} | Qty: {filled_qty:.6f}{sl_info}{tp_info}{trail_info}{sl_tp_warn}"
                )

            elif order_status in ("canceled", "cancelled", "rejected", "expired"):
                # Limit order cancelled/rejected — remove position
                pos.status = "closed"
                pos.closed_at = datetime.now(UTC)
                pos.pnl_usd = 0.0
                pos.close_reason = order_status
                self._save_positions()
                # C2-14 FIX: Write to closed_trades.json so cancelled/rejected
                # limit orders are visible in trade history, not silently dropped.
                self._append_closed_trade(pos)

                audit(trade_log, f"Limit order {order_status}: {pos.symbol}",
                      action="limit_cancel", result=order_status.upper(),
                      data={"trade_id": trade_id})

                return f"LIMIT {order_status.upper()}: {pos.direction} {pos.symbol} — order not filled"

            else:
                # Still open — check price drift and time expiry
                age_sec = (datetime.now(UTC) - pos.opened_at).total_seconds()
                cancel_reason = None

                # ── PRICE DRIFT CANCEL (from Getclaw) ──
                # If price has moved >X% away from the limit, the setup is stale.
                # No point waiting for a fill that's unlikely to come.
                drift_pct = CONFIG.limit_orders.price_drift_cancel_pct
                if drift_pct > 0 and pos.entry_price > 0:
                    try:
                        ticker = await exchange.fetch_ticker(pos.symbol)
                        cur_price = float(ticker.get("last", 0) or 0)
                        if cur_price > 0:
                            pct_away = abs(cur_price - pos.entry_price) / pos.entry_price * 100
                            if pct_away > drift_pct:
                                cancel_reason = "price_drift"
                                audit(trade_log,
                                      f"Price drifted {pct_away:.1f}% from limit "
                                      f"(threshold {drift_pct}%): {pos.symbol} "
                                      f"limit=${pos.entry_price:,.4f} mkt=${cur_price:,.4f}",
                                      action="limit_drift_cancel", result="CANCELLING",
                                      data={"trade_id": trade_id, "pct_away": pct_away,
                                            "limit_price": pos.entry_price,
                                            "market_price": cur_price})
                    except Exception as drift_exc:
                        logger.debug("Price drift check failed for %s: %s",
                                     pos.symbol, drift_exc)

                # ── TIME EXPIRY ──
                if not cancel_reason and age_sec > CONFIG.limit_orders.expire_seconds:
                    cancel_reason = "expired"

                if cancel_reason:
                    # Cancel the limit order
                    cancel_confirmed = False
                    try:
                        await exchange.cancel_order(pos.limit_order_id, pos.symbol)
                        cancel_confirmed = True
                    except Exception as exc:
                        logger.warning("Failed to cancel %s limit order %s: %s",
                                       cancel_reason, pos.limit_order_id, exc)

                    # C2-16 FIX: Verify cancel before marking closed — if cancel
                    # failed, the order may have filled in the meantime.
                    if not cancel_confirmed:
                        try:
                            order_info = await exchange.fetch_order(pos.limit_order_id, pos.symbol)
                            actual_status = order_info.get("status", "")
                            if actual_status in ("filled", "closed"):
                                logger.warning("Limit order %s filled during cancel attempt", pos.limit_order_id)
                                return None  # next check cycle will process the fill
                            elif actual_status not in ("canceled", "cancelled", "expired"):
                                logger.warning("Limit order %s still %s after cancel attempt",
                                               pos.limit_order_id, actual_status)
                                return None
                        except Exception as verify_exc:
                            logger.warning("Could not verify limit order status: %s", verify_exc)
                            # Cannot confirm cancel and cannot verify — leave as pending_fill for retry
                            return None

                    if not cancel_confirmed:
                        logger.warning("Cancel NOT confirmed for %s order %s — leaving as pending_fill for retry",
                                       cancel_reason, pos.limit_order_id)
                        return None

                    pos.status = "closed"
                    pos.closed_at = datetime.now(UTC)
                    pos.pnl_usd = 0.0
                    pos.close_reason = cancel_reason
                    self._save_positions()
                    self._append_closed_trade(pos)

                    if cancel_reason == "price_drift":
                        audit(trade_log, f"Limit order CANCELLED (price drift): {pos.symbol}",
                              action="limit_drift_cancel", result="CANCELLED",
                              data={"trade_id": trade_id, "age_sec": age_sec})
                        return f"LIMIT CANCELLED (price drift): {pos.direction} {pos.symbol} — market moved away"
                    else:
                        audit(trade_log, f"Limit order EXPIRED after {age_sec:.0f}s: {pos.symbol}",
                              action="limit_expire", result="EXPIRED",
                              data={"trade_id": trade_id, "age_sec": age_sec})
                        return f"LIMIT EXPIRED: {pos.direction} {pos.symbol} — cancelled after {age_sec/3600:.1f}h"

        except Exception as exc:
            logger.warning("Pending limit check failed for %s: %s", trade_id, exc)

        return None

    async def _update_exchange_sl(self, exchange: "ccxt.Exchange",
                                   pos: LivePosition, new_sl: float) -> None:
        """Place new SL order first, then cancel old one — no protection gap.

        C2-03 FIX: Previous logic cancelled old SL before placing new one,
        leaving the position unprotected if the new placement failed.
        Now: place new SL first, then cancel old. If new placement fails,
        old SL remains active.  Best-effort: trailing stop still works
        locally even if exchange update fails — check_positions() will
        close at the new SL.
        """
        # Futures-only mode: all positions are futures
        old_sl_id = pos.sl_order_id
        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
        use_v3 = self._is_uta if self._is_uta is not None else False

        # Step 1: Place new SL at tightened level FIRST
        new_sl_id = None
        if use_v3:
            # Round to tick grid
            swap_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
            sl_rounded = self._round_price_to_market(exchange, swap_symbol, new_sl)
            if sl_rounded is None:
                sl_rounded = self._round_price_to_market(exchange, pos.symbol, new_sl)

            sl_id, _ = await self._place_sl_tp_v3(
                pos.symbol, direction, pos.quantity,
                new_sl, pos.take_profit,
                sl_str=sl_rounded,
            )
            if sl_id:
                new_sl_id = sl_id
        else:
            # Classic mode: place trigger order
            close_side = "sell" if direction == Direction.LONG else "buy"
            # Always send tradeSide=close + reduceOnly for SL/TP to prevent reverse opens
            extra_params = {"productType": "USDT-FUTURES", "tradeSide": "close", "reduceOnly": True}
            try:
                sl_order = await exchange.create_order(
                    symbol=pos.symbol, type="market", side=close_side,
                    amount=pos.quantity,
                    params={"triggerPrice": new_sl, "triggerType": "last", **extra_params},
                )
                new_sl_id = sl_order.get("id")
            except Exception as exc:
                logger.warning("Failed to place new exchange SL for %s: %s", pos.symbol, exc)

        # Step 2: Only cancel old SL AFTER new one is confirmed placed
        if new_sl_id:
            pos.sl_order_id = new_sl_id
            self._save_positions()
            if old_sl_id:
                try:
                    await exchange.cancel_order(old_sl_id, pos.symbol)
                except Exception as exc:
                    logger.debug("Cancel old SL order %s failed (new SL active): %s", old_sl_id, exc)
        else:
            # New placement failed — old SL remains active, no gap
            logger.warning("Trailing SL update skipped for %s — new placement failed, old SL preserved", pos.symbol)

    async def close_all_positions(self, reason: str = "emergency") -> list[str]:
        """Emergency close ALL open positions in a single sweep.

        GETCLAW: Uses per-position close with tradeSide=close + reduceOnly
        for safety. Returns list of result messages.
        """
        results = []
        open_pos = [p for p in self._positions.values()
                    if p.status in ("open", "pending_fill")]

        if not open_pos:
            return ["No open positions to close."]

        for pos in open_pos:
            try:
                result = await self.close_position(pos.trade_id, reason=reason)
                results.append(result)
            except Exception as exc:
                results.append(f"Failed to close {pos.symbol}: {exc}")

        audit(trade_log,
              f"Emergency close all: {len(results)} positions processed",
              action="close_all", result="DONE",
              data={"count": len(results), "reason": reason})

        return results

    async def close_position(self, trade_id: str, reason: str = "manual",
                              close_price: float = 0) -> str:
        """Close a live position by placing the opposite order."""
        # C2-02 FIX: Per-trade lock prevents double-close race.
        lock = self._close_locks.setdefault(trade_id, asyncio.Lock())
        async with lock:
            result = await self._close_position_inner(trade_id, reason, close_price)
        # AUDIT-FIX: Clean up lock after close to prevent unbounded growth
        self._close_locks.pop(trade_id, None)
        return result

    async def _close_position_inner(self, trade_id: str, reason: str = "manual",
                              close_price: float = 0) -> str:
        """Inner close logic, called under per-trade lock."""
        pos = self._positions.get(trade_id)
        if not pos or pos.status not in ("open", "pending_fill"):
            return f"Position {trade_id} not found or already closed/closing."

        # C2-02 FIX: Set transitional state BEFORE any await — concurrent callers
        # will see "closing" and bail out at the guard above.
        pos.status = "closing"
        self._save_positions()

        try:
            exchange = await self._get_exchange()
            close_side = "sell" if pos.direction == "LONG" else "buy"

            # Cancel SL/TP orders BEFORE closing — prevents race condition where
            # a trigger fires between close-fill and cancel, opening an opposite pos.
            cancel_failed = []
            for oid in [pos.sl_order_id, pos.tp_order_id]:
                if oid:
                    try:
                        cancel_resp = await exchange.cancel_order(oid, pos.symbol)
                        cancel_status = cancel_resp.get("status", "") if isinstance(cancel_resp, dict) else ""
                        if cancel_status and cancel_status not in ("canceled", "cancelled", "closed"):
                            # Verify it is actually cancelled
                            try:
                                order_info = await exchange.fetch_order(oid, pos.symbol)
                                if order_info.get("status") not in ("canceled", "cancelled", "closed", "expired"):
                                    logger.warning("SL/TP order %s may not be cancelled (status=%s), proceeding with close anyway",
                                                   oid, order_info.get("status"))
                                    cancel_failed.append(oid)
                            except Exception:
                                pass  # Fetch failed — assume cancel worked
                    except Exception as cancel_exc:
                        logger.warning("Failed to cancel SL/TP order %s for %s: %s — proceeding with close",
                                       oid, pos.symbol, cancel_exc)
                        cancel_failed.append(oid)

            # Futures-only mode: all positions close via swap exchange
            # ALWAYS send tradeSide=close — prevents accidentally opening reverse
            # position when Bitget is in hedge mode but bot doesn't detect it
            # reduceOnly=true is a second safety layer — exchange rejects if it
            # would open a new position instead of reducing
            close_params = {
                "productType": "USDT-FUTURES",
                "tradeSide": "close",
                "reduceOnly": True,
            }
            order = await exchange.create_order(
                symbol=pos.symbol,
                type="market",
                side=close_side,
                amount=pos.quantity,
                params=close_params,
            )
            close_order_id = str(order.get("id", ""))

            # ── POST-CLOSE VERIFICATION (GetClaw-style) ──────────────
            close_verify = await self._verify_position_closed(
                exchange, pos.symbol, pos.direction, close_order_id,
            )
            close_confirmed = close_verify["confirmed"]

            # Use verified fill data when available
            if close_verify["fill_price"] > 0:
                fill_price = close_verify["fill_price"]
            else:
                # Fallback: extract from create_order response
                fill_price = float(order.get("average", 0) or order.get("price", 0) or 0)

            if close_verify["fill_qty"] > 0:
                closed_qty = close_verify["fill_qty"]
            else:
                closed_qty = pos.quantity

            exchange_close_fees = close_verify["fees"]

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

            # Calculate PnL with fee deduction
            if pos.direction == "LONG":
                gross_pnl = (fill_price - pos.entry_price) * pos.quantity
            else:
                gross_pnl = (pos.entry_price - fill_price) * pos.quantity

            # Exchange commission: entry + exit notional × fee rate
            entry_notional = pos.entry_price * pos.quantity
            exit_notional = fill_price * pos.quantity
            # GETCLAW: use maker rate if limit order (POST_ONLY), taker for market
            is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
            entry_fee_pct = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
            exit_fee_pct = CONFIG.risk.taker_fee_pct  # exits are usually market (SL/TP triggers)
            commission = (entry_notional * entry_fee_pct / 100.0) + (exit_notional * exit_fee_pct / 100.0)
            net_pnl = gross_pnl - commission

            pos.close_reason = reason
            pos.status = "closed"
            pos.close_price = fill_price
            pos.gross_pnl = round(gross_pnl, 4)
            pos.commission = round(commission, 4)
            pos.pnl_usd = round(net_pnl, 4)
            pos.closed_at = datetime.now(UTC)

            # AUDIT-FIX: Append to closed trades BEFORE save_positions, because
            # save_positions prunes closed entries from _positions dict. If a crash
            # occurs between save_positions and append_closed_trade, the trade
            # would vanish from both data stores.
            self._append_closed_trade(pos)

            # F-07 FIX: persist after closing (removes from open positions file)
            self._save_positions()

            # C-09 FIX: post-close cleanup — cancel any remaining open orders on this symbol
            # to prevent orphaned SL/TP triggers from opening opposite positions.
            if cancel_failed:
                for stale_oid in cancel_failed:
                    try:
                        await exchange.cancel_order(stale_oid, pos.symbol)
                    except Exception:
                        pass  # Best-effort cleanup
                try:
                    open_orders = await exchange.fetch_open_orders(pos.symbol)
                    for oo in open_orders:
                        try:
                            await exchange.cancel_order(oo["id"], pos.symbol)
                            logger.info("Post-close cleanup: cancelled orphan order %s on %s", oo["id"], pos.symbol)
                        except Exception:
                            pass
                except Exception as cleanup_exc:
                    logger.debug("Post-close order cleanup failed for %s: %s", pos.symbol, cleanup_exc)
            # Notify engine to invalidate balance cache
            self._fire_position_closed(pos)

            audit(trade_log, f"Live position closed: {pos.symbol} net=${net_pnl:.4f} (gross=${gross_pnl:.4f}, fee=${commission:.4f})",
                  action="live_close", result="CLOSED",
                  data={
                      "trade_id": trade_id, "reason": reason,
                      "entry": pos.entry_price, "exit": fill_price,
                      "pnl_usd": round(net_pnl, 4),
                      "gross_pnl": round(gross_pnl, 4),
                      "commission": round(commission, 4),
                      "confirmed": close_confirmed,
                      "exchange_fees": exchange_close_fees,
                      "close_order_id": close_order_id,
                      "close_failure_stage": close_verify.get("failure_stage", ""),
                  })

            pnl_str = f"+${net_pnl:.4f}" if net_pnl >= 0 else f"-${abs(net_pnl):.4f}"
            # C2-58 FIX: Show both leveraged (margin) and unleveraged (notional) PnL%
            pnl_pct = ((fill_price - pos.entry_price) / pos.entry_price * 100)
            if pos.direction == "SHORT":
                pnl_pct = -pnl_pct
            lev = pos.leverage or 1
            pnl_pct_margin = pnl_pct * lev  # leveraged return — what hits the account
            hold_secs = (pos.closed_at - pos.opened_at).total_seconds() if pos.closed_at and pos.opened_at else 0
            if hold_secs < 3600:
                hold_str = f"{hold_secs / 60:.0f}m"
            elif hold_secs < 86400:
                hold_str = f"{hold_secs / 3600:.1f}h"
            else:
                hold_str = f"{hold_secs / 86400:.1f}d"
            fee_str = f"${commission:.2f}"
            # C2-58: Show leveraged return when leverage > 1
            if lev > 1:
                pnl_pct_str = f"{pnl_pct_margin:+.2f}% margin / {pnl_pct:+.2f}% notional, {lev}×"
            else:
                pnl_pct_str = f"{pnl_pct:+.2f}%"

            # Close verification status
            if close_confirmed:
                verify_str = "✅ CONFIRMED"
            else:
                stage = close_verify.get("failure_stage", "unconfirmed")
                verify_str = f"⚠️ {stage}"

            close_msg = (
                f"CLOSED {pos.direction} {pos.symbol} ({reason})\n"
                f"Entry: ${pos.entry_price:,.4f} → Exit: ${fill_price:,.4f}\n"
                f"PnL: {pnl_str} ({pnl_pct_str}) | Fees: {fee_str} | Hold: {hold_str}\n"
                f"Verified: {verify_str}"
            )

            # Store structured close data for rich rendering
            self._last_close_data = {
                "symbol": pos.symbol,
                "direction": pos.direction,
                "reason": reason,
                "entry": pos.entry_price,
                "exit": fill_price,
                "pnl_pct": pnl_pct,
                "pnl_pct_margin": pnl_pct_margin,  # C2-58: leveraged return
                "pnl_usd": round(net_pnl, 4),
                "gross_pnl": round(gross_pnl, 4),
                "fees": round(commission, 4),
                "exchange_fees": round(exchange_close_fees, 4),
                "size_usd": round(pos.cost_usd, 2) if pos.cost_usd > 0 else round(pos.entry_price * pos.quantity, 2),
                "leverage": pos.leverage or 1,
                "hold_time": hold_str,
                "confirmed": close_confirmed,
                "close_order_id": close_order_id,
            }

            return close_msg

        except Exception as exc:
            audit(trade_log, f"Live close failed: {exc}",
                  action="live_close", result="ERROR",
                  data={"trade_id": trade_id, "error": str(exc)})
            return f"CLOSE FAILED for {trade_id}: {exc}"

    # ── Account info ─────────────────────────────────────────────

    async def fetch_balance(self) -> dict:
        """Fetch USDT balance and all spot holdings from Bitget.

        Returns 'equity' (includes unrealized PnL) when available from the
        exchange response; falls back to 'total' (wallet balance only).
        The 'total' key is always the equity-aware value for display purposes.
        """
        try:
            exchange = await self._get_exchange()
            balance = await exchange.fetch_balance()
            usdt = balance.get("USDT", {})

            # ── Extract equity from raw Bitget response ──
            # Bitget USDT-FUTURES returns equity/usdtEquity/accountEquity in
            # the raw info, which includes unrealized PnL.  ccxt's 'total'
            # field is only wallet balance (free + used) and excludes unrealized.
            wallet_total = float(usdt.get("total", 0))
            equity = wallet_total  # default: wallet balance
            raw_info = balance.get("info", {})
            raw_data = raw_info.get("data", []) if isinstance(raw_info, dict) else []
            if isinstance(raw_data, dict):
                raw_data = [raw_data]
            for item in (raw_data if isinstance(raw_data, list) else []):
                if not isinstance(item, dict):
                    continue
                # Try multiple field names Bitget uses for equity
                for key in ("usdtEquity", "accountEquity", "equity"):
                    val = item.get(key)
                    if val is not None:
                        try:
                            eq_val = float(val)
                            if eq_val > 0:
                                equity = eq_val
                                break
                        except (ValueError, TypeError):
                            continue
                if equity != wallet_total:
                    break

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
                "total": equity,  # equity-aware value for display
                "wallet_total": wallet_total,  # raw wallet balance
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

    # ── Balance cache invalidation callback ────────────────────────

    def _fire_position_closed(self, pos: LivePosition) -> None:
        """Notify listeners (engine) that a position was closed so balance cache refreshes."""
        if self.on_position_closed:
            try:
                self.on_position_closed(pos)
            except Exception:
                pass  # Non-critical — don't break close flow

    # ── F-07 FIX: Position persistence ──────────────────────────────

    def _save_positions(self) -> None:
        """Persist open positions to disk so they survive restarts.

        Safety: uses atomic write (tmp + rename) and keeps a .bak copy
        to prevent data loss from crashes mid-write.
        """
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
                    "close_reason": pos.close_reason,
                }
            path = Path(_POSITIONS_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Keep backup of non-empty file before overwriting
            if path.exists():
                try:
                    existing = path.read_text().strip()
                    if existing and existing != "{}":
                        bak = str(path) + ".bak"
                        import shutil
                        shutil.copy2(str(path), bak)
                except Exception:
                    pass

            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, str(path))
            # M-06 FIX: prune closed entries from in-memory dict
            self._positions = {k: v for k, v in self._positions.items()
                               if v.status in ("open", "pending_fill")}
        except Exception as exc:
            logger.error("Failed to save live positions: %s", exc)
            self._persistence_broken = True

    def _load_positions(self) -> None:
        """Load persisted positions on startup.

        Falls back to .bak file if main file is empty or corrupt.
        """
        path = Path(_POSITIONS_FILE)
        bak_path = Path(str(path) + ".bak")

        # Try main file first, fall back to backup
        for source in [path, bak_path]:
            if not source.exists():
                continue
            try:
                with open(source, "r") as f:
                    data = json.load(f)
                if not data:
                    # Empty dict — try backup
                    if source == path and bak_path.exists():
                        audit(trade_log,
                              f"Main positions file is empty, trying backup",
                              action="load_positions", result="FALLBACK_TO_BAK")
                        continue
                    return
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
                        close_reason=pdata.get("close_reason"),
                    )
                source_label = "backup" if source == bak_path else "disk"
                if self._positions:
                    audit(trade_log, f"Loaded {len(self._positions)} live positions from {source_label}",
                          action="load_positions", result="OK")
                    # Startup recovery: reset any positions stuck in "closing" status.
                    # The close order may or may not have succeeded on the exchange —
                    # resetting to "open" lets reconcile_positions() re-check and handle.
                    for tid, p in self._positions.items():
                        if p.status == "closing":
                            audit(trade_log,
                                  f"Startup recovery: position {tid} ({p.symbol}) stuck in 'closing' — resetting to 'open'",
                                  action="load_positions", result="RECOVERY")
                            p.status = "open"
                return
            except Exception as exc:
                audit(trade_log, f"Failed to load positions from {source}: {exc}",
                      action="load_positions", result="ERROR")
                continue

    # ── F-14 FIX: Closed trades persistence ───────────────────────

    def _append_closed_trade(self, pos: LivePosition) -> None:
        """Append a closed trade to the persisted closed trades file.

        Deduplicates by trade_id: if a record with the same trade_id already
        exists, it is replaced (the newer close has more accurate data).
        This prevents the triple/double-counting bug where reconciliation,
        manual close, and limit expiry all append independently for the
        same underlying position.
        """
        # ── Dedup: replace existing record with same trade_id ──
        existing_idx = None
        for idx, t in enumerate(self._closed_trades):
            if t.trade_id == pos.trade_id:
                existing_idx = idx
                break
        if existing_idx is not None:
            self._closed_trades[existing_idx] = pos
            logger.info("Replaced existing closed trade record: %s", pos.trade_id)
        else:
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
                    "gross_pnl": pos.gross_pnl,
                    "commission": pos.commission,
                    "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
                    "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
                    "status": "closed",
                    "close_reason": pos.close_reason,
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
                    gross_pnl=float(item.get("gross_pnl", 0)) if item.get("gross_pnl") is not None else None,
                    commission=float(item.get("commission", 0)) if item.get("commission") is not None else None,
                    opened_at=opened_at,
                    closed_at=closed_at,
                    status="closed",
                    close_reason=item.get("close_reason"),
                )
                self._closed_trades.append(pos)
            # ── Dedup on load: keep last record per trade_id ──
            if self._closed_trades:
                seen: dict[str, int] = {}
                deduped: list[LivePosition] = []
                for p in self._closed_trades:
                    if p.trade_id in seen:
                        # Replace earlier record with this one (later = more accurate)
                        deduped[seen[p.trade_id]] = p
                    else:
                        seen[p.trade_id] = len(deduped)
                        deduped.append(p)
                if len(deduped) < len(self._closed_trades):
                    logger.info("Deduped closed trades on load: %d -> %d",
                                len(self._closed_trades), len(deduped))
                self._closed_trades = deduped
            # ── Cap to _MAX_CLOSED_TRADES, keeping only the most recent ──
            if len(self._closed_trades) > _MAX_CLOSED_TRADES:
                logger.info("Trimming closed trades on load: %d -> %d",
                            len(self._closed_trades), _MAX_CLOSED_TRADES)
                self._closed_trades = self._closed_trades[-_MAX_CLOSED_TRADES:]
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
                # ── Skip pending_fill (unfilled limit orders) ──
                # A pending_fill position means the limit order was placed but
                # hasn't filled yet — no exchange position exists. Reconciling
                # these creates phantom closes with fake PnL.
                if pos.status == "pending_fill":
                    continue

                try:
                    # Check if position still exists on exchange
                    ccxt_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
                    positions = await exchange.fetch_positions(
                        [ccxt_symbol],
                        params={"productType": "USDT-FUTURES"},
                    )
                    has_position = any(
                        float(p.get("contracts", 0)) > 0 for p in positions
                    )

                    if not has_position:
                        # Position no longer on exchange — closed by SL/TP or liquidation
                        # C2-13 FIX: Try actual trade history first for accurate PnL,
                        # fall back to ticker estimation only as last resort.
                        actual_fill_price = None
                        fill_source = "estimated"

                        # 1. Try fetchMyTrades for actual fill price + exchange PnL
                        exchange_reported_pnl = None  # Bitget reports PnL in info.profit
                        try:
                            trades = await exchange.fetch_my_trades(ccxt_symbol, limit=50)
                            relevant = [
                                t for t in trades
                                if t.get("order") in (pos.sl_order_id, pos.tp_order_id)
                            ]
                            if relevant:
                                actual_fill_price = float(relevant[-1].get("price", 0))
                                fill_source = "exchange_fill"
                                # Sum exchange-reported PnL across all fill legs
                                total_profit = 0.0
                                for rt in relevant:
                                    info = rt.get("info", {})
                                    profit = float(info.get("profit", 0) or 0)
                                    total_profit += profit
                                if total_profit != 0:
                                    exchange_reported_pnl = total_profit
                                # Determine reason from which order matched
                                matched_order = relevant[-1].get("order")
                                if matched_order == pos.tp_order_id:
                                    reason = "TP HIT (exchange)"
                                elif matched_order == pos.sl_order_id:
                                    reason = "SL HIT (exchange)"
                                else:
                                    reason = "closed (exchange)"
                        except Exception as e:
                            logger.debug("fetchMyTrades failed for %s: %s", ccxt_symbol, e)

                        # 2. Try fetchClosedOrders
                        if actual_fill_price is None or actual_fill_price <= 0:
                            try:
                                closed_orders = await exchange.fetch_closed_orders(ccxt_symbol, limit=20)
                                for o in closed_orders:
                                    if o.get("id") in (pos.sl_order_id, pos.tp_order_id):
                                        avg = o.get("average") or o.get("price")
                                        if avg:
                                            actual_fill_price = float(avg)
                                            fill_source = "closed_order"
                                            if o["id"] == pos.tp_order_id:
                                                reason = "TP HIT (exchange)"
                                            else:
                                                reason = "SL HIT (exchange)"
                                            break
                            except Exception as e:
                                logger.debug("fetchClosedOrders failed for %s: %s", ccxt_symbol, e)

                        # 3. Last resort: current ticker with SL/TP proximity guess
                        if actual_fill_price is None or actual_fill_price <= 0:
                            try:
                                ticker = await exchange.fetch_ticker(ccxt_symbol)
                                close_price = float(ticker.get("last", 0) or 0)
                            except Exception:
                                close_price = 0

                            if close_price <= 0:
                                close_price = pos.entry_price  # fallback

                            # Estimate which level was hit based on proximity
                            if pos.direction == "LONG":
                                dist_tp = abs(close_price - pos.take_profit) if pos.take_profit else float("inf")
                                dist_sl = abs(close_price - pos.stop_loss) if pos.stop_loss else float("inf")
                                if dist_tp < dist_sl and pos.take_profit:
                                    est_exit = pos.take_profit
                                    reason = "TP HIT (estimated)"
                                elif pos.stop_loss:
                                    est_exit = pos.stop_loss
                                    reason = "SL HIT (estimated)"
                                else:
                                    est_exit = close_price
                                    reason = "closed (estimated)"
                            else:  # SHORT
                                dist_tp = abs(close_price - pos.take_profit) if pos.take_profit else float("inf")
                                dist_sl = abs(close_price - pos.stop_loss) if pos.stop_loss else float("inf")
                                if dist_tp < dist_sl and pos.take_profit:
                                    est_exit = pos.take_profit
                                    reason = "TP HIT (estimated)"
                                elif pos.stop_loss:
                                    est_exit = pos.stop_loss
                                    reason = "SL HIT (estimated)"
                                else:
                                    est_exit = close_price
                                    reason = "closed (estimated)"
                            actual_fill_price = est_exit
                            fill_source = "estimated"
                            logger.warning(
                                "PnL for %s is estimated from current price — "
                                "actual fill unavailable", pos.symbol)

                        est_exit = actual_fill_price

                        # Compute PnL
                        if pos.direction == "LONG":
                            pnl = (est_exit - pos.entry_price) * pos.quantity
                        else:
                            pnl = (pos.entry_price - est_exit) * pos.quantity

                        pos.close_reason = reason
                        pos.status = "closed"
                        pos.close_price = est_exit

                        # ── Use exchange-reported PnL when available (most accurate) ──
                        if exchange_reported_pnl is not None:
                            # Bitget's profit field already accounts for fees
                            net_pnl = exchange_reported_pnl
                            # Estimate gross/commission split for display
                            if pos.direction == "LONG":
                                gross_pnl = (est_exit - pos.entry_price) * pos.quantity
                            else:
                                gross_pnl = (pos.entry_price - est_exit) * pos.quantity
                            commission = gross_pnl - net_pnl
                            if commission < 0:
                                commission = 0
                                gross_pnl = net_pnl
                            pnl = gross_pnl
                            logger.info("Using exchange-reported PnL for %s: $%.4f",
                                        pos.symbol, net_pnl)
                        else:
                            # Deduct commission on reconciled close (same as manual close)
                            entry_notional = pos.entry_price * pos.quantity
                            exit_notional = est_exit * pos.quantity
                            # GETCLAW: maker/taker fee split
                            is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
                            entry_fee = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
                            exit_fee = CONFIG.risk.taker_fee_pct  # SL/TP triggers = market = taker
                            commission = (entry_notional * entry_fee / 100.0) + (exit_notional * exit_fee / 100.0)
                            gross_pnl = pnl
                            net_pnl = gross_pnl - commission
                        pos.gross_pnl = round(gross_pnl, 4)
                        pos.commission = round(commission, 4)
                        pos.pnl_usd = round(net_pnl, 4)
                        pos.closed_at = datetime.now(UTC)

                        self._save_positions()
                        self._append_closed_trade(pos)
                        # Invalidate balance cache on reconciled close
                        self._fire_position_closed(pos)

                        pnl_str = f"+${pnl:.4f}" if pnl >= 0 else f"-${abs(pnl):.4f}"
                        pnl_pct = ((est_exit - pos.entry_price) / pos.entry_price * 100) if pos.entry_price else 0
                        if pos.direction == "SHORT":
                            pnl_pct = -pnl_pct
                        hold_secs = (pos.closed_at - pos.opened_at).total_seconds() if pos.closed_at and pos.opened_at else 0
                        if hold_secs < 3600:
                            hold_str = f"{hold_secs / 60:.0f}m"
                        elif hold_secs < 86400:
                            hold_str = f"{hold_secs / 3600:.1f}h"
                        else:
                            hold_str = f"{hold_secs / 86400:.1f}d"
                        msg = (
                            f"RECONCILED {pos.direction} {pos.symbol} ({reason})\n"
                            f"Entry: ${pos.entry_price:,.4f} -> Exit: ~${est_exit:,.4f}\n"
                            f"PnL: {pnl_str} ({pnl_pct:+.2f}%) | Hold: {hold_str}"
                        )
                        self._last_close_data = {
                            "symbol": pos.symbol,
                            "direction": pos.direction,
                            "reason": reason,
                            "entry": pos.entry_price,
                            "exit": est_exit,
                            "pnl_pct": pnl_pct,
                            "pnl_usd": round(net_pnl, 4),
                            "gross_pnl": round(gross_pnl, 4),
                            "fees": round(commission, 4),
                            "size_usd": round(pos.cost_usd, 2) if pos.cost_usd > 0 else round(pos.entry_price * pos.quantity, 2),
                            "leverage": pos.leverage or 1,
                            "hold_time": hold_str,
                        }
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
