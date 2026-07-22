"""
Boot-health helpers — env preflight + poller-restart predicate.

These are the pure core of two incident fixes: (1) a wiped-.env redeploy that
must be diagnosed in ONE loud line naming every missing secret, and (2) a
stalled Telegram poller that must self-heal instead of leaving the bot silent.
"""

from bot.core import boot_health as bh


class TestEnvPreflight:
    def test_all_present_is_clean(self):
        env = {k: "x" * 40 for k in bh.CRITICAL_ENV + bh.IMPORTANT_ENV}
        report = bh.env_preflight(env)
        assert report == {"critical": [], "important": []}
        assert "all critical and important secrets present" in bh.format_preflight(report)

    def test_missing_token_is_critical(self):
        env = {k: "x" * 40 for k in bh.IMPORTANT_ENV}  # everything but the token
        report = bh.env_preflight(env)
        assert report["critical"] == ["TELEGRAM_BOT_TOKEN"]
        assert report["important"] == []
        msg = bh.format_preflight(report)
        assert "MISSING CRITICAL" in msg and "TELEGRAM_BOT_TOKEN" in msg

    def test_names_every_missing_var_at_once(self):
        # The whole point: an env wipe is diagnosed in ONE line, not one-at-a-time.
        report = bh.env_preflight({})
        assert report["critical"] == ["TELEGRAM_BOT_TOKEN"]
        assert set(report["important"]) == set(bh.IMPORTANT_ENV)
        msg = bh.format_preflight(report)
        for name in bh.CRITICAL_ENV + bh.IMPORTANT_ENV:
            assert name in msg

    def test_blank_and_whitespace_count_as_missing(self):
        env = {"TELEGRAM_BOT_TOKEN": "   ", "BOT_SYNC_SECRET": ""}
        report = bh.env_preflight(env)
        assert "TELEGRAM_BOT_TOKEN" in report["critical"]
        assert "BOT_SYNC_SECRET" in report["important"]

    def test_important_missing_is_not_critical(self):
        env = {"TELEGRAM_BOT_TOKEN": "t" * 40}  # token present, web secrets gone
        report = bh.env_preflight(env)
        assert report["critical"] == []
        assert set(report["important"]) == set(bh.IMPORTANT_ENV)
        assert "degraded" in bh.format_preflight(report)

    def test_missing_env_preserves_order(self):
        assert bh.missing_env(["A", "B", "C"], {"B": "y"}) == ["A", "C"]


class TestPollerRestartPredicate:
    def test_restart_when_stopped_and_not_shutting_down(self):
        assert bh.poller_should_restart(running=False, stopping=False) is True

    def test_no_restart_while_healthy(self):
        assert bh.poller_should_restart(running=True, stopping=False) is False

    def test_no_restart_during_intentional_shutdown(self):
        # Even though it's not running, a deliberate stop must NOT be fought.
        assert bh.poller_should_restart(running=False, stopping=True) is False
        assert bh.poller_should_restart(running=True, stopping=True) is False
