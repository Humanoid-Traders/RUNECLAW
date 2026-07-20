# Stop-loss local/exchange drift — fixed (M-02, completed)

## The bug (silent over-report of protection)

Every place that **tightens** a stop advanced the *local* `pos.stop_loss` and
persisted it **before** — and independent of — the exchange actually accepting
the tighter stop. `_update_exchange_sl` is best-effort: on failure it keeps the
**looser old stop** live on the exchange (correct — no protection gap) but
returned nothing, so the caller had already written the tighter value locally.

Result: the position's persisted / displayed / locally-enforced stop claimed
**more protection than the exchange was holding**. While the monitor loop is
alive the local check papers over it, but during any downtime (restart, crash,
network partition) the exchange stop is the *only* thing protecting the
position — and it was the looser one. That is exactly the "don't trust the
dashboard" failure RUNECLAW exists to prevent, on our own surface.

The trailing path is the sharp edge: it ratchets on every tick over the whole
life of a winner, so a single silent exchange failure leaves a persistent gap.

## The fix

`_update_exchange_sl` now returns `bool` — `True` only when a new SL order is
**confirmed live on the exchange**, `False` when placement failed and the old
(looser) stop is still what the exchange holds. Every tightening caller advances
the local stop **only** on `True`:

- **Trailing stop** (`live_executor.py`, `check_positions`): call the exchange
  first; on success write `pos.stop_loss`, persist, audit `UPDATED`, emit the
  mind-stream event. On failure, keep the old local stop and audit
  `EXCHANGE_UPDATE_FAILED` (WARNING) — protection preserved at the level the
  exchange actually holds, never over-reported.
- **Partial-TP ladder** (`_run_partial_tp`): the `move_sl` / post-close SL
  tighten ratchets local state only after the exchange confirms (`_would_tighten`
  gates the exchange call; `_ratchet_sl` runs only on `ok`).
- **Pyramid SL→breakeven** (`engine.py`,
  `_pyramid_move_existing_sl_to_breakeven`): the winner's local stop moves to
  breakeven only after the exchange confirms; otherwise the original stop is
  preserved and the failure is audited.

Invariant restored: **local `pos.stop_loss` is never tighter than what the
exchange is enforcing.** Under-protection is now always honestly displayed;
over-protection can no longer be silently claimed.

## Tests

`tests/test_trailing_sl_update_safety.py`: `_update_exchange_sl` returns `True`
on confirmed placement (v3 + classic) and `False` on failure; a source-invariant
test asserts the trailing block writes `pos.stop_loss` only inside the
`if sl_applied:` success branch. `tests/test_partial_tp_wiring.py` and
`tests/test_pyramid_rollback_and_adopt_toctou.py` updated so their stubs return
`True` (the confirmed-exchange case). Full executor/engine suite green.
