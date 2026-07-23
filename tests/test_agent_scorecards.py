"""MARKETPLACE Phase 2b — committed per-agent benchmark scorecards.

Every marketplace agent carries a REAL, reproducible frozen-benchmark scorecard
(generated offline by scripts/gen_agent_scorecards.py, gated by the Phase-2a
preset filters). §4-safe: percent/ratio only, never a dollar figure, stamped
with the dataset hash so anyone can reproduce it in the Lab.
"""
import glob
import json
import os

from bot.core import strategy_catalog as sc
from bot.skills.skill_registry import RunStrategySkill

_SC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "benchmark", "scorecards")

# Metric KEYS that would leak an absolute dollar figure onto a public card (§4).
# Checked as JSON keys, never as raw substrings (symbols like BTC/USDT contain
# "usd", which is not a leak).
_FORBIDDEN = ("net_pnl", "total_pnl", "final_equity", "balance", "avg_win_usd",
              "avg_loss_usd", "total_commission", "max_drawdown_usd")


def _all_keys(obj):
    """Every dict key anywhere in a nested structure."""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _all_keys(v)
    return out


def test_a_scorecard_exists_for_every_preset():
    files = {os.path.basename(p) for p in glob.glob(os.path.join(_SC_DIR, "*.json"))}
    for key in RunStrategySkill.PRESETS:
        assert f"{sc._slug(key)}.json" in files, f"missing scorecard for {key}"


def test_scorecards_are_section4_safe_percent_ratio_only():
    for path in glob.glob(os.path.join(_SC_DIR, "*.json")):
        raw = open(path, encoding="utf-8").read()
        assert "$" not in raw, f"dollar sign in {path}"
        card = json.loads(raw)
        keys = _all_keys(card)
        for bad in _FORBIDDEN:
            assert bad not in keys, f"forbidden dollar key '{bad}' in {path}"
        assert card.get("format") == "runeclaw.agent.scorecard.v1"
        assert card.get("dataset_hash"), "scorecard must carry a dataset hash"
        # Only percent/ratio metrics.
        for mk in card["metrics"]:
            assert mk in {"total_return_pct", "profit_factor", "win_rate",
                          "max_drawdown_pct", "sharpe_ratio", "sortino_ratio",
                          "calmar_ratio", "total_trades"}


def test_catalog_attaches_scorecard_with_provenance():
    for card in sc.catalog():
        s = card.get("scorecard")
        assert s is not None, f"{card['id']} has no scorecard attached"
        assert s["dataset"] and len(s["dataset_hash"]) == 12   # truncated for display
        m = s["metrics"]
        for mk in ("total_return_pct", "profit_factor", "win_rate",
                   "max_drawdown_pct", "sharpe_ratio", "total_trades"):
            assert mk in m
        # No dollar field survives onto the card.
        assert not any(k in m for k in _FORBIDDEN)


def test_catalog_scorecard_is_failsoft(monkeypatch):
    # A missing scorecard dir just yields scorecard=None, never a crash.
    monkeypatch.setattr(sc, "_SCORECARD_DIR", "/nonexistent/path/xyz")
    cat = sc.catalog()
    assert cat and all(c["scorecard"] is None for c in cat)


def test_generator_gate_args_map_preset_filters():
    from scripts.gen_agent_scorecards import _gate_args
    # Dip sniper: confidence + regime + rsi gates.
    dip = _gate_args(RunStrategySkill.PRESETS["dip sniper"])
    assert "--confidence-threshold" in dip and "--regime-filter" in dip
    assert "--rsi-max" in dip and "TREND_DOWN" in dip
    # Momentum hunter: volume-spike + regime, NO confidence gate.
    mom = _gate_args(RunStrategySkill.PRESETS["momentum hunter"])
    assert "--volume-spike-min" in mom and "3.0" in mom
    assert "--confidence-threshold" not in mom
    # Full scan: no gates at all.
    assert _gate_args(RunStrategySkill.PRESETS["full scan"]) == []


def test_lab_run_request_accepts_preset_gates():
    from bot.api.lab import LabRunRequest
    req = LabRunRequest(dataset="majors_1h", symbols=["BTC/USDT:USDT"],
                        volume_spike_min=3.0, regime_filter="TREND_UP", rsi_max=35.0)
    assert req.volume_spike_min == 3.0 and req.regime_filter == "TREND_UP"
    assert req.rsi_max == 35.0
    # Defaults are OFF.
    plain = LabRunRequest(dataset="majors_1h")
    assert plain.volume_spike_min is None and plain.regime_filter == ""
    assert plain.rsi_max is None
