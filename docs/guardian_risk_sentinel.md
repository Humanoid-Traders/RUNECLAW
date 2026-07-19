# Guardian — Systemic Risk Sentinel

> The AI proposes. Deterministic controls authorize. The sentinel warns.

The Digital Twin asks *"what if the market moves against the book?"*; the Sentinel
asks the complementary, **static** question — *is the book structurally crowded
right now*, such that a single move would hit everything at once?

## A truthful scope note

RUNECLAW observes its **own** positions and market data, not the wider agent
population, so this is **not** a cross-agent systemic monitor — it cannot see what
other agents hold. What it *can* see, and what actually causes a one-move wipeout
of a single book, is **intra-book crowding**. The module is named for the risk
category it addresses (correlated, coordinated failure) while being honest about
the observable surface.

## What it flags — `bot/guardian/risk_sentinel.py`

`analyze(positions)` is a pure, deterministic function of the book snapshot (no
engine, no exchange, no clock, no network):

| Concern | Trips when |
| --- | --- |
| `correlated_concentration` | one correlation group (BTC / ETH / MEME / L2 …) holds ≥ 35% (medium) / 50% (high) of gross notional |
| `directional_crowding` | net one-sidedness `|long − short| / gross` ≥ 0.60 (medium) / 0.85 (high) |
| `correlated_cluster` | ≥ 3 (medium) / 4 (high) positions in the *same group and same direction* |
| `shared_liquidation_zone` | ≥ 3 same-direction positions whose isolated-margin liquidation prices sit within a 5%-wide adverse-move band |

It returns the gross notional, the net direction and bias, the dominant group and
its share, the list of triggered concerns, and a rolled-up risk level
(`none` / `low` / `medium` / `high`).

The `shared_liquidation_zone` check reuses the Digital Twin's canonical
`liquidation_move_frac` (one formula, no drift) and a sliding-window cluster
count over the sorted liquidation moves.

## The guarantees

1. **Pure + deterministic.** Trivially unit-testable; can never touch the trade
   path.
2. **Read-only telemetry.** The sentinel *warns*; it never proposes, blocks, or
   alters a trade.
3. **Fail-open.** A position missing usable inputs is skipped; any fault degrades
   to a calm "no concern" assessment.
4. **Off by default for the chain.** The assessment always computes on demand
   (e.g. the admin `/sentinel` command). A `SENTINEL` verdict is *sealed* to the
   tamper-evident chain only when `GUARDIAN_RISK_SENTINEL_ENABLED` is on.

## Where it runs

- **Telegram** — `/sentinel` (admin, read-only) shows the crowding assessment:
  concentration, net direction, the tripped concerns. Seals a `SENTINEL` verdict
  when the flag is on.
- **Engine** — `engine.run_risk_sentinel(user_id="")` normalises the live book
  (shared `_twin_positions`), runs the pure detector, and records the verdict
  when enabled.

## Enabling the chain record

```bash
# /sentinel works regardless — it's read-only telemetry. This flag only controls
# whether each run also seals a SENTINEL verdict to the evidence chain.
GUARDIAN_RISK_SENTINEL_ENABLED=true
```

Read from `CONFIG.risk` (frozen at import — restart to change) and mirrored in
`config/risk_manifest.yaml`.

## Relationship to the risk engine

The engine's risk gate already enforces *hard* correlation caps at entry (group
counts, same-direction limits). The Sentinel is the **observability** companion:
it doesn't gate anything, it makes the book's current crowding legible as a
single rolled-up verdict on the evidence chain — so an operator can see the shape
of the book's fragility, not just that individual entries passed their caps.
