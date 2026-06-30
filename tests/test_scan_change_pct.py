"""
Scan results populate change_pct so the %-change display works (deep-audit low #55).

_fmt_quick / _fmt_detail render r["change_pct"], but _scan_symbol never put it in
the result dict, so `change` was always 0 and change_str always empty — the
%-change was permanently invisible. _scan_symbol now computes the ~24h change.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.skills.scan_skill import _fmt_detail, _fmt_quick, _scan_symbol


def _ohlcv():
    # 40 bars of 4h. Closes flat at 100 except the last = 110 → c[-7]=100,
    # c[-1]=110 → +10% over the trailing window.
    bars = [[i, 100.0, 101.0, 99.0, 100.0, 1000.0] for i in range(39)]
    bars.append([39, 100.0, 111.0, 99.0, 110.0, 1000.0])
    return bars


def _row(**over):
    r = {"sym": "BTC/USDT", "price": 110.0, "dir": "LONG", "score": 0.5,
         "rsi": 60.0, "atr": 1.0, "vol_ratio": 1.0, "sma20": 105.0, "patterns": []}
    r.update(over)
    return r


class TestScanSymbolPopulatesChange:
    def test_change_pct_present_and_correct(self):
        ex = SimpleNamespace(fetch_ohlcv=AsyncMock(return_value=_ohlcv()))
        res = asyncio.run(_scan_symbol(ex, "BTC/USDT"))
        assert res is not None
        assert "change_pct" in res
        assert res["change_pct"] == 10.0


class TestFormattersRenderChange:
    def test_quick_shows_positive_change(self):
        assert "+10.0%" in _fmt_quick(_row(change_pct=10.0))

    def test_quick_shows_negative_change(self):
        assert "-5.0%" in _fmt_quick(_row(change_pct=-5.0))

    def test_quick_hides_zero_change(self):
        assert "%" not in _fmt_quick(_row(change_pct=0.0)).split("RSI")[0]

    def test_detail_shows_change(self):
        assert "+10.0%" in _fmt_detail(_row(change_pct=10.0))
