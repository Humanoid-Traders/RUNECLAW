"""
Scalp time-stop window widened from 30 minutes to 2 hours.

Operator feedback: the scalp strategy's TIME_STOP threshold (30 min, no
profit -> force-close) was firing too fast for how the bot actually holds
scalp trades in practice. Widened to 2 hours (warn at the 1-hour mark,
keeping the same warn:close 1:2 ratio the other strategy types use).
"""

from bot.config import CONFIG, StrategyTypeConfig


class TestScalpTimeStopWindow:
    def test_scalp_close_threshold_is_two_hours(self):
        assert CONFIG.strategy_types.scalp_time_close_hours == 2.0

    def test_scalp_warn_threshold_is_one_hour(self):
        assert CONFIG.strategy_types.scalp_time_warn_hours == 1.0

    def test_get_time_close_hours_reflects_the_new_default(self):
        assert StrategyTypeConfig().get_time_close_hours("scalp") == 2.0

    def test_get_time_warn_hours_reflects_the_new_default(self):
        assert StrategyTypeConfig().get_time_warn_hours("scalp") == 1.0

    def test_other_strategy_types_are_unchanged(self):
        cfg = StrategyTypeConfig()
        assert cfg.get_time_close_hours("intraday") == 4.0
        assert cfg.get_time_close_hours("swing") == 48.0
        assert cfg.get_time_close_hours("position") == 168.0
