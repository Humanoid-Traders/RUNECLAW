"""
i18n routing for the LLM commands (/setllm, /llmstatus, /llmreset, /llmtiers)
and the rate-limit message.

Only the genuine prose is routed — admin-only notices, the security warning,
the result cards, and the titles. The code/config blocks (the <pre> command
examples, provider lists, env-var hints, per-tier rows) deliberately stay
English. English byte-identical; zh users get translations.
"""

from bot.utils.i18n import _STRINGS, t

SEP = "─" * 16
L = "en"


def _eq(got, exp):
    assert got == exp, f"\n got={got!r}\n exp={exp!r}"


def test_admin_only_variants():
    _eq(f"\U0001f512 {t('admin_only_llm_set', L)}",
        "\U0001f512 <b>Admin only</b>\n\nChanging the LLM provider/key is restricted to admins.")
    _eq(f"\U0001f512 {t('admin_only_llm_reset', L)}",
        "\U0001f512 <b>Admin only</b>\n\nResetting the LLM provider/key is restricted to admins.")


def test_security_warning():
    _eq(f"⚠️ {t('llm_security_warning', L)}",
        "⚠️ <b>Security warning:</b> API keys should only be set in private chats with the bot. "
        "Your message containing the key will be deleted.")


def test_result_cards():
    _eq(f"✅ {t('llm_provider_updated', L, sep=SEP, provider='groq', model='default')}",
        f"✅ <b>LLM PROVIDER UPDATED</b>\n{SEP}\n- Provider: <code>groq</code>\n"
        "- Model: <code>default</code>\n- Status: 🟢 active")
    _eq(f"🔴 {t('llm_update_failed', L, msg='boom')}", "🔴 <b>LLM UPDATE FAILED</b>\n\nboom")
    _eq(f"🔄 {t('llm_config_reset', L, sep=SEP, msg='done')}",
        f"🔄 <b>LLM CONFIG RESET</b>\n{SEP}\n- done\n- Status: 🟢 using .env defaults")


def test_titles_and_rate_limit():
    _eq(f"🤖 {t('llm_status_title', L)}\n{SEP}\n", f"🤖 <b>LLM STATUS</b>\n{SEP}\n")
    _eq(f"🎯 {t('llm_tiers_title', L)}\n{SEP}\n", f"🎯 <b>Multi-Tier LLM Routing</b>\n{SEP}\n")
    _eq(f"⚠️ {t('rate_limit', L)}", "⚠️ Rate limit. Wait a moment.")


def test_keys_have_chinese():
    for k in ("admin_only_llm_set", "admin_only_llm_reset", "llm_security_warning",
              "llm_provider_updated", "llm_update_failed", "llm_status_title",
              "llm_config_reset", "llm_tiers_title", "rate_limit"):
        assert k in _STRINGS and _STRINGS[k]["zh"].strip(), k
