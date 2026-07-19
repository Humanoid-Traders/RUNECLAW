# Risk sentry — proactive watch over your standing book

Where the Authority Envelope **authorizes a new order** at confirm time, the
sentry **watches the book you already hold** and warns when the current state
drifts toward trouble. It makes the envelope proactive, not just a gate.

## What it flags (`bot/guardian/risk_sentry.py`, pure & deterministic)

- **Envelope drift** — a held symbol no longer in your allowlist, or on your
  blocklist (your envelope tightened under an open position), or a position over
  your per-trade cap.
- **Daily spend** — 24h notional at ≥80% of your cap (caution) or over it (warn),
  read from the same `AuthoritySpendLedger` the per-trade authorization uses.
- **Concentration** — one asset dominating gross exposure (>40% by default).
- **Correlated crowding** — 2+ correlated majors held the same side ("closer to
  one bet than several").
- **Book leverage** — gross exposure > 3× equity.

Returns `{alerts:[{level,category,symbol?,msg}], count, worst_level, gross_usd}`,
ranked worst-first. **Detection-only** — it emits alerts, never closes, resizes,
or places anything (that stays your confirm-gated action). No LLM, no network:
same posture → same alerts, every number verifiable.

## Surfaces

- **Gateway** `GET /gateway/sentry?telegram_id=…` — sources the user's open
  positions (notional = entry × qty), their bound envelope, 24h spend, and
  equity, then runs `assess`.
- **Web** `GET /api/sentry` (JWT) + a "Risk sentry" card in the Portfolio view
  showing the ranked flags with a worst-level badge.

## Tests

`tests/test_risk_sentry.py` (S1–S7): clean book is clear; envelope drift
(symbol no longer allowed / blocklisted held); over per-trade cap; daily-spend
near/over; concentration (needs >1 symbol); stacked correlated same-side; book
leverage + determinism + input-untouched. 12 green; `mypy` clean.
