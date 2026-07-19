# RESEARCH.md — §5 deep research (repo-aware + web)

Web items verified July 2026. Sources listed per item. Anything not
independently confirmed is marked `PENDING`/`UNVERIFIED`.

## 5.1 ERC-8004 registry interfaces + reference impl + testnet deployments

**Status: confirmed at reference-impl/testnet stage; NOT "final".** The prompt's
"ratified ~Jan 2026" is imprecise — ERC-8004 was proposed 2025-08-13 and remains a
**draft** with reference implementations on testnets and early mainnet as of
2026. Flag this wording before citing it externally.

Three registries (`github.com/erc-8004/erc-8004-contracts`):
- **Identity Registry** — ERC-721 + URIStorage. Fns: `register(...)`,
  `setAgentURI(agentId,...)`, `setAgentWallet(...)` (control proven via
  EIP-712/ERC-1271), `getAgentWallet`, `get/setMetadata(agentId,key[,value])`.
- **Reputation Registry** — `giveFeedback(...)` (no self-feedback),
  `readFeedback`, `readAllFeedback(agentId,clients,tag1,tag2,includeRevoked)`,
  `getSummary(...) -> (count, summaryValue, decimals)`, `revokeFeedback`,
  `appendResponse`. **Holds feedback scores/tags — not attested PnL.** (Confirms
  the strategic premise: the reputation primitive is not a track-record proof.)
- **Validation Registry** — `validationRequest(validator, agentId, requestURI,
  requestHash)`, `validationResponse(requestHash, response, responseURI,
  responseHash, tag)`, plus `getValidationStatus/Summary/…`. **This is our
  Phase-3 anchor target.** Caveat: the Validation Registry spec is *explicitly
  still under active revision with the TEE community* — treat its ABI as **not
  frozen**.

Testnet deployments (same addresses across chains):
- Base Sepolia — IdentityRegistry `0x8004A818BFB912233c491871b3d84c89A494BD9e`,
  ReputationRegistry `0x8004B663056A597Dffe9eCcC1965A193B7388713`.
- Ethereum Sepolia — same two addresses. (30+ chains listed; Base Sepolia is our
  target per §3.)

**Phase-3 impact only** (Phase 1 does not touch chain). Full ABI freeze deferred
to Phase-3 prep — `PENDING` until we pin the exact commit/tag of the contracts
repo we build against, because the Validation Registry is moving.

Sources: erc-8004/erc-8004-contracts (GitHub); ethereum-magicians.org ERC-8004
thread; eco.com ERC-8004 explainer.

## 5.2 EIP-7702 / session keys + Bitget least-privilege keys

**EIP-7702** — live since Ethereum **Pectra (May 2025)**. Lets an EOA set a
delegation pointer to contract code → session keys: time-bounded sub-keys scoped
to (contract set, value cap, time window). This is the on-chain model for
Phase-2 authority envelopes. `PENDING`: pin one reference session-key module
(candidates found: openfort, 7blocklabs minimal-trust impl) before Phase-2.

**Bitget least-privilege (CEX envelope for Phase 2)** — confirmed enforceable at
the key level:
- Permissions are separable: **read-only / trade / withdraw**. **Withdraw is
  disabled by default** on new keys.
- IP whitelist: up to **20** IPs per key; requests from other IPs rejected even
  with valid secret.
- Up to **10** keys per account → one least-privilege key per strategy is
  feasible.

Design consequence: the Phase-2 "no-withdraw, trade-only, IP-locked" CEX envelope
is a **provable key configuration**, not just a software gate — `verify.py` /
red-team can assert the key lacks withdraw scope. `PENDING`: confirm CCXT surfaces
the key's permission set for programmatic assertion, else assert via a
withdraw-attempt-must-fail red-team probe.

Sources: bitget.com API docs + "API Key Terms of Use"; openfort.io EIP-7702;
ercsolved.dev EIP-7702; smartagentkeys.com session-key writeups.

## 5.3 Prior art — "verifiable agent PnL" / attested track record

**Claim under test:** "Nobody has shipped Proof-of-PnL." **Verdict: PLAUSIBLE but
UNVERIFIED — do not assert first-mover as proven.** What the survey found:
- **ERC-8004 Reputation Registry** — feedback scores/tags, not fills-derived PnL
  (§5.1). Does not prove PnL.
- **ERC-8126** (finalized ~Jun 2026) — ZK risk-scoring (0–100) + privacy for agent
  *verification*; a risk score, not a fills-first PnL statement.
- **General verifiable-AI stack** — TEE attestation, zkML (still expensive),
  optimistic/economic schemes. These attest *computation ran*, not *these were the
  exchange fills and this is the honest PnL*. Sources note "fully trustless
  autonomous agents managing third-party capital remain rare" (April 2026).
- An arXiv empirical study ("Can Trustless Agents Be Trusted?") documents the
  ERC-8004 ecosystem's gaps — useful ammunition, `PENDING` full read.

**Where RUNECLAW differs:** fills-first (reconcile positions ← raw fills, not
summary/score), with an explicit **per-fill trust tier** and a
**selective-omission defense** (contiguity + balance-delta) for the CEX case —
which none of the above address, because they attest inference/identity/feedback,
not exchange execution provenance.

**Honesty guardrail:** I did **not** exhaustively survey every agent product
(Virtuals, Olas, Almanak, Giza, etc.) for a shipped fills-attestation feature.
Until that sweep is done, the public framing must be *"we have not found a shipped
fills-first Proof-of-PnL"* — not *"nobody has one."* This is a `PENDING` competitor
sweep, tracked as a Phase-1 prerequisite for any first-mover marketing claim.

Sources: cryptonomist.ch ERC-8126; phemex verifiable-AI; everstake verifiable-AI;
arxiv.org/pdf/2606.26028.

## 5.4 Per-chain fill re-derivation (the hardest part of "both")

`verify.py` must reconstruct a fill from a **public RPC** given wallet + tx ref.
Two very different shapes:

### Solana (target venue: Jupiter; also Meteora/Raydium PENDLE)
- RPC: `getTransaction(signature, {maxSupportedTransactionVersion:0,
  encoding:"jsonParsed"})` against a public endpoint (`api.mainnet-beta.solana.com`
  is what `app/lib/solana.js:20` already uses; a paid RPC recommended for reliable
  historical fetch — `PENDING` choice).
- **Canonical fill derivation = token-balance deltas**, not instruction decode:
  diff `meta.preTokenBalances` vs `meta.postTokenBalances` filtered to the
  agent's `owner` → signed (Δbase, Δquote) = the fill (price = |Δquote/Δbase|,
  qty = |Δbase|, fee from Δ of the fee mint / lamport delta). Robust across
  Jupiter route changes because it measures *what the wallet actually received/
  paid*, program-agnostic.
- `PENDING`: confirm the exact `owner`-attribution rule for versioned txs +
  address-lookup-table accounts, and how partial-route hops net out (they should,
  since we measure endpoint balance delta).

### EVM / Base (target: Uniswap v3, later v4)
- RPC: `eth_getTransactionReceipt(txHash)` → `logs[]`; a public Base RPC
  (`app/lib/wallet.js` already holds per-chain public RPCs).
- **Uniswap v3 `Swap` event** — signature hash `topic0 =
  0x7a6f9cbbaf2f9feccfd2e1e45f4f3b20f1dfaf425d9b97fb32c7a313562c861f`,
  `Swap(address indexed sender, address indexed recipient, int256 amount0,
  int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)`. Fill =
  signed (amount0, amount1) on the pool; map token0/token1 → base/quote; fee =
  gas (native) + LP fee tier (from the pool). Filter logs to pools where
  `recipient == agent wallet`.
- **Uniswap v4** — single-PoolManager + UniversalRouter; the pool `Swap` event
  shape differs. `PENDING`: pin the v4 PoolManager `Swap` event ABI + how to map a
  `PoolId` back to token pair before targeting v4.
- Alternative robust path (mirrors Solana): decode ERC-20 `Transfer` logs
  (`topic0 = 0xddf252ad…`) to/from the agent wallet in the tx and net them — more
  program-agnostic than pool-specific `Swap` decoding. `PENDING`: decide
  Swap-event vs Transfer-netting as the canonical v0 method (leaning
  Transfer-netting for robustness, Swap-event for price precision).

**Scope call:** `verify.py v0` should ship **one** on-chain chain end-to-end
(recommend **Base/EVM via Transfer-netting** — simplest deterministic re-derivation,
and Base Sepolia is already the Phase-3 anchor chain) and stub Solana behind an
explicit `UNVERIFIED: solana re-derivation not yet implemented` marker rather than
half-implement both. One-variable-per-version discipline.

Sources: web3-ethereum-defi docs (uniswap_v3 events); docs.uniswap.org
IUniswapV3PoolEvents; QuickNode/Cobo Jupiter guides; baransel.dev Solana parsing.

## 5.5 Repo primitive reuse map (per phase)

| Primitive | Where | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|---|
| `fetch_my_trades` raw fills | `live_executor.py:3062`, `exchange_sync.py:225` | **reuse** (CEX fills source) | — | — | — | — |
| SHA-256 hash chain | `audit_chain.py:42-55` | **reuse** (per-fill hashes) | — | reuse (anchor) | — | — |
| SHA-256 Merkle root | `attestation.py:122-141` | **reuse** (statement root) | — | reuse | — | — |
| Ed25519 batch signing | `attestation.py:145-183` | **reuse** (`cex_operator_signed` tier) | — | — | — | — |
| Flight Recorder DECISION/OUTCOME | `flight_recorder.py` | **reuse** (epoch = OUTCOME sealing) | — | — | — | — |
| `equity_basis.js` reconcile | `equity_basis.js:34-55` | **extend** (fills, not just closes) | — | — | — | reuse |
| `reports_parity` oracle | `reports_parity.test.js` | **extend** (CSF parity) | — | — | — | reuse |
| `intent_policy.py` clamp/tighten | `bot/guardian/intent_policy.py` | — | **reuse** (CEX envelope) | — | write-veto | — |
| `risk_engine` 23-check gate | `bot/risk/risk_engine.py` | — | **reuse** (envelope enforce) | — | veto path | — |
| `escape_agent.py` | `bot/guardian/escape_agent.py` | — | **wire** (atomic revoke) | — | — | — |
| `red_team.py` (risk-envelope) | `bot/core/red_team.py` | — | **extend** (+injection/withdraw probes — net-new categories) | — | reuse | — |
| `firewall.py` injection detect | `bot/guardian/firewall.py` | — | reuse | — | reuse | — |
| `solana.js` / `wallet.js` public RPC | `solana.js:20`, `wallet.js:32` | **reuse** (on-chain re-derivation RPC) | — | — | — | — |
| ERC-8004 registries | testnet (§5.1) | — | — | **new** (mint + anchor) | — | — |
| Common Statement Format + `verify.py` | — | **new** | — | — | — | consumed |
| CEX omission defense (contiguity+Δbalance) | — | **new** | — | — | — | — |
| On-chain fill re-derivation | — | **new** | — | — | — | — |

**Net:** Phase 1 is ~70% reuse of existing tamper-evidence + fill plumbing; the
new surface is the CSF, `verify.py`, the omission defense, and on-chain
re-derivation.

## 5.6 Surface generalization — prediction markets, NFTs, DeFi (design note)

The CSF was built venue-agnostic on purpose: a "fill" is any signed value transfer
with a price, and the trust tier follows the venue's provenance. So the broader
AI/web3/DeFi surface is **not new machinery — it is new `onchain_public` venues
under the same `verify.py`.** All read/verify only; none touch the Non-Goals
(no launch/pump/shill mechanics — this productizes *"verify the fills,"* not
promotion).

| Surface | CSF `venue` | How `verify.py` re-derives a "fill" | Trust tier |
|---|---|---|---|
| **Prediction markets** (Polymarket) | `polygon:polymarket` | CTF `OrderFilled`/`PositionSplit` + USDC `Transfer` netting on Polygon RPC; PnL = payout − cost basis per market resolution | `onchain_public` |
| **NFTs** (OpenSea/Blur) | `base:seaport` / `eth:blur` | Seaport `OrderFulfilled` (offer/consideration) + ERC-721/1155 `Transfer`; "fill" price = ETH/USDC leg; PnL = sale − acquisition | `onchain_public` |
| **DeFi LP / lending** (Uniswap v3 LP, Aave) | `base:uniswap-v3-lp`, `base:aave-v3` | position deltas from `Mint`/`Burn`/`Collect` (LP) or `Supply`/`Withdraw`/`Borrow`/`Repay` + interest accrual; already partially read by `app/lib/defi.js` | `onchain_public` |
| **Perp DEX** (Hyperliquid, GMX) | `hyperliquid`, `arbitrum:gmx` | HL: L1 fills API (semi-public → tier between onchain and cex); GMX: `IncreasePosition`/`DecreasePosition` events | `onchain_public` / mixed |

**Why this is the right shape:** the same fills-first, min-tier-honest, Merkle-
signed statement + `verify.py` re-computer works for every one — a Polymarket
resolution or an NFT flip is *more* verifiable than a CEX trade (fully on-chain),
so they slot in at the **strongest** tier. Each is a separate future version
(one variable each), gated behind its own `verify.py` re-derivation + a
pre-registered prediction, exactly like the Base EVM slice. **Not built now;**
scoped here so the CSF `venue`/`venue_type` design is validated against them
before we commit. ERC-8004 identity (Phase 3) then anchors *one* portable
reputation across all of these surfaces — the durable asset.
