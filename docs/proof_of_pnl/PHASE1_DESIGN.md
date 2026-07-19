# Phase 1 — Proof-of-PnL core (design note, pre-code)

Design only. No Phase-1 code until this + GROUND_TRUTH + RESEARCH pass review.

## Scope decision (one-variable discipline)

v0 ships **two source paths under one format**, but deliberately implements one
on-chain chain end-to-end and stubs the other, rather than half-building both:

| Source | Tier | v0 status |
|---|---|---|
| CEX Bitget (`fetch_my_trades`) | `cex_operator_signed` | **implemented** (validated vs the real `live_trade_proof.json` fills) |
| EVM/Base (Transfer-netting from public RPC) | `onchain_public` | **implemented** (validated vs one real Base tx — `PENDING` pick) |
| Solana (token-balance deltas) | `onchain_public` | **stub** → emits `UNVERIFIED: solana re-derivation not implemented`; no fake root |
| CEX-in-TEE | `cex_tee_attested` | **deferred** (Validation-Registry TEE spec still moving, §RESEARCH 5.1) |

Rationale: the CEX tier is the weakest but is the one with **real committed data**
today (3 spot round-trips); the EVM tier is the strongest and is the Phase-3
anchor chain. Shipping those two proves the honesty-ladder end-to-end with real
data on both ends. Solana is scoped but not faked.

## Common Statement Format — freeze `v0`

`CSF_VERSION = "v0"`. Canonical JSON: UTF-8, `sort_keys=True`, no whitespace,
integers in **minor units** (no float PnL in the hash path — `Decimal` for
compute, integer minor units for hashing). Deterministic ordering: `(ts,
source_ref)`.

```
Fill:
  venue         # "bitget" | "base:uniswap-v3" | "solana:jupiter"
  venue_type    # "cex" | "onchain"
  market        # normalized symbol/pool, e.g. "BTC/USDT"
  side          # "buy" | "sell"
  price, qty, fee, fee_ccy   # minor-unit ints + scale; Decimal on read
  ts            # ms, integer
  source_ref    # onchain: "<txhash>#<logIndex>" ; cex: "<trade_id>@<order_id>"
  trust_tier    # derived from venue_type + attestation, NEVER operator-chosen
  fill_hash     # sha256(canonical(fill without fill_hash))
Epoch:
  csf_version, range_start, range_end
  account_ids   # wallet addr(s) | cex account label
  open_snapshot, close_snapshot   # signed balances (cex reconciliation anchor)
  fills[]       # canonically ordered
  merkle_root   # attestation.compute_merkle_root(fill_hash[])
  metrics { net_pnl, fees, funding, pf, sharpe, max_dd, round_trips }
  trust_tier    # = min(fill.trust_tier)  ← headline honesty field
  attestation   # { type: "ed25519"|"onchain_anchor"|"none", sig, pubkey } | null
  status        # "published" | "INCOMPLETE" | "UNVERIFIED"
```

Tier ordering for the `min`: `onchain_public` (3) > `cex_tee_attested` (2) >
`cex_operator_signed` (1). Headline tier = min across fills. Non-negotiable: a
weaker tier can never render as stronger (enforced in the type, tested).

## CEX selective-omission defense (the crux)

A CEX epoch is `published` only if ALL hold, else `INCOMPLETE` (never dressed up):
1. **Contiguity** — `range_start..range_end` contiguous; fills sorted; **no
   order-id gaps** within the window (detect via `fetch_my_trades` full pagination
   + `fetch_closed_orders` cross-check — `PENDING` confirm CCXT Bitget supports
   gapless order-id pagination).
2. **Balance-delta reconciliation** — `Σ(fill realized PnL − fees ± funding)` must
   equal `close_snapshot − open_snapshot` within tolerance `≤ Σ|fees| + ε`.
   Fills-must-explain-balance. Any unexplained delta → `INCOMPLETE`.
3. Snapshots are **signed** (Ed25519, reuse `attestation.py`) so the open/close
   anchors are themselves tamper-evident.

This defeats "sign only winners": omitting a losing fill breaks the balance-delta
reconciliation, so the epoch cannot reach `published`.

## `verify.py <statement.json>` contract

Standalone; **no import of any exchange `summary`**; re-computes from scratch:
- **onchain fills** → re-fetch `eth_getTransactionReceipt` from a public Base RPC,
  re-derive the fill by netting ERC-20 `Transfer` logs to/from the wallet (price
  cross-checked against the `Swap` event when present), confirm each `source_ref`.
- **cex fills** → verify the Ed25519 signature over `merkle_root` + snapshots;
  re-run contiguity + balance-delta; recompute metrics.
- Recompute every `fill_hash`, the `merkle_root`, and `metrics`. **Output:** `PASS`
  (identical root + metrics) or a precise per-field diff. Exit non-zero on any
  mismatch or on `trust_tier` inflation.

## Proposed module layout (new: `bot/proofofpnl/`)

- `csf.py` — schema, canonicalization, `fill_hash`, tier ordering (pure, no I/O).
- `ingest_cex.py` — `fetch_my_trades` + snapshots → `Fill[]` + open/close.
- `ingest_onchain_evm.py` — receipt → Transfer-netting → `Fill[]`.
- `reconcile.py` — contiguity + balance-delta → `published|INCOMPLETE`.
- `statement.py` — build epoch, Merkle (reuse `attestation.compute_merkle_root`),
  sign (reuse `attestation.sign_batch`).
- `verify.py` (repo root or `scripts/`) — the outsider re-computer.
- tests: `test_csf_determinism.py`, `test_cex_omission.py`,
  `test_verify_roundtrip.py`.

Reuse (per RESEARCH 5.5): `attestation.py` (Merkle+Ed25519), `audit_chain.py`
(SHA-256), `fetch_my_trades` (`live_executor.py:3062`), public RPCs
(`solana.js`/`wallet.js`). Extend `equity_basis.js`/`reports_parity` as the
metrics oracle.

## Acceptance → test mapping

| Acceptance (§Phase-1) | Test |
|---|---|
| (1) independent machine reproduces exact root+metrics, both source types | `test_verify_roundtrip.py` — build then `verify.py`, assert identical root; run twice, assert byte-identical |
| (2) any mutation/omission changes root or trips completeness | `test_csf_determinism.py` (mutation) + `test_cex_omission.py` (drop a fill → `INCOMPLETE`) |
| (3) on-chain epoch verifies with zero server trust | `test_verify_roundtrip.py::onchain` — `verify.py` re-fetches from public RPC only |
| (4) CEX `trust_tier` never over-claims | `test_csf_determinism.py::tier_min` — min-tier enforced; inflation attempt rejected |
| (5) no `summary` field in the path | grep-guard test: assert no `summary` key read in `bot/proofofpnl/**` + `verify.py` |

## Honest limitations (stated up front)

- The only real CEX data today is **3 spot round-trips** — v0 validates the
  pipeline, not a track record worth advertising. No metric from it may be shown
  as performance.
- Perp **funding** handling is `PENDING` (live proof is spot; funding=0 for spot).
- Solana re-derivation is **stubbed** (`UNVERIFIED`), not implemented.
- TEE tier is **deferred** (spec moving).
- First-mover ("nobody ships Proof-of-PnL") stays **UNVERIFIED** pending the §5.3
  competitor sweep — no such marketing claim until that's done.
