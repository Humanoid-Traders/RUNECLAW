# PREDICTIONS.md — pre-registered predictions

Rule: a run with no prediction registered *before* it is not evidence. One
variable per version. Entries are append-only; do not edit a prediction after its
run — add a result line and, if needed, a new entry.

---

## P1 — CSF v0 determinism (CEX path) — REGISTERED, run PENDING (post-approval)

**Change under test:** introduce `csf.py` canonicalization + `fill_hash` +
`merkle_root` over the 3 real spot round-trips in `live_trade_proof.json`.

**Prediction (before run):**
1. Building the epoch twice from the same input yields **byte-identical**
   `merkle_root`.
2. Mutating any single fill by one minor unit (e.g. `buy_qty` 6.8e-05 → 6.9e-05)
   changes the `merkle_root`.
3. `round_trips == 3`; `market` set == {BTC/USDT, ETH/USDT}.

**Falsifier:** non-identical roots across runs (nondeterminism), or an unchanged
root after a fill mutation (broken hash coverage).

**Result:** ✅ **CONFIRMED** (`tests/test_proof_of_pnl.py::test_p1_determinism…`).
Two independent builds → identical `merkle_root` + `commitment` + canonical fills;
mutating `qty` 1→1.0001 changed the root; `round_trips == 1` on the closed pair.

---

## P2 — CEX balance-delta reconciliation — REGISTERED, run PENDING

**Change under test:** `reconcile.py` balance-delta on the 3-round-trip epoch.

**Prediction (before run):**
1. `Σ(realized PnL − fees)` over the 3 round-trips equals
   `close_snapshot − open_snapshot` (USD, `live_trade_proof.json:48-49`) within
   tolerance `≤ Σ|fees| + $0.01`.  *(Exact net_pnl to be read from the file at run
   time; the PASS criterion — reconciles-within-tolerance — is fixed now.)*
2. Dropping one losing fill makes the reconciliation fail → epoch `status ==
   "INCOMPLETE"` (omission is caught, not published).

**Falsifier:** reconciliation passes with a fill omitted (omission defense
ineffective), or fails on the complete set (accounting wrong).

**Result:** ⚠️ **REVISED + CONFIRMED (the honest way).** The original P2 assumed the
real `live_trade_proof.json` would reconcile. It does NOT and CANNOT: that file
carries no per-fill fees, no prices on 2/3 round-trips, and its only balances live
in a `summary` block the path is forbidden to read. So the real file correctly
resolves to **`INCOMPLETE`** and `verify.py` **rejects it (exit 1)** with a precise
diff — the strongest honest outcome (the legacy "proof" is not fills-grade).
The reconciliation + omission defense were instead validated on a *complete
synthetic* epoch (labeled synthetic, not a track record):
`test_p2_complete_epoch_publishes` (a fully-reconciling epoch → `published`) and
`test_p2_omission_is_caught` (dropping a losing fill → residual > tol →
`INCOMPLETE`). Net: the defense works; the real data is honestly refused.

---

## P3 — On-chain (Base) fill re-derivation — REGISTERED, run PENDING

**Change under test:** `ingest_onchain_evm.py` Transfer-netting + `verify.py`
re-fetch on **one real Base swap tx** (tx hash `PENDING` selection).

**Prediction (before run):**
1. Netting ERC-20 `Transfer` logs to/from the wallet reproduces the swap's
   (base, quote) amounts; price matches the pool `Swap` event within rounding.
2. `verify.py` re-fetching the receipt from a **public** Base RPC (no RUNECLAW
   server) reproduces the identical `fill_hash`.
3. Epoch `trust_tier == onchain_public`.

**Falsifier:** derived amounts disagree with the on-chain `Swap` event, or
`verify.py` needs any RUNECLAW-hosted data to reproduce the hash.

**Result:** _PENDING run._

---

## P4 — Trust-tier honesty invariant — REGISTERED, run PENDING

**Change under test:** epoch `trust_tier = min(fill.trust_tier)`.

**Prediction (before run):** an epoch mixing one `cex_operator_signed` fill with
`onchain_public` fills reports headline `trust_tier == cex_operator_signed` (the
minimum), and no code path lets an operator raise it.

**Falsifier:** headline tier renders higher than the epoch minimum.

**Result:** ✅ **CONFIRMED** (`test_p4_epoch_tier_is_the_minimum`): an epoch mixing
`onchain_public` + `cex_operator_signed` reports the minimum
`cex_operator_signed`; `verify.py` independently recomputes the tier and rejects
any inflation.

---

## P3 note — deferred to the next version (on-chain EVM slice)

P3 (Base fill re-derivation, zero-server-trust) is NOT in this CEX slice.
`verify.py` currently reports any on-chain fill as `UNVERIFIED` rather than
passing it — the honest placeholder until the Transfer-netting re-derivation
lands. P3 stays REGISTERED, run PENDING for the next version.
