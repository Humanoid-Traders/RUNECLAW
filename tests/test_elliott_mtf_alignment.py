"""
Cross-degree (all-timeframes) Elliott alignment (2026-07-13).

The engine already fetches 15m/1h/4h/1d (200 bars each) for every
analysis, but Elliott only read ONE of them — the degree matched to the
setup's strategy_type. The other three series sat unused. mtf_wave_map
now reads waves on every degree and produces one bounded agreement vote:
nested with-trend structure across degrees is the textbook Elliott entry;
a terminal 4h/1d wave 5 / ending diagonal argues against chasing a
lower-degree signal. Zero extra API calls.
"""

from __future__ import annotations

import inspect

import numpy as np

from bot.core.elliott import mtf_wave_map


def _impulse(signal="bullish", wave="3", conf=0.7):
    return {"name": "Elliott Impulse Wave", "signal": signal,
            "confidence": conf, "key_levels": {"current_wave": wave},
            "description": f"impulse in wave {wave}"}


def _diagonal(signal="bearish", conf=0.6):
    return {"name": "Elliott Ending Diagonal", "signal": signal,
            "confidence": conf, "key_levels": {}}


def _wxy(signal="bearish", conf=0.6):
    return {"name": "Elliott WXY Correction", "signal": signal,
            "confidence": conf, "key_levels": {}}


# ── pure map semantics ───────────────────────────────────────────────
def test_nested_bullish_impulses_align_strongly():
    m = mtf_wave_map({tf: _impulse() for tf in ("15m", "1h", "4h", "1d")})
    assert m["n_timeframes"] == 4
    assert m["alignment"] > 0.5
    assert m["dominant_bias"] == "bullish"
    assert m["higher_degree_terminal"] is False
    # every degree carries a W3 boost (1.25 x 0.7 conf)
    assert all(d["action"] == "enter" for d in m["by_tf"].values())


def test_higher_degree_ending_diagonal_flags_terminal():
    m = mtf_wave_map({"15m": _impulse(), "1h": _impulse(),
                      "1d": _diagonal()})
    assert m["higher_degree_terminal"] is True
    # the diagonal contributes little (weight_mult 0.3) but against
    assert m["by_tf"]["1d"]["action"] == "avoid"


def test_low_degree_diagonal_is_not_terminal():
    """Exhaustion on 15m only must NOT set the higher-degree flag."""
    m = mtf_wave_map({"15m": _diagonal(), "4h": _impulse()})
    assert m["higher_degree_terminal"] is False


def test_completed_correction_votes_the_resumption():
    """A completed WXY labeled with the CORRECTION's direction (bearish
    pullback in an uptrend) must vote the resumption — positive."""
    m = mtf_wave_map({"4h": _wxy(signal="bearish")})
    assert m["by_tf"]["4h"]["effective"] > 0     # flipped to bullish


def test_higher_degrees_outweigh_lower():
    """1d bearish vs 15m bullish at equal confidence → net bearish."""
    m = mtf_wave_map({"15m": _impulse("bullish"), "1d": _impulse("bearish")})
    assert m["alignment"] < 0


def test_empty_and_none_inputs_are_neutral():
    for arg in ({}, None, {"1h": None}):
        m = mtf_wave_map(arg)
        assert m["alignment"] == 0.0
        assert m["dominant_bias"] == "neutral"
        assert m["n_timeframes"] == 0


def test_alignment_is_clamped():
    m = mtf_wave_map({"1h": _impulse(conf=5.0)})   # absurd confidence
    assert -1.0 <= m["alignment"] <= 1.0


# ── analyzer wiring ──────────────────────────────────────────────────
def test_apply_builds_map_from_all_timeframes(monkeypatch):
    import bot.core.analyzer as az

    calls = []

    def fake_detectors(scratch, highs, lows, closes, swings):
        calls.append(len(closes))
        scratch["elliott_pattern"] = _impulse()

    monkeypatch.setattr(az, "_run_elliott_detectors", fake_detectors)
    rows = [[i, 1.0, 1.1, 0.9, 1.0, 100.0] for i in range(40)]
    indicators: dict = {}
    az._apply_mtf_elliott_alignment(
        indicators, {"15m": rows, "1h": rows, "4h": rows, "1d": rows})
    assert len(calls) == 4                          # every degree scanned
    assert indicators["elliott_mtf"]["n_timeframes"] == 4
    assert indicators["elliott_mtf"]["alignment"] > 0


def test_apply_requires_two_timeframes(monkeypatch):
    import bot.core.analyzer as az
    monkeypatch.setattr(
        az, "_run_elliott_detectors",
        lambda scratch, *a: scratch.__setitem__("elliott_pattern", _impulse()))
    rows = [[i, 1.0, 1.1, 0.9, 1.0, 100.0] for i in range(40)]
    indicators: dict = {}
    az._apply_mtf_elliott_alignment(indicators, {"1h": rows})
    assert "elliott_mtf" not in indicators          # single degree = no map


def test_confluence_vote_and_gating_wired():
    import bot.core.analyzer as az
    src = inspect.getsource(az.Analyzer._score_confluence)
    assert "ew_mtf_align" in src
    assert "higher_degree_terminal" in src          # terminal halves the vote
    src2 = inspect.getsource(az.Analyzer.analyze)
    assert "elliott_mtf_alignment_enabled" in src2
    assert "_apply_mtf_elliott_alignment" in src2


def test_config_flag_exists():
    from bot.config import CONFIG
    assert isinstance(CONFIG.analyzer.elliott_mtf_alignment_enabled, bool)
