"""
RUNECLAW Bitget WebSocket Real-Time Price Feed.

Provides sub-second price updates for position monitoring (SL/TP) and
fresher data for signal generation. Supplements the REST polling scanner --
does NOT replace it.

Bitget v3 public WebSocket endpoint: wss://ws.bitget.com/v3/ws/public

Integration hook (in engine._check_open_positions):
    prices = ws_feed.get_prices() if ws_feed.is_connected() else await exchange.fetch_tickers()
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Optional

from bot.utils.logger import system_log, audit

# Conditional import -- keeps tests working when websockets is not installed.
try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BITGET_WS_URL = "wss://ws.bitget.com/v3/ws/public"
PING_INTERVAL_S = 25
RECONNECT_BASE_S = 1
RECONNECT_MAX_S = 60


# ---------------------------------------------------------------------------
# PriceTick dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PriceTick:
    """Immutable snapshot of the latest ticker data for one symbol."""
    symbol: str            # ccxt format, e.g. "BTC/USDT"
    last: float
    bid: float
    ask: float
    volume_24h: float
    change_pct_24h: float
    timestamp: datetime
    source: str = "ws"
    # v3 futures-only fields (0.0 when unavailable or on spot)
    funding_rate: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    open_interest: float = 0.0
    next_funding_time: float = 0.0  # unix ms


# ---------------------------------------------------------------------------
# Symbol conversion helpers
# ---------------------------------------------------------------------------

def symbols_to_bitget(symbols: list[str]) -> list[str]:
    """Convert ccxt format symbols to Bitget WS format.

    "BTC/USDT" -> "BTCUSDT"
    """
    return [s.replace("/", "") for s in symbols]


def symbol_from_bitget(s: str) -> str:
    """Convert Bitget WS instId to ccxt format.

    "BTCUSDT" -> "BTC/USDT"

    Heuristic: split before the quote asset (USDT / USDC / BTC / ETH).
    Falls back to returning the raw string if no known quote is detected.
    """
    for quote in ("USDT", "USDC", "BTC", "ETH", "DAI"):
        if s.endswith(quote) and len(s) > len(quote):
            base = s[: -len(quote)]
            return f"{base}/{quote}"
    return s


# ---------------------------------------------------------------------------
# BitgetWSFeed
# ---------------------------------------------------------------------------

class BitgetWSFeed:
    """Async WebSocket feed that maintains a real-time price cache.

    Designed to run as a standalone ``asyncio.Task`` alongside the engine.
    All public accessors are thread-safe (read-only snapshots of dicts).
    """

    def __init__(self, symbols: list[str] | None = None) -> None:
        # Symbols tracked (ccxt format).
        self._symbols: set[str] = set(symbols or [])
        # Price cache -- written only from the WS reader task.
        self._ticks: dict[str, PriceTick] = {}
        self._connected = False
        self._ws: object | None = None  # websockets.WebSocketClientProtocol
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._reconnect_delay = RECONNECT_BASE_S
        self._last_msg_ts: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin the WebSocket connection loop in the background."""
        if not HAS_WEBSOCKETS:
            audit(
                system_log,
                "websockets library not installed -- WS feed disabled. "
                "Install with: pip install websockets",
                action="ws_feed_start",
                result="SKIP",
            )
            return

        if self._task is not None and not self._task.done():
            return  # already running

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="ws_feed")

    async def stop(self) -> None:
        """Gracefully disconnect and cancel the background task."""
        self._stop_event.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected = False

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, symbols: list[str]) -> None:
        """Add symbols (ccxt format) to the subscription set.

        If already connected the subscription message is sent immediately
        via a fire-and-forget task.
        """
        new = set(symbols) - self._symbols
        if not new:
            return
        self._symbols.update(new)
        if self._connected and self._ws is not None:
            asyncio.ensure_future(self._send_subscribe(list(new)))

    def unsubscribe(self, symbols: list[str]) -> None:
        """Remove symbols from the subscription set."""
        removing = set(symbols) & self._symbols
        if not removing:
            return
        self._symbols -= removing
        for s in removing:
            self._ticks.pop(s, None)
        if self._connected and self._ws is not None:
            asyncio.ensure_future(self._send_unsubscribe(list(removing)))

    # ------------------------------------------------------------------
    # Public accessors (thread-safe: return snapshots)
    # ------------------------------------------------------------------

    def get_prices(self) -> dict[str, float]:
        """Return ``{symbol: last_price}`` for all tracked symbols."""
        return {sym: tick.last for sym, tick in self._ticks.items()}

    def get_tick(self, symbol: str) -> Optional[PriceTick]:
        """Get the latest tick for *symbol* (ccxt format)."""
        return self._ticks.get(symbol)

    def get_all_ticks(self) -> dict[str, PriceTick]:
        """Return a shallow copy of the full tick cache."""
        return dict(self._ticks)

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_for_telegram(self) -> str:
        """Human-readable status string for Telegram status messages."""
        status = "CONNECTED" if self._connected else "DISCONNECTED"
        latency = ""
        if self._last_msg_ts:
            age = time.time() - self._last_msg_ts
            latency = f" | last msg {age:.1f}s ago"
        count = len(self._ticks)
        return f"WS Feed: {status} | {count} symbols{latency}"

    # ------------------------------------------------------------------
    # Internal: connection loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Outer loop: connect, subscribe, read messages, reconnect."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                audit(
                    system_log,
                    f"WS feed error: {exc}",
                    action="ws_feed_error",
                    result="ERROR",
                )
            finally:
                self._connected = False

            if self._stop_event.is_set():
                break

            # Exponential backoff
            audit(
                system_log,
                f"WS feed reconnecting in {self._reconnect_delay}s",
                action="ws_feed_reconnect",
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._reconnect_delay
                )
                break  # stop_event was set during the wait
            except asyncio.TimeoutError:
                pass
            self._reconnect_delay = min(
                self._reconnect_delay * 2, RECONNECT_MAX_S
            )

    async def _connect_and_listen(self) -> None:
        """Single connection lifetime: open, subscribe, read until error."""
        async with websockets.connect(  # type: ignore[union-attr]
            BITGET_WS_URL,
            ping_interval=PING_INTERVAL_S,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = RECONNECT_BASE_S

            audit(
                system_log,
                f"WS feed connected to {BITGET_WS_URL}",
                action="ws_feed_connected",
            )

            # Subscribe to all tracked symbols
            if self._symbols:
                await self._send_subscribe(list(self._symbols))

            # Message read loop
            async for raw in ws:
                if self._stop_event.is_set():
                    break
                self._last_msg_ts = time.time()
                try:
                    self._handle_message(raw)
                except Exception as exc:
                    audit(
                        system_log,
                        f"WS parse error: {exc}",
                        action="ws_feed_parse",
                        result="WARN",
                    )

    # ------------------------------------------------------------------
    # Internal: subscribe / unsubscribe helpers
    # ------------------------------------------------------------------

    async def _send_subscribe(self, symbols: list[str]) -> None:
        if self._ws is None:
            return
        args = [
            {"instType": "usdt-futures", "topic": "ticker", "symbol": inst}
            for inst in symbols_to_bitget(symbols)
        ]
        msg = json.dumps({"op": "subscribe", "args": args})
        try:
            await self._ws.send(msg)
            audit(
                system_log,
                f"WS subscribed to {len(args)} symbols",
                action="ws_feed_subscribe",
                data={"symbols": symbols_to_bitget(symbols)},
            )
        except Exception as exc:
            audit(
                system_log,
                f"WS subscribe send error: {exc}",
                action="ws_feed_subscribe",
                result="ERROR",
            )

    async def _send_unsubscribe(self, symbols: list[str]) -> None:
        if self._ws is None:
            return
        args = [
            {"instType": "usdt-futures", "topic": "ticker", "symbol": inst}
            for inst in symbols_to_bitget(symbols)
        ]
        msg = json.dumps({"op": "unsubscribe", "args": args})
        try:
            await self._ws.send(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal: message parsing
    # ------------------------------------------------------------------

    def _handle_message(self, raw: str | bytes) -> None:
        """Parse a single WS message and update the tick cache."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        # Bitget sends "pong" as a plain string sometimes
        if raw == "pong":
            return

        data = json.loads(raw)

        # Subscription confirmations / errors
        if "event" in data:
            event = data.get("event")
            if event == "error":
                audit(
                    system_log,
                    f"WS event error: {data}",
                    action="ws_feed_event",
                    result="ERROR",
                )
            return

        # Ticker data
        action = data.get("action")
        arg = data.get("arg", {})
        topic = arg.get("topic") or arg.get("channel")  # v3 uses "topic", v2 fallback
        if topic != "ticker":
            return

        for item in data.get("data", []):
            self._process_ticker(item)

    def _process_ticker(self, item: dict) -> None:
        """Convert a Bitget v3 ticker payload to a PriceTick and cache it."""
        inst_id = item.get("instId") or item.get("symbol", "")
        symbol = symbol_from_bitget(inst_id)

        ts_ms = item.get("ts")
        try:
            timestamp = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC) if ts_ms else datetime.now(UTC)
        except (ValueError, TypeError):
            timestamp = datetime.now(UTC)

        tick = PriceTick(
            symbol=symbol,
            # v3 fields first, v2 fallbacks for transition safety
            last=_float(item.get("lastPrice", item.get("lastPr", 0))),
            bid=_float(item.get("bid1Price", item.get("bidPr", 0))),
            ask=_float(item.get("ask1Price", item.get("askPr", 0))),
            volume_24h=_float(item.get("volume24h", item.get("baseVolume", 0))),
            change_pct_24h=_float(item.get("price24hPcnt", item.get("change24h", 0))),
            timestamp=timestamp,
            # v3 futures-only fields
            funding_rate=_float(item.get("fundingRate", 0)),
            mark_price=_float(item.get("markPrice", 0)),
            index_price=_float(item.get("indexPrice", 0)),
            open_interest=_float(item.get("openInterest", 0)),
            next_funding_time=_float(item.get("nextFundingTime", 0)),
        )
        self._ticks[symbol] = tick


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _float(v: object) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
