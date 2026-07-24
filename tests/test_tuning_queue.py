"""Tuning-audit queue batch — every change dark-flagged or recording-only,
per the direct-A/B doctrine (measure first; default ON only on improvement).

1. Shadow-priced liquidity guard: Check #17 (the only hard book/depth/spread
   gate) was exempt from the counterfactual shadow book that exists to price
   gates — its rejection branch returned BEFORE the recording hook. Now it
   records (recording-only, fail-open).
2. VP two-pass direction (dark): the volume-profile voter's momentum-vs-
   contrarian split read the RUNNING vote sum — voter-order dependent,
   flipping arbitrarily near net-zero. The flag defers the VP vote until the
   full electorate has registered.
3. current_wave emission (dark): wave_action's documented W3 boost was dead
   code — no detector emitted the field. The flag emits it from both partial
   impulse detectors.
4. Pattern price objectives (dark): fib extensions, harmonic D, Wyckoff
   phase extremes and necklines were computed then discarded; the flag feeds
   them into the level-aware SL/TP snap map as 'pattern_target'.
5. Measurement infra: ablation CURATED covers the SMC + order-flow voters
   that had zero attribution; stale flag comments corrected; the dead
   MIN_BOOK_DEPTH_USD knob is labeled dead.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from bot.config import CONFIG

ROOT = Path(__file__).resolve().parent.parent


class TestFlagsShipDark:
    def test_all_three_new_flags_default_off(self):
        assert CONFIG.analyzer.vp_twopass_direction_enabled is False
        assert CONFIG.analyzer.pattern_target_levels_enabled is False
        import os
        assert os.getenv("ELLIOTT_CURRENT_WAVE_ENABLED") is None, \
            "test env must not pre-set the dark flag"


class TestShadowPricedLiquidityGuard:
    def test_liquidity_rejection_records_to_the_shadow_book(self):
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine)
        i = src.find("Trade REJECTED by liquidity guard")
        assert i > 0
        seg = src[i:i + 2600]
        assert "SHADOW_BOOK.record_rejection" in seg, \
            "Check #17 must feed the counterfactual ledger"
        assert 'f"LIQUIDITY: {liq_reason}"' in seg
        assert "return None" in seg


class TestVpTwoPass:
    def test_flag_off_keeps_the_inline_vote_and_on_defers(self):
        from bot.core.analyzer import Analyzer
        src = inspect.getsource(Analyzer._score_confluence)
        assert "vp_twopass_direction_enabled" in src
        assert "_vp_deferred = vp" in src
        # The deferred vote runs AFTER the last regular voter and BEFORE the
        # family caps, on the full electorate.
        deferred = src.find("if _vp_deferred is not None:")
        caps = src.find("Mean-reversion oscillator de-correlation")
        assert 0 < deferred < caps, "deferred VP vote must precede the family caps"


class TestCurrentWaveEmission:
    def test_dark_by_default_and_emitted_under_the_flag(self, monkeypatch):
        import numpy as np
        from bot.core import chart_patterns as cp

        # Build a 3-wave (waves 1-3 visible) bullish sequence the partial
        # detector recognizes: swings low(10)→high(30)→low(22)→high(60).
        seg = []
        for a, b, n in ((10, 30, 8), (30, 22, 8), (22, 60, 10), (60, 55, 6)):
            seg += [a + (b - a) * i / n for i in range(n)]
        closes = np.array(seg, dtype=float)
        highs = closes + 0.5
        lows = closes - 0.5

        res_off = cp.detect_elliott_impulse(highs, lows, closes)
        monkeypatch.setenv("ELLIOTT_CURRENT_WAVE_ENABLED", "1")
        res_on = cp.detect_elliott_impulse(highs, lows, closes)
        # Whatever variant fires, the FLAG must gate the field — never present
        # dark, present (as "3") whenever a partial fires with the flag on.
        if res_off is not None:
            assert "current_wave" not in (res_off.get("key_levels") or {})
        if res_on is not None and "Partial" in res_on.get("name", ""):
            assert res_on["key_levels"].get("current_wave") == "3"

    def test_source_gates_both_partial_returns(self):
        src = Path(ROOT, "bot", "core", "chart_patterns.py").read_text()
        assert src.count('_env_bool("ELLIOTT_CURRENT_WAVE_ENABLED", False)') == 2


class TestPatternTargetLevels:
    def test_extras_hook_feeds_pattern_targets_under_the_flag(self):
        from bot.core.analyzer import Analyzer
        src = inspect.getsource(Analyzer.analyze)
        assert "pattern_target_levels_enabled" in src
        assert '"pattern_target"' in src
        # Metadata safety mirrors the harvest fix: bools skipped.
        i = src.find("_PT_KEYS")
        assert "isinstance(_lv, bool)" in src[i:i + 900]

    def test_level_kind_registered(self):
        from bot.core.levels import _KIND_BASE_SCORE
        assert _KIND_BASE_SCORE.get("pattern_target") == 1.1


class TestMeasurementInfra:
    def test_ablation_covers_the_round2_voters(self):
        src = Path(ROOT, "scripts", "voter_ablation.py").read_text()
        m = re.search(r"CURATED = \[(.*?)\]", src, re.S)
        listed = set(re.findall(r"\"([a-z0-9_]+)\"", m.group(1)))
        for label in ("fvg", "premium_discount", "mtf_choch", "mtf_alignment",
                      "of_funding", "of_whale_bias", "of_book_imbalance",
                      "of_cvd_divergence", "of_spot_futures_div", "of_oi_price_div"):
            assert label in listed, f"{label} missing from the ablation harness"

    def test_stale_flag_comments_corrected(self):
        src = Path(ROOT, "bot", "core", "order_flow.py").read_text()
        # The three 2026-06-activated flags must no longer claim "default OFF".
        for anchor in ("OF_GUARD_TOP_DEPTH_ENABLED", "OF_TIME_BARS_ENABLED",
                       "OF_FUNDING_VOTE_FIXED_SCALE"):
            i = src.find(f'_env_bool("{anchor}", True)')
            assert i > 0, f"{anchor} still defaults True"
            back = src[max(0, i - 700):i]
            assert "default OFF" not in back, f"stale 'default OFF' note near {anchor}"

    def test_dead_knob_is_labeled_dead(self):
        src = Path(ROOT, "bot", "config.py").read_text()
        i = src.find("min_book_depth_usd")
        assert "DEAD KNOB" in src[max(0, i - 600):i]
