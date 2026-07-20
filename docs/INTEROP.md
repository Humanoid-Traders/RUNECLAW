# RUNECLAW Agent Interoperability — Design (PR MM)

**Status: DESIGN ONLY.** This document specifies how RUNECLAW's existing
trust surfaces map onto the emerging agent-interoperability standards, and
what a payment layer *would* look like. Nothing here authorizes
implementation of payments: every item in §4 is explicitly gated on
operator + legal review. No payment code ships with this document.

---

## 1. What already exists (shipped, verifiable today)

RUNECLAW already produces the artifacts agent-trust registries are being
designed to consume:

| Surface | Where | Property |
|---|---|---|
| ERC-8004-shaped identity card | `bot/proofofpnl/erc8004.py`, served at `/agent/:address` + MCP `get_agent_card` | Identity bound to a sealed track record; card is *unbacked* unless the statement reconciles (open/close balances must match fills net) |
| Sealed Proof-of-PnL statement | `/proof`, `/api/public/proofofpnl`, MCP `get_proof_of_pnl` | `publish_hash = SHA-256(canonical bundle)`; any consumer re-derives it in any language |
| Signed verification | server-side re-derivation + Ed25519 | `verify_card` re-checks hash and signature; failures are reported, never hidden |
| On-chain anchor | card `anchor` field | **Honestly UNVERIFIED** until a real transaction confirms it — no anchoring claim is ever fabricated |
| Machine-consumable tools | `POST /mcp` (17 read-only tools), documented at `/developers` | Any MCP-capable agent consumes the above without trusting this server: the re-derivation recipe rides the payload |

The design principle carried through all of it: **counterparties verify
artifacts, not reputations.** An agent deciding whether to trust RUNECLAW's
track record needs zero API keys and zero goodwill — it needs SHA-256.

## 2. Mapping onto ERC-8004 registries

ERC-8004 sketches three registries: identity, reputation, validation.

- **Identity registry.** The card's `identity.agent_address` is the join
  key. Registration would be a single on-chain transaction binding the
  address to the card hash. *Design position:* register only when the
  operator opts in; until the registration tx confirms, the card's anchor
  stays `UNVERIFIED` (current behavior — no change needed).
- **Reputation registry.** Third parties may post feedback about an agent.
  *Design position:* RUNECLAW never posts self-feedback and never
  solicits feedback — its reputation input is the re-derivable statement
  itself. Consumers who want a reputation entry can post the verified
  card hash; we provide the artifact, not the endorsement.
- **Validation registry.** Independent validators attest that they
  re-derived the statement. The `/api/public/agent/:address` response is
  already the exact input a validator needs (`card`, `verified`,
  `problems`, `publication.publish_hash`). *Design position:* expose a
  stable validation payload (done); leave attestation posting to the
  validators themselves.

**Interop invariant:** nothing in any registry interaction may claim more
than the sealed statement supports. A registry entry is a pointer to a
verifiable artifact — never a substitute for verifying it.

## 3. MCP as the agent-to-agent transport

The MCP server is the interop workhorse today: identity cards, sealed
statements, the leaderboard, safety scans and radars are all consumable by
any MCP client with no auth. Two design rules keep this safe as it grows:

1. **Read-only by default, forever.** Trade-capable or state-changing
   tools are a separate operator-gated decision, never an incremental
   addition to the public server.
2. **Honesty travels on the wire.** Disclaimers ("never a verdict",
   "guided-only", anti-sybil) are part of the tool *payloads*, not just
   the UI — a downstream agent that republishes our data republishes the
   caveats with it.

## 4. Payment scaffolding — design only, gated

The x402 pattern (HTTP 402 + signed stablecoin payment, settled by a
facilitator) is the emerging standard for agent-to-agent payments. If
RUNECLAW ever charges agents for premium intelligence (deeper dossiers,
higher rate limits), the design is:

- **Flow:** client calls a priced endpoint → 402 with payment
  requirements (amount, asset, chain, receiver) → client returns a signed
  payment payload → facilitator verifies + settles → 200 with the data.
- **Scope:** payments would price *access to intelligence RUNECLAW already
  publishes* — never trading services, never custody, never performance
  products.
- **Custody stance unchanged:** RUNECLAW receives payments to an
  operator-controlled address; it never holds a counterparty's keys or
  funds. The non-custodial authority-envelope rule is unaffected.

**Hard gates before any implementation** (each independently blocking):

1. Operator decision to charge at all (product call).
2. Legal review: money-transmission / VASP exposure of receiving
   stablecoin payments per jurisdiction; terms of service.
3. Facilitator selection review (settlement trust, chargeback semantics).
4. Rate-limit + abuse design so a payment never bypasses safety rails.

Until all four clear, the repo intentionally contains **no** payment
endpoint, no 402 handler, and no facilitator integration.

## 5. Interop invariants (§4 hard lines, restated)

Any future interop work inherits these unconditionally:

- **No dollar amounts on public/community surfaces.** Handles, ratios and
  percentages only; dollar figures appear solely on the operator's own
  deliberate feeds and users' private surfaces.
- **No fabricated performance.** Nothing is displayed (or served to
  agents) that cannot be reconstructed from raw recorded fills.
- **Airdrop participation stays guided-only.** No signing for users, no
  multi-wallet, no sybil mechanics — regardless of what a counterparty
  agent requests.
- **Safety reads are heuristic flags, never verdicts** — and the
  disclaimer is part of the payload.
- **Non-custodial authority envelope.** No interop feature may require
  holding user keys or moving user funds outside the human-set, revocable
  envelope.
- **The anchor never lies.** On-chain anchoring is claimed only after a
  confirmed transaction, in interop contexts exactly as on `/proof`.

## 6. Sequencing (when the operator green-lights)

1. Registry registration tx for the operator identity (smallest step,
   makes the anchor real). **Operator decision (2026-07): root anchor on
   Base (chain 8453) — fast and cheap now. If the identity later becomes
   high-value/canonical, mirror or promote the root anchor to Ethereum
   mainnet (chain 1); per-chain anchor records coexist, Base stays the
   root.** Tooling: `/anchor` builds the unsigned tx, the operator signs
   from their own wallet, `/anchor confirm <tx>` verifies on-chain before
   anything upgrades to VERIFIED. The bot never holds a key and never
   sends a transaction.
2. Validation-payload stability guarantee (versioned schema for
   `/api/public/agent/:address`).
3. Only then, and only after §4's gates: the 402 experiment on one
   premium endpoint.
