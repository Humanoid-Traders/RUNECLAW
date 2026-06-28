# Voter-Weight Learning (Phase B — instrument-first)

> **Status: INSTRUMENTED & DATA-ACCRUING. Not yet applied.**
> The confluence scorer now emits a named per-voter breakdown and decision
> records persist it; the learner is built and tested. **Application** of learned
> weights to the live confluence sum is a separate, flag-gated follow-up (B2).
> Until then, no decision is changed.

## Why

RUNECLAW scores ~35 confluence voters with **hand-tuned** weights ("guesses, not
backtested"). This program learns, from the bot's own completed trades, which
voters actually predict winning trades, and turns that into a small bounded
weight multiplier — so good voters count for a little more, poor ones a little
less.

## Instrument-first (this PR)

Real voter-weight learning needs per-voter vote data the bot didn't record (only
the aggregate confluence). So step one is to make that data exist, safely:

1. **Named breakdown.** `_score_confluence` now records each voter as
   `(name, vote, weight)` via an additive `aw()` helper. This is **byte-identical**
   — the votes/weights values and the returned confluence are unchanged (locked
   by `tests/test_voter_instrumentation.py` against fixed reference values and a
   300-case oracle used during development).
2. **Persistence.** The breakdown is attached to each `TradeIdea`
   (`idea._confluence_votes`) and saved on the decision record
   (`DecisionMemory.confluence_votes`), joined to the outcome by `paper_trade_id`.
3. **Learner.** `bot/learning/voter_weights.py` · `VoterWeightLearner` consumes
   `(votes, direction, won)` samples and produces a bounded per-voter multiplier.

## The learner

For each voter, count the trades where its vote **agreed** with the trade
direction, and the win rate among those. Compare to the base win rate:

```
edge   = agree_win_rate - base_rate            # [-1, 1]
shrink = n / (n + shrinkage)                    # thin voters barely move
mult   = clamp(1 + edge * gain * shrink, 0.5, 1.5)
```

Safety (mirrors `confidence_calibration.py`):

- **Bounded** multiplier in `[0.5, 1.5]` — influence shifts modestly, never flips.
- **Identity** (1.0) below `min_samples` (20 trades) / `min_voter_samples` (8
  agreeing trades) and for any unseen voter.
- **Shrinkage** toward 1.0 by per-voter sample count.

## Operating it

- **Status / refit (admin):** `/calibration` shows the voter-weight learner
  alongside calibration and expectancy; `/calibration refit` rebuilds it from
  history. Storage: `data/learning/voter_weights.json`.

## Next (B2 — application)

Wire the learned multiplier into the live confluence sum behind a default-OFF
flag, applied at the `aw()` helper: `weight *= learner.multiplier(name)`. Because
the multiplier is `1.0` until enough history accrues (and the flag is off by
default), enabling it is a gradual, observable change — and it can never move a
weight outside `[0.5, 1.5]` of its hand-tuned value.
