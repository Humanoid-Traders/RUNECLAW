"""Tuning-audit fixes: limit-entry harvest sanitation + scan/analyze pattern
parity + ablation coverage of the live Elliott electorate.

Audit findings fixed here:
1. _compute_limit_entry treated EVERY numeric key_levels value as a price —
   bar indices (D_idx=33), fib ratios (w3_fib=1.618) and booleans
   (truncated=True; bool is int → 1.0) entered the limit-price candidate
   list and could actually bind on sub-$2 symbols.
2. scan_read stored the raw chart_patterns_geo list only — never deriving
   the typed keys (elliott_impulse, wyckoff_pattern, …, bullish/bearish
   weights), so every pattern-family voter was silent in /scan scoring
   while analyze() counted them: documented drift the shared helper kills.
3. scripts/voter_ablation.py CURATED listed only the dead legacy "elliott"
   label — the live ew_* voters (plus fib_extension, candles_mtf) had zero
   drop-one attribution and could never be measured.
"""
from __future__ import annotations

import numpy as np

from bot.core.analyzer import Analyzer, _compute_limit_entry
from bot.utils.models import Direction


def _closes(n=30, px=1.5):
    return np.array([px] * n)


class TestHarvestSanitation:
    def test_metadata_never_becomes_a_limit_price(self):
        # Sub-$2 symbol at 1.50: 1-ATR window is [0.5, 1.5] for a LONG.
        # truncated=True (1.0), w3_fib≈1.6→ no, D_idx=33→no... the poisonous
        # one is the bool: it lands INSIDE the window and used to bind.
        indicators = {
            "chart_patterns_geo": [{
                "name": "Elliott 5-Wave Impulse", "signal": "bullish",
                "confidence": 0.7,
                "key_levels": {
                    "truncated": True,          # bool → must be skipped
                    "extended_w3": True,
                    "w3_fib": 1.45,             # ratio inside the window → skipped
                    "b_retrace_fib": 0.62,
                    "y_ratio": 1.05,
                    "D_idx": 33,                # bar index → skipped
                },
            }],
        }
        out = _compute_limit_entry(1.5, 1.0, Direction.LONG, indicators, _closes())
        assert out is None, f"metadata bound as a limit price: {out}"

    def test_genuine_price_levels_still_harvest(self):
        indicators = {
            "chart_patterns_geo": [{
                "name": "Double Bottom", "signal": "bullish", "confidence": 0.6,
                "key_levels": {"neckline": 1.30, "bot1": 1.10, "bot2": 1.12,
                               "D_idx": 4, "truncated": False},
            }],
        }
        out = _compute_limit_entry(1.5, 0.5, Direction.LONG, indicators, _closes())
        assert out is not None, "genuine price levels must still harvest"
        assert any(abs(out - p) < 1e-9 for p in (1.30, 1.10, 1.12)), \
            f"the limit must bind on a REAL pattern level (got {out})"


class TestScanAnalyzeParity:
    PATTERNS = [
        {"name": "Elliott 5-Wave Impulse", "signal": "bullish", "confidence": 0.72,
         "key_levels": {"w1_top": 110.0}},
        {"name": "Wyckoff Accumulation", "signal": "bullish", "confidence": 0.61,
         "key_levels": {"range_top": 108.0}},
        {"name": "Double Top", "signal": "bearish", "confidence": 0.55,
         "key_levels": {"neckline": 100.0}},
    ]

    def test_shared_helper_derives_every_typed_key(self):
        ind: dict = {}
        Analyzer._extract_pattern_indicators(ind, list(self.PATTERNS))
        assert ind["elliott_impulse"]["name"] == "Elliott 5-Wave Impulse"
        assert ind["wyckoff_pattern"]["name"] == "Wyckoff Accumulation"
        assert ind["elliott_pattern"] is ind["elliott_impulse"]   # legacy key
        # Dedicated-voter patterns stay OUT of the aggregate; the geometric
        # Double Top is counted.
        assert ind["chart_patterns_bearish_count"] == 1
        assert ind["chart_patterns_bullish_count"] == 0
        assert abs(ind["chart_patterns_bearish_weight"] - 0.55) < 1e-9

    def test_scan_read_uses_the_shared_helper(self):
        import inspect
        src = inspect.getsource(Analyzer.scan_read)
        assert "Analyzer._extract_pattern_indicators(indicators, gp)" in src
        # analyze() delegates to the same helper — one derivation, no drift.
        src2 = inspect.getsource(Analyzer.analyze)
        assert "Analyzer._extract_pattern_indicators(indicators, chart_patterns)" in src2


class TestAblationCoversLiveVoters:
    def test_curated_lists_the_typed_elliott_labels(self):
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath(
            "scripts", "voter_ablation.py").read_text()
        m = re.search(r"CURATED = \[(.*?)\]", src, re.S)
        assert m, "CURATED list present"
        listed = set(re.findall(r"\"([a-z0-9_]+)\"", m.group(1)))
        for label in ("ew_impulse", "ew_corrective", "ew_diagonal", "ew_wxy",
                      "ew_mtf_align", "fib_extension", "candles_mtf"):
            assert label in listed, f"{label} missing from the ablation harness"
