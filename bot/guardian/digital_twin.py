"""Portfolio Digital Twin — the foresight layer of Guardian.

    The AI proposes. Deterministic controls authorize. The twin foresees.

The Flight Recorder proves what *happened*; the Intent Compiler bounds what the
agent is *allowed* to do; the Firewall guards what comes *in*. The Digital Twin
answers the remaining question — **what would happen to the current book if the
market moved against it** — before the market gets the chance to.

``run(positions, equity, scenarios)`` is a **pure, deterministic** stress
simulator (no engine, no exchange, no clock, no network). It takes a snapshot of
the open positions and an account equity, applies a catalogue of parametric price
shocks (flash crash, correlated tail, alt capitulation, short squeeze), and for
each one computes the projected P&L, the projected account drawdown, and exactly
which positions would be **liquidated** — using isolated-margin liquidation math
derived from each position's entry and leverage.

Design stance (matches the rest of Guardian):

* **Pure + deterministic.** Every function is a pure function of its inputs, so
  the whole twin is trivially unit-testable and can never touch the trade path.
  It shocks from each position's *entry* price (not a live mark), so the result
  is reproducible and reflects the shock's impact on committed margin — it is a
  *risk* estimate, not a live-mark valuation.
* **Read-only foresight.** The twin never proposes, blocks, or alters a trade.
  It only *describes* the book's fragility. Recording the result to the
  tamper-evident chain is the caller's gated, telemetry-first choice.
* **Honest math, stated assumptions.** Liquidation is modelled as isolated-margin
  wipeout (adverse move ≈ ``(1 - maintenance) / leverage`` from entry). Cross,
  hedged, or portfolio-margin books liquidate later than this estimate, so the
  twin is deliberately *conservative* (it flags fragility early, never late).
"""

from __future__ import annotations

from typing import Any, Optional

TWIN_VERSION = 1

# Risk ordering for rollups (mirrors the firewall's convention).
RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# Default Bitget-ish USDT-M maintenance-margin rate. Liquidation happens slightly
# before full margin wipeout; a small conservative default flags fragility early.
DEFAULT_MAINTENANCE = 0.005

# Drawdown thresholds (percent of equity lost in a scenario) → severity.
_DD_HIGH = 25.0
_DD_MEDIUM = 10.0

# ── Built-in scenario catalogue ───────────────────────────────────────
# A scenario is a set of fractional price shocks keyed by correlation group, with
# "*" as the default for any unlisted group. Negative = price down. Longs and
# shorts revalue oppositely on their own, so one price move models both sides.
_SCENARIOS: list[dict] = [
    {"name": "flash_crash",      "label": "Flash crash · −20% across the board",
     "shocks": {"*": -0.20}},
    {"name": "severe_crash",     "label": "Severe correlated crash · −35% tail",
     "shocks": {"*": -0.35}},
    {"name": "alt_capitulation", "label": "Alt capitulation · majors mild, alts −35%",
     "shocks": {"BTC": -0.10, "ETH": -0.12, "*": -0.35}},
    {"name": "short_squeeze",    "label": "Short squeeze · +20% across the board",
     "shocks": {"*": 0.20}},
]


def scenarios() -> list[dict]:
    """The built-in stress scenarios (copies, so callers can't mutate the catalogue)."""
    return [dict(s, shocks=dict(s["shocks"])) for s in _SCENARIOS]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf guard
        return None
    return f


def liquidation_move_frac(leverage: Any, maintenance: float = DEFAULT_MAINTENANCE) -> Optional[float]:
    """The adverse fractional price move (from entry) that liquidates an
    isolated-margin position: ``(1 - maintenance) / leverage``. Returns None when
    leverage is unusable (≤ 0). A 10× position with 0.5% maintenance liquidates
    on a ~9.95% adverse move."""
    lev = _num(leverage)
    if lev is None or lev <= 0:
        return None
    return max(0.0, (1.0 - maintenance) / lev)


def liquidation_price(entry: Any, leverage: Any, direction: str,
                      maintenance: float = DEFAULT_MAINTENANCE) -> Optional[float]:
    """Isolated-margin liquidation price. LONG liquidates below entry, SHORT
    above. Returns None when inputs are unusable — a caller treats None as
    'can't estimate', never as 'safe'."""
    e = _num(entry)
    frac = liquidation_move_frac(leverage, maintenance)
    if e is None or e <= 0 or frac is None:
        return None
    if str(direction).upper().startswith("S"):   # SHORT
        return e * (1.0 + frac)
    return e * (1.0 - frac)                       # LONG


def position_pnl(entry: Any, qty: Any, direction: str, price: Any) -> Optional[float]:
    """Unrealised P&L in USD at ``price``: ``(price - entry) * qty`` for a LONG,
    mirrored for a SHORT. Pure; returns None when inputs are unusable."""
    e, q, p = _num(entry), _num(qty), _num(price)
    if e is None or q is None or p is None:
        return None
    if str(direction).upper().startswith("S"):   # SHORT
        return (e - p) * q
    return (p - e) * q                            # LONG


def _shock_for(shocks: dict, group: str) -> float:
    v = _num(shocks.get(group, shocks.get("*", 0.0)))
    return v if v is not None else 0.0


def _scenario_risk(drawdown_pct: float, liquidations: int) -> str:
    if liquidations > 0 or drawdown_pct >= _DD_HIGH:
        return "high"
    if drawdown_pct >= _DD_MEDIUM:
        return "medium"
    if drawdown_pct > 0:
        return "low"
    return "none"


def simulate_scenario(positions: list[dict], equity: Any, scenario: dict,
                      maintenance: float = DEFAULT_MAINTENANCE) -> dict:
    """Apply one scenario's price shocks to the book. Pure; never raises.

    Returns a dict with the scenario name/label, the projected book P&L, the
    projected equity and drawdown %, the list of positions that would be
    liquidated, and a per-position breakdown. A position missing usable inputs is
    skipped (fail-open) rather than aborting the whole simulation."""
    eq = _num(equity) or 0.0
    shocks = scenario.get("shocks", {})
    total_pnl = 0.0
    liquidations: list[str] = []
    rows: list[dict] = []
    for pos in positions or []:
        entry = _num(pos.get("entry"))
        qty = _num(pos.get("qty"))
        if entry is None or entry <= 0 or qty is None:
            continue
        direction = str(pos.get("direction", "LONG")).upper()
        group = str(pos.get("group") or "*")
        shock = _shock_for(shocks, group)
        shocked_price = entry * (1.0 + shock)
        pnl = position_pnl(entry, qty, direction, shocked_price) or 0.0
        liq_price = liquidation_price(entry, pos.get("leverage"), direction, maintenance)
        # Isolated-margin liquidation: shocked price at/through the liq price.
        liquidated = False
        if liq_price is not None:
            liquidated = (shocked_price <= liq_price) if not direction.startswith("S") \
                else (shocked_price >= liq_price)
        symbol = str(pos.get("symbol", "?"))
        if liquidated:
            liquidations.append(symbol)
        total_pnl += pnl
        rows.append({
            "symbol": symbol, "direction": direction, "group": group,
            "shock_pct": round(shock * 100, 2),
            "pnl_usd": round(pnl, 2), "liquidated": liquidated,
        })
    projected_equity = eq + total_pnl
    drawdown_pct = round(max(0.0, -total_pnl) / eq * 100, 2) if eq > 0 else 0.0
    return {
        "name": scenario.get("name", "scenario"),
        "label": scenario.get("label", ""),
        "projected_pnl_usd": round(total_pnl, 2),
        "projected_equity_usd": round(projected_equity, 2),
        "drawdown_pct": drawdown_pct,
        "liquidations": liquidations,
        "liquidation_count": len(liquidations),
        "risk": _scenario_risk(drawdown_pct, len(liquidations)),
        "positions": rows,
    }


def run(positions: list[dict], equity: Any, scenario_list: Optional[list[dict]] = None,
        maintenance: float = DEFAULT_MAINTENANCE) -> dict:
    """Stress-test the whole book across every scenario. Pure; never raises.

    Returns::

        {
          "version": int,
          "position_count": int,
          "equity_usd": float,
          "scenarios": [ <simulate_scenario dict>, ... ],
          "worst": <the scenario dict with the deepest drawdown / most liquidations>,
          "risk": "none" | "low" | "medium" | "high",   # the worst scenario's risk
          "fragile": [ {"symbol","liq_move_pct"}, ... ],  # positions nearest liquidation
        }
    """
    try:
        eq = _num(equity) or 0.0
        scen = scenario_list if scenario_list is not None else scenarios()
        results = [simulate_scenario(positions, eq, s, maintenance) for s in scen]
        # Per-position fragility, independent of any scenario: how far (adverse
        # %) each position sits from its own liquidation. Smaller = more fragile.
        fragile: list[dict] = []
        for pos in positions or []:
            frac = liquidation_move_frac(pos.get("leverage"), maintenance)
            if frac is not None:
                fragile.append({"symbol": str(pos.get("symbol", "?")),
                                "liq_move_pct": round(frac * 100, 2)})
        fragile.sort(key=lambda r: float(r["liq_move_pct"]))
        worst = None
        if results:
            worst = max(results, key=lambda r: (r["liquidation_count"], r["drawdown_pct"]))
        return {
            "version": TWIN_VERSION,
            "position_count": len([p for p in (positions or [])
                                   if _num(p.get("entry")) and _num(p.get("qty")) is not None]),
            "equity_usd": round(eq, 2),
            "scenarios": results,
            "worst": worst,
            "risk": worst["risk"] if worst else "none",
            "fragile": fragile[:8],
        }
    except Exception:
        # Foresight must never break a caller — degrade to an empty report.
        return {"version": TWIN_VERSION, "position_count": 0, "equity_usd": 0.0,
                "scenarios": [], "worst": None, "risk": "none", "fragile": []}


def twin_payload(positions: list[dict], equity: Any,
                 scenario_list: Optional[list[dict]] = None,
                 maintenance: float = DEFAULT_MAINTENANCE) -> dict:
    """A compact, JSON-serialisable record for the Flight Recorder / telemetry:
    the worst-case scenario, the overall risk, a per-scenario summary, and the
    most fragile positions — never the full per-position breakdown."""
    report = run(positions, equity, scenario_list, maintenance)
    worst = report.get("worst") or {}
    return {
        "version": TWIN_VERSION,
        "risk": report["risk"],
        "position_count": report["position_count"],
        "equity_usd": report["equity_usd"],
        "worst_scenario": worst.get("name", ""),
        "worst_drawdown_pct": worst.get("drawdown_pct", 0.0),
        "worst_liquidations": worst.get("liquidations", [])[:12],
        "scenarios": [
            {"name": s["name"], "drawdown_pct": s["drawdown_pct"],
             "liquidation_count": s["liquidation_count"], "risk": s["risk"]}
            for s in report["scenarios"]
        ],
        "fragile": report["fragile"][:6],
    }
