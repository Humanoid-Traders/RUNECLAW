"""
Soft loss-streak limit stays strictly below the hard breaker (deep-audit low #48).

Risk check #9 soft-rejects a few losses before the hard circuit breaker
(max_consecutive_losses) trips. The old `max(2, hard - 2)` equalled or exceeded
the hard limit when it was configured <= 2, so the soft warning fired at/after
the breaker instead of before it. The helper now keeps soft strictly below hard
(default hard=5 → soft=3 unchanged).
"""

from bot.risk.risk_engine import RiskEngine

_soft = RiskEngine._soft_loss_streak_limit


class TestSoftLimit:
    def test_default_unchanged(self):
        assert _soft(5) == 3   # default config: warn at 3, hard-stop at 5

    def test_sane_values_two_below_and_floored(self):
        assert _soft(4) == 2
        assert _soft(3) == 2   # max(2, 1) but still < 3

    def test_low_hard_two_stays_below(self):
        # Was the bug: hard=2 → max(2,0)=2 == hard. Now strictly below.
        assert _soft(2) == 1
        assert _soft(2) < 2

    def test_hard_one_degenerate_equals(self):
        # Breaker fires at the first loss; soft can't be below 1.
        assert _soft(1) == 1

    def test_strictly_below_across_range(self):
        for hard in range(2, 21):
            assert _soft(hard) < hard, f"soft >= hard at {hard}"
            assert _soft(hard) >= 1

    def test_coerces_and_floors_bad_input(self):
        assert _soft(0) == 1      # floored to 1
        assert _soft(5.0) == 3    # float coerced
