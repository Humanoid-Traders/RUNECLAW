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
4. **Serve publicly** — gateway `GET /gateway/public/proofofpnl` → web
   `GET /api/public/proofofpnl` (NO auth) → page `/proof`. Same sealed
   statement, no login. The publication is public-safe by construction, so
   serving it openly is deliberate, not a leak.

## Verify it yourself — in the browser

`/proof` (`app/public/proof.html`) re-derives the `publish_hash` **in the
visitor's own browser** and re-checks public-safety, so a prospective user
trusts math on their machine, not our word. This is exact because:

* the sealer hashes `json.dumps(bundle, sort_keys=True, separators=(",",":"),
  ensure_ascii=False)` and the CSF invariant guarantees every number in the
  bundle is already a string — so a recursive key-sort + `JSON.stringify` +
  `crypto.subtle.digest('SHA-256')` reproduces the same bytes;
* `tests/test_proofofpnl_publish.py::test_p7` runs the page's actual
  `canonical()` under node and asserts the hash matches the Python sealer, so
  the two can't silently drift.

The page shows the sealed hash, the browser-re-derived hash, the public-safety
result, the server's independent re-verification, and the anchor's honest
UNVERIFIED status — with a copyable JSON statement and off-page re-verification
steps.

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

## Operator: run it continuously (SHIPPED)

The scheduler is now wired into the engine — the feed is no longer empty in
production. `bot/proofofpnl/scheduler.ProofOfPnLPublisher` is the cadenced,
fail-safe unit; the engine's main loop calls it after each successful tick via
`RuneClawEngine._maybe_publish_proofofpnl`:

```
# engine loop, once per tick (bot/core/engine.py)
publisher = get_operator_publisher()          # cached, env-configured
if publisher.should_publish(now):             # enabled AND cadence due
    trades = await live_executor._get_exchange().fetch_my_trades(...)  # REAL fills
    publisher.publish(now, trades, range_start=…, range_end=…)
    # -> assemble_track_record(...) -> publish_now(bundle, published_at_ts=now)
```

Discipline preserved end-to-end:

* **DEFAULT-OFF** — nothing publishes unless `PROOFOFPNL_PUBLISH_ENABLED=1`.
* **FAIL-OPEN** — every step (fetch, assemble, seal) is wrapped; a bad epoch
  logs and skips. Publishing can never break or block the trading loop.
* **LIVE-ONLY** — runs only when `CONFIG.is_live()` and a live executor exists;
  paper mode has no verifiable fills to publish.
* **HONEST INCOMPLETE** — balances are omitted in this slice, so the epoch
  reconciles to `INCOMPLETE` (never dressed up). Signed open/close snapshot
  anchoring is the next slice.

Env (see `.env.example`): `PROOFOFPNL_PUBLISH_ENABLED`,
`PROOFOFPNL_PUBLISH_INTERVAL_S` (default hourly, floored 60s),
`PROOFOFPNL_LOOKBACK_DAYS` (default 30), `PROOFOFPNL_AGENT_ADDRESS` (optional
identity card), `PROOFOFPNL_ACCOUNT_ID`, and `PROOFOFPNL_PUBLICATION_PATH`
(where the latest publication persists — ensure `data/` survives redeploys).
The web then serves whatever the sealer last wrote, with an honest freshness
marker.

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
