"""
Venue abstraction (bot/core/venues.py) — Bitget zero-drift contract +
Hyperliquid adapter correctness (2026-07-12).

The Bitget tests pin EXACT params/symbols the executor sent before the
abstraction existed: if any of these change, live Bitget behavior changed
and the PR is wrong. The Hyperliquid tests encode the ccxt quirks that
would silently break orders: USDC symbol mapping, tp-vs-sl trigger kinds,
128-bit hex client ids, and the market-order price requirement.
"""
import asyncio
from types import SimpleNamespace

import pytest

from bot.core.venues import (BingxVenue, BitgetVenue, BybitVenue,  # noqa: F401
                             HyperliquidVenue, Venue, get_venue)


BG = BitgetVenue()
HL = HyperliquidVenue()
BY = BybitVenue()
BX = BingxVenue()


# ── factory ──────────────────────────────────────────────────────────
def test_get_venue_defaults_to_bitget():
    assert get_venue().id == "bitget"          # CONFIG default VENUE=bitget
    assert get_venue("bitget").id == "bitget"
    assert get_venue("hyperliquid").id == "hyperliquid"
    assert get_venue("HYPERLIQUID ").id == "hyperliquid"
    assert get_venue("bybit").id == "bybit"
    assert get_venue("bingx").id == "bingx"


def test_get_venue_unknown_falls_back_to_bitget():
    assert get_venue("binance").id == "bitget"   # fail-safe, never crash


# ── Bitget: zero-drift contract (params byte-equal to the old literals) ──
def test_bitget_swap_symbol_matches_old_idioms():
    # old: symbol if ":USDT" in symbol else f"{symbol}:USDT"
    assert BG.swap_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert BG.swap_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    # old: symbol.replace("/USDT", "/USDT:USDT")
    assert BG.swap_symbol("HOME/USDT") == "HOME/USDT:USDT"
    assert BG.swap_symbol("XAU/USDT") == "XAU/USDT:USDT"


def test_bitget_order_symbol_is_identity():
    # The executor historically passes spot-form symbols to create_order on
    # the swap-default exchange — the venue layer must NOT "fix" that.
    assert BG.order_symbol("BTC/USDT") == "BTC/USDT"
    assert BG.order_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"


def test_bitget_params_dicts_are_byte_identical_to_history():
    assert BG.futures_params() == {"productType": "USDT-FUTURES"}
    assert BG.entry_params("isolated", 5) == {
        "productType": "USDT-FUTURES",
        "marginMode": "isolated",
        "leverage": "5",
    }
    assert BG.close_params(True) == {
        "productType": "USDT-FUTURES", "reduceOnly": True}
    assert BG.close_params(False) == {
        "productType": "USDT-FUTURES", "reduceOnly": True,
        "tradeSide": "close"}
    assert BG.close_params(None) == BG.close_params(False)  # unknown → classic
    for kind in ("sl", "tp"):
        assert BG.trigger_params(kind, 123.45) == {
            "triggerPrice": 123.45,
            "triggerType": "last",
            "productType": "USDT-FUTURES",
            "tradeSide": "close",
            "reduceOnly": True,
        }
    assert BG.plan_order_query_params() == {
        "productType": "USDT-FUTURES", "isPlan": "plan_order"}
    assert BG.post_only_params() == {"timeInForce": "post_only"}
    assert BG.gtc_params() == {"timeInForce": "GTC"}
    assert BG.balance_fetch_params() == {"type": "swap"}
    assert BG.balance_coin == "USDT"


def test_bitget_client_oid_identity_and_id_params():
    assert BG.client_oid("rcABC123") == "rcABC123"
    assert BG.order_id_params("rcABC123") == {
        "clientOid": "rcABC123", "clientOrderId": "rcABC123"}


def test_bitget_is_plan_order_accepts_all():
    # Server-side isPlan filter — everything returned IS a plan order.
    assert BG.is_plan_order({}) is True


def test_bitget_capabilities():
    assert BG.supports_hedge_mode is True
    assert BG.supports_native_triggers is True
    assert BG.market_order_needs_price is False


# ── Hyperliquid adapter ──────────────────────────────────────────────
def test_hl_symbol_mapping_usdt_to_usdc():
    assert HL.swap_symbol("UNI/USDT") == "UNI/USDC:USDC"
    assert HL.swap_symbol("UNI/USDT:USDT") == "UNI/USDC:USDC"
    assert HL.swap_symbol("BTC/USDC:USDC") == "BTC/USDC:USDC"
    # order calls must always get the venue perp form
    assert HL.order_symbol("UNI/USDT") == "UNI/USDC:USDC"


def test_hl_trigger_kinds_are_distinct():
    """ccxt hyperliquid maps triggerPrice -> tpsl 'sl' and takeProfitPrice
    -> 'tp'. Sending a TP as triggerPrice would create a wrong-way stop."""
    sl = HL.trigger_params("sl", 100.0)
    tp = HL.trigger_params("tp", 120.0)
    assert sl == {"triggerPrice": 100.0, "reduceOnly": True}
    assert tp == {"takeProfitPrice": 120.0, "reduceOnly": True}
    # Bitget-only params must NOT leak into the exact-schema HL payload
    for p in (sl, tp):
        assert "productType" not in p and "tradeSide" not in p
        assert "triggerType" not in p


def test_hl_client_oid_is_128bit_hex_and_deterministic():
    a = HL.client_oid("rcIDEA1BTCUSDT")
    b = HL.client_oid("rcIDEA1BTCUSDT")
    c = HL.client_oid("rcIDEA1BTCUSDT-r1")
    assert a == b            # retry dedup depends on determinism
    assert a != c            # retry key is distinct
    assert a.startswith("0x") and len(a) == 34
    int(a, 16)               # valid hex
    # only clientOrderId — a foreign "clientOid" key would leak into the
    # exact-schema payload (ccxt hyperliquid doesn't omit unknown params)
    assert set(HL.order_id_params("rcX")) == {"clientOrderId"}
    assert HL.order_id_params("rcX")["clientOrderId"] == HL.client_oid("rcX")


def test_hl_is_plan_order_filters_triggers_only():
    """fetch_open_orders on HL has no server-side plan filter — the cleanup
    must never cancel a resting entry limit order."""
    assert HL.is_plan_order({"triggerPrice": 100.0}) is True
    assert HL.is_plan_order({"info": {"isTrigger": True}}) is True
    assert HL.is_plan_order({"id": "1", "type": "limit"}) is False


def test_hl_capabilities_and_constants():
    assert HL.supports_hedge_mode is False
    assert HL.supports_native_triggers is False
    assert HL.market_order_needs_price is True
    assert HL.balance_coin == "USDC"
    assert HL.min_notional_usd == 10.0
    assert HL.entry_params("isolated", 5) == {}
    assert HL.close_params(None) == {"reduceOnly": True}
    assert HL.balance_fetch_params() == {}


# ── Bybit + BingX adapters (USDT linear perps, coin amounts) ─────────
@pytest.mark.parametrize("v", [BY, BX], ids=["bybit", "bingx"])
def test_usdt_venue_order_symbol_maps_to_perp_form(v):
    """Both venues list spot AND swap markets — a bare "BTC/USDT" would
    resolve to SPOT. order_symbol must always hand ccxt the perp form."""
    assert v.order_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert v.order_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    assert v.swap_symbol("UNI/USDT") == "UNI/USDT:USDT"


@pytest.mark.parametrize("v", [BY, BX], ids=["bybit", "bingx"])
def test_usdt_venue_trigger_dialect(v):
    """SL/TP via stopLossPrice/takeProfitPrice so ccxt derives the trigger
    direction — raw triggerPrice would need a manual triggerDirection on
    Bybit and could create a wrong-way trigger."""
    sl = v.trigger_params("sl", 100.0)
    tp = v.trigger_params("tp", 120.0)
    assert sl == {"stopLossPrice": 100.0, "reduceOnly": True}
    assert tp == {"takeProfitPrice": 120.0, "reduceOnly": True}
    for p in (sl, tp):
        assert "productType" not in p and "triggerType" not in p


@pytest.mark.parametrize("v", [BY, BX], ids=["bybit", "bingx"])
def test_usdt_venue_plan_filter_is_client_side(v):
    """No trusted server-side plan filter — cleanup must never cancel a
    resting entry limit order."""
    assert v.is_plan_order({"triggerPrice": 1.0}) is True
    assert v.is_plan_order({"stopLossPrice": 1.0}) is True
    assert v.is_plan_order({"info": {"stopOrderType": "Stop"}}) is True
    assert v.is_plan_order({"id": "1", "type": "limit"}) is False


@pytest.mark.parametrize("v", [BY, BX], ids=["bybit", "bingx"])
def test_usdt_venue_capabilities(v):
    assert v.quote == "USDT" and v.balance_coin == "USDT"
    assert v.supports_hedge_mode is False
    assert v.supports_native_triggers is False
    assert v.market_order_needs_price is False
    assert v.margin_mode_call_first is True
    # ids pass through untouched (both accept <=36-char alphanumeric ids)
    assert v.client_oid("rcABC123") == "rcABC123"
    assert v.order_id_params("rcX") == {"clientOrderId": "rcX"}


def test_min_notionals_per_venue():
    assert BG.min_notional_usd == 5.0
    assert HL.min_notional_usd == 10.0
    assert BY.min_notional_usd == 5.0
    assert BX.min_notional_usd == 2.0   # the small-account venue


def test_bingx_leverage_needs_side_both():
    assert BX.leverage_params("isolated") == {
        "marginMode": "isolated", "side": "BOTH"}
    assert BY.leverage_params("isolated") == {}          # v5 sets separately
    assert BG.leverage_params("isolated") == {"marginMode": "isolated"}


def test_bybit_create_exchange_and_missing_creds():
    cfg = SimpleNamespace(bybit_api_key="bk", bybit_api_secret="bs")
    ex = BY.create_exchange(cfg)
    try:
        assert ex.id == "bybit"
        assert ex.apiKey == "bk" and ex.secret == "bs"
        assert ex.options["defaultType"] == "swap"
    finally:
        _close(ex)
    empty = SimpleNamespace(bybit_api_key="", bybit_api_secret="")
    with pytest.raises(RuntimeError, match="BYBIT_API_KEY"):
        BY.create_exchange(empty)


def test_bingx_create_exchange_and_missing_creds():
    cfg = SimpleNamespace(bingx_api_key="xk", bingx_api_secret="xs")
    ex = BX.create_exchange(cfg)
    try:
        assert ex.id == "bingx"
        assert ex.apiKey == "xk" and ex.secret == "xs"
    finally:
        _close(ex)
    empty = SimpleNamespace(bingx_api_key="", bingx_api_secret="")
    with pytest.raises(RuntimeError, match="BINGX_API_KEY"):
        BX.create_exchange(empty)


def test_generic_leverage_path_uses_venue_hooks():
    import inspect
    from bot.core.live_executor import LiveExecutor
    src = inspect.getsource(LiveExecutor._ensure_leverage_generic)
    assert "margin_mode_call_first" in src
    assert "self._venue.leverage_params(" in src


# ── exchange construction ────────────────────────────────────────────
def _close(ex):
    asyncio.run(ex.close())


def test_bitget_create_exchange_reproduces_options():
    cfg = SimpleNamespace(api_key="k", api_secret="s", passphrase="p",
                          sandbox=True, trade_mode="futures")
    ex = BG.create_exchange(cfg)
    try:
        assert ex.id == "bitget"
        assert ex.apiKey == "k" and ex.secret == "s" and ex.password == "p"
        assert ex.options["defaultType"] == "swap"
        assert ex.options["uta"] is True
    finally:
        _close(ex)


def test_bitget_create_exchange_missing_creds_raises():
    cfg = SimpleNamespace(api_key="", api_secret="", passphrase="",
                          sandbox=True, trade_mode="futures")
    with pytest.raises(RuntimeError, match="BITGET_API_KEY"):
        BG.create_exchange(cfg)


def test_bitget_create_exchange_missing_passphrase_fails_loud():
    # Key+secret present but NO passphrase: ccxt would otherwise build the client
    # and throw a cryptic `bitget requires "password" credential` on the first
    # private call (the live "auth FAILED / unprotected positions" incident).
    # We fail loud at build time, naming the missing input.
    cfg = SimpleNamespace(api_key="k", api_secret="s", passphrase="",
                          sandbox=True, trade_mode="futures")
    with pytest.raises(RuntimeError, match="BITGET_PASSPHRASE"):
        BG.create_exchange(cfg)


def test_bitget_per_user_missing_passphrase_points_to_connect():
    cfg = SimpleNamespace(api_key="x", api_secret="x", passphrase="x",
                          sandbox=True, trade_mode="futures")
    with pytest.raises(RuntimeError, match="/connect"):
        BG.create_exchange(cfg, {"api_key": "k", "api_secret": "s", "passphrase": ""})
    with pytest.raises(RuntimeError, match="/connect"):
        BG.create_exchange(cfg, credentials={"api_key": "", "api_secret": ""})


def test_hl_create_exchange_uses_wallet_credentials():
    cfg = SimpleNamespace(hyperliquid_wallet_address="0xabc",
                          hyperliquid_private_key="0xkey",
                          hyperliquid_testnet=False,
                          trade_mode="futures")
    ex = HL.create_exchange(cfg)
    try:
        assert ex.id == "hyperliquid"
        assert ex.walletAddress == "0xabc"
        assert ex.privateKey == "0xkey"
        assert ex.options["defaultType"] == "swap"
    finally:
        _close(ex)


def test_hl_create_exchange_missing_creds_raises():
    cfg = SimpleNamespace(hyperliquid_wallet_address="",
                          hyperliquid_private_key="",
                          hyperliquid_testnet=False,
                          trade_mode="futures")
    with pytest.raises(RuntimeError, match="HYPERLIQUID_WALLET_ADDRESS"):
        HL.create_exchange(cfg)


# ── executor wiring ──────────────────────────────────────────────────
def test_executor_defaults_to_bitget_venue(tmp_path):
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    assert ex._venue.id == "bitget"


def test_per_user_executor_is_always_bitget(tmp_path, monkeypatch):
    """The /connect credential store has no venue field — a per-user
    executor must stay on Bitget even when the operator runs Hyperliquid."""
    import bot.core.live_executor as le
    monkeypatch.setattr(
        le, "get_venue",
        lambda vid=None: get_venue(vid if vid is not None else "hyperliquid"))
    op = le.LiveExecutor(state_dir=str(tmp_path))
    assert op._venue.id == "hyperliquid"
    user = le.LiveExecutor(user_id="42",
                           credentials={"api_key": "k", "api_secret": "s"},
                           state_dir=str(tmp_path))
    assert user._venue.id == "bitget"


@pytest.mark.asyncio
async def test_venue_market_price_is_none_on_bitget(tmp_path):
    """price=None keeps every Bitget create_order call byte-identical."""
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor(state_dir=str(tmp_path))
    price = await ex._venue_market_price(None, "BTC/USDT")
    assert price is None


# ── source pins (money-path wiring stays venue-routed) ───────────────
def test_execute_wires_market_price_and_venue_params():
    import inspect
    from bot.core.live_executor import LiveExecutor
    src = inspect.getsource(LiveExecutor.execute)
    assert "self._venue.entry_params(" in src
    assert "market_order_needs_price" in src
    assert "self._venue.post_only_params()" in src


def test_sl_tp_and_close_paths_are_venue_routed():
    import inspect
    from bot.core.live_executor import LiveExecutor
    for meth, needles in [
        (LiveExecutor._place_sl_tp,
         ["self._venue.trigger_params(\"sl\"", "self._venue.trigger_params(\"tp\"",
          "self._venue.plan_order_query_params()", "is_plan_order"]),
        (LiveExecutor._update_exchange_sl,
         ["self._venue.trigger_params(\"sl\"", "supports_native_triggers"]),
        (LiveExecutor._partial_close,
         ["self._venue.close_params(", "_venue_market_price"]),
    ]:
        src = inspect.getsource(meth)
        for n in needles:
            assert n in src, f"{meth.__name__} missing {n}"
