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
import time as _time
from collections import deque
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from bot.compat import UTC
from typing import Optional

from bot.config import CONFIG
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
# A connection must survive this long before the reconnect backoff resets to
# base. Resetting at connect time (the old behavior) meant a link that opens
# fine but drops seconds later re-armed the 1s delay every cycle — an endless
# ~1-2s reconnect storm exactly matching what ops observed live.
STABLE_CONNECTION_S = 60
WS_IDLE_POLL_S = 5.0  # how often the idle watchdog checks the last-message age


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
        self._conn_started_at: float | None = None
        self._last_msg_ts: float = 0.0
        # WS trade-tape CVD (WS_CVD_ENABLED): true aggressor-side cumulative
        # volume delta per symbol, deduped by trade id. Buckets are per-minute
        # (minute_ts, cum_delta_at_end, last_price) for trend/divergence.
        self._cvd: dict[str, dict] = {}

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

    def get_prices(self, max_age_sec: Optional[float] = None) -> dict[str, float]:
        """Return ``{symbol: last_price}`` for tracked symbols.

        When ``max_age_sec`` is given and > 0, only ticks whose timestamp is within
        that many seconds of now are returned — so a silently-stalled feed can't
        serve a stale price to stop logic. ``None``/0 returns every tick (the
        original behaviour). A tick with an unreadable timestamp is treated as
        stale (excluded) under a freshness filter.
        """
        if not max_age_sec:
            return {sym: tick.last for sym, tick in self._ticks.items()}
        now = time.time()
        out: dict[str, float] = {}
        for sym, tick in self._ticks.items():
            try:
                if now - tick.timestamp.timestamp() <= max_age_sec:
                    out[sym] = tick.last
            except Exception:
                continue  # unreadable timestamp → treat as stale
        return out

    def seconds_since_last_msg(self) -> Optional[float]:
        """Age in seconds of the most recent WS message across all symbols, or
        None if no message has been received yet. For health/observability."""
        if not self._last_msg_ts:
            return None
        return time.time() - self._last_msg_ts

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

    @staticmethod
    def _is_idle_stalled(last_msg_ts: float, now: float, timeout: float) -> bool:
        """True when the feed is connected but no message has arrived for longer
        than `timeout` seconds. timeout <= 0 disables (never stalled). A
        not-yet-seeded last_msg_ts (0/falsy) is not treated as stalled — the
        watchdog seeds it at connect to start the grace period."""
        if not timeout or timeout <= 0:
            return False
        if not last_msg_ts:
            return False
        return (now - last_msg_ts) > timeout

    async def _idle_watchdog(self) -> None:
        """Force a reconnect when the socket is open but the data stream has gone
        silent. ping/pong keepalive only catches a dead socket; a feed that stays
        pong-alive while Bitget stops pushing ticker data (subscription dropped
        server-side, half-open stall) would otherwise freeze prices forever.
        Closing the socket breaks the read loop so the outer loop reconnects and
        resubscribes. Gated by CONFIG.execution.ws_idle_timeout_sec (0 disables →
        byte-identical, watchdog returns immediately)."""
        timeout = float(getattr(CONFIG.execution, "ws_idle_timeout_sec", 0.0) or 0.0)
        if timeout <= 0:
            return
        poll = min(WS_IDLE_POLL_S, timeout)
        while self._connected and not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll)
                return  # stop requested during the wait
            except asyncio.TimeoutError:
                pass
            if self._is_idle_stalled(self._last_msg_ts, time.time(), timeout):
                age = time.time() - self._last_msg_ts
                audit(
                    system_log,
                    f"WS feed STALLED: no data for {age:.0f}s (>{timeout:.0f}s) "
                    f"— forcing reconnect/resubscribe",
                    action="ws_feed_stall",
                    result="STALL",
                    level=logging.WARNING,
                )
                try:
                    if self._ws is not None:
                        await self._ws.close()
                except Exception:
                    pass
                return  # read loop will end; outer loop reconnects

    async def _run_loop(self) -> None:
        """Outer loop: connect, subscribe, read messages, reconnect."""
        while not self._stop_event.is_set():
            self._conn_started_at = None
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

            # Reset the backoff only after a PROVEN-stable connection (survived
            # STABLE_CONNECTION_S). Resetting at connect time re-armed the 1s
            # delay on every open-then-drop cycle → reconnect storm.
            if (self._conn_started_at is not None
                    and time.time() - self._conn_started_at >= STABLE_CONNECTION_S):
                self._reconnect_delay = RECONNECT_BASE_S

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
            # NOT resetting _reconnect_delay here: the outer loop resets it
            # only after this connection survives STABLE_CONNECTION_S.
            self._conn_started_at = time.time()
            # Seed the last-message clock at connect so the idle watchdog measures
            # the grace period from now (not from a stale prior-connection value).
            self._last_msg_ts = time.time()

            audit(
                system_log,
                f"WS feed connected to {BITGET_WS_URL}",
                action="ws_feed_connected",
            )

            # Subscribe to all tracked symbols
            if self._symbols:
                await self._send_subscribe(list(self._symbols))

            # Idle-stall watchdog runs alongside the read loop (no-op when the
            # timeout is 0). If the feed goes silent it closes the socket, which
            # ends the read loop below and triggers a reconnect + resubscribe.
            watchdog = asyncio.create_task(self._idle_watchdog())
            # App-level heartbeat: Bitget's v3 server expects a client-originated
            # "ping" and can drop an otherwise-idle connection without it. The
            # transport ping (ping_interval above) demonstrably keeps the link up
            # on its own, but sending Bitget's documented text "ping" too is
            # belt-and-suspenders for flakier links — and completes the pong
            # handler in _handle_message, which previously had no ping to answer.
            keepalive = asyncio.create_task(self._keepalive())
            try:
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
            finally:
                watchdog.cancel()
                keepalive.cancel()
                for _t in (watchdog, keepalive):
                    try:
                        await _t
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _keepalive(self) -> None:
        """Send Bitget's app-level heartbeat (the literal text ``"ping"``) every
        PING_INTERVAL_S. Bitget replies ``"pong"`` (consumed in
        ``_handle_message``); some endpoints drop an idle-looking socket without
        this even while the transport-level ping keeps it technically open.
        Fail-quiet: a send error just returns — the read loop surfaces the dead
        socket and the outer loop reconnects."""
        while self._connected and not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=PING_INTERVAL_S)
                return  # stop requested during the wait
            except asyncio.TimeoutError:
                pass
            if self._ws is None:
                return
            try:
                await self._ws.send("ping")
            except Exception:
                return

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
        if getattr(CONFIG.execution, "ws_cvd_enabled", False):
            args += [
                {"instType": "usdt-futures", "topic": "trade", "symbol": inst}
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
        if getattr(CONFIG.execution, "ws_cvd_enabled", False):
            args += [
                {"instType": "usdt-futures", "topic": "trade", "symbol": inst}
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
        if topic == "trade":
            inst_id = arg.get("instId") or arg.get("symbol", "")
            self._process_trades(symbol_from_bitget(inst_id), data.get("data", []))
            return
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


    # ------------------------------------------------------------------
    # WS trade-tape CVD
    # ------------------------------------------------------------------

    def _process_trades(self, symbol: str, items: list[dict]) -> None:
        """Accumulate true cumulative volume delta from public trades.

        Deduped by trade id (a reconnect replays the recent tape), signed by
        the aggressor side, bucketed per minute for trend/divergence reads.
        Fail-open: any malformed item is skipped.
        """
        if not symbol or not items:
            return
        state = self._cvd.get(symbol)
        if state is None:
            state = {
                "cum": 0.0,
                "buckets": deque(maxlen=240),   # (minute_ts, cum_at_end, last_price)
                "seen": deque(maxlen=4000),
                "seen_set": set(),
                "trades": 0,
                "last_update": 0.0,
            }
            self._cvd[symbol] = state
        for item in items:
            try:
                tid = str(item.get("tradeId") or item.get("i") or "")
                if tid and tid in state["seen_set"]:
                    continue
                price = _float(item.get("price", item.get("p", 0)))
                size = _float(item.get("size", item.get("v", 0)))
                side = str(item.get("side", item.get("S", ""))).lower()
                if price <= 0 or size <= 0 or side not in ("buy", "sell"):
                    continue
                delta = price * size * (1.0 if side == "buy" else -1.0)
                state["cum"] += delta
                state["trades"] += 1
                state["last_update"] = _time.time()
                if tid:
                    if len(state["seen"]) == state["seen"].maxlen:
                        state["seen_set"].discard(state["seen"][0])
                    state["seen"].append(tid)
                    state["seen_set"].add(tid)
                ts_ms = _float(item.get("ts", 0)) or _time.time() * 1000.0
                minute = int(ts_ms // 60_000)
                buckets = state["buckets"]
                if buckets and buckets[-1][0] == minute:
                    buckets[-1] = (minute, state["cum"], price)
                else:
                    buckets.append((minute, state["cum"], price))
            except Exception:
                continue

    def get_cvd(self, symbol: str, max_age_sec: float = 180.0) -> Optional[dict]:
        """Fresh tape CVD for a symbol, or None (stale/absent → the caller
        falls back to the REST-window approximation)."""
        state = self._cvd.get(symbol)
        if not state or state["trades"] == 0:
            return None
        age = _time.time() - state["last_update"]
        if age > max_age_sec:
            return None
        buckets = list(state["buckets"])
        return {
            "cum_delta_usd": float(state["cum"]),
            "series": [b[1] for b in buckets],
            "prices": [b[2] for b in buckets],
            "trades": int(state["trades"]),
            "age_sec": round(age, 1),
        }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _float(v: object) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
