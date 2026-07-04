"""SKIP_SIGNAL_TYPES gates negative-edge signal families the frozen-benchmark
attribution (under the LIVE partial-TP exit) flags as a persistent drag. Default
empty = trade every family, so behavior is unchanged unless deliberately set.
"""
from bot.config import CONFIG
from bot.core.analyzer import _is_gated_signal_type


def test_default_config_trades_all_families():
    assert CONFIG.analyzer.skip_signal_types == ""


def test_empty_config_gates_nothing():
    assert _is_gated_signal_type("momentum_confluence", "") is False


def test_single_family_gated():
    assert _is_gated_signal_type("momentum_confluence", "momentum_confluence") is True
    assert _is_gated_signal_type("regime_trend", "momentum_confluence") is False


def test_comma_separated_set_with_whitespace():
    cfg = " momentum_confluence , volume_spike "
    assert _is_gated_signal_type("volume_spike", cfg) is True
    assert _is_gated_signal_type("momentum_confluence", cfg) is True
    assert _is_gated_signal_type("regime_trend", cfg) is False


def test_unknown_family_never_gated_by_partial_match():
    # Substring must not match — only exact family names.
    assert _is_gated_signal_type("momentum", "momentum_confluence") is False
