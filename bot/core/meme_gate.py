"""Meme-buy safety gate — pure, fail-closed precondition for any memecoin buy.

The agent may BUY a memecoin only when EVERY precondition passes. This composes
the rug/honeypot scanner (``bot.core.token_safety``) with the DEX
liquidity/age/exit-ability read and basic position-sizing sanity into a single
allow/deny decision. It is DETECTION + REFUSAL only — never a positive/buy
signal, never execution, and (by design) never token creation.

Fail-closed: any missing critical input makes its check FAIL, so the gate
denies on incomplete data rather than assuming the best. This is the hard
precondition a future money-path executor must clear before any memecoin buy;
it moves no funds itself.
"""
from __future__ import annotations

from typing import Any, Optional

DEFAULTS: dict[str, Any] = {
    "min_liquidity_usd": 50_000.0,   # thin pools can't be exited without slippage
    "min_age_hours": 24.0,           # brand-new listings are rug-prone
    "max_position_usd": 250.0,       # small by default — these mostly go to zero
    "max_pct_of_liquidity": 1.0,     # a buy must be <=1% of pool depth
    "require_safe_verdict": True,    # 'caution' is NOT enough to buy
    "require_prior_sells": True,     # someone must have exited already
}


def _num(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v
    except (TypeError, ValueError):
        return None


def default_params(**over: Any) -> dict:
    p = dict(DEFAULTS)
    p.update({k: v for k, v in over.items() if v is not None})
    return p


def evaluate_meme_buy(*, safety_report: Optional[dict] = None,
                      radar_risk: Optional[dict] = None,
                      liquidity_usd: Any = None, age_hours: Any = None,
                      sells_24h: Any = None, buys_24h: Any = None,
                      size_usd: Any = None,
                      params: Optional[dict] = None) -> dict:
    """Decide whether a memecoin BUY is permitted. Returns::

        {allowed: bool, reason: str, blocking: [str],
         checks: [{name, ok, detail}], verdict, params}

    ``allowed`` is True only when every check passes (fail-closed)."""
    p = params or default_params()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. Rug/honeypot verdict — the scanner never says "buy", only how unsafe.
    verdict = (safety_report or {}).get("verdict")
    if p["require_safe_verdict"]:
        ok = verdict == "safe"
    else:
        ok = verdict in ("safe", "caution")
    add("token_safety", bool(ok and verdict is not None),
        f"scanner verdict: {verdict}" if verdict else "no scanner report (fail-closed)")

    # 2. Liquidity floor — can the position be exited at all?
    liq = _num(liquidity_usd)
    add("liquidity_floor", liq is not None and liq >= p["min_liquidity_usd"],
        (f"liquidity ${int(liq):,} (min ${int(p['min_liquidity_usd']):,})"
         if liq is not None else "liquidity unknown (fail-closed)"))

    # 3. Age floor — brand-new pools are the rug sweet spot.
    age = _num(age_hours)
    add("age_floor", age is not None and age >= p["min_age_hours"],
        (f"age {age:.1f}h (min {p['min_age_hours']:.0f}h)"
         if age is not None else "age unknown (fail-closed)"))

    # 4. Exit-ability — a token nobody has sold may be a de-facto honeypot.
    if p["require_prior_sells"]:
        s = _num(sells_24h)
        add("can_exit", s is not None and s > 0,
            (f"{int(s)} sells in 24h" if s is not None
             else "sell count unknown (fail-closed)"))

    # 5. Position sizing sanity (only when a size is supplied — sizing is
    #    otherwise a separate concern, but the gate enforces it when given).
    sz = _num(size_usd)
    if sz is not None:
        add("size_cap", sz <= p["max_position_usd"],
            f"size ${sz:,.0f} (max ${p['max_position_usd']:,.0f})")
        if liq is not None and liq > 0:
            pct = sz / liq * 100.0
            add("size_vs_liquidity", pct <= p["max_pct_of_liquidity"],
                f"{pct:.2f}% of pool (max {p['max_pct_of_liquidity']:.1f}%)")

    # 6. Radar composite risk tier — 'extreme' is an outright stand-down.
    tier = (radar_risk or {}).get("tier")
    add("risk_tier", bool(tier is not None and tier != "extreme"),
        f"risk tier: {tier}" if tier else "no risk read (fail-closed)")

    blocking = [c["name"] for c in checks if not c["ok"]]
    allowed = len(blocking) == 0
    return {
        "allowed": allowed,
        "reason": ("all safety preconditions passed" if allowed
                   else "blocked by: " + ", ".join(blocking)),
        "checks": checks,
        "blocking": blocking,
        "verdict": verdict,
        "params": p,
    }


def human_readable(decision: Optional[dict]) -> str:
    if not isinstance(decision, dict):
        return "No decision."
    head = "✅ BUY permitted" if decision.get("allowed") else "⛔ BUY blocked"
    lines = [head + " — " + str(decision.get("reason", ""))]
    for c in decision.get("checks", []):
        mark = "✓" if c.get("ok") else "✗"
        lines.append(f"  {mark} {c.get('name')}: {c.get('detail')}")
    return "\n".join(lines)
