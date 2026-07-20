"""
On-chain data provider (BYOK) — exchange flows / whale accumulation as a signal.

RUNECLAW's "smart money" read is exchange-only: a large print can't be told from a
liquidation, and real wallet/flow data (Glassnode / Arkham / Nansen) is absent.
This module is the BYOK scaffolding for that data: give it an API key and it
fetches a small set of on-chain metrics and turns them into a bounded confluence
vote. **Without a key it is completely inert** — exactly like the LLM rule-based
fallback — so it is safe to ship default-OFF and wire up later.

Self-contained on purpose: it reads its own ``ONCHAIN_*`` env config (no coupling
to the frozen CONFIG), fetches on a TTL cache, and is fail-open (any error → no
signal, never an exception into the decision path). It does not place trades.

Metric → directional bias (contrarian to exchange positioning):
  - exchange **netflow** NEGATIVE (coins leaving exchanges) → accumulation → bullish
  - **whale** net accumulation → bullish
  - **stablecoin** supply rising (dry powder) → bullish
Each is clipped, weighted, and blended into a single bias in ``[-1, 1]`` with a
confidence reflecting how many metrics were actually available.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("runeclaw.onchain")

# Component weights for the composite bias (sum is normalised by availability).
_W_NETFLOW = 1.0
_W_WHALE = 1.0
_W_STABLE = 0.6
_W_DEXFLOW = 0.8

_VOTE_WEIGHT = 0.7          # confluence weight of the on-chain voter
_CACHE_TTL_S = 600.0        # 10 min — on-chain metrics move slowly
_MAX_ABS = 1.0


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def onchain_enabled() -> bool:
    """True only when explicitly enabled AND an API key is configured."""
    return _env("ONCHAIN_ENABLED").lower() in ("1", "true", "yes") and bool(_env("ONCHAIN_API_KEY"))


def onchain_flow_enabled() -> bool:
    """Keyless flow mode (PR JJ): DEX taker-flow bias pulled from the web
    app's flow radar over the bot-secret sync channel. No paid API involved.
    Default OFF — flip ONCHAIN_FLOW_ENABLED=1 to let the voter contribute."""
    return _env("ONCHAIN_FLOW_ENABLED").lower() in ("1", "true", "yes")


@dataclass
class OnChainSnapshot:
    """Normalised on-chain metrics + the derived directional bias."""
    symbol: str = ""
    exchange_netflow: Optional[float] = None      # +inflow / -outflow (normalised ~[-1,1])
    whale_net: Optional[float] = None             # +accumulation / -distribution
    stablecoin_supply_change: Optional[float] = None  # +minting / -burning
    dex_taker_flow: Optional[float] = None        # +net on-chain buying / -selling
    bias: float = 0.0                             # composite directional bias [-1,1]
    confidence: float = 0.0                       # [0,1], scales with metrics available
    components_ok: list = field(default_factory=list)

    def to_confluence_votes(self) -> list:
        """Bounded ``(name, vote, weight)`` votes for the confluence scorer.
        Empty when there is no usable signal."""
        if not self.components_ok or abs(self.bias) < 1e-9:
            return []
        return [("onchain_flow", _clip(self.bias), _VOTE_WEIGHT * max(0.0, min(1.0, self.confidence)))]


def compute_bias(metrics: dict) -> OnChainSnapshot:
    """Map a normalised metrics dict to an OnChainSnapshot.

    ``metrics`` keys (all optional, each already normalised to ~[-1, 1]):
      ``exchange_netflow`` (+inflow/-outflow), ``whale_net`` (+accum/-distrib),
      ``stablecoin_supply_change`` (+mint/-burn). Missing metrics are skipped and
      reduce confidence rather than biasing toward zero.
    """
    snap = OnChainSnapshot(symbol=str(metrics.get("symbol", "")))
    contribs = []   # (weight, signed_contribution)

    nf = metrics.get("exchange_netflow")
    if nf is not None:
        snap.exchange_netflow = _clip(float(nf))
        contribs.append((_W_NETFLOW, -snap.exchange_netflow))   # outflow=bullish
        snap.components_ok.append("netflow")

    wh = metrics.get("whale_net")
    if wh is not None:
        snap.whale_net = _clip(float(wh))
        contribs.append((_W_WHALE, snap.whale_net))             # accumulation=bullish
        snap.components_ok.append("whale")

    sc = metrics.get("stablecoin_supply_change")
    if sc is not None:
        snap.stablecoin_supply_change = _clip(float(sc))
        contribs.append((_W_STABLE, snap.stablecoin_supply_change))  # minting=bullish
        snap.components_ok.append("stablecoin")

    df = metrics.get("dex_taker_flow")
    if df is not None:
        snap.dex_taker_flow = _clip(float(df))
        contribs.append((_W_DEXFLOW, snap.dex_taker_flow))           # net buying=bullish
        snap.components_ok.append("dexflow")

    if contribs:
        total_w = sum(w for w, _ in contribs)
        snap.bias = _clip(sum(w * c for w, c in contribs) / total_w) if total_w else 0.0
        # Confidence scales with metric coverage. The keyless flow mode has a
        # single metric by design — cap its solo confidence at 1/3 so a lone
        # taker-flow read never votes with the strength of a full BYOK stack.
        snap.confidence = min(1.0, len(contribs) / 3.0)
    return snap


class OnChainProvider:
    """Fetches on-chain metrics from a configurable BYOK endpoint, cached + fail-open."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, OnChainSnapshot]] = {}

    async def fetch(self, symbol: str) -> Optional[OnChainSnapshot]:
        """Return a fresh-or-cached snapshot for ``symbol`` or None when disabled /
        unavailable. Never raises."""
        if not (onchain_enabled() or onchain_flow_enabled()):
            return None
        now = time.monotonic()
        cached = self._cache.get(symbol)
        if cached and (now - cached[0]) < _CACHE_TTL_S:
            return cached[1]
        try:
            metrics = await self._fetch_metrics(symbol)
        except Exception as exc:                       # network / parse / auth
            log.warning("On-chain fetch failed for %s: %s", symbol, exc)
            return cached[1] if cached else None
        if not metrics:
            return cached[1] if cached else None
        snap = compute_bias({**metrics, "symbol": symbol})
        self._cache[symbol] = (now, snap)
        return snap

    async def _fetch_metrics(self, symbol: str) -> Optional[dict]:
        """Fetch raw metrics from the configured provider and normalise them.

        Provider-agnostic: expects a JSON object with any of ``exchange_netflow``,
        ``whale_net``, ``stablecoin_supply_change`` (already normalised, or mapped
        by ``_normalise``). Override / extend per concrete provider.
        """
        import aiohttp
        base = _env("ONCHAIN_BASE_URL")
        key = _env("ONCHAIN_API_KEY")
        if not base or not key:
            # Keyless flow mode: the web app's DEX taker-flow radar over the
            # bot-secret sync channel (blocking urllib → thread).
            if onchain_flow_enabled():
                import asyncio
                return await asyncio.to_thread(_flow_metrics_from_web, symbol)
            return None
        url = f"{base.rstrip('/')}/metrics"
        params = {"symbol": symbol, "apikey": key}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning("On-chain API status %s for %s", resp.status, symbol)
                    return None
                data = await resp.json()
        return _normalise(data)


def _normalise(data: dict) -> dict:
    """Map a raw provider payload to the normalised metric keys, passing through
    already-normalised values and ignoring anything unrecognised. Defensive: a
    malformed payload yields an empty dict (→ no signal), never an exception."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k in ("exchange_netflow", "whale_net", "stablecoin_supply_change"):
        v = data.get(k)
        if v is None:
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


# ── Keyless flow source (web app's DEX taker-flow radar) ─────────────────────

# Radar cache shared across symbols (one pull covers every base). None
# sentinel, NEVER 0.0 — time.monotonic() starts near zero on fresh boots.
_FLOW_RADAR_TTL_S = 120.0
_flow_radar_cache: Optional[dict] = None
_flow_radar_ts: Optional[float] = None


def _flow_metrics_from_web(symbol: str) -> Optional[dict]:
    """Metrics dict {dex_taker_flow: bias} for ``symbol``'s base, from the web
    flow radar. Blocking (urllib) — call via asyncio.to_thread. Fail-open."""
    global _flow_radar_cache, _flow_radar_ts
    now = time.monotonic()
    if (_flow_radar_cache is None or _flow_radar_ts is None
            or (now - _flow_radar_ts) >= _FLOW_RADAR_TTL_S):
        from bot.utils.web_data_pull import fetch_onchain_flow
        radar = fetch_onchain_flow()
        if isinstance(radar, dict) and isinstance(radar.get("bases"), list):
            _flow_radar_cache = radar
            _flow_radar_ts = now
        elif _flow_radar_cache is None:
            return None                      # nothing cached, nothing fetched
    base = str(symbol or "").upper().split("/")[0].replace("USDT", "")
    for row in (_flow_radar_cache or {}).get("bases", []):
        if str(row.get("base", "")).upper() == base:
            try:
                bias = float(row.get("flow_bias"))
            except (TypeError, ValueError):
                return None
            # A thin sample is already damped web-side; still require a real
            # sample marker so junk never becomes a vote.
            if row.get("sample") not in ("ok", "thin"):
                return None
            return {"dex_taker_flow": bias}
    return None                              # base not covered by the radar


def reset_flow_radar_cache() -> None:
    """Test hook."""
    global _flow_radar_cache, _flow_radar_ts
    _flow_radar_cache = None
    _flow_radar_ts = None


_PROVIDER: Optional[OnChainProvider] = None


def get_onchain_provider() -> OnChainProvider:
    """Process-wide singleton provider."""
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = OnChainProvider()
    return _PROVIDER
