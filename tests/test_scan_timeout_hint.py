"""
Interactive-scan timeout diagnostic hint (live incident 2026-07-11).

Right after the operator's model-id change, /latest_signal timed out twice in
a row with only "Scan is taking longer than usual" — no way to tell a failing
LLM (bad model id burning the fallback chain per symbol) from exchange
throttling. The timeout message now appends a brain-health hint from
Analyzer.llm_health().
"""
from types import SimpleNamespace

from bot.skills.telegram_handler import _scan_timeout_hint


def _analyzer(streak):
    return SimpleNamespace(llm_health=lambda: {
        "degraded_streak": streak, "degraded_seconds": streak * 30.0,
        "last_ok_seconds_ago": None})


def test_degraded_brain_named_as_likely_cause():
    hint = _scan_timeout_hint(_analyzer(5))
    assert "LLM brain degraded" in hint
    assert "5 analyses" in hint
    assert "/llmstatus" in hint


def test_healthy_brain_rules_out_the_llm():
    hint = _scan_timeout_hint(_analyzer(0))
    assert "healthy" in hint
    assert "exchange" in hint


def test_missing_analyzer_is_silent():
    assert _scan_timeout_hint(None) == ""
    assert _scan_timeout_hint(SimpleNamespace()) == ""  # no llm_health attr


def test_broken_health_is_silent():
    broken = SimpleNamespace(llm_health=lambda: (_ for _ in ()).throw(RuntimeError))
    assert _scan_timeout_hint(broken) == ""


def test_timeout_branch_uses_the_hint():
    import inspect
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._cmd_latest_signal)
    assert "_scan_timeout_hint" in src
