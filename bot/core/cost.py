"""
RUNECLAW Cost Tracker -- session operating-cost ledger.

Separates operating costs (LLM tokens, infra) from trade PnL by design.
Trading costs (commission, slippage) are attributed per-trade in the portfolio.
Operating costs are tracked at the session level and netted in the waterfall:

    Gross trading PnL
      - exchange commission
      - slippage
    = Trading net PnL
      - LLM token cost
      - infra/hosting cost
    = Strategy net PnL after agent costs

Fail-closed cost accounting: an unknown model is not assumed free.
Tokens are recorded and flagged as UNPRICED so the operator knows cost is
unknown, not zero.

Per-category breakdown: scan / analyze / thesis / risk_decision / other.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

from bot.utils.logger import audit, system_log


# USD per 1,000,000 tokens.  ILLUSTRATIVE — verify against current provider
# pricing, which changes and differs by model.  Do not trust these numbers.
# Keep in config/env for production; hardcoded here for prototype convenience.
LLM_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":              {"in": 2.50,  "out": 10.00},
    "gpt-4o-mini":         {"in": 0.15,  "out": 0.60},
    "claude-sonnet-5":   {"in": 3.00,  "out": 15.00},
    "claude-opus-4-8":     {"in": 15.00, "out": 75.00},
    "claude-haiku-4-5-20251001":    {"in": 0.80,  "out": 4.00},
    "claude-3-5-sonnet-20241022": {"in": 3.00, "out": 15.00},
    "claude-sonnet-4-20250514":   {"in": 3.00, "out": 15.00},
}

# Family-prefix fallback (checked in order, first prefix match wins) for model
# IDs not in the exact table above. The operator can point any tier at any
# model via env; the exact table will always lag behind dated IDs like
# "claude-sonnet-4-20250514". An unmatched PAID model previously booked $0.00
# (UNPRICED), which silently DISARMED the daily dollar-budget guard — with a
# paid key on every tier that meant unbounded spend up to the call limit
# (live incident 2026-07-11: all 4 tiers set to claude-sonnet-4-20250514,
# not in the table). Family pricing is an approximation — Anthropic has kept
# per-family pricing stable across versions — and an approximate cost that
# arms the budget guard beats an exact $0 that doesn't.
LLM_PRICING_FAMILIES: list[tuple[str, dict[str, float]]] = [
    ("claude-opus-",   {"in": 15.00, "out": 75.00}),
    ("claude-sonnet-", {"in": 3.00,  "out": 15.00}),
    ("claude-haiku-",  {"in": 0.80,  "out": 4.00}),
    ("claude-3-",      {"in": 3.00,  "out": 15.00}),
    ("gpt-4o-mini",    {"in": 0.15,  "out": 0.60}),
    ("gpt-4",          {"in": 2.50,  "out": 10.00}),
]


def resolve_llm_price(model: str) -> tuple[Optional[dict], bool]:
    """Price for a model id: (price, exact). Exact table first, then the
    family-prefix fallback; (None, False) when nothing matches."""
    price = LLM_PRICING.get(model)
    if price is not None:
        return price, True
    m = (model or "").lower()
    for prefix, fam_price in LLM_PRICING_FAMILIES:
        if m.startswith(prefix):
            return fam_price, False
    return None, False

# Categories for per-bucket cost tracking
COST_CATEGORIES = ("scan", "analyze", "thesis", "risk_decision", "other")


def _default_category_costs() -> dict[str, float]:
    return {cat: 0.0 for cat in COST_CATEGORIES}


def _default_category_calls() -> dict[str, int]:
    return {cat: 0 for cat in COST_CATEGORIES}


@dataclass
class CostSummary:
    """Point-in-time snapshot of session operating costs."""
    llm_cost_usd: float = 0.0
    infra_cost_usd: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    unpriced_calls: int = 0  # model not in price table — cost is UNKNOWN, not zero
    # Per-category breakdown
    cost_by_category: dict[str, float] = field(default_factory=_default_category_costs)
    calls_by_category: dict[str, int] = field(default_factory=_default_category_calls)

    @property
    def operating_cost_usd(self) -> float:
        return round(self.llm_cost_usd + self.infra_cost_usd, 6)

    @property
    def avg_cost_per_call(self) -> float:
        return round(self.llm_cost_usd / self.llm_calls, 6) if self.llm_calls > 0 else 0.0


class CostTracker:
    """Session operating-cost ledger.  Separate from trade PnL by design.

    Threading model: same single-threaded asyncio assumption as RiskEngine.
    RLock is defensive only.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._s = CostSummary()
        self._lifetime = CostSummary()  # W1 FIX: separate lifetime vs daily
        self._current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _maybe_reset_daily(self) -> None:
        """W1 FIX: Reset daily counters at UTC day boundary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_day:
            # Accumulate into lifetime before resetting daily
            self._lifetime.llm_cost_usd += self._s.llm_cost_usd
            self._lifetime.infra_cost_usd += self._s.infra_cost_usd
            self._lifetime.llm_calls += self._s.llm_calls
            self._lifetime.prompt_tokens += self._s.prompt_tokens
            self._lifetime.completion_tokens += self._s.completion_tokens
            self._lifetime.unpriced_calls += self._s.unpriced_calls
            # Reset daily
            self._s = CostSummary()
            self._current_day = today

    def record_llm(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        symbol: str = "",
        category: str = "other",
    ) -> float:
        """Record an LLM API call.  Returns USD cost (0.0 if model is unpriced).

        Fail-closed accounting: unknown model -> tokens recorded, cost = 0,
        but unpriced_calls is incremented so the operator knows the true cost
        is unknown, not zero.

        category: one of scan / analyze / thesis / risk_decision / other.
        """
        price, _exact = resolve_llm_price(model)
        priced = price is not None
        cost = (
            (prompt_tokens / 1_000_000) * price["in"]
            + (completion_tokens / 1_000_000) * price["out"]
        ) if priced else 0.0

        cat = category if category in COST_CATEGORIES else "other"

        with self._lock:
            self._maybe_reset_daily()
            self._s.llm_cost_usd += cost
            self._s.llm_calls += 1
            self._s.prompt_tokens += prompt_tokens
            self._s.completion_tokens += completion_tokens
            self._s.cost_by_category[cat] = self._s.cost_by_category.get(cat, 0.0) + cost
            self._s.calls_by_category[cat] = self._s.calls_by_category.get(cat, 0) + 1
            if not priced:
                self._s.unpriced_calls += 1

        audit(
            system_log,
            f"LLM cost {model}: ${cost:.6f} [{cat}]",
            action="cost_llm",
            result="PRICED" if priced else "UNPRICED",
            data={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost, 6),
                "symbol": symbol,
                "priced": priced,
                "category": cat,
            },
        )
        return cost

    def record_infra(self, cost_usd: float, note: str = "") -> None:
        """Record an infrastructure cost (hosting, data feeds, etc.)."""
        with self._lock:
            self._s.infra_cost_usd += cost_usd

    def snapshot(self) -> CostSummary:
        """Return a frozen copy of current daily cost state."""
        with self._lock:
            self._maybe_reset_daily()
            # Deep-copy the mutable dicts
            s = replace(self._s)
            s.cost_by_category = dict(self._s.cost_by_category)
            s.calls_by_category = dict(self._s.calls_by_category)
            return s

    def snapshot_lifetime(self) -> CostSummary:
        """Return lifetime costs (all days combined)."""
        with self._lock:
            self._maybe_reset_daily()
            lt = replace(self._lifetime)
            # Add current day's costs
            lt.llm_cost_usd += self._s.llm_cost_usd
            lt.infra_cost_usd += self._s.infra_cost_usd
            lt.llm_calls += self._s.llm_calls
            lt.prompt_tokens += self._s.prompt_tokens
            lt.completion_tokens += self._s.completion_tokens
            lt.unpriced_calls += self._s.unpriced_calls
            return lt
