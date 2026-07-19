# Trade co-pilot — a deterministic second opinion

Before a user confirms a manual order, the co-pilot reviews it against the
objective facts a disciplined trader would check, and shows the verdict right in
the ticket. It **advises**; it never blocks or places anything — the risk gate
and (for live) the Authority Envelope remain the authorities.

## What it checks (`bot/core/trade_copilot.py`, pure)

- **Geometry** — stop/target on the correct side of entry for the direction
  (else `invalid`).
- **Reward:risk** — flags below 1.5; notes a strong ≥2.5.
- **Stop distance** — flags a too-tight (<0.3%, noise-out risk) or too-wide
  (>15%, large loss) stop.
- **Size vs equity** — flags margin >20% of equity (concentration); else notes
  the share.
- **Engine bias alignment** — when the caller supplies the engine's current lean
  on the symbol, flags a counter-bias trade.
- **Existing exposure** — notes stacking (same side) vs hedging (opposite side).

Returns `{verdict: clear|caution|invalid, score/100, rr, stop_pct, target_pct,
flags:[{level,msg}], notes:[...]}`. **Deterministic** — same input, same review;
no LLM, no network, so it always works and the arithmetic is verifiable. A caller
may layer an LLM one-liner on top, but the substance is real numbers.

## Surfaces

- **Gateway** `POST /gateway/trade/copilot` — enriches with the caller's paper
  equity for the size check; passes through engine bias / exposure when supplied.
- **Web** `POST /api/trade/copilot` (JWT) + a "🤖 Second opinion" button in the
  Trade ticket that renders the verdict, flags, and notes inline before you
  Review/confirm.

## Tests

`tests/test_trade_copilot.py` (C1–C6): geometry validation, reward:risk flag,
stop-distance flags, size vs equity, bias alignment + exposure notes, determinism
+ render. 14 green; `mypy` clean.
