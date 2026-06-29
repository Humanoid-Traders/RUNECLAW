# On-Chain Data Provider (BYOK)

> **Status: SCAFFOLDING, default OFF and INERT without a key.** Provides the
> module + bounded confluence vote; wiring it into the analyzer is a trivial
> follow-up. Until enabled with a key, it makes no network call and produces no
> signal — exactly like the LLM rule-based fallback.

## Why

RUNECLAW's "smart money" read is **exchange-only** — a large print can't be told
from a liquidation, and real wallet/flow data is absent. This is the BYOK
scaffolding to bring in on-chain metrics (exchange flows, whale accumulation,
stablecoin supply) as a bounded, contrarian confluence signal.

## How it works

`bot/core/onchain.py`:

- **Inert gate** — `onchain_enabled()` is true only when `ONCHAIN_ENABLED=true`
  **and** `ONCHAIN_API_KEY` is set. Otherwise `fetch()` returns `None`.
- **Provider** — `OnChainProvider.fetch(symbol)` calls a configurable endpoint
  (`ONCHAIN_BASE_URL`), **cached** on a 10-minute TTL and **fail-open** (network /
  parse / auth error → no signal, never an exception into the decision path).
- **Bias** — `compute_bias(metrics)` maps normalised metrics to a directional
  bias in `[-1, 1]`, contrarian to exchange positioning:
  - exchange **netflow** negative (coins leaving exchanges) → accumulation → bullish
  - **whale** net accumulation → bullish · **stablecoin** supply rising → bullish
  - confidence scales with how many of the 3 metrics were available.
- **Vote** — `OnChainSnapshot.to_confluence_votes()` returns a single bounded
  `("onchain_flow", bias, weight)` (weight ≤ 0.7, scaled by confidence), or `[]`
  when there's no usable signal.

## Configuration

```
ONCHAIN_ENABLED=false
ONCHAIN_API_KEY=            # your Glassnode / Arkham / Nansen-style key
ONCHAIN_BASE_URL=          # endpoint returning normalised metrics
ONCHAIN_PROVIDER=
```

The provider is **provider-agnostic**: `_normalise()` defensively maps a payload's
`exchange_netflow` / `whale_net` / `stablecoin_supply_change` fields, ignoring
anything unrecognised. Point `ONCHAIN_BASE_URL` at an adapter that returns those
keys (already normalised to ~`[-1, 1]`).

## Safety

- **Inert without a key** — default OFF, no network, no signal.
- **Bounded** (`weight ≤ 0.7`) — can only shade confluence, never dominate.
- **Fail-open + cached** — failures degrade to no-signal; the TTL bounds requests.
- **Never trades** — pure data/voter; does not touch execution.

## Next

Wire `to_confluence_votes()` into `_score_confluence` (one bulk-voter line, like
order-flow / MTF) once the in-flight analyzer PRs settle, and an LLM news/social
sentiment scorer can reuse the same BYOK-inert pattern.
