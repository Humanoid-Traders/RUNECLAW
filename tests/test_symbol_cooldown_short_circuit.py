"""
Post-SL per-symbol cooldown is checked first, before the analysis pipeline
(deep-audit low #32).

The per-symbol SL cooldown used to be evaluated at the END of _analyze_signal —
after the OHLCV fetch, order-flow analysis, and the full analyzer run — so a
symbol that just stopped out still paid for the entire pipeline before being
skipped. The check now runs at the TOP, short-circuiting on signal.symbol
(== idea.asset) before any I/O.
"""

import inspect
import time
from types import SimpleNamespace

from bot.core.engine import RuneClawEngine
from bot.core.live_executor import normalize_symbol
from bot.utils.models import MarketSignal


def _signal(symbol="BTC/USDT"):
    return MarketSignal(symbol=symbol, price=50000.0, change_pct_24h=1.0,
                        volume_usd_24h=1e8, momentum_score=0.2)


class TestActiveCooldownShortCircuits:
    async def test_returns_none_without_touching_pipeline(self):
        # Fake self exposes ONLY the cooldown map. If the cooldown check did not
        # short-circuit, the function would dereference self.scanner / analyzer
        # and raise AttributeError — so a clean None proves the early return.
        key = normalize_symbol("BTC/USDT")
        fake = SimpleNamespace(_symbol_cooldowns={key: time.monotonic() + 1000})
        result = await RuneClawEngine._analyze_signal(fake, _signal("BTC/USDT"))
        assert result is None


class TestExpiredCooldownIsCleared:
    async def test_expired_entry_popped_then_proceeds(self):
        key = normalize_symbol("BTC/USDT")
        fake = SimpleNamespace(_symbol_cooldowns={key: time.monotonic() - 1})
        # Expired → the check pops it and falls through into the pipeline, which
        # dereferences missing attrs on the fake self; that error is caught by the
        # fetch try/except and the function returns None. We only care that the
        # expired entry was cleared before that.
        result = await RuneClawEngine._analyze_signal(fake, _signal("BTC/USDT"))
        assert result is None
        assert key not in fake._symbol_cooldowns


class TestCheckPrecedesIO:
    def test_cooldown_check_is_before_ohlcv_and_order_flow(self):
        src = inspect.getsource(RuneClawEngine._analyze_signal)
        cd = src.index("_symbol_cooldowns")
        ohlcv = src.index("_cached_ohlcv")
        order_flow = src.index("order_flow.analyze")
        assert cd < ohlcv, "cooldown check must precede the OHLCV fetch"
        assert cd < order_flow, "cooldown check must precede order-flow analysis"
