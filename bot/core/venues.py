"""
Venue abstraction for the live money path.

RUNECLAW's LiveExecutor was written against Bitget USDT-perps: the ccxt
constructor, symbol formats ("BTC/USDT:USDT"), the productType/tradeSide
param dialect, the UTA-vs-classic account split, and the native v3
strategy-order channel were all inlined. This module makes the venue a
first-class object so a second perps venue can plug in WITHOUT touching
Bitget behavior.

Design rules (in order of importance):
  1. ZERO Bitget drift. Every BitgetVenue method reproduces the exact
     params/symbols the executor sent before this module existed —
     including the identity `order_symbol` (Bitget resolves spot-form
     symbols on the swap exchange today; do not "fix" that).
  2. The bot's INTERNAL canonical symbol stays "BASE/USDT" everywhere
     (ideas, risk engine, blacklists, learning). Venues translate only at
     the exchange boundary. normalize_symbol()/display_symbol() already
     strip any quote/settle suffix, so USDC symbols round-trip cleanly.
  3. Per-user executors (/connect flow) are Bitget-only — the encrypted
     credential store has no venue field. VENUE applies to the operator's
     shared executor.

Selection: VENUE env var ("bitget" default, "hyperliquid"). Hyperliquid is
USDC-margined perps (one-way only, no hedge mode, no UTA), authenticated
with HYPERLIQUID_WALLET_ADDRESS + HYPERLIQUID_PRIVATE_KEY. ccxt quirks
encoded here:
  - market orders (incl. trigger markets) REQUIRE a price (slippage bound)
  - TP triggers must be sent as takeProfitPrice (triggerPrice == SL)
  - clientOrderId must be a 128-bit hex string ("0x" + 32 hex chars)
  - min notional is $10 per order (ccxt market limits carry it)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)

# Runtime venue override — set by the admin /venue command, survives
# restarts, and takes precedence over the VENUE env var so switching
# venues never requires editing .env. Removing the file (or /venue clear)
# reverts to the env-configured venue.
_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
VENUE_OVERRIDE_FILE = os.path.join(_STATE_DIR, "venue_override.json")


class Venue:
    """Base venue spec — attribute defaults are documentation only; both
    concrete venues override everything they use."""

    id: str = ""
    display_name: str = ""
    quote: str = "USDT"                 # quote/settle coin of the perp market
    balance_coin: str = "USDT"          # coin whose free balance backs margin
    min_notional_usd: float = 5.0
    supports_hedge_mode: bool = False   # venue can run hedge (two-sided) mode
    supports_native_triggers: bool = False  # Bitget v3 strategy-order channel
    market_order_needs_price: bool = False  # ccxt needs price on market orders

    # ── construction / credentials ────────────────────────────────
    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        raise NotImplementedError

    def missing_credentials_error(self, per_user: bool) -> str:
        raise NotImplementedError

    def has_operator_credentials(self, cfg: Any) -> bool:
        raise NotImplementedError

    # ── symbols ───────────────────────────────────────────────────
    def swap_symbol(self, symbol: str) -> str:
        """Map an internal symbol ("UNI/USDT", "UNI/USDT:USDT", "UNI") to
        this venue's ccxt perp symbol."""
        if f":{self.quote}" in symbol:
            return symbol
        if symbol.endswith(f"/{self.quote}"):
            return f"{symbol}:{self.quote}"
        base = symbol.split("/")[0]
        return f"{base}/{self.quote}:{self.quote}"

    def order_symbol(self, symbol: str) -> str:
        """Symbol to hand ccxt order/position calls. Bitget: IDENTITY —
        the executor historically passes spot-form symbols and Bitget's
        swap-default exchange resolves them; changing that would alter
        live behavior. Non-Bitget venues map to their perp symbol."""
        return symbol

    # ── param dialects ────────────────────────────────────────────
    def futures_params(self, **extra: Any) -> dict:
        """Product-scoping params merged into fetch/order calls."""
        return dict(extra)

    def entry_params(self, margin_mode: str, leverage: int) -> dict:
        """Params for the entry create_order call."""
        return {}

    def post_only_params(self) -> dict:
        return {"postOnly": True}

    def gtc_params(self) -> dict:
        return {"timeInForce": "GTC"}

    def close_params(self, is_uta: Optional[bool]) -> dict:
        """Params for reduceOnly market closes (full and partial)."""
        return {"reduceOnly": True}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        """Params for a reduce-only SL/TP trigger-market order.
        kind: "sl" | "tp"."""
        raise NotImplementedError

    def plan_order_query_params(self) -> dict:
        """Params for fetch_open_orders when listing pending SL/TP orders."""
        return {}

    def is_plan_order(self, order: dict) -> bool:
        """Whether an order returned by the plan-order query is an SL/TP
        trigger (venues without a server-side filter need a client check)."""
        return True

    # ── leverage/margin setup (generic ccxt path) ─────────────────
    # Some venues want an explicit set_margin_mode call before leverage;
    # some need extra params on set_leverage (BingX: side=BOTH in one-way).
    margin_mode_call_first: bool = False

    def leverage_params(self, margin_mode: str) -> dict:
        return {"marginMode": margin_mode}

    # ── idempotency ───────────────────────────────────────────────
    def client_oid(self, oid: str) -> str:
        """Venue-legal client order id from an internal idempotency key.
        Deterministic: same input -> same output, so timeout-retry dedup
        still works."""
        return oid

    def order_id_params(self, coid: str) -> dict:
        """Params carrying the client order id on create_order."""
        return {"clientOid": coid, "clientOrderId": coid}

    # ── balance ───────────────────────────────────────────────────
    def balance_fetch_params(self) -> dict:
        return {}


class BitgetVenue(Venue):
    """Bitget USDT-M perpetuals — encodes the executor's historical
    behavior EXACTLY (params, symbols, options). Do not "improve" values
    here without a live A/B: this class is the zero-regression contract."""

    id = "bitget"
    display_name = "Bitget"
    quote = "USDT"
    balance_coin = "USDT"
    min_notional_usd = 5.0
    supports_hedge_mode = True
    supports_native_triggers = True
    market_order_needs_price = False

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        if credentials:
            api_key = credentials.get("api_key") or ""
            api_secret = credentials.get("api_secret") or ""
            passphrase = credentials.get("passphrase") or ""
        else:
            api_key, api_secret, passphrase = (
                cfg.api_key, cfg.api_secret, cfg.passphrase)
        if not api_key or not api_secret:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        is_futures = cfg.trade_mode == "futures"
        return ccxt.bitget({
            "aiohttp_trust_env": True,  # honor HTTPS_PROXY/CA env (no-op without proxy)
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "sandbox": cfg.sandbox,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap" if is_futures else "spot",
                "uta": True,  # Support Bitget Unified Trading Account
            },
        })

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("This user has no linked Bitget credentials. Use /connect "
                    "to link an account before trading live.")
        return ("BITGET_API_KEY and BITGET_API_SECRET required for live "
                "trading. Set them in .env and restart.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(cfg.api_key and cfg.api_secret)

    def futures_params(self, **extra: Any) -> dict:
        p: dict = {"productType": "USDT-FUTURES"}
        p.update(extra)
        return p

    def entry_params(self, margin_mode: str, leverage: int) -> dict:
        return {
            "productType": "USDT-FUTURES",
            "marginMode": margin_mode,
            "leverage": str(leverage),
        }

    def post_only_params(self) -> dict:
        return {"timeInForce": "post_only"}

    def close_params(self, is_uta: Optional[bool]) -> dict:
        # UTA v3 does NOT support tradeSide — reduceOnly is enough there.
        p: dict = {"productType": "USDT-FUTURES", "reduceOnly": True}
        if not is_uta:
            p["tradeSide"] = "close"
        return p

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        # Classic (non-UTA) ccxt trigger dialect. Always tradeSide=close +
        # reduceOnly so an SL/TP can never open a reverse position.
        return {
            "triggerPrice": trigger_price,
            "triggerType": "last",
            "productType": "USDT-FUTURES",
            "tradeSide": "close",
            "reduceOnly": True,
        }

    def plan_order_query_params(self) -> dict:
        return {"productType": "USDT-FUTURES", "isPlan": "plan_order"}

    def balance_fetch_params(self) -> dict:
        return {"type": "swap"}


class HyperliquidVenue(Venue):
    """Hyperliquid USDC-margined perpetual futures (DEX) via ccxt.

    Account topology is simple: one-way positions only, cross or isolated
    margin per symbol, no UTA/classic split, no native strategy-order
    channel (ccxt trigger orders ARE the venue's trigger orders and rest
    correctly, unlike Bitget UTA where they fire immediately — the whole
    reason the v3 path exists is absent here)."""

    id = "hyperliquid"
    display_name = "Hyperliquid"
    quote = "USDC"
    balance_coin = "USDC"
    min_notional_usd = 10.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = True

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        # Per-user credentials are Bitget-only by design (see module doc);
        # only the operator's env-configured wallet reaches here.
        wallet = getattr(cfg, "hyperliquid_wallet_address", "") or ""
        priv = getattr(cfg, "hyperliquid_private_key", "") or ""
        if not wallet or not priv:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        exchange = ccxt.hyperliquid({
            "aiohttp_trust_env": True,
            "walletAddress": wallet,
            "privateKey": priv,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })
        if getattr(cfg, "hyperliquid_testnet", False):
            exchange.set_sandbox_mode(True)
        return exchange

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("Per-user live trading is Bitget-only; the Hyperliquid "
                    "venue trades the operator wallet configured in .env.")
        return ("HYPERLIQUID_WALLET_ADDRESS and HYPERLIQUID_PRIVATE_KEY "
                "required for live trading on Hyperliquid. Set them in .env "
                "and restart.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(getattr(cfg, "hyperliquid_wallet_address", "")
                    and getattr(cfg, "hyperliquid_private_key", ""))

    def order_symbol(self, symbol: str) -> str:
        # Internal symbols are USDT-quoted; Hyperliquid perps are USDC.
        return self.swap_symbol(symbol)

    def entry_params(self, margin_mode: str, leverage: int) -> dict:
        # Margin mode + leverage are set per symbol via set_leverage()
        # beforehand; Hyperliquid's order payload carries neither.
        return {}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        # ccxt maps triggerPrice -> tpsl "sl" and takeProfitPrice -> "tp";
        # sending a TP as plain triggerPrice would create a WRONG-WAY stop.
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"triggerPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        # No server-side isPlan filter — identify trigger orders so plan
        # cleanup never cancels a resting entry limit order.
        if order.get("triggerPrice") or order.get("stopPrice"):
            return True
        info = order.get("info") or {}
        if isinstance(info, dict):
            return bool(info.get("isTrigger") or info.get("triggerPx"))
        return False

    def client_oid(self, oid: str) -> str:
        # Hyperliquid cloid must be a 128-bit hex string. md5 is exactly
        # 128 bits; deterministic so retry dedup still holds. (Not a
        # security context — just an id-width transform.)
        return "0x" + hashlib.md5(oid.encode(), usedforsecurity=False).hexdigest()

    def order_id_params(self, coid: str) -> dict:
        # No Bitget-style "clientOid" — ccxt hyperliquid does not omit
        # unknown params from the exact-schema payload.
        return {"clientOrderId": self.client_oid(coid)}


def _is_trigger_order(order: dict) -> bool:
    """Client-side trigger/conditional detection for venues whose
    fetch_open_orders has no reliable server-side plan filter — SL/TP
    cleanup must never cancel a resting entry limit order."""
    if order.get("triggerPrice") or order.get("stopPrice") \
            or order.get("stopLossPrice") or order.get("takeProfitPrice"):
        return True
    info = order.get("info") or {}
    if isinstance(info, dict):
        return bool(info.get("triggerPrice") or info.get("stopOrderType")
                    or info.get("stopPrice") or info.get("isTrigger"))
    return False


class BybitVenue(Venue):
    """Bybit USDT linear perpetuals via ccxt.

    Near drop-in: USDT-quoted "X/USDT:USDT" symbols, coin-denominated
    amounts, ~$5 min notional, resting conditional orders. Differences
    encoded here:
      - order_symbol MUST map to the perp form — Bybit loads spot AND
        swap markets, and a bare "BTC/USDT" resolves to SPOT
      - SL/TP via stopLossPrice/takeProfitPrice params (ccxt derives the
        trigger direction; raw triggerPrice would need triggerDirection)
      - one-way position mode assumed (Bybit's default); hedge-mode
        accounts must be switched to one-way before going live
    """

    id = "bybit"
    display_name = "Bybit"
    quote = "USDT"
    balance_coin = "USDT"
    min_notional_usd = 5.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = False
    margin_mode_call_first = True

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        api_key = getattr(cfg, "bybit_api_key", "") or ""
        api_secret = getattr(cfg, "bybit_api_secret", "") or ""
        if not api_key or not api_secret:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        return ccxt.bybit({
            "aiohttp_trust_env": True,
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("Per-user live trading is Bitget-only; the Bybit venue "
                    "trades the operator account configured in .env.")
        return ("BYBIT_API_KEY and BYBIT_API_SECRET required for live "
                "trading on Bybit. Set them in .env and restart. Account "
                "must be in ONE-WAY position mode.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(getattr(cfg, "bybit_api_key", "")
                    and getattr(cfg, "bybit_api_secret", ""))

    def order_symbol(self, symbol: str) -> str:
        # Bybit resolves "BTC/USDT" to the SPOT market — always perp form.
        return self.swap_symbol(symbol)

    def leverage_params(self, margin_mode: str) -> dict:
        # v5 set-leverage takes buyLeverage/sellLeverage (ccxt fills them
        # from the leverage argument); marginMode is set separately.
        return {}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"stopLossPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        return _is_trigger_order(order)

    def order_id_params(self, coid: str) -> dict:
        # Bybit orderLinkId (<=36 chars) via ccxt's clientOrderId alias;
        # no Bitget-style raw "clientOid" key.
        return {"clientOrderId": coid}


class BingxVenue(Venue):
    """BingX USDT perpetuals via ccxt.

    The small-account venue: $2 min order notional (vs $5 Bitget /
    $10 Hyperliquid), 700+ USDT perps, coin-denominated amounts.
    Quirks encoded here:
      - order_symbol maps to the perp form (spot+swap both listed)
      - set_leverage requires a side param — "BOTH" in one-way mode
      - SL/TP via stopLossPrice/takeProfitPrice params, client-side
        trigger filtering on open-order queries
    """

    id = "bingx"
    display_name = "BingX"
    quote = "USDT"
    balance_coin = "USDT"
    min_notional_usd = 2.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = False
    margin_mode_call_first = True

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        api_key = getattr(cfg, "bingx_api_key", "") or ""
        api_secret = getattr(cfg, "bingx_api_secret", "") or ""
        if not api_key or not api_secret:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        return ccxt.bingx({
            "aiohttp_trust_env": True,
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("Per-user live trading is Bitget-only; the BingX venue "
                    "trades the operator account configured in .env.")
        return ("BINGX_API_KEY and BINGX_API_SECRET required for live "
                "trading on BingX. Set them in .env and restart. Account "
                "must be in ONE-WAY position mode.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(getattr(cfg, "bingx_api_key", "")
                    and getattr(cfg, "bingx_api_secret", ""))

    def order_symbol(self, symbol: str) -> str:
        return self.swap_symbol(symbol)

    def leverage_params(self, margin_mode: str) -> dict:
        # One-way mode: BingX requires side=BOTH on set-leverage.
        return {"marginMode": margin_mode, "side": "BOTH"}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"stopLossPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        return _is_trigger_order(order)

    def order_id_params(self, coid: str) -> dict:
        return {"clientOrderId": coid}


_VENUES: dict[str, Venue] = {
    "bitget": BitgetVenue(),
    "hyperliquid": HyperliquidVenue(),
    "bybit": BybitVenue(),
    "bingx": BingxVenue(),
}


def valid_venue_ids() -> list[str]:
    return sorted(_VENUES)


def get_venue_override() -> Optional[str]:
    """The persisted runtime venue override, or None. Any unreadable /
    invalid file content is treated as no-override (fail-safe)."""
    try:
        with open(VENUE_OVERRIDE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        vid = str(data.get("venue", "")).strip().lower()
        if vid in _VENUES:
            return vid
        if vid:
            logger.warning("venue_override.json names unknown venue '%s' "
                           "— ignoring", vid)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — corrupt file must never trade-block
        logger.warning("venue_override.json unreadable (%s) — ignoring", exc)
    return None


def set_venue_override(venue_id: Optional[str]) -> None:
    """Persist (or clear, with None) the runtime venue override.
    Atomic write so a crash mid-save can't leave a corrupt file."""
    if venue_id is None:
        try:
            os.remove(VENUE_OVERRIDE_FILE)
        except FileNotFoundError:
            pass
        return
    vid = venue_id.strip().lower()
    if vid not in _VENUES:
        raise ValueError(f"unknown venue '{venue_id}' "
                         f"(valid: {', '.join(sorted(_VENUES))})")
    os.makedirs(os.path.dirname(VENUE_OVERRIDE_FILE) or ".", exist_ok=True)
    tmp = VENUE_OVERRIDE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"venue": vid}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, VENUE_OVERRIDE_FILE)


def get_venue(venue_id: Optional[str] = None) -> Venue:
    """Resolve a venue spec. No id -> runtime override (set by /venue,
    persisted across restarts) > VENUE env (CONFIG.exchange.venue).
    Unknown ids fall back to Bitget with a critical log rather than
    crashing the trading loop."""
    if venue_id is None:
        venue_id = get_venue_override()
    if venue_id is None:
        from bot.config import CONFIG
        venue_id = getattr(CONFIG.exchange, "venue", "bitget")
    v = _VENUES.get((venue_id or "bitget").strip().lower())
    if v is None:
        logger.critical(
            "Unknown VENUE '%s' — falling back to bitget. "
            "Valid venues: %s", venue_id, ", ".join(sorted(_VENUES)))
        return _VENUES["bitget"]
    return v
