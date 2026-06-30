"""
Shadow-mode calibration / setup-expectancy deltas are observable (#36).

When confidence calibration or the setup-expectancy nudge is OFF (the default),
the analyzer still computes the would-be value and logs the delta so its effect
can be evaluated before enabling. Those deltas went to logger.debug — which has
no handler — so shadow mode was effectively invisible. They are now emitted on
the same visible audit channel the APPLIED path uses, tagged result="SHADOW".
"""

import inspect

from bot.core.analyzer import Analyzer


class TestShadowDeltasAudited:
    def _src(self):
        return inspect.getsource(Analyzer.analyze)

    def test_no_invisible_debug_shadow_logs(self):
        src = self._src()
        assert 'logger.debug("Calibration shadow' not in src
        assert 'logger.debug("Setup-expectancy shadow' not in src

    def test_both_shadows_emit_visible_audit(self):
        src = self._src()
        # Both shadow branches now audit with a SHADOW result on the trade channel.
        assert 'action="confidence_calibration", result="SHADOW"' in src
        assert 'action="setup_expectancy", result="SHADOW"' in src
        assert src.count('result="SHADOW"') >= 2
