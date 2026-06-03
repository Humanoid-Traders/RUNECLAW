"""
RUNECLAW -- Multi-user Telegram middleware.
File: bot/skills/user_middleware.py

Every incoming message passes through require_registered().
  - Unregistered chat_ids get a friendly prompt to register + /link
  - Registered users get their personal UserContext injected
  - All bot commands become per-user automatically
"""

from __future__ import annotations
import functools, logging, os
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.db.models import (
    get_user_by_chat_id, get_user_settings, get_user_portfolio,
    save_user_portfolio, consume_link_token, link_telegram,
    unlink_telegram, UserSettings,
)
from bot.db.models import User as DBUser

log = logging.getLogger(__name__)

REGISTER_URL = os.getenv("WEBSITE_URL", "https://YOUR_DOMAIN/register")


# -- UserContext: injected into every handler --------------------------------

class UserContext:
    """Lightweight per-request user state."""

    def __init__(self, user: DBUser, settings: UserSettings, portfolio: dict):
        self.user = user
        self.settings = settings
        self.portfolio = portfolio

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def chat_id(self) -> str:
        return self.user.telegram_chat_id

    @property
    def equity(self) -> float:
        return self.portfolio["equity"]

    def save_portfolio(self) -> None:
        save_user_portfolio(
            self.user_id,
            self.portfolio["equity"],
            self.portfolio["daily_pnl"],
            self.portfolio["positions"],
            self.portfolio["trade_history"],
        )


def require_registered(handler):
    """
    Decorator -- gates any command behind registration + Telegram link.

    Usage:
        @require_registered
        async def cmd_scan(update, context, uc: UserContext):
            ...do scan for uc.user...
    """
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat or not update.message:
            return
        # Only allow private chats for user-gated commands
        if update.effective_chat.type != "private":
            await update.message.reply_text(
                "RUNECLAW commands are available in private chat only."
            )
            return
        chat_id = str(update.effective_chat.id)
        user = get_user_by_chat_id(chat_id)

        if user is None or not user.is_active:
            await update.message.reply_text(
                "Welcome to RUNECLAW\n\n"
                "You need a free account to use this bot.\n\n"
                f"1. Register at: {REGISTER_URL}\n"
                "2. Then come back and send: /link <your-token>",
                parse_mode=None,
            )
            return

        settings = get_user_settings(user.id)
        portfolio = get_user_portfolio(user.id)
        uc = UserContext(user, settings, portfolio)

        try:
            await handler(update, context, uc)
        except Exception as exc:
            log.exception(f"Handler error for user {user.id}: {exc}")
            await update.message.reply_text(
                f"Something went wrong: {type(exc).__name__}\n"
                "The team has been notified. Try again in a moment.",
                parse_mode=None,
            )

    return wrapper


# -- /link command -----------------------------------------------------------

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /link <token>
    Called by the user after they register on the website and copy their token.
    """
    chat_id = str(update.effective_chat.id)
    username = update.effective_user.username or ""

    # Already linked?
    existing = get_user_by_chat_id(chat_id)
    if existing:
        await update.message.reply_text(
            "This Telegram is already linked to a RUNECLAW account.\n"
            "Use /unlink to disconnect first.",
        )
        return

    # Need token
    if not context.args:
        await update.message.reply_text(
            "Link your RUNECLAW account\n\n"
            f"1. Register / log in at {REGISTER_URL}\n"
            "2. Go to Dashboard and copy your link token\n"
            "3. Send: /link <token>",
        )
        return

    token = context.args[0].strip()
    user_id = consume_link_token(token)

    if user_id is None:
        await update.message.reply_text(
            "Token invalid or expired (tokens last 10 minutes).\n"
            f"Generate a new one at {REGISTER_URL}",
        )
        return

    success = link_telegram(user_id, chat_id, username)
    if not success:
        await update.message.reply_text(
            "This Telegram account is already linked to another RUNECLAW account.\n"
            "Use /unlink first, then link the correct account.",
        )
        return

    user = get_user_by_chat_id(chat_id)
    await update.message.reply_text(
        f"Linked successfully!\n\n"
        f"Account: {user.email}\n"
        f"Plan: {user.plan}\n\n"
        "You now have full access to RUNECLAW.\n"
        "Try: /scan /portfolio /fullscan",
    )


# -- /unlink command ---------------------------------------------------------

async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect this Telegram from the linked RUNECLAW account."""
    chat_id = str(update.effective_chat.id)
    user = get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text(
            "This Telegram is not linked to any account."
        )
        return

    unlink_telegram(user.id)
    await update.message.reply_text(
        f"Unlinked from {user.email}.\n"
        "Your data is preserved. Use /link to reconnect.",
    )


# -- /me command -------------------------------------------------------------

@require_registered
async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE,
                 uc: UserContext) -> None:
    """Show the user's account info."""
    pf = uc.portfolio
    s = uc.settings
    await update.message.reply_text(
        f"<b>Your RUNECLAW Account</b>\n\n"
        f"Email:    <code>{uc.user.email}</code>\n"
        f"Plan:     <code>{uc.user.plan}</code>\n"
        f"Equity:   <code>${pf['equity']:.2f}</code>\n"
        f"Open P&amp;L: <code>${pf['daily_pnl']:.2f}</code>\n"
        f"Trades:   <code>{len(pf['trade_history'])}</code>\n\n"
        f"LLM: <code>{s.llm_provider}</code> | "
        f"Notifications: <code>{'on' if s.notifications_on else 'off'}</code>",
        parse_mode="HTML",
    )
