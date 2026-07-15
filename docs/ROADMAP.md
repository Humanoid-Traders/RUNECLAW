# RUNECLAW — Product &amp; Protocol Roadmap

**From an autonomous trader to an on-chain agent economy.**

Today RUNECLAW is an autonomous, risk-gated AI that trades perpetuals across four
venues and that you can chat with and trade alongside from the web. This roadmap
extends that engine along five fronts — sharper agents, more venues, native web3
&amp; staking, social growth, and the trust layer that has to come first.

> Directional roadmap, not a commitment or an offer. Order and scope will shift
> with evidence and regulation. Nothing here is financial advice or a
> solicitation to buy any token. Trading derivatives involves substantial risk
> of loss.

## What already ships today

- **Live engine** — autonomous scan → LLM analysis → 23-check risk gate → execute.
- **4 venues** — Bitget, Bybit, BingX, Hyperliquid (on-chain).
- **Per-user live** — bring-your-own-keys, encrypted at rest, operator-gated.
- **Web app** — chat, place-trade-from-chat, live portfolio, onboarding, invites.

## Legend

| Status | Meaning |
| --- | --- |
| 🟢 Shipped / extending | Live today, or a direct extension of a live system |
| 🟡 Building / near-term | Actively in progress or next up |
| 🔵 Planned | Scoped, scheduled after near-term |
| ◆ Vision | Gated behind the [Guardrails](#guardrails) — no user launch until those are met |

Horizons: **Now** · **Next** (~0–3 mo) · **Later** (~3–9 mo) · **Vision** (9+ mo).

---

## 1. Agent intelligence

Make the reasoning deeper, more personal, and self-improving — building on the
existing analyzer, learning loop, and shadow book.

| Capability | Horizon | Status | Builds on |
| --- | --- | --- | --- |
| **Real-time signal fusion** — news, funding/liquidation feeds, and social alpha (X, Telegram) folded into the vote ensemble | Next | 🟡 | the analyzer's voter model |
| **Per-user agent memory** — remembers risk appetite, watchlist, past decisions; sizes to your calibrated confidence | Next | 🟡 | chat + confidence calibration |
| **Multi-agent ensemble** — specialist sub-agents (scalp/swing/macro/market-maker) under a portfolio coordinator that allocates risk by live expectancy | Later | 🔵 | the strategy engine |
| **Talk-to-build strategies** — describe a strategy in plain language; it compiles to gated, backtested config (never unchecked live orders) | Later | 🔵 | the gated config system |
| **On-chain intelligence** — smart-money/whale tracking, DEX flow, liquidity-pool reads as first-class signals | Later | 🔵 | on-chain execution path |
| **Online self-improvement** — reinforcement from live outcomes, every change validated against the shadow book before it touches capital | Vision | ◆ | shadow-book replay |

## 2. Execution &amp; venues

The venue-abstraction layer already makes adding markets a data change, not a
rewrite. Push it toward every liquid perp market, on-chain and off.

| Capability | Horizon | Status | Builds on |
| --- | --- | --- | --- |
| **More CEX venues + smart routing** — best price / lowest fees across connected venues, maker-preferred | Next | 🟢 | the venue adapter + router |
| **On-chain perp DEXs** — native adapters for dYdX v4, GMX, Vertex, Drift (Solana), beside Hyperliquid | Later | 🔵 | new venue adapters |
| **Cross-venue funding arbitrage** — capture funding/basis spreads delta-neutral (backbone of the yield vaults) | Later | 🔵 | powers Web3 · vaults |
| **Intent-based execution** — solvers compete to fill across chains/venues, gas-aware and MEV-protected | Vision | ◆ | account abstraction |

## 3. Web3, staking &amp; vaults

Non-custodial by default. Users keep their keys; the protocol adds staking,
agent-managed vaults, and shared upside — **every step gated by the
[Guardrails](#guardrails)**.

| Capability | Horizon | Status | Notes |
| --- | --- | --- | --- |
| **Self-custody sign-in** — connect a wallet (WalletConnect / MetaMask) to log in and trade on-chain venues without handing over keys | Next | 🟡 | new auth path |
| **Verifiable track record** — signed, on-chain-anchored trade history; trust performance without trusting the operator | Next | 🟡 | trust primitive for vaults + copy |
| **Idle-margin yield** — optionally park unused stablecoin margin in audited lending (e.g. Aave); opt-in, withdraw anytime | Next | 🔵 | opt-in, audit-gated |
| **$CLAW staking** — stake for fee discounts, higher live limits, priority agents, and a share of protocol revenue | Later | ◆ | gated — see Guardrails |
| **Agent vaults (ERC-4626)** — deposit stablecoins into a vault an agent trades (e.g. delta-neutral funding-farming); hold standard vault shares, redeem on demand | Later | ◆ | gated · non-custodial · audited |
| **DAO governance** — token-holders vote on risk params, new venues, promoted strategies; on-chain performance-fee splits | Vision | ◆ | gated · post-token |

## 4. Product, social &amp; growth

Turn single users into a network — building directly on the referral system that
just shipped.

| Capability | Horizon | Status | Builds on |
| --- | --- | --- | --- |
| **Invite friends** — unique links, signup attribution, live "friends joined" count | Now | 🟢 | live today |
| **Referral rewards &amp; tiers** — turn invites into perks (fee credits, higher limits, post-token rewards) with milestone tiers | Next | 🟡 | the invite system |
| **Leaderboards &amp; shareable cards** — opt-in performance leaderboards and one-tap shareable trade cards for Telegram/X | Next | 🔵 | new social surface |
| **Copy-trading marketplace** — follow top agents/users; creators earn a share of follower fees | Later | 🔵 | verifiable track record |
| **Agent marketplace** — publish a strategy as a subscribable agent; the protocol handles risk gating, billing, revenue split | Vision | ◆ | the agent economy |

## 5. Trust, risk &amp; compliance

The layer that gates everything above. Real money is already live — so safety
leads, it doesn't follow.

| Capability | Horizon | Status | Notes |
| --- | --- | --- | --- |
| **Hard risk engine** — 23-check gate, circuit breakers, per-user loss breakers, margin caps, kill switch, encrypted secrets vault | Now | 🟢 | live today |
| **Independent security audit** — third-party review of money endpoints, credential store, gateway | Next | 🟡 | precedes wider live rollout |
| **Contract audits &amp; proof-of-reserves** — every vault/staking contract audited; reserves provable on-chain before any deposit | Later | 🔵 | blocks vault launch |
| **Compliance tiers &amp; disclosures** — jurisdiction-aware access, KYC tiers for higher limits/vaults, clear risk disclosures | Later | 🔵 | gates token &amp; vaults |
| **Insurance fund** — protocol-owned backstop for vault tail-risk, funded from performance fees | Vision | ◆ | post-revenue |

---

## Guardrails

**Nothing marked "Vision" (◆) ships to users until this is true.**

A token, managed vaults, revenue share, and yield products carry real securities,
regulatory, and smart-contract risk. They are exciting — and they are exactly
where projects hurt their users. So each is gated, non-negotiably, behind:

- **Legal review** per jurisdiction before any token, vault, or revenue-share goes live.
- **Independent audits** — security review of the app, and separate smart-contract audits for every on-chain contract.
- **Non-custodial by default** — users keep their keys and can withdraw at any time; the protocol never takes custody it doesn't need.
- **Plain risk disclosures** — leverage, drawdown, and smart-contract risk stated up front, never buried.
- **Staged rollout with caps** — small limits first, widened only on evidence, with a global kill switch throughout.
- **Proof over promises** — verifiable track record and on-chain reserves before we ask anyone to deposit.

## How we sequence the work

- **Extend, don't rebuild.** Every near-term item leans on a proven system — the venue adapter, risk engine, learning loop, or web app — so we ship fast without new blast radius.
- **Gated &amp; opt-in.** New capability lands behind a flag, off by default. Live money and web3 features are choices the user makes, not defaults they inherit.
- **Safety leads.** Audits, disclosures, and caps come before the feature they protect — not after an incident.
- **Proof, not hype.** Verifiable performance and on-chain transparency are prerequisites for anything that touches other people's capital.
