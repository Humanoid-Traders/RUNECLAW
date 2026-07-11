"""
LLM-brain-offline alerting (live incident: free-tier quota exhausted).

When every LLM provider fails for N consecutive theses, the analyzer silently
falls to the rule engine and keeps trading blind. This was invisible to the
operator. The analyzer now tracks a consecutive-fallback streak, and the
proactive monitor fires a CRITICAL alert once it crosses the threshold (and an
INFO once a provider answers again). Rule-engine-by-design never trips it —
only a real all-provider failure advances the streak.
"""
from types import SimpleNamespace

import bot.core.proactive_monitor as pm
from bot.core.analyzer import Analyzer
from bot.core.proactive_monitor import ProactiveMonitor


# ── analyzer streak logic ────────────────────────────────────────────
def _fresh_analyzer():
    """A bare Analyzer with only the health-tracking state initialized —
    avoids the heavy real __init__ (network clients, caches)."""
    a = Analyzer.__new__(Analyzer)
    a._llm_degraded_streak = 0
    a._llm_last_ok_monotonic = 0.0
    a._llm_degraded_since_monotonic = 0.0
    a._llm_last_error = ""
    return a


class TestAnalyzerHealth:
    def test_degraded_advances_streak(self):
        a = _fresh_analyzer()
        a._note_llm_degraded()
        a._note_llm_degraded()
        assert a.llm_health()["degraded_streak"] == 2
        assert a.llm_health()["degraded_seconds"] >= 0

    def test_ok_resets_streak(self):
        a = _fresh_analyzer()
        a._note_llm_degraded()
        a._note_llm_degraded()
        a._note_llm_ok()
        h = a.llm_health()
        assert h["degraded_streak"] == 0
        assert h["degraded_seconds"] == 0.0
        assert h["last_ok_seconds_ago"] is not None

    def test_healthy_baseline_reports_zero(self):
        assert _fresh_analyzer().llm_health()["degraded_streak"] == 0

    def test_failure_reason_captured_and_cleared(self):
        """Live incident: /llmstatus showed the streak without the CAUSE. The
        primary provider's error is now captured (truncated) and cleared on
        the next success."""
        a = _fresh_analyzer()
        a._note_llm_degraded("Error code: 401 - invalid x-api-key")
        h = a.llm_health()
        assert h["last_error"] == "Error code: 401 - invalid x-api-key"
        a._note_llm_degraded("x" * 500)          # truncated to 200
        assert len(a.llm_health()["last_error"]) == 200
        a._note_llm_ok()
        assert a.llm_health()["last_error"] == ""


# ── monitor alert ────────────────────────────────────────────────────
def _patch_cfg(monkeypatch, enabled=True, min_streak=3):
    # CONFIG.analyzer is a frozen dataclass — swap the whole module-level CONFIG
    # for a stand-in whose .analyzer carries just the fields the check reads.
    monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(analyzer=SimpleNamespace(
        llm_degraded_alert_enabled=enabled,
        llm_degraded_alert_min_streak=min_streak)))


def _mon(streak, monkeypatch, enabled=True, min_streak=3):
    _patch_cfg(monkeypatch, enabled=enabled, min_streak=min_streak)
    analyzer = SimpleNamespace(
        llm_health=lambda: {"degraded_streak": streak,
                            "degraded_seconds": streak * 60.0,
                            "last_ok_seconds_ago": None})
    engine = SimpleNamespace(analyzer=analyzer)
    return ProactiveMonitor(engine)


class TestMonitorAlert:
    def test_alerts_when_streak_crosses_threshold(self, monkeypatch):
        a = _mon(3, monkeypatch)._check_llm_degraded()
        assert len(a) == 1
        assert a[0].alert_type == "LLM_DEGRADED"
        assert a[0].severity == "CRITICAL"

    def test_quiet_below_threshold(self, monkeypatch):
        assert _mon(2, monkeypatch)._check_llm_degraded() == []

    def test_fires_once_then_quiet_while_degraded(self, monkeypatch):
        mon = _mon(5, monkeypatch)
        first = mon._check_llm_degraded()
        second = mon._check_llm_degraded()      # still degraded, same state
        assert len(first) == 1 and second == []

    def test_recovery_emits_restored_once(self, monkeypatch):
        mon = _mon(5, monkeypatch)
        mon._check_llm_degraded()               # go degraded
        # Now the analyzer recovers: swap in a healthy health() reading.
        mon.engine.analyzer.llm_health = lambda: {
            "degraded_streak": 0, "degraded_seconds": 0.0,
            "last_ok_seconds_ago": 1.0}
        rec = mon._check_llm_degraded()
        assert len(rec) == 1 and rec[0].alert_type == "LLM_RESTORED"
        assert mon._check_llm_degraded() == []  # stays quiet after recovery

    def test_disabled_flag_silences(self, monkeypatch):
        assert _mon(9, monkeypatch, enabled=False)._check_llm_degraded() == []

    def test_missing_analyzer_is_quiet(self, monkeypatch):
        _patch_cfg(monkeypatch, enabled=True)
        mon = ProactiveMonitor(SimpleNamespace())
        assert mon._check_llm_degraded() == []
