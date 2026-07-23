"""MARKETPLACE Phase 1 — the Strategy-Agent catalogue.

Every card in the catalogue is a REAL engine preset (never a fabricated agent),
its "how it trades" line is DERIVED from the live config (so it can't drift from
what the agent actually does), and the whole surface is §4-safe: strategy design
+ regime + qualitative risk only — never a dollar figure, never a claimed return.
The public gateway route serves it with no auth.
"""
import inspect

from bot.core import strategy_catalog as sc
from bot.skills.skill_registry import RunStrategySkill


def test_catalog_is_exactly_the_real_presets():
    cards = sc.catalog()
    assert cards, "catalogue must not be empty"
    # One card per real preset — no invented agents, none dropped.
    card_names = {c["name"] for c in cards}
    preset_labels = {cfg.get("label", key.title())
                     for key, cfg in RunStrategySkill.PRESETS.items()}
    assert card_names == preset_labels
    assert len(cards) == len(RunStrategySkill.PRESETS)


def test_every_card_has_the_marketplace_shape():
    for c in sc.catalog():
        for field in ("id", "name", "icon", "tagline", "how", "regime",
                      "risk", "risk_label", "horizon", "run"):
            assert field in c, f"card missing {field}: {c}"
        assert c["id"] and c["how"].startswith("Trades ")
        # The run alias is a real chat/Telegram shortcut for this agent.
        assert c["run"]


def test_catalog_is_section4_safe_no_dollar_amounts():
    import re
    blob = repr(sc.catalog())
    assert "$" not in blob, "no dollar sign may appear in the public catalogue"
    # No claimed-return phrasing either — the catalogue describes design, not P&L.
    lowered = blob.lower()
    assert not re.search(r"\d[\d,.]*\s*%\s*(return|profit|gain)", lowered)
    for leaky in ("net_pnl", "return_pct", "usd"):
        assert leaky not in lowered


def test_how_it_trades_is_derived_from_real_config():
    # Dip Sniper's line must reflect the ACTUAL preset thresholds, not editorial.
    dip = sc.get_agent("dip-sniper")
    assert dip is not None
    cfg = RunStrategySkill.PRESETS["dip sniper"]
    how = dip["how"]
    if cfg.get("rsi_threshold") is not None:
        assert f"RSI below {cfg['rsi_threshold']}" in how
    if cfg.get("confidence_threshold") is not None:
        assert f"{round(cfg['confidence_threshold'] * 100)}%" in how
    # Safe Scalper trades the most-liquid pairs (top3_volume) — derived phrasing.
    scalp = sc.get_agent("safe-scalper")
    assert scalp is not None
    if RunStrategySkill.PRESETS["safe scalper"].get("symbols") == "top3_volume":
        assert "most-liquid" in scalp["how"]


def test_get_agent_slug_and_miss():
    assert sc.get_agent("dip-sniper")["name"]
    assert sc.get_agent("Dip Sniper")["id"] == "dip-sniper"  # slug-normalised
    assert sc.get_agent("does-not-exist") is None
    assert sc.get_agent("") is None


def test_catalog_fail_soft_on_bad_source(monkeypatch):
    # If the preset source can't be read, the catalogue is empty, never a crash.
    import bot.skills.skill_registry as reg
    monkeypatch.delattr(reg.RunStrategySkill, "PRESETS", raising=False)
    assert sc.catalog() == []


def test_public_gateway_route_is_registered_no_auth():
    src = inspect.getsource(__import__("bot.web.user_gateway", fromlist=["x"]))
    assert 'add_get("/public/strategies", handle_strategies_public)' in src
    h = inspect.getsource(
        __import__("bot.web.user_gateway", fromlist=["x"]).handle_strategies_public)
    # Public by construction — no per-user guard, and it serves the catalogue.
    assert "_guard_user" not in h
    assert "strategy_catalog.catalog()" in h
