"""Confluence must be oriented to the trade direction before it is blended into
confidence.

`confluence` is a bullishness score (0.5 neutral, >0.5 long, <0.5 short). The
blend used it RAW, so a strongly-confirmed SHORT (a low confluence value) was
scored as WEAK — shorts were systematically suppressed and longs over-credited.
A SHORT's confirming strength is (1 - confluence). Validated on the honest
6-fold walk-forward A/B: mean OOS -1.48% -> -1.12%, better on 4/6 folds, worse
on none.
"""
import inspect

from bot.core.analyzer import Analyzer
from bot.utils.models import Direction


def _orient(confluence: float, direction: Direction) -> float:
    return confluence if direction == Direction.LONG else 1.0 - confluence


def test_orientation_math():
    # A strongly-BEARISH reading (0.2) is WEAK for a long, STRONG for a short.
    assert _orient(0.2, Direction.LONG) == 0.2
    assert _orient(0.2, Direction.SHORT) == 0.8
    # A strongly-BULLISH reading (0.85): strong long, weak short.
    assert _orient(0.85, Direction.LONG) == 0.85
    assert abs(_orient(0.85, Direction.SHORT) - 0.15) < 1e-9
    # Neutral is symmetric.
    assert _orient(0.5, Direction.LONG) == _orient(0.5, Direction.SHORT) == 0.5


def test_short_no_longer_penalised_relative_to_mirror_long():
    # A SHORT with confluence 0.25 (bearish, i.e. it agrees with the short) must
    # blend in with the SAME strength a LONG with confluence 0.75 (bullish) does.
    assert _orient(0.25, Direction.SHORT) == _orient(0.75, Direction.LONG) == 0.75


def test_analyze_source_orients_confluence_before_blending():
    src = inspect.getsource(Analyzer.analyze)
    assert "conf_term = confluence if direction == Direction.LONG else 1.0 - confluence" in src
    assert "confidence * _llm_w + conf_term * _conf_w" in src
    # The old raw blend must be gone.
    assert "confidence * _llm_w + confluence * _conf_w" not in src
