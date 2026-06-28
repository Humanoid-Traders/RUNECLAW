# Per-Setup Expectancy (Phase C)

> **Status: BUILT & SHADOW-ONLY.** Default OFF (`SETUP_EXPECTANCY_ENABLED=false`).
> Until enabled, the nudge is computed and logged but **not applied** — the
> decision path is unchanged.

## Why

RUNECLAW already records every completed trade and `experience.get_similar_setups()`
surfaces the matching history — but only into the **LLM prompt**, never the
decision gate. This closes that loop: a setup's own track record nudges its
confidence. If longs on SOL in a RANGE regime have historically won 30%, shade
that setup down; if 70%, shade it up.

## How it works

`bot/learning/setup_expectancy.py` · `SetupExpectancy`:

1. **Aggregate.** Build a win-rate table keyed by `(symbol, regime, direction)`
   from completed `DecisionMemory` records (non-null `pnl_result`).
2. **Nudge.** `confidence_nudge()` returns a signed, bounded adjustment:
   `(win_rate - 0.5) · 2 · max_nudge · shrink`, where `shrink = n / (n + shrinkage)`
   tempers thin samples. Bounded to `[-max_nudge, +max_nudge]` (default ±0.05).
3. **Identity below evidence.** Zero nudge below `min_samples` (10) or for an
   unseen setup — it can only refine once there is history, never fabricate one.

## Safety

- **Default OFF / shadow-mode** — logs the would-be nudge, changes nothing.
- **Bounded** — at most ±0.05; it can only *shade* a confidence, never dominate
  the analyzer or risk gate.
- **Shrinkage** — a setup with few trades barely moves.
- **Fail-open** — any error in the hook leaves confidence untouched.
- **Never bypasses risk** — it only adjusts a confidence that still passes every
  risk-engine check.

## Operating it

- **Status / reload (admin):** `/calibration` shows both learning overlays
  (calibration + expectancy) and their mode; `/calibration refit` reloads them
  from history.
- **Enable:** set `SETUP_EXPECTANCY_ENABLED=true` once there's enough history and
  the shadow logs look sane.

## Wiring

- Hook: `bot/core/analyzer.py` — right after the confidence-calibration hook,
  before the min-confidence gate (`get_setup_expectancy().confidence_nudge(...)`).
- Flag: `CONFIG.analyzer.setup_expectancy_enabled`.
