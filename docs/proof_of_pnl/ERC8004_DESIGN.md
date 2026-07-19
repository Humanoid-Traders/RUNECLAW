# Phase 3 — ERC-8004 Identity + Reputation Binding

> A portable, verifiable agent identity: not "trust our dashboard's reputation
> score" but "here is a signed card binding a public identity to a track record
> you can re-derive from raw fills, and a custody posture you can check."

## What ERC-8004 is (verified facts)

ERC-8004 defines three registries for agent trust — **Identity**, **Reputation**,
and **Validation**. Deployed (deterministic addresses) on Base Sepolia:

- IdentityRegistry: `0x8004A818BFB912233c491871b3d84c89A494BD9e`
- ReputationRegistry: `0x8004B663056A597Dffe9eCcC1965A193B7388713`
- Validation Registry: address/ABI **NOT frozen** at time of writing → any
  Validation-registry interaction is marked `UNVERIFIED` here, never faked.

An agent has an on-chain identity (an address / agent-id in the Identity
registry); reputation is **attested about** that identity, not self-declared.

## The binding this module produces

An **Agent Identity Card** is a deterministic, content-hashed, Ed25519-signed
object that binds, in one verifiable artifact:

1. **Identity** — the agent's ERC-8004 identity: `{chain, identity_registry,
   agent_address}`.
2. **Track record** — the *commitment* of a **published** Proof-of-PnL statement:
   its `merkle_root`, `trust_tier`, and fills-derived `metrics` (net PnL, profit
   factor, Sharpe, max drawdown, round trips). Nothing self-reported — the card
   refuses to bind an `INCOMPLETE`/unpublished statement.
3. **Reputation** — outcome-based, **derived from the statement's fills only**
   (`reputation_from_statement`), carrying the trust tier forward so a reputation
   number can never claim more confidence than its weakest fill.
4. **Custody posture** — the bound Authority Envelope's hash + headline scope
   (venues, per-trade cap, withdraw allowed?), so a reader sees what the agent was
   *permitted* to do, not just what it did.

The card's identity is its content hash; it is signed with the same
`bot.utils.attestation` Ed25519 engine used for CSF statements. `verify_card`
re-derives the hash and checks the signature with **no RUNECLAW server**.

## The honest on-chain anchor

`anchor_plan(card)` returns the *intended* ReputationRegistry anchoring call
(target address, the card hash to anchor, chain) — but the card's `anchor.status`
is **`UNVERIFIED`** until a real tx is submitted and confirmed, exactly as the
Proof-of-PnL on-chain slice reported `UNVERIFIED` for a fill it could not
re-derive. We do not claim an on-chain reputation anchor exists until one does.

## Pre-registered predictions (before the tests)

- **E1 — no reputation without a published statement.** `build_identity_card`
  refuses (returns a card with `status="unbacked"` and no reputation) when the
  statement is not `published`. *Falsifier:* a reputation block produced from an
  INCOMPLETE statement.
- **E2 — trust-tier carried forward.** The card's reputation `trust_tier` equals
  the statement's epoch tier (the minimum over fills). *Falsifier:* a card
  reputation claiming a higher tier than the statement.
- **E3 — determinism + signature.** The same inputs yield the same `card_hash` on
  any machine; `verify_card` passes for a signed card and fails if any bound field
  (root, tier, metric, envelope hash, agent address) is mutated. *Falsifier:* an
  unstable hash or a mutation that still verifies.
- **E4 — anchor is honestly unverified.** A freshly built card reports
  `anchor.status == "UNVERIFIED"` and names the ReputationRegistry it *would*
  anchor to; it never claims an on-chain anchor exists. *Falsifier:* a card
  reporting a confirmed anchor without a real tx.

Results: `tests/test_erc8004_identity.py`.
