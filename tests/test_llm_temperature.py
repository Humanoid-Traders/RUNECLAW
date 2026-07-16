"""Claude 5 temperature deprecation (live incident 2026-07-16).

Anthropic's Claude 5 family rejects an explicit `temperature` with
400 invalid_request_error ("`temperature` is deprecated for this model");
sending one from the analyzer took the whole brain down to the rule engine.
model_accepts_temperature is the single source of truth for which models
must have the parameter omitted.
"""

from bot.llm.provider import model_accepts_temperature


def test_claude_5_family_rejects_temperature():
    assert model_accepts_temperature("claude-sonnet-5") is False       # the live incident
    assert model_accepts_temperature("claude-sonnet-5-20260401") is False
    assert model_accepts_temperature("claude-fable-5") is False
    assert model_accepts_temperature("Claude-Sonnet-5") is False       # case-insensitive


def test_older_claude_models_still_accept_temperature():
    # Haiku 4.5 is the non-admin fallback — misclassifying it would silently
    # change its sampling. "4-5" must not be confused with the 5 family.
    assert model_accepts_temperature("claude-haiku-4-5-20251001") is True
    assert model_accepts_temperature("claude-opus-4-8") is True
    assert model_accepts_temperature("claude-3-5-sonnet-20241022") is True


def test_non_anthropic_and_empty_accept_temperature():
    assert model_accepts_temperature("gpt-4o") is True
    assert model_accepts_temperature("runeclaw-v6") is True
    assert model_accepts_temperature("") is True
    assert model_accepts_temperature(None) is True
