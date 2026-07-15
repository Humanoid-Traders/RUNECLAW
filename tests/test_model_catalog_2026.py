"""
Model catalog currency guard (2026 refresh).

The admin tier (which the operator's live trade analysis uses) was bumped from
Claude Sonnet 4.6 to the current Sonnet 5. This test guards two invariants so a
future catalog bump can't silently break live LLM calls or cost accounting:

  1. Every model referenced by a routing table has a KNOWN cost (else spend goes
     untracked and budgets misfire).
  2. The admin routing uses the current Sonnet id (not a stale one).
"""

from bot.core.cost import resolve_llm_price
from bot.llm.provider import (
    ADMIN_TIER_ROUTING,
    DEFAULT_TIER_ROUTING,
    ELITE_TIER_ROUTING,
    PRO_TIER_ROUTING,
)


def _all_routing_models():
    for table in (DEFAULT_TIER_ROUTING, ADMIN_TIER_ROUTING,
                  ELITE_TIER_ROUTING, PRO_TIER_ROUTING):
        for cfg in table.values():
            yield cfg["model"]


def test_every_routing_model_is_priced():
    # A model with NO resolvable price books $0.00 and disarms the daily dollar
    # budget guard (2026-07-11 incident class). Exact-vs-family doesn't matter —
    # a resolvable (approximate) price is what arms the guard.
    unpriced = []
    for model in _all_routing_models():
        price, _exact = resolve_llm_price(model)
        if not price:
            unpriced.append(model)
    assert not unpriced, f"routing models with no resolvable price (untracked spend): {unpriced}"


def test_admin_tier_uses_current_sonnet():
    for tier, cfg in ADMIN_TIER_ROUTING.items():
        assert cfg["model"] == "claude-sonnet-5", (
            f"admin {tier} should use the current Sonnet, got {cfg['model']}")


def test_no_stale_sonnet_46_in_routing():
    assert "claude-sonnet-4-6" not in set(_all_routing_models())
