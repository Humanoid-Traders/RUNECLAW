"""
i18n routing for the admin commands (/approve, /revoke, /grant_live,
/revoke_live, /set_tier, /users).

These are admin-only and templated; the safety contract is that every routed
message is BYTE-IDENTICAL in English to the literal it replaced (emoji,
separators, HTML and placeholders all preserved). This test reconstructs each
message from its key + wrapper and compares to the original.
"""

import html

from bot.utils.i18n import _STRINGS, t

SEP = "─" * 16
TIERS = ["basic", "pro", "elite", "admin"]
L = "en"


def _eq(got, exp):
    assert got == exp, f"\n got={got!r}\n exp={exp!r}"


def test_short_shared_strings():
    _eq(f"\U0001f512 {t('admin_only', L)}", "\U0001f512 Admin only.")
    _eq(f"\U0001f534 {t('invalid_tg_id', L)}", "\U0001f534 Invalid Telegram ID.")
    _eq(f"\U0001f534 {t('invalid_tg_id_numeric', L)}", "\U0001f534 Invalid Telegram ID. Must be numeric.")
    _eq(t("invalid_tg_id_format", L), "Invalid Telegram ID format.")
    _eq(f"\U0001f534 {t('cannot_revoke_self', L)}", "\U0001f534 Cannot revoke yourself.")
    _eq(f"\U0001f534 {t('user_not_found', L)}", "\U0001f534 User not found.")
    _eq(f"\U0001f534 {t('grant_live_failed', L)}", "\U0001f534 Failed to grant live trading.")
    _eq(f"\U0001f534 {t('set_tier_failed', L)}", "\U0001f534 Failed to update tier.")


def test_approve_messages():
    _eq(f"\U0001f4cb {t('approve_usage', L)}",
        "\U0001f4cb <b>Usage</b>\n\n<code>/approve &lt;telegram_id&gt; [role]</code>\n\n"
        "Roles: <code>trader</code> (default), <code>viewer</code>, <code>admin</code>")
    _eq(f"\U0001f534 {t('invalid_role', L, role=html.escape('admin'))}",
        "\U0001f534 Invalid role: <code>admin</code>\n"
        "Valid: <code>trader</code>, <code>viewer</code>, <code>admin</code>")
    tm = "\U0001f525 Live"
    _eq(f"✅ {t('approve_result', L, sep=SEP, name='Bob', id='123', role='admin', trade_mode=tm)}",
        f"✅ <b>USER APPROVED</b>\n{SEP}\n- Name: <b>Bob</b>\n- ID: <code>123</code>\n"
        f"- Role: <code>admin</code>\n- Trading: {tm}\n- Status: \U0001f7e2 authorized\n\n"
        "<i>Use /grant_live or /revoke_live to change trading mode</i>")
    _eq(f"🟢 {t('access_granted', L, sep=SEP, role='admin')}",
        f"🟢 <b>Access Granted</b>\n{SEP}\nYour RUNECLAW account has been approved.\n"
        "- Role: <code>admin</code>\n\nUse /start to begin trading.")
    _eq(f"🔴 {t('approve_failed', L, id='123')}", "🔴 Failed to approve <code>123</code>")


def test_revoke_messages():
    _eq(t("revoke_usage", L), "<code>/revoke &lt;telegram_id&gt;</code>")
    _eq(f"⚠️ {t('revoke_result', L, sep=SEP, id='123')}",
        f"⚠️ <b>ACCESS REVOKED</b>\n{SEP}\n- ID: <code>123</code>\n- Status: 🔴 <code>pending</code>")
    _eq(f"\U0001f534 {t('user_not_found_id', L, id='123')}", "\U0001f534 User <code>123</code> not found")


def test_grant_and_revoke_live():
    _eq(f"\U0001f4cb {t('grant_live_usage', L)}",
        "\U0001f4cb <b>Usage</b>\n\n<code>/grant_live &lt;telegram_id&gt;</code>\n\n"
        "Grants live trading permission to a user.\nWithout this, users trade paper only.")
    _eq(f"\U0001f534 {t('grant_live_not_approved', L, id='123')}",
        "\U0001f534 User <code>123</code> not found or not approved.\nUse /approve first.")
    _eq(f"\U0001f525 {t('grant_live_result', L, name='Bob', id='123', role='trader')}",
        "\U0001f525 <b>LIVE TRADING GRANTED</b>\n\n- User: <b>Bob</b> (<code>123</code>)\n"
        "- Role: <code>trader</code>\n- Trading: \U0001f525 Live\n\n"
        "<i>This user can now execute live trades on the exchange.</i>")
    _eq(t("revoke_live_usage", L),
        "<code>/revoke_live &lt;telegram_id&gt;</code>\n\nRestricts user to paper trading only.")
    _eq(f"\U0001f4dd {t('revoke_live_result', L, name='Bob', id='123')}",
        "\U0001f4dd <b>LIVE TRADING REVOKED</b>\n\n- User: <b>Bob</b> (<code>123</code>)\n"
        "- Trading: \U0001f4dd Paper only")


def test_set_tier_and_users():
    tiers_str = " / ".join(f"<code>{x}</code>" for x in TIERS)
    _eq(f"\U0001f4cb {t('set_tier_usage', L, tiers=tiers_str)}",
        "\U0001f4cb <b>Usage</b>\n\n<code>/set_tier &lt;telegram_id&gt; &lt;tier&gt;</code>\n\n"
        f"Tiers: {tiers_str}\n\n"
        "\U0001f7e2 <b>basic</b> — Paper trading, basic analysis\n"
        "\U0001f535 <b>pro</b> — + Backtesting, patterns, strategies\n"
        "\U0001f7e1 <b>elite</b> — + Live eligible, priority signals, early access\n"
        "\U0001f534 <b>admin</b> — Full access")
    valid = ", ".join(f"<code>{x}</code>" for x in TIERS)
    _eq(f"\U0001f534 {t('invalid_tier', L, tier='gold', valid=valid)}",
        f"\U0001f534 Invalid tier: <code>gold</code>\nValid: {valid}")
    _eq(f"\U0001f534 {t('user_not_found_id_period', L, id='123')}",
        "\U0001f534 User <code>123</code> not found.")
    _eq(f"\U0001f3af {t('set_tier_result', L, name='Bob', id='123', tier_label='🟡 elite', role='trader')}",
        "\U0001f3af <b>TIER UPDATED</b>\n\n- User: <b>Bob</b> (<code>123</code>)\n"
        "- Tier: 🟡 elite\n- Role: <code>trader</code>")
    _eq(f"\U0001f3af {t('account_upgraded', L, tier_label='🟡 elite')}",
        "\U0001f3af <b>Account Upgraded</b>\n\nYour tier has been updated to: 🟡 elite\n"
        "Use /start to see your new features.")
    _eq(f"\U0001f4cb {t('no_registered_users', L)}", "\U0001f4cb <b>No registered users</b>")
    _eq(f"👥 {t('users_header', L, n=5)}\n", "👥 <b>REGISTERED USERS</b>  (5 total)\n")
    _eq(f"\n<i>{t('users_more', L, n=20)}</i>", "\n<i>Showing last 15 of 20</i>")


def test_all_admin_keys_have_chinese():
    keys = [
        "admin_only", "invalid_tg_id", "invalid_tg_id_numeric", "invalid_tg_id_format",
        "approve_usage", "invalid_role", "approve_result", "access_granted", "approve_failed",
        "revoke_usage", "cannot_revoke_self", "revoke_result", "user_not_found_id",
        "user_not_found_id_period", "user_not_found", "grant_live_usage",
        "grant_live_not_approved", "grant_live_result", "grant_live_failed",
        "revoke_live_usage", "revoke_live_result", "set_tier_usage", "invalid_tier",
        "set_tier_result", "account_upgraded", "set_tier_failed", "no_registered_users",
        "users_header", "users_more",
    ]
    for k in keys:
        assert k in _STRINGS and _STRINGS[k]["zh"].strip(), k
