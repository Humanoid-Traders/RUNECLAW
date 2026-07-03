"""Voter-ablation hook + harness parsers.

The ABLATE_VOTERS env mutes named voters (weight → 0) in _score_confluence so
the harness can measure each voter's marginal contribution without touching
voter code. Unset → byte-identical scoring.
"""

import importlib.util
import os

import numpy as np

from bot.core.analyzer import Analyzer
from bot.core.ta_utils import Regime
from bot.utils.models import MarketSignal

_spec = importlib.util.spec_from_file_location(
    "voter_ablation",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "voter_ablation.py"))
abl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(abl)


def _sig():
    return MarketSignal(symbol="T/USDT", price=100.0, change_pct_24h=1.0,
                        volume_usd_24h=1e6)


def _breakdown(indicators):
    bd = []
    Analyzer.__dict__["_score_confluence"].__func__(
        indicators, Regime.TREND_UP, _sig(), breakdown=bd)
    return {n: w for n, v, w in bd}


class TestAblationHook:
    def teardown_method(self):
        os.environ.pop("ABLATE_VOTERS", None)
        Analyzer._ablate_cache = None

    def _ind(self):
        return {"candle_bullish_strength": 3.0, "candle_bearish_strength": 0.0,
                "candle_bullish_count": 3, "candle_bearish_count": 0,
                "macd_histogram": 0.5, "rsi": 45.0}

    def test_unset_is_noop(self):
        os.environ.pop("ABLATE_VOTERS", None)
        Analyzer._ablate_cache = None
        w = _breakdown(self._ind())
        assert w.get("macd", 0) > 0 and w.get("candlestick", 0) > 0

    def test_named_voter_muted(self):
        os.environ["ABLATE_VOTERS"] = "macd,candlestick"
        Analyzer._ablate_cache = None
        w = _breakdown(self._ind())
        assert w.get("macd") == 0.0 and w.get("candlestick") == 0.0

    def test_env_change_reparsed(self):
        os.environ["ABLATE_VOTERS"] = "macd"
        Analyzer._ablate_cache = None
        assert _breakdown(self._ind()).get("macd") == 0.0
        os.environ["ABLATE_VOTERS"] = "rsi"   # change value
        assert _breakdown(self._ind()).get("macd") > 0.0   # macd live again


class TestHarnessParsers:
    def test_parse_full_run(self):
        text = ("  Total Return:           +2.15%\n  Total Trades:     36\n"
                "  Winners:          27  (75%)\n  Profit Factor:    1.96\n"
                "  Sharpe Ratio:     1.20\n")
        p = abl._parse(text, wf=False)
        assert p["ok"] and p["return_pct"] == 2.15 and p["trades"] == 36
        assert p["profit_factor"] == 1.96

    def test_parse_walk_forward(self):
        text = "  => profitable folds 2/6 | mean OOS ret -1.29% | worst -3.97%"
        p = abl._parse(text, wf=True)
        assert p["ok"] and p["profitable_folds"] == 2 and p["mean_oos_pct"] == -1.29

    def test_verdict_directions(self):
        base = {"ok": True, "return_pct": 2.0}
        assert "CARRIES" in abl._verdict(base, {"ok": True, "return_pct": 1.0}, False)
        assert "HARMFUL" in abl._verdict(base, {"ok": True, "return_pct": 3.0}, False)
        assert "neutral" in abl._verdict(base, {"ok": True, "return_pct": 2.05}, False)
