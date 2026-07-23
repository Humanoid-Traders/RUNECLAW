"""Strategy-Agent marketplace catalogue (read-only, PUBLIC-safe).

A browsable catalogue of the engine's named strategy agents. Every agent here
is one of the REAL engine presets (``RunStrategySkill.PRESETS``) — so the
"how it trades" line is DERIVED from the live config and can never drift from
what the agent actually does. Editorial fields (tagline, regime fit, risk tag,
horizon) sit alongside.

§4 compliance: this is public-safe. It carries strategy DESIGN + regime +
qualitative risk only — never a dollar amount and never a fabricated return.
Verified performance is shown as percent/ratio elsewhere (the honest Strategy
Lab backtester + the verifiable leaderboard), never invented here.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

# Committed, reproducible per-agent benchmark scorecards (generated offline by
# scripts/gen_agent_scorecards.py). Percent/ratio only, stamped with the dataset
# hash — see that script. Loaded fail-soft so a missing scorecard just omits the
# stats from the card, never breaks the catalogue.
_SCORECARD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "benchmark", "scorecards")

# Editorial, marketplace-facing metadata keyed by the real preset id. Kept
# deliberately small — the substance (how it trades) is derived, not authored.
_META: dict[str, dict[str, str]] = {
    "dip sniper": {
        "tagline": "Buys capitulation — oversold dips inside a downtrend, only "
                   "when conviction is high.",
        "regime": "Downtrends / mean-reversion",
        "risk": "balanced",
        "horizon": "swing",
    },
    "momentum hunter": {
        "tagline": "Rides strength — jumps on volume-backed breakouts while the "
                   "trend is up.",
        "regime": "Uptrends / momentum",
        "risk": "aggressive",
        "horizon": "intraday",
    },
    "safe scalper": {
        "tagline": "Small, tight, frequent — the most liquid pairs with a hard "
                   "1.5-ATR stop and a high conviction bar.",
        "regime": "Any / liquidity-led",
        "risk": "tight",
        "horizon": "scalp",
    },
    "full scan": {
        "tagline": "The house strategy — the full 21-check pipeline with every "
                   "proven default on.",
        "regime": "All regimes",
        "risk": "balanced",
        "horizon": "adaptive",
    },
}

_RISK_LABEL = {
    "tight": "🟢 Tight risk",
    "balanced": "🟡 Balanced",
    "aggressive": "🟠 Aggressive",
}


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-")


def _how_it_trades(cfg: dict[str, Any]) -> str:
    """Human 'how it trades' line derived from the preset's real config, so it
    stays honest to actual behaviour. No numbers are invented — only the
    thresholds the engine actually applies are surfaced."""
    parts: list[str] = []
    sym = cfg.get("symbols")
    if sym == "top3_volume":
        parts.append("the 3 most-liquid pairs")
    elif sym:
        parts.append(str(sym))
    else:
        parts.append("all scanned pairs")
    regime = cfg.get("regime")
    if regime:
        parts.append(f"only in {str(regime).replace('_', ' ').lower()}")
    rsi = cfg.get("rsi_threshold")
    if rsi is not None:
        parts.append(f"RSI below {rsi}")
    vspike = cfg.get("volume_spike_min")
    if vspike is not None:
        parts.append(f"a volume spike over {vspike:g}×")
    conf = cfg.get("confidence_threshold")
    if conf is not None:
        parts.append(f"engine confidence ≥ {round(conf * 100)}%")
    sl = cfg.get("sl_atr_mult")
    tp = cfg.get("tp_atr_mult")
    if sl is not None or tp is not None:
        bits = []
        if sl is not None:
            bits.append(f"{sl:g}-ATR stop")
        if tp is not None:
            bits.append(f"{tp:g}-ATR target")
        parts.append(" / ".join(bits))
    return "Trades " + ", ".join(parts) + "."


def _load_scorecard(agent_id: str) -> Optional[dict]:
    """The committed benchmark scorecard for this agent slug, or None. Public-safe
    by construction (the generator writes percent/ratio only); we still strip any
    non-metric/dollar-ish keys defensively before it reaches a card."""
    try:
        path = os.path.join(_SCORECARD_DIR, f"{agent_id}.json")
        with open(path, encoding="utf-8") as fh:
            card = json.load(fh)
    except Exception:
        return None
    if not isinstance(card, dict):
        return None
    metrics = card.get("metrics") or {}
    return {
        "dataset": card.get("dataset", ""),
        "dataset_hash": (card.get("dataset_hash", "") or "")[:12],
        "symbols": card.get("symbols", []),
        "bars": card.get("bars"),
        "gates": card.get("gates", {}),
        "unmodeled": card.get("unmodeled", []),
        "metrics": {
            "total_return_pct": metrics.get("total_return_pct"),
            "profit_factor": metrics.get("profit_factor"),
            "win_rate": metrics.get("win_rate"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "total_trades": metrics.get("total_trades"),
        },
    }


def _run_alias(key: str) -> str:
    """The chat/Telegram shortcut for this agent (e.g. 'dip' -> dip sniper)."""
    from bot.skills.skill_registry import RunStrategySkill
    for alias, target in RunStrategySkill.ALIASES.items():
        if target == key:
            return alias
    return key


def catalog() -> list[dict]:
    """The marketplace catalogue — one card per real engine strategy agent.
    Public-safe: design, regime, and qualitative risk only. Fail-soft: returns
    [] if the preset source can't be read."""
    try:
        from bot.skills.skill_registry import RunStrategySkill
        presets = RunStrategySkill.PRESETS
    except Exception:
        return []
    out: list[dict] = []
    for key, cfg in presets.items():
        meta = _META.get(key, {})
        risk = meta.get("risk", "balanced")
        aid = _slug(key)
        out.append({
            "id": aid,
            "name": cfg.get("label", key.title()),
            "icon": cfg.get("icon", "🤖"),
            "tagline": meta.get("tagline", ""),
            "how": _how_it_trades(cfg),
            "regime": meta.get("regime", ""),
            "risk": risk,
            "risk_label": _RISK_LABEL.get(risk, "🟡 Balanced"),
            "horizon": meta.get("horizon", ""),
            "run": _run_alias(key),
            # Reproducible frozen-benchmark scorecard (percent/ratio only), or
            # None if not yet generated. Lets the marketplace card show verified
            # numbers with a one-tap "reproduce in the Lab".
            "scorecard": _load_scorecard(aid),
        })
    return out


def get_agent(agent_id: str) -> Optional[dict]:
    """One agent card by slug id, or None."""
    aid = _slug(agent_id or "")
    for a in catalog():
        if a["id"] == aid:
            return a
    return None
