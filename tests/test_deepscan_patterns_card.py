"""
Deep-scan patterns card + real-ATR setup numbers.

Two changes are pinned here:
  1. render_patterns_card() turns the deep-scan pattern observations (per-symbol
     chart + candle patterns) into a PNG, mirroring the text readout — so the
     scan can be shown as an image like every other scan.
  2. DeepScanSkill now derives a TRUE-RANGE ATR from the OHLCV it already
     fetches, instead of a flat price*0.02 placeholder. The placeholder made
     every setup show an identical ~4.4%/6.6% stop/target; a real per-symbol
     ATR makes those numbers reflect actual volatility.
"""

from bot.formatters.signal_card import render_patterns_card
from bot.skills.skill_registry import _true_range_atr


_HITS = [
    {
        "symbol": "ETH/USDT", "price": 1582.52, "chg": -0.04, "rsi": 51,
        "vol_spike": False,
        "chart_patterns": [
            {"name": "S/R Flip (Support -> Resistance)", "signal": "bearish", "confidence": 0.70},
            {"name": "Elliott ABC Expanded Flat", "signal": "bullish", "confidence": 0.70},
            {"name": "Wyckoff Accumulation", "signal": "bullish", "confidence": 0.70},
        ],
        "candle_patterns": {"doji": "neutral", "spinning_top": "neutral"},
    },
    {
        "symbol": "OPENAI/USDT", "price": 1336.06, "chg": -0.1, "rsi": 26,
        "vol_spike": True,
        "chart_patterns": [
            {"name": "Double Top", "signal": "bearish", "confidence": 0.71},
            {"name": "Wyckoff Accumulation", "signal": "bullish", "confidence": 0.70},
        ],
        "candle_patterns": {},
    },
]


def _is_png(b: bytes) -> bool:
    return isinstance(b, bytes) and b[:4] == b"\x89PNG"


class TestPatternsCard:
    def test_renders_png(self):
        png = render_patterns_card(_HITS, scan_label="DEEP SCAN 4H",
                                   timestamp="15:31 UTC", subtitle="2 hits · 4h")
        assert _is_png(png)
        assert len(png) > 1000

    def test_empty_is_safe(self):
        png = render_patterns_card([])
        assert _is_png(png)  # still a valid image, not a crash

    def test_tolerates_missing_fields(self):
        # Only a symbol — no price/rsi/patterns. Must not raise.
        png = render_patterns_card([{"symbol": "BTC/USDT"}])
        assert _is_png(png)

    def test_respects_max_symbols(self):
        many = [dict(_HITS[0], symbol=f"S{i}/USDT") for i in range(20)]
        # Should render without error and cap internally.
        png = render_patterns_card(many, max_symbols=5)
        assert _is_png(png)

    def test_handles_high_and_low_confidence(self):
        hits = [{
            "symbol": "X/USDT", "price": 1.0, "chg": 0.0, "rsi": 50,
            "chart_patterns": [
                {"name": "A", "signal": "bullish", "confidence": 0.0},
                {"name": "B", "signal": "bearish", "confidence": 1.0},
                {"name": "C", "signal": "neutral", "confidence": 1.5},  # clamps
            ],
            "candle_patterns": {"hammer": "bullish"},
        }]
        assert _is_png(render_patterns_card(hits))


class TestTrueRangeAtr:
    def test_constant_range(self):
        # high-low = 20 every bar, no gaps -> ATR = 20.
        assert _true_range_atr([110] * 30, [90] * 30, [100] * 30) == 20.0

    def test_gap_uses_close_to_high(self):
        # prev close 100, then high 130 / low 120 -> TR = max(10, 30, 20) = 30.
        assert _true_range_atr([100, 130], [100, 120], [100, 125], period=14) == 30.0

    def test_flat_series_returns_zero(self):
        # No movement -> 0.0 so the caller applies its own fallback.
        assert _true_range_atr([100] * 30, [100] * 30, [100] * 30) == 0.0

    def test_too_short_returns_zero(self):
        assert _true_range_atr([100], [100], [100]) == 0.0

    def test_distinct_symbols_get_distinct_atr(self):
        # The whole point of the fix: volatility differs by symbol, so the ATR
        # is NOT a fixed fraction of price.
        calm = _true_range_atr([101] * 30, [99] * 30, [100] * 30)    # range 2
        wild = _true_range_atr([120] * 30, [80] * 30, [100] * 30)    # range 40
        assert wild > calm
        # And neither equals the old flat 2%-of-price placeholder (100*0.02=2)
        # for the wild one.
        assert wild != 2.0
