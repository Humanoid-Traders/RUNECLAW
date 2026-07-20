# Public verifiable leaderboard (Proof-of-PnL)

> The one leaderboard a memecoin bot can't fake: every row is an anonymous,
> opted-in agent ranked by its **cryptographically re-verifiable** record.

This is slice **A1** — the pure ranking core + a handle-keyed registry
(`bot/proofofpnl/leaderboard.py`). It gathers no fills and publishes nothing;
it only ranks whatever sealed, public-safe publications have been registered.

## The two hard rules

1. **Re-verified or excluded.** Every entry's `publish_hash` is re-derived and
   its public-safety re-checked (`verify_publication`) before it can rank. A
   tampered or unsafe publication is dropped — at both the ranking layer
   (`build_row`) and the registry boundary (`LeaderboardRegistry.put`).
2. **No dollar magnitudes, ever.** Ranking uses only SIZE-AGNOSTIC, fills-derived
   metrics: **profit factor** (primary; `'inf'` for a flawless record sorts
   top), tie-broken by **round-trips** then **Sharpe**. `net_pnl`, `fees`, and
   `max_dd` carry account-size information and never appear on a row. This
   mirrors the existing (paper-stake) leaderboard's privacy contract, now backed
   by sealed statements.

A row carries: `rank`, `handle`, `profit_factor`, `sharpe`, `round_trips`,
`trust_tier`, `reconciliation`, `publish_hash`, `published_at`, `verified` — and
nothing that reveals balance.

## API

* `rank_entries(entries, *, min_round_trips=1, limit=50)` — pure; takes
  `{handle, publication}` items, returns the ranked anonymous board.
* `LeaderboardRegistry` — thread-safe JSON store, `handle → latest publication`,
  kept separate from the single-operator `PublicationStore` so the board never
  disturbs the `/proof` feed. `put` / `remove` (opt-out) / `all_entries` /
  `ranked`. `PROOFOFPNL_LEADERBOARD_PATH` sets the file.

## Consent

Appearing is opt-in and revocable, reusing the existing anonymous
`leaderboard_handle` model (never an email). A member is on the board only
because they chose a handle and consented to publish their sealed record;
opt-out drops them (`remove`).

## Slice plan

* **A1 ✅** — pure ranking core + registry + tests. Foundation, no producer.
* **A2 ✅ (this)** — the board goes live, operator first. The operator opts in by
  setting `PROOFOFPNL_LEADERBOARD_HANDLE`; each published epoch then also
  registers the sealed statement (`engine._maybe_publish_proofofpnl`), and the
  gateway serves the ranked, anonymous, re-verifiable board with **no auth** at
  `GET /gateway/public/leaderboard` (`_leaderboard_payload`). Default OFF (no
  handle → no registration); fail-open; never touches trading. Per-user opt-in
  publishing (each member seals their own fills) is a later slice — it needs the
  Node↔engine handle bridge and per-user fill-gathering, scoped separately.
* **A3** — public `/leaderboard` page + Node relay, each row re-verifiable in the
  visitor's own browser (mirrors the `/proof` serving chain).

## Tests

`tests/test_proofofpnl_leaderboard.py` (LB1-LB8): ranks by profit factor;
no dollar field ever surfaces; a tampered publication is excluded; missing /
duplicate handles dropped + deduped; `min_round_trips` filter; `'inf'` sorts
top; registry round-trip + refusal of unverifiable/empty; env-path singleton.
8 green; ruff + mypy clean.
