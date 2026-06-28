"""
i18n routing for the account commands (/link, /unlink, /me, /sync) in
user_middleware.py.

These live in the SQLite-backed middleware, which has no UserContext access to
the JSON UserStore where the language pref lives — so _user_lang() reads it
directly (read-only, fail-safe to English). The strings are routed through t()
with English byte-identity; zh users get translations.
"""

from bot.skills.user_middleware import _user_lang
from bot.utils.i18n import _STRINGS, t

URL = "https://x.page"
L = "en"


def _eq(got, exp):
    assert got == exp, f"\n got={got!r}\n exp={exp!r}"


def test_user_lang_fails_safe():
    # Unknown / unresolved chat id must default to English, never raise.
    assert _user_lang("000000") == "en"
    assert _user_lang(None) == "en"


def test_link_messages():
    _eq(t("link_already_linked", L),
        "This Telegram is already linked to a RUNECLAW account.\n"
        "Use /unlink to disconnect first.")
    _eq(t("link_prompt", L, url=URL),
        f"Link your RUNECLAW account\n\n1. Register / log in at {URL}\n"
        "2. Go to Dashboard and copy your link token\n3. Send: /link <token>")
    _eq(t("link_token_invalid", L, url=URL),
        f"Token invalid or expired (tokens last 10 minutes).\nGenerate a new one at {URL}")
    _eq(t("link_validate_failed", L), "Could not validate token. Please try again in a moment.")
    _eq(t("link_unreachable", L),
        "Could not reach the website to validate your token.\nPlease try again in a moment.")
    _eq(t("link_other_account", L),
        "This Telegram account is already linked to another RUNECLAW account.\n"
        "Use /unlink first, then link the correct account.")
    _eq(t("link_success", L, email="a@b.c", plan="pro"),
        "Linked successfully!\n\nAccount: a@b.c\nPlan: pro\n\n"
        "You now have full access to RUNECLAW.\nTry: /scan /portfolio /fullscan")


def test_unlink_messages():
    _eq(t("unlink_not_linked", L), "This Telegram is not linked to any account.")
    _eq(t("unlink_success", L, email="a@b.c"),
        "Unlinked from a@b.c.\nYour data is preserved. Use /link to reconnect.")


def test_me_and_sync():
    _eq(t("me_account", L, email="a@b.c", plan="pro", equity="10.00", pnl="1.00",
          trades=5, llm="openai", notif="on"),
        "<b>Your RUNECLAW Account</b>\n\nEmail:    <code>a@b.c</code>\n"
        "Plan:     <code>pro</code>\nEquity:   <code>$10.00</code>\n"
        "Open P&amp;L: <code>$1.00</code>\nTrades:   <code>5</code>\n\n"
        "LLM: <code>openai</code> | Notifications: <code>on</code>")
    _eq(t("sync_success", L, equity="10.00", positions=2, trades=3, url=URL),
        f"Dashboard synced.\nEquity: $10.00\nOpen positions: 2\nClosed trades: 3\n\n"
        f"View at: {URL}/dashboard")
    _eq(t("sync_failed", L), "Sync failed. Please try again in a moment.")


def test_keys_have_chinese():
    for k in ("link_already_linked", "link_prompt", "link_token_invalid",
              "link_validate_failed", "link_unreachable", "link_other_account",
              "link_success", "unlink_not_linked", "unlink_success", "me_account",
              "sync_success", "sync_failed"):
        assert k in _STRINGS and _STRINGS[k]["zh"].strip(), k
        assert t(k, "zh") != t(k, "en") or "{" in _STRINGS[k]["en"], k
