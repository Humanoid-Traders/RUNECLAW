"""
Paper-close outcomes also tag the real regime (deep-audit follow-up).

PR #158 routed the LIVE close path through _outcome_regime so outcomes carry the
analyzer's real regime (not the gated "UNKNOWN" _current_regime). The paper-close
write path added by PR #156 still used _current_regime; with both merged, it now
uses the same helper, so paper learning history is keyed by the real regime and
setup-expectancy can actually match it.
"""

import inspect

from bot.core.engine import RuneClawEngine


def test_paper_close_uses_outcome_regime():
    src = inspect.getsource(RuneClawEngine)
    # The paper-close record now tags the real regime via the shared helper…
    assert "market_regime=self._outcome_regime(c.asset)" in src
    # …and no longer reads the gated _current_regime in the paper-close block.
    assert "market_regime=str(getattr(self.risk, '_current_regime', '') or '')" not in src
