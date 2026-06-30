"""
Deep-audit flag status report (/flags command backing).

flag_status reads the effective config (CONFIG + env) and reports which gated
fixes are ON/OFF, grouped by how safe they are to enable. Pure read; backs the
/flags Telegram command and docs/FLAG_ACTIVATION.md.
"""

import bot.core.flag_status as fs
from bot.core.flag_status import audit_flag_report, format_flag_report


def _flat(report):
    return {env: on for _, items in report for env, _, on in items}


class TestReportStructure:
    def test_groups_and_items_present(self):
        report = audit_flag_report()
        titles = [t for t, _ in report]
        assert any("recommended ON" in t for t in titles)
        assert any("backtest" in t.lower() for t in titles)
        assert any("Learning" in t for t in titles)
        flat = _flat(report)
        # A representative flag from each tier is catalogued.
        for env in ("WS_IDLE_TIMEOUT_SEC", "OF_TIME_BARS_ENABLED",
                    "LEARN_FROM_PAPER_CLOSES", "DAILY_LOSS_BREAKER_AUTORESET",
                    "LLM_FALLBACK_COST_ACCOUNTING"):
            assert env in flat

    def test_all_values_are_bool(self):
        for _, items in audit_flag_report():
            for env, label, on in items:
                assert isinstance(env, str) and isinstance(label, str)
                assert isinstance(on, bool)

    def test_default_off_flags_report_off(self, monkeypatch):
        # Tier-4 judgment/sizing flags still ship default-OFF (config-backed rows).
        for env in ("REGIME_SIZING_ENABLED", "DROP_UNCLOSED_CANDLE_ENABLED",
                    "DAILY_LOSS_BREAKER_AUTORESET"):
            monkeypatch.delenv(env, raising=False)
        flat = _flat(audit_flag_report())
        for env in ("REGIME_SIZING_ENABLED", "DROP_UNCLOSED_CANDLE_ENABLED",
                    "DAILY_LOSS_BREAKER_AUTORESET"):
            assert flat[env] is False

    def test_default_on_guards_report_on(self):
        # REST/WS staleness guards default to a positive value → ON.
        flat = _flat(audit_flag_report())
        assert flat["LIVE_TICKER_MAX_AGE_SEC"] is True
        assert flat["WS_MAX_TICK_AGE_SEC"] is True
        # Tier 1 safety/observability flags are now enabled by default.
        assert flat["WS_IDLE_TIMEOUT_SEC"] is True
        assert flat["VERIFY_CLASSIC_SLTP_ON_RESTART"] is True
        # Tier 3 learning flags are now enabled by default (config-backed rows).
        assert flat["LEARN_FROM_PAPER_CLOSES"] is True
        assert flat["CONFIDENCE_CALIBRATION_ENABLED"] is True


class TestEnvReflection:
    def test_env_only_flag_reflects_env(self, monkeypatch):
        monkeypatch.delenv("OF_TIME_BARS_ENABLED", raising=False)
        assert _flat(audit_flag_report())["OF_TIME_BARS_ENABLED"] is False
        monkeypatch.setenv("OF_TIME_BARS_ENABLED", "true")
        assert _flat(audit_flag_report())["OF_TIME_BARS_ENABLED"] is True

    def test_env_on_helper_accepts_truthy_spellings(self):
        import os
        for v in ("1", "true", "YES", "On"):
            os.environ["X_FLAG_TEST"] = v
            assert fs._env_on("X_FLAG_TEST") is True
        os.environ["X_FLAG_TEST"] = "0"
        assert fs._env_on("X_FLAG_TEST") is False
        del os.environ["X_FLAG_TEST"]


class TestFormatter:
    def test_renders_counts_and_marks(self):
        text = format_flag_report()
        assert "Deep-audit flags" in text
        assert "ON)" in text  # the on/total counter
        assert "✅" in text or "⬜" in text
        assert "FLAG_ACTIVATION.md" in text
