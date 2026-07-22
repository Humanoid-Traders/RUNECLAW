"""
Venue-safety hardening (deep-audit money-path + venue findings).

Three fixes:
1. Self-heal no longer cancel-then-replaces a HEALTHY v3 combined stop —
   it re-places only when the exchange CONFIRMS the stop is missing
   (_stop_live_on_exchange returns False), eliminating the naked window.
2. _ensure_leverage_generic now VERIFIES leverage (abort on stuck
   mismatch) and margin mode (alert on cross-when-isolated) — the guards
   were previously Bitget-only, so other venues could silently run wrong
   leverage / cross margin.
3. cancel_order routes the symbol through venue.order_symbol — identity
   on Bitget (zero regression), perp-form on Bybit/BingX/Hyperliquid where
   the bare spot-form symbol mis-resolves and the cancel fails.
"""

import inspect


from bot.core.live_executor import LiveExecutor
from bot.core.venues import get_venue


class _FakePos:
    symbol = "BTC/USDT"
    trade_id = "TI-x"
    direction = "LONG"


class _FakeExchange:
    def __init__(self, positions):
        self._positions = positions

    async def fetch_positions(self, syms, params=None):
        return self._positions


def _executor(tmp_path):
    return LiveExecutor(state_dir=str(tmp_path))


# ── 1. self-heal naked-window gate ────────────────────────────────────

class TestStopLiveOnExchange:
    def _run(self, ex, positions, tmp_path):
        import asyncio
        lx = _executor(tmp_path)

        async def _fake_get_exchange():
            return _FakeExchange(positions)

        lx._get_exchange = _fake_get_exchange
        return asyncio.get_event_loop().run_until_complete(
            lx._stop_live_on_exchange(_FakePos()))

    def test_stop_present_returns_true(self, tmp_path):
        pos = [{"contracts": 1.0, "info": {"stopLoss": "95.0"}}]
        assert self._run(None, pos, tmp_path) is True

    def test_stop_absent_returns_false(self, tmp_path):
        pos = [{"contracts": 1.0, "info": {"stopLoss": "0"}}]
        assert self._run(None, pos, tmp_path) is False

    def test_no_position_returns_none(self, tmp_path):
        assert self._run(None, [], tmp_path) is None

    def test_selfheal_only_replaces_on_confirmed_missing(self):
        src = _selfheal_source()
        assert "_stop_live_on_exchange" in src
        assert "_stop_live is False" in src


def _selfheal_source() -> str:
    """The self-heal method scans open positions and re-places SL/TP; find
    it by the marker string regardless of its exact name."""
    for name, obj in inspect.getmembers(LiveExecutor, inspect.isfunction):
        try:
            s = inspect.getsource(obj)
        except (OSError, TypeError):
            continue
        if "needs_fix" in s and "sl_order_id == pos.tp_order_id" in s:
            return s
    return ""


# ── 2. generic leverage/margin verification ───────────────────────────

class TestGenericLeverageVerify:
    def test_generic_path_verifies_and_aborts(self):
        src = inspect.getsource(LiveExecutor._ensure_leverage_generic)
        assert "fetch_leverage" in src
        assert "ABORTING" in src
        assert "RuntimeError" in src

    def test_generic_path_checks_margin_mode(self):
        src = inspect.getsource(LiveExecutor._ensure_leverage_generic)
        assert "margin_mode_mismatch" in src
        assert "cross" in src.lower()

    def test_unverifiable_leverage_warns_not_aborts(self):
        src = inspect.getsource(LiveExecutor._ensure_leverage_generic)
        assert "leverage_unverified" in src
        assert "_lev_unverified_warned" in src


# ── 3. cancel_order symbol routing ────────────────────────────────────

class TestCancelSymbolRouting:
    def test_no_bare_spot_symbol_in_cancel(self):
        src = inspect.getsource(LiveExecutor)
        # every cancel_order must route the symbol through the venue mapper
        import re
        bare = re.findall(r"cancel_order\([^)]*,\s*pos\.symbol\)", src)
        assert not bare, f"bare-symbol cancels remain: {bare}"
        assert "cancel_order" in src and "order_symbol(pos.symbol)" in src

    def test_order_symbol_identity_on_bitget(self):
        # zero-regression proof: Bitget order_symbol is identity, so the
        # rewrite is a no-op on the production venue.
        v = get_venue("bitget")
        assert v.order_symbol("BTC/USDT") == "BTC/USDT"
        assert v.order_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"

    def test_order_symbol_maps_to_perp_on_bybit(self):
        v = get_venue("bybit")
        # bare spot-form must become the perp form the order was placed with
        assert v.order_symbol("BTC/USDT") == "BTC/USDT:USDT"
