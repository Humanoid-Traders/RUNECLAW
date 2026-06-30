"""
LLM async rate limiter uses a dedicated RPM cap, not the daily budget (#43).

The limiter's max_rpm was int(daily_call_limit / 24 * 60) — e.g. 500/24*60 ≈
1250 RPM (and 1250-limit configs hit ~3000) — so it never throttled and the
429-prevention it existed for was a no-op. It now reads CONFIG.llm.max_rpm, a
dedicated per-provider bound independent of the daily budget.
"""

from bot.config import CONFIG
from bot.core.analyzer import Analyzer


class TestRpmConfig:
    def test_dedicated_max_rpm_field_exists_with_sane_default(self):
        assert hasattr(CONFIG.llm, "max_rpm")
        assert 1 <= CONFIG.llm.max_rpm <= 600  # a real per-minute cap, not thousands

    def test_limiter_uses_config_rpm_not_daily_derived(self):
        a = Analyzer()
        assert a._rate_limiter._max_rpm == CONFIG.llm.max_rpm
        # And crucially NOT the old daily-derived value, which never throttled.
        daily_derived = int(CONFIG.llm.daily_call_limit / 24 * 60)
        assert a._rate_limiter._max_rpm < daily_derived
