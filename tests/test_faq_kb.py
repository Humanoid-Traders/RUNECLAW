"""INCIDENT: the public landing chat must always respond — and never leak.

The website's landing-page chat surfaced a raw internal error to anonymous
visitors ("No LLM configured. Use /setllm … add LLM_API_KEY to .env."), which is
both a broken first impression and a config leak (F-15). The five starter
questions now get instant, deterministic, §4-safe answers from the built-in FAQ,
and the no-model path serves a friendly public fallback — never internal config.
"""
import inspect

from bot.core import faq_kb

# The five landing-page starter questions, verbatim.
_STARTERS = [
    "What is RUNECLAW?",
    "How does it manage risk?",
    "What is a liquidity sweep?",
    "How does leverage work?",
    "Which exchanges are supported?",
]


def test_every_starter_question_gets_a_canned_answer():
    for q in _STARTERS:
        ans = faq_kb.faq_answer(q)
        assert ans and len(ans) > 40, f"no FAQ answer for {q!r}"


def test_matching_is_case_and_punctuation_insensitive():
    assert faq_kb.faq_answer("what is runeclaw") is not None
    assert faq_kb.faq_answer("WHICH EXCHANGES ARE SUPPORTED") is not None
    assert faq_kb.faq_answer("how does leverage work???") is not None


def test_free_form_questions_do_not_match_and_reach_the_llm():
    for q in ["should I use leverage on SOL right now?",
              "is BTC bullish today?", "hello", "", "gm"]:
        assert faq_kb.faq_answer(q) is None, f"{q!r} should fall through to the LLM"


def test_faq_and_fallback_are_section4_safe_and_leak_free():
    blob = ("".join(a for q in _STARTERS for a in [faq_kb.faq_answer(q)])
            + faq_kb.public_fallback()).lower()
    assert "$" not in blob, "no dollar amounts on the public chat surface (§4)"
    for leak in (".env", "setllm", "api_key", "llm_api_key", "add a key"):
        assert leak not in blob, f"public reply leaks internal config: {leak}"


def test_public_fallback_guides_without_leaking():
    fb = faq_kb.public_fallback()
    assert "RUNECLAW" in fb and "Sign in" in fb
    assert "What is RUNECLAW?" in fb          # points to the starter topics


def test_chat_path_short_circuits_faq_and_never_leaks():
    from bot.skills import telegram_handler as th
    src = inspect.getsource(th)
    # The old leaky public string is gone entirely.
    assert "add LLM_API_KEY to .env" not in src
    assert "No LLM configured. Use /setllm to set a provider" not in src
    # The FAQ short-circuit + non-leaky fallback are wired into the chat path.
    assert "faq_answer" in src and "public_fallback" in src
