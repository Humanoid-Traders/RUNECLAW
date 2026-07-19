# Continuous Proof-of-PnL publishing (+ ERC-8004 anchor)

> "Don't trust the dashboard — verify the fills." Productized: the agent
> re-derives its track record from raw fills each epoch, seals it, and publishes
> a public-safe, re-verifiable statement — with the on-chain anchor's status
> kept honestly **UNVERIFIED** until a real tx confirms it.

## The pipeline

1. **Assemble** (`assemble_track_record`) — fills → a CSF statement (trust-tiered:
   `onchain_public` > `cex_tee_attested` > `cex_operator_signed`), an ERC-8004
   identity card, and the card's `anchor` plan. Public-safe (no exchange
   `summary`).
2. **Publish** (`bot/proofofpnl/publish.py`) — `build_publication(bundle,
   published_at_ts)` seals the bundle: a SHA-256 `publish_hash` over the
   canonical bundle, the `published_at` stamp, the lifted `anchor` (UNVERIFIED),
   the trust tier, and the reconciliation status. `publish_now(...)` persists it
   as the single latest publication (`PublicationStore`).
3. **Serve** — gateway `GET /gateway/proofofpnl` → web `GET /api/proofofpnl`
   (JWT): the latest publication + `verified` (re-derived hash + public-safety),
   `fresh`, and `age_seconds`.

## Discipline

- **Public-safe** — `build_publication` refuses a bundle carrying an exchange
  `summary` (same rule as `verify.py` / `assemble.is_public_safe`).
- **No fabricated proof** — the anchor stays `UNVERIFIED`; an incomplete epoch
  publishes as-is with its `INCOMPLETE` reconciliation. Nothing is dressed up.
- **Deterministic** — `published_at` is passed in, never wall-clock-read in the
  sealer, so the same bundle + stamp → the same `publish_hash` every time.
- **Re-verifiable by anyone** — `verify_publication` re-derives the hash and
  re-checks public-safety; catching any post-seal tampering. The fills
  themselves re-derive via `verify.py` section-7 (on-chain Transfer-netting).

## Operator: run it continuously

The sealer is the unit a scheduler calls each epoch. On the operator path
(where the fills + signed snapshots live):

```
# pseudocode — operator scheduler, once per epoch
bundle = assemble_track_record(ccxt_trades, account_ids=…, open_balance=…,
                               close_balance=…, agent_address=…, envelope=…)
publish_now(bundle, published_at_ts=int(now), epoch_seq=seq)
```

`PROOFOFPNL_PUBLICATION_PATH` sets where the latest publication persists (ensure
`data/` survives redeploys). The web then serves whatever the sealer last wrote,
with an honest freshness marker.

## The anchor (ERC-8004)

The identity card already names the Base-Sepolia `ReputationRegistry` it *would*
anchor to and carries `status: UNVERIFIED`. Submitting a real anchoring tx
(keys, gas, and a frozen Validation Registry ABI) is the remaining step and is
deliberately **not** faked here — the published statement claims only what is
true today: a re-derivable, signed, trust-tiered track record with a *designed*
on-chain anchor.

## Tests

`tests/test_proofofpnl_publish.py` (P1–P6): seals with a re-derivable hash;
refuses an unsafe bundle; anchor stays UNVERIFIED; incomplete epoch publishes
honestly; verify re-derives + catches tampering; freshness window; store
round-trip + `publish_now` persists. 7 green; `mypy` clean.
