"""Non-custodial yield feeders for the Idle-Asset Yield Optimizer.

The optimizer (``bot.core.idle_yield``) is pure — it matches idle holdings to
whatever yield *options* the caller hands it. This module is the caller's
supplier for the NON-CUSTODIAL side: real on-chain staking and DeFi-lending
rates (Lido, Rocket Pool, Aave, …), so a user's idle ETH/stables can be
matched to a rate where **they keep custody**, not only to a CEX Earn product.

Source of truth: DefiLlama's public yields API (``yields.llama.fi/pools``) —
one well-known, audited-protocol-wide feed that carries base APY and TVL for
thousands of pools. We do NOT surface all of them: a curated allowlist maps a
small set of (project, symbol, chain) identities to our assets, so only
established, liquid venues appear. Everything else is ignored.

Discipline (same spine as the rest of the platform):
* **No fabricated rates.** A failed fetch, a schema drift, or a pool missing
  from the response yields NO option for that asset — never a made-up APY.
* **Base yield only.** We read ``apyBase`` (the organic supply/staking rate),
  falling back to ``apy`` only when base is absent — reward-token inflation is
  not counted as if it were safe carry.
* **TVL floor.** Pools below a liquidity floor are dropped as too thin to
  recommend.
* **Injectable fetch.** The network call is a seam so the feeder is fully
  testable offline; the default uses ``urllib`` with a short timeout and
  degrades to an empty list on any error.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional
from urllib.request import Request, urlopen

from bot.core.idle_yield import STAKING, DEFI_LENDING

log = logging.getLogger(__name__)

DEFILLAMA_YIELDS_URL = "https://yields.llama.fi/pools"

# Drop pools thinner than this (USD TVL) — a rate on a near-empty pool is not a
# rate a user should act on.
MIN_TVL_USD = 20_000_000.0

# Curated allowlist. Each entry maps a DefiLlama pool identity to one of our
# assets. `project`/`symbol` are matched case-insensitively; `chain` is a
# preference (the highest-APY match wins if a project lists an asset on several
# chains). Every venue here is non-custodial by construction — on-chain
# staking or a lending-protocol supply position; the user's keys hold the claim.
_CURATED: tuple[dict, ...] = (
    # ETH liquid staking.
    {"project": "lido", "symbol": "STETH", "asset": "ETH",
     "source": "Lido", "kind": STAKING, "risk_tier": "low"},
    {"project": "rocket-pool", "symbol": "RETH", "asset": "ETH",
     "source": "Rocket Pool", "kind": STAKING, "risk_tier": "low"},
    # Stablecoin lending (Aave v3 supply).
    {"project": "aave-v3", "symbol": "USDC", "asset": "USDC",
     "source": "Aave v3", "kind": DEFI_LENDING, "risk_tier": "low"},
    {"project": "aave-v3", "symbol": "USDT", "asset": "USDT",
     "source": "Aave v3", "kind": DEFI_LENDING, "risk_tier": "low"},
    {"project": "aave-v3", "symbol": "DAI", "asset": "DAI",
     "source": "Aave v3", "kind": DEFI_LENDING, "risk_tier": "low"},
    # ETH supplied on Aave (WETH market) — lower than staking but composable.
    {"project": "aave-v3", "symbol": "WETH", "asset": "ETH",
     "source": "Aave v3 (WETH)", "kind": DEFI_LENDING, "risk_tier": "low"},
)


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _default_fetch(url: str, timeout: float = 8.0) -> Any:
    """Fetch + parse JSON from ``url``; None on any failure (never raises)."""
    try:
        req = Request(url, headers={"User-Agent": "RUNECLAW-idle-yield/1.0",
                                    "Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:      # noqa: S310 (https const)
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("Idle-yield feed fetch failed (%s): %s", url, exc)
        return None


def noncustodial_options_from_llama(payload: Any) -> list[dict]:
    """Turn a DefiLlama ``/pools`` payload into optimizer options (pure).

    Keeps the best base-APY pool per (asset, source) that clears the TVL floor.
    Returns ``[]`` for a malformed payload — never a fabricated rate.
    """
    pools = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(pools, list):
        return []

    # (asset, source) -> best option seen so far.
    best: dict[tuple[str, str], dict] = {}
    for p in pools:
        if not isinstance(p, dict):
            continue
        project = str(p.get("project") or "").lower().strip()
        symbol = str(p.get("symbol") or "").upper().strip()
        tvl = _f(p.get("tvlUsd")) or 0.0
        # Prefer the organic base rate; only fall back to headline apy.
        apy = _f(p.get("apyBase"))
        if apy is None:
            apy = _f(p.get("apy"))
        if apy is None or apy <= 0 or tvl < MIN_TVL_USD:
            continue
        for entry in _CURATED:
            if entry["project"] != project or entry["symbol"] != symbol:
                continue
            key = (entry["asset"], entry["source"])
            opt = {
                "asset": entry["asset"],
                "source": entry["source"],
                "kind": entry["kind"],
                "apy": round(apy, 4),
                "lockup_days": 0,          # all curated venues are withdraw-anytime
                "custodial": False,        # non-custodial by construction
                "risk_tier": entry["risk_tier"],
                "tvl_usd": round(tvl, 0),
                "chain": str(p.get("chain") or ""),
            }
            prev = best.get(key)
            if prev is None or opt["apy"] > prev["apy"]:
                best[key] = opt
    # Deterministic order: asset, then source.
    return sorted(best.values(), key=lambda o: (o["asset"], o["source"]))


def fetch_noncustodial_options(
        fetch: Optional[Callable[[str], Any]] = None) -> list[dict]:
    """Fetch live non-custodial yield options (Lido/Rocket Pool/Aave …).

    ``fetch`` is an injectable ``(url) -> parsed-json-or-None`` seam (tests pass
    a fake; production uses ``_default_fetch``). Any failure → ``[]`` so the
    optimizer simply sees no non-custodial option, never a wrong one.
    """
    fetch = fetch or _default_fetch
    payload = fetch(DEFILLAMA_YIELDS_URL)
    return noncustodial_options_from_llama(payload)


def build_idle_options(savings_catalog: Optional[dict] = None, *,
                       extra_catalogs: Optional[dict[str, dict]] = None,
                       noncustodial: Optional[list[dict]] = None,
                       include_noncustodial: bool = True,
                       fetch: Optional[Callable[[str], Any]] = None) -> list[dict]:
    """Assemble the full cross-source option set for the optimizer.

    Combines (custodial) CEX Earn catalogs with (non-custodial) on-chain rates:
      * ``savings_catalog`` — the primary venue's Earn catalog
        (``{coin: {flexible, fixed, ...}}`` from ``yield_radar``); becomes
        custodial CEX-Earn options via ``idle_yield.options_from_savings_catalog``.
      * ``extra_catalogs`` — ``{venue_label: catalog}`` for other CEX Earn
        sources (e.g. Bybit), each labelled by venue.
      * ``noncustodial`` — pre-fetched non-custodial options, or (when None and
        ``include_noncustodial``) fetched live via ``fetch_noncustodial_options``.

    Pure except for the optional live non-custodial fetch (itself seam-injectable).
    """
    from bot.core.idle_yield import options_from_savings_catalog

    options: list[dict] = []
    if savings_catalog:
        options += options_from_savings_catalog(savings_catalog)
    for label, cat in (extra_catalogs or {}).items():
        options += options_from_savings_catalog(cat, source=str(label))
    if noncustodial is None and include_noncustodial:
        noncustodial = fetch_noncustodial_options(fetch=fetch)
    options += list(noncustodial or [])
    return options
