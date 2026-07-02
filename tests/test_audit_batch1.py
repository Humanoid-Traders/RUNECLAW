"""Audit top-25 fixes, batch 1 (immediate).

#1  LLM direction guard — thesis direction cannot overrule a clear
    deterministic consensus (veto/haircut margins).
#2  Uncalibrated-LLM weight cap defaults ON.
#3  Flag comments must not contradict their coded defaults.
#11 Perfectly flat market yields neutral RSI=50, not 0.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.utils.models import Direction


class TestDirectionGuard:
    M = (0.10, 0.20)  # haircut, veto margins

    def test_long_with_bullish_consensus_passes(self):
        assert Analyzer._direction_guard_action(Direction.LONG, 0.75, *self.M) is None

    def test_short_with_bearish_consensus_passes(self):
        assert Analyzer._direction_guard_action(Direction.SHORT, 0.25, *self.M) is None

    def test_neutral_confluence_no_action(self):
        assert Analyzer._direction_guard_action(Direction.LONG, 0.5, *self.M) is None
        assert Analyzer._direction_guard_action(Direction.SHORT, 0.5, *self.M) is None

    def test_mild_opposition_no_action(self):
        # Opposition below the haircut margin is tolerated.
        assert Analyzer._direction_guard_action(Direction.LONG, 0.45, *self.M) is None

    def test_moderate_opposition_haircut(self):
        # LONG against confluence 0.35 (0.15 bearish deviation) -> haircut.
        assert Analyzer._direction_guard_action(Direction.LONG, 0.35, *self.M) == "haircut"
        assert Analyzer._direction_guard_action(Direction.SHORT, 0.65, *self.M) == "haircut"

    def test_strong_opposition_veto(self):
        # LONG against confluence 0.25 (0.25 bearish deviation) -> veto.
        assert Analyzer._direction_guard_action(Direction.LONG, 0.25, *self.M) == "veto"
        assert Analyzer._direction_guard_action(Direction.SHORT, 0.80, *self.M) == "veto"

    def test_guard_flag_defaults_on(self):
        a = CONFIG.analyzer
        assert a.llm_direction_guard_enabled is True
        assert 0.0 < a.llm_direction_haircut_margin < a.llm_direction_veto_margin


class TestUncalibratedWeightCap:
    def test_cap_defaults_on(self):
        assert CONFIG.analyzer.uncalibrated_llm_weight_cap_enabled is True

    def test_cap_value_bounds(self):
        assert 0.0 <= CONFIG.analyzer.uncalibrated_llm_weight_cap <= 1.0


class TestFlatMarketRsi:
    def test_flat_market_rsi_is_neutral_50(self):
        n = 40
        closes = np.full(n, 100.0)
        highs = closes + 0.0
        lows = closes - 0.0
        vols = np.full(n, 1000.0)
        ind = Analyzer._compute_indicators(highs, lows, closes, vols)
        assert ind is not None
        assert ind["rsi"] == 50.0

    def test_all_gains_rsi_near_100(self):
        n = 40
        closes = 100.0 + np.arange(n, dtype=float)
        ind = Analyzer._compute_indicators(closes + 0.5, closes - 0.5, closes,
                                           np.full(n, 1000.0))
        assert ind is not None
        assert ind["rsi"] > 95.0


class TestFlagDocConsistency:
    """Audit fix #3: a comment saying 'default OFF' directly above a flag whose
    coded default is True (or vice versa) misleads operators into flipping
    repaint/safety posture believing it's a documented no-op."""

    _BOOL_RE = re.compile(r'_env_bool\("([A-Z0-9_]+)",\s*(True|False)\)')
    _OFF_RE = re.compile(r"default\s*:?\s*OFF|Default\s*:?\s*OFF|default is off", re.IGNORECASE)
    _ON_RE = re.compile(r"default\s*:?\s*ON\b|Default\s*:?\s*ON\b", re.IGNORECASE)

    def _comment_block_above(self, lines: list[str], idx: int) -> str:
        block: list[str] = []
        j = idx - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            block.append(lines[j])
            j -= 1
        return "\n".join(reversed(block))

    def test_config_bool_defaults_match_comments(self):
        src = Path("bot/config.py").read_text()
        lines = src.splitlines()
        mismatches = []
        for i, line in enumerate(lines):
            m = self._BOOL_RE.search(line)
            if not m:
                continue
            env, default = m.group(1), m.group(2) == "True"
            block = self._comment_block_above(lines, i)
            if not block:
                continue
            if default and self._OFF_RE.search(block) and not self._ON_RE.search(block):
                mismatches.append(f"{env}: default True but comment says OFF")
            if not default and self._ON_RE.search(block) and not self._OFF_RE.search(block):
                mismatches.append(f"{env}: default False but comment says ON")
        assert not mismatches, "Stale flag comments:\n" + "\n".join(mismatches)
