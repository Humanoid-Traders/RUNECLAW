"""
Learning auto-refit — keep the learned models fresh as outcomes accrue.

The three learners (confidence calibration, voter weights, per-setup expectancy)
are fitted on closed-trade history, but were only ever fitted on demand (a manual
/calibration refit). Left alone they go stale: the market moves on, but the curves
don't. This counts closed outcomes and re-fits all three every N trades, so the
models track recent reality automatically.

Safe by construction:
  - Refitting only updates the persisted learner state; it NEVER changes a trade
    decision on its own — each learner's *application* is still behind its own
    default-OFF flag. So an operator can leave auto-refit on at all times and the
    models simply stay warm, ready the moment they enable application.
  - Fail-open per learner: one learner failing to fit never blocks the others or
    the close path.
  - Gated by LEARNING_AUTO_REFIT_ENABLED (default OFF); the caller checks the flag.
"""

from __future__ import annotations

import logging

log = logging.getLogger("runeclaw.auto_refit")


class LearningAutoRefit:
    """Counts closed outcomes and refits the learners every ``interval`` trades."""

    def __init__(self, interval: int = 25) -> None:
        self.interval = max(1, int(interval))
        self._count = 0
        self._refits = 0

    def note_closed_trade(self, analyzer=None) -> bool:
        """Record one closed outcome. Returns True if a refit was triggered."""
        self._count += 1
        if self._count % self.interval != 0:
            return False
        self._refit_all(analyzer)
        self._refits += 1
        return True

    def _refit_all(self, analyzer=None) -> None:
        # Confidence calibration.
        try:
            from bot.learning.confidence_calibration import refit_and_save as _cal_refit
            _cal_refit()
            if analyzer is not None and hasattr(analyzer, "refresh_calibrator"):
                analyzer.refresh_calibrator()
        except Exception as exc:
            log.warning("auto-refit: calibration failed: %s", exc)
        # Voter weights.
        try:
            from bot.learning.voter_weights import refit_and_save as _vw_refit
            _vw_refit()
        except Exception as exc:
            log.warning("auto-refit: voter weights failed: %s", exc)
        # Per-setup expectancy (reload the singleton from the store).
        try:
            from bot.learning.setup_expectancy import get_setup_expectancy
            get_setup_expectancy(reload=True)
        except Exception as exc:
            log.warning("auto-refit: setup expectancy failed: %s", exc)
        log.info("Learning auto-refit complete (%d closed trades)", self._count)

    def summary(self) -> str:
        return (f"auto-refit: {self._count} closed trades, {self._refits} refits "
                f"(every {self.interval})")
