"""Idle-Asset Yield Optimizer — cross-venue, cross-source best-rate matching.

    Idle capital earning $0 that could be earning. Scan every connected wallet
    and account, match each idle asset to the best available rate — RECOMMEND,
    never auto-deploy.

The cross-source brain over the single-venue Yield Radar. Pure and deterministic:
the caller aggregates idle holdings (from the unified cross-venue portfolio) and
yield options (Bitget Earn catalog, Lido/Aave/other-CEX feeders); this module
matches each idle asset to its best available rate.

Discipline (non-custodial + honest, matching the rest of the platform):
* RECOMMENDATION-ONLY — never moves funds; the action path stays confirm-gated
  ``/stake``.
* Custodial vs non-custodial is ALWAYS surfaced; ``prefer_noncustodial`` lets a
  marginally-lower non-custodial rate win, and the tradeoff is stated — a higher
  custodial APY is never silently preferred.
* NO fabricated rates — an asset with no known option is ``no_option``, never a
  made-up APY.
"""

from __future__ import annotations

from typing import Any, Optional

CEX_EARN = "cex_earn"
STAKING = "staking"
DEFI_LENDING = "defi_lending"
_NONCUSTODIAL_KINDS = (STAKING, DEFI_LENDING)

# Default APY margin (percentage points) within which a non-custodial option is
# preferred over a higher custodial one when prefer_noncustodial is set.
DEFAULT_NONCUSTODIAL_MARGIN = 1.0


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _norm_asset(a: Any) -> str:
    return str(a or "").upper().strip()


def _norm_option(o: dict) -> Optional[dict]:
    """Normalise a raw yield option; drop it if it has no usable APY/asset."""
    asset = _norm_asset(o.get("asset"))
    apy = _f(o.get("apy"))
    if not asset or apy is None or apy <= 0:
        return None
    kind = str(o.get("kind") or "").lower().strip() or CEX_EARN
    # custodial defaults from kind when not explicitly given: CEX Earn custodial,
    # on-chain staking/DeFi non-custodial.
    custodial = o.get("custodial")
    if custodial is None:
        custodial = kind == CEX_EARN
    lockup = _f(o.get("lockup_days"))
    return {
        "asset": asset,
        "source": str(o.get("source") or ""),
        "kind": kind,
        "apy": round(apy, 4),
        "lockup_days": (int(lockup) if lockup is not None and lockup >= 0 else 0),
        "custodial": bool(custodial),
        "risk_tier": str(o.get("risk_tier") or ("low" if not custodial else "medium")),
    }


def _rank_key(opt: dict, prefer_noncustodial: bool, margin: float) -> tuple:
    """Sort key so higher APY wins, but under ``prefer_noncustodial`` a
    non-custodial option gets an effective APY bump of ``margin`` for ranking —
    letting it beat a marginally-higher custodial one. Ties break toward
    non-custodial, then lower lockup."""
    eff = opt["apy"] + (margin if (prefer_noncustodial and not opt["custodial"]) else 0.0)
    return (eff, (0 if opt["custodial"] else 1), -opt["lockup_days"])


def optimize(holdings: list[dict], options: list[dict], *,
             min_usd: float = 10.0,
             max_lockup_days: Optional[int] = None,
             prefer_noncustodial: bool = True,
             noncustodial_margin: float = DEFAULT_NONCUSTODIAL_MARGIN) -> dict:
    """Match idle holdings to the best available yield option per asset.

    ``holdings``: ``[{asset, free_amount, usd_value, location}]`` (idle balances).
    ``options``:  ``[{asset, source, kind, apy, lockup_days, custodial, risk_tier}]``.

    Returns a report (see docs/idle_yield.md). Recommendation-only.
    """
    # Index normalized options by asset.
    by_asset: dict[str, list[dict]] = {}
    for raw in (options or []):
        opt = _norm_option(raw)
        if opt is None:
            continue
        if max_lockup_days is not None and opt["lockup_days"] > max_lockup_days:
            continue
        by_asset.setdefault(opt["asset"], []).append(opt)

    recs: list[dict] = []
    unmatched: list[str] = []
    total_idle = 0.0
    total_deployable = 0.0
    total_year = 0.0

    for h in (holdings or []):
        asset = _norm_asset(h.get("asset"))
        idle_usd = _f(h.get("usd_value")) or 0.0
        if not asset or idle_usd <= 0:
            continue
        total_idle += idle_usd

        if idle_usd < min_usd:
            recs.append({"asset": asset, "idle_usd": round(idle_usd, 2),
                         "status": "below_min", "best": None, "alternatives": [],
                         "est_year_usd": 0.0,
                         "location": str(h.get("location") or "")})
            continue

        opts = sorted(by_asset.get(asset, []),
                      key=lambda o: _rank_key(o, prefer_noncustodial, noncustodial_margin),
                      reverse=True)
        if not opts:
            unmatched.append(asset)
            recs.append({"asset": asset, "idle_usd": round(idle_usd, 2),
                         "status": "no_option", "best": None, "alternatives": [],
                         "est_year_usd": 0.0,
                         "location": str(h.get("location") or "")})
            continue

        best = opts[0]
        est_year = idle_usd * best["apy"] / 100.0
        total_deployable += idle_usd
        total_year += est_year
        note = ""
        # State the tradeoff when a non-custodial pick beat a higher custodial rate.
        higher_cust = next((o for o in opts[1:]
                            if o["custodial"] and o["apy"] > best["apy"]), None)
        if not best["custodial"] and higher_cust is not None:
            note = (f"non-custodial pick at {best['apy']:g}% chosen over a custodial "
                    f"{higher_cust['apy']:g}% ({higher_cust['source']}) — you keep custody")
        recs.append({
            "asset": asset,
            "idle_usd": round(idle_usd, 2),
            "status": "recommended",
            "best": best,
            "alternatives": opts[1:4],
            "est_year_usd": round(est_year, 2),
            "note": note,
            "location": str(h.get("location") or ""),
        })

    # Rank recommendations by incremental income (most $/yr first).
    recs.sort(key=lambda r: r.get("est_year_usd", 0.0), reverse=True)
    return {
        "recommendations": recs,
        "unmatched": sorted(set(unmatched)),
        "total_idle_usd": round(total_idle, 2),
        "total_deployable_usd": round(total_deployable, 2),
        "total_est_year_usd": round(total_year, 2),
    }


def options_from_savings_catalog(catalog: Optional[dict], *,
                                 source: str = "Bitget Earn") -> list[dict]:
    """Adapter: turn the existing Yield Radar savings catalog
    (``{coin: {"flexible": apy, "fixed": apy, "flexible_id": id}}`` from
    ``bot.core.yield_radar.fetch_savings_catalog``) into optimizer options — so the
    optimizer composes with the data that already flows, no re-fetch. CEX Earn is
    custodial; the flexible tier is lockup-free, the fixed tier carries a nominal
    lockup so ``max_lockup_days=0`` correctly prefers flexible."""
    out: list[dict] = []
    for coin, tiers in (catalog or {}).items():
        asset = _norm_asset(coin)
        flex = _f((tiers or {}).get("flexible"))
        fixed = _f((tiers or {}).get("fixed"))
        if flex is not None and flex > 0:
            out.append({"asset": asset, "source": source, "kind": CEX_EARN,
                        "apy": flex, "lockup_days": 0, "custodial": True})
        if fixed is not None and fixed > 0:
            out.append({"asset": asset, "source": f"{source} (fixed)", "kind": CEX_EARN,
                        "apy": fixed, "lockup_days": 30, "custodial": True})
    return out


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render of an idle-yield report (no markup)."""
    if not report or not isinstance(report, dict):
        return "No idle-yield report."
    lines = [
        f"IDLE YIELD: ${report.get('total_deployable_usd', 0):,.2f} deployable of "
        f"${report.get('total_idle_usd', 0):,.2f} idle → "
        f"~${report.get('total_est_year_usd', 0):,.2f}/yr if deployed",
        "(recommendation only — nothing is moved without your explicit confirm)",
    ]
    for r in report.get("recommendations", []):
        if r["status"] != "recommended":
            continue
        b = r["best"]
        cust = "custodial" if b["custodial"] else "non-custodial"
        lk = f", {b['lockup_days']}d lock" if b["lockup_days"] else ", flexible"
        lines.append(
            f"• {r['asset']}: ${r['idle_usd']:,.2f} → {b['apy']:g}% "
            f"({b['source']}, {cust}{lk}) ≈ ${r['est_year_usd']:,.2f}/yr")
        if r.get("note"):
            lines.append(f"    ↳ {r['note']}")
    no_opt = [r["asset"] for r in report.get("recommendations", [])
              if r["status"] == "no_option"]
    if no_opt:
        lines.append(f"No known rate for: {', '.join(no_opt)} (not fabricated)")
    return "\n".join(lines)
