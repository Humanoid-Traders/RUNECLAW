# Guardian — Portfolio Digital Twin

> The AI proposes. Deterministic controls authorize. The twin foresees.

The Flight Recorder proves what *happened*; the Intent Compiler bounds what the
agent is *allowed* to do; the Firewall guards what comes *in*. The Digital Twin
answers the remaining question — **what would happen to the current book if the
market moved against it** — before the market gets the chance to.

`bot/guardian/digital_twin.py` is a **pure, deterministic** stress simulator (no
engine, no exchange, no clock, no network). It takes a snapshot of the open
positions and an account equity, applies a catalogue of parametric price shocks,
and for each one computes the projected P&L, the projected account drawdown, and
exactly which positions would be **liquidated**.

## The scenarios

| Scenario | Shock |
| --- | --- |
| `flash_crash` | −20% across the board |
| `severe_crash` | −35% correlated tail |
| `alt_capitulation` | BTC −10%, ETH −12%, everything else −35% |
| `short_squeeze` | +20% across the board (hits short books) |

A scenario is a set of fractional price shocks keyed by correlation group (from
the risk engine's `_correlation_group`), with `*` as the default for unlisted
groups. One price move models both sides — a long loses and a short gains on the
same shock, automatically.

## The math (stated assumptions)

- **Liquidation** is modelled as isolated-margin wipeout: the adverse price move
  from entry that exhausts the position's margin is `(1 - maintenance) / leverage`
  (default maintenance 0.5%). A 10× position liquidates on a ~9.95% adverse move.
  Cross, hedged, or portfolio-margin books liquidate *later* than this, so the
  twin is deliberately **conservative** — it flags fragility early, never late.
- **P&L** per position is `(shocked_price − entry) × qty` for a long, mirrored
  for a short. The twin shocks from each position's *entry* (not a live mark), so
  the result is reproducible and reflects the shock's impact on committed
  margin — it is a **risk estimate**, not a live-mark valuation.
- **Per-position fragility** — independent of any scenario — is the adverse %
  move each position sits from its own liquidation. Smaller = more fragile.

## The guarantees

1. **Pure + deterministic.** Every function is a pure function of its inputs, so
   the whole twin is trivially unit-testable and can never touch the trade path.
2. **Read-only foresight.** The twin never proposes, blocks, or alters a trade.
   It only *describes* the book's fragility.
3. **Fail-open.** A position missing usable inputs is skipped, never fatal; a
   fault anywhere degrades to an empty "calm" report. Foresight can never break a
   caller.
4. **Off by default for the chain.** The simulation is safe read-only foresight
   and always computes on demand (e.g. the admin `/twin` command). A `TWIN`
   verdict is *sealed* to the tamper-evident chain only when
   `GUARDIAN_DIGITAL_TWIN_ENABLED` is on.

## Where it runs

- **Telegram** — `/twin` (admin, read-only) stress-tests the live book and shows
  the per-scenario projected drawdown, liquidations, and the most fragile
  positions. When the flag is on it also seals a `TWIN` verdict.
- **Engine** — `engine.run_digital_twin(user_id="")` normalises the live book
  (`_twin_positions`), pulls account equity, runs the pure simulator, and records
  the verdict when enabled. Operator book by default; a specific user's executor
  when `user_id` is given.

## Enabling the chain record

```bash
# The /twin command works regardless — it's read-only foresight. This flag only
# controls whether each run also seals a TWIN verdict to the evidence chain.
GUARDIAN_DIGITAL_TWIN_ENABLED=true
```

Read from `CONFIG.risk` (frozen at import — restart to change) and mirrored in
`config/risk_manifest.yaml`.

## Design stance

Pure and dependency-light (stdlib only), so the simulator is trivially testable
(`tests/test_digital_twin.py`) and can never take down a chat, a command, or a
trade. It is a *describer* of risk, not a controller of it — the authorising
decisions stay with the deterministic risk gate and the Intent Compiler; the twin
just makes the book's hidden fragility legible before it bites.
