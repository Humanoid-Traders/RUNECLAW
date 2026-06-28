# Confidence Calibration (Phase A)

> **Status: BUILT & SHADOW-ONLY.** Default OFF
> (`CONFIDENCE_CALIBRATION_ENABLED=false`). Until enabled, the calibrated value
> is computed and logged but **not applied** — the decision path is unchanged.

## Why

RUNECLAW blends LLM + confluence into a `blended_confidence` in `[0,1]` that
gates every trade — and, for admins, auto-trades at `>= 0.85`. But that number is
the *model's opinion*, not a measured probability. Calibration fits a
**reliability curve** from the bot's own closed-trade history so a calibrated
`0.85` means **~85% historical win rate** — which makes the auto-trade threshold
mean what it says.

## How it works

`bot/learning/confidence_calibration.py` · `ConfidenceCalibrator`:

1. **Samples.** `(confidence, won)` pairs are pulled from completed
   `DecisionMemory` records (`confidence` + non-null `pnl_result`).
2. **Fit.** Confidence is binned; each bin's empirical win rate is computed,
   **shrunk toward the raw confidence** for thin bins (so sparse buckets stay
   near identity), then made **monotonic non-decreasing** via isotonic
   regression (Pool-Adjacent-Violators).
3. **Apply.** `calibrate(x)` interpolates the curve. Below `min_samples` (30) or
   when unfitted it is **exact identity** — calibration can only *refine* a
   confidence once there is evidence, never fabricate one.

## Safety

- **Default OFF**; shadow-mode logs the would-be delta and changes nothing.
- **Fail-open**: any error in the hook leaves confidence untouched.
- **Monotonic**: a higher raw confidence never maps to a lower calibrated value,
  so trade ordering is preserved and noise can't invert it.
- **Never bypasses risk**: it only adjusts a confidence that still passes through
  every risk-engine check.

## Operating it

- **Status / refit (admin):** `/calibration` shows the curve and mode;
  `/calibration refit` rebuilds it from closed-trade history and the live
  analyzer picks it up immediately.
- **Enable:** set `CONFIDENCE_CALIBRATION_ENABLED=true` once the curve is fitted
  on enough history and the shadow logs look sane.
- **Storage:** `data/learning/confidence_calibration.json`.

## Wiring

- Fit/persist: `confidence_calibration.refit_and_save()`.
- Hook: `bot/core/analyzer.py` — right after the final `blended_confidence` clamp
  and before the min-confidence gate (`_get_calibrator()` → `calibrate()`).
- Flag: `CONFIG.analyzer.confidence_calibration_enabled`.
