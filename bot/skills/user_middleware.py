"""
RUNECLAW -- Multi-user Telegram middleware.
File: bot/skills/user_middleware.py

Every incoming message passes through require_registered().
  - Unregistered chat_ids get a friendly prompt to register + /link
  - Registered users get their personal UserContext injected
  - All bot commands become per-user automatically
"""

from __future__ import annotations
import functools, json, logging, os, urllib.request, urllib.error
from telegram import Update
from telegram.ext import ContextTypes

from bot.db.models import (
    get_user_by_chat_id, get_user_settings, get_user_portfolio,
    save_user_portfolio, link_telegram,
    unlink_telegram, UserSettings,
)
from bot.db.models import User as DBUser

from bot.utils.i18n import t

log = logging.getLogger(__name__)

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://pmvc58g2.mule.page")
REGISTER_URL = WEBSITE_URL


def _user_lang(chat_id) -> str:
    """Resolve the user's UI language for i18n. Fails safe to English.

    The language preference lives in the JSON UserStore (keyed by Telegram id),
    a separate store from the SQLite records this middleware uses — so we read
    it directly here (read-only; UserStore() just loads data/users.json).
    """
    try:
        from bot.utils.i18n import get_user_lang
        from bot.utils.user_store import UserStore
        return get_user_lang(UserStore(), str(chat_id))
    except Exception:
        return "en"


def _ensure_local_user(user_id: int, email: str, plan: str) -> None:
    """Create a stub user in the bot's local SQLite if it doesn't exist yet.
    This bridges the website (MySQL) and bot (SQLite) user stores."""
    from bot.db.models import get_db
    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if existing:
            return
        # Insert stub user with a placeholder password hash (not usable for login)
        db.execute(
            "INSERT INTO users (id, email, password_hash, plan) VALUES (?, ?, ?, ?)",
            (user_id, email, "website-linked:no-local-password", plan),
        )
        db.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        db.execute("INSERT INTO user_portfolio (user_id) VALUES (?)", (user_id,))


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
        await update.message.reply_text(t("link_already_linked", _user_lang(chat_id)))
        return

    # Need token
    if not context.args:
        await update.message.reply_text(t("link_prompt", _user_lang(chat_id), url=REGISTER_URL))
        return

    token = context.args[0].strip()

    # Validate token via website API (tokens live in MySQL, not local SQLite)
    api_url = f"{WEBSITE_URL}/api/auth/validate-token"
    payload = json.dumps({"token": token, "chat_id": chat_id}).encode()
    req_obj = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RUNECLAW-Bot/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req_obj, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            await update.message.reply_text(t("link_token_invalid", _user_lang(chat_id), url=REGISTER_URL))
            return
        log.error(f"Token validation HTTP error: {e.code}")
        await update.message.reply_text(t("link_validate_failed", _user_lang(chat_id)))
        return
    except Exception as exc:
        log.error(f"Token validation error: {exc}")
        await update.message.reply_text(t("link_unreachable", _user_lang(chat_id)))
        return

    user_id = result["user_id"]
    email = result["email"]
    plan = result.get("plan", "free")

    # Ensure a matching user record exists in local SQLite (website uses MySQL,
    # bot uses SQLite -- we create a stub so FK constraints are satisfied)
    _ensure_local_user(user_id, email, plan)

    # Link in local SQLite so bot commands work
    success = link_telegram(user_id, chat_id, username)
    if not success:
        await update.message.reply_text(t("link_other_account", _user_lang(chat_id)))
        return

    # Initial sync: push current portfolio state to website
    try:
        from bot.utils.website_sync import sync_in_background
        portfolio = get_user_portfolio(user_id)
        sync_in_background(user_id, portfolio.get("equity", 800), [], [])
    except Exception as exc:
        log.warning(f"Initial sync failed: {exc}")

    await update.message.reply_text(t("link_success", _user_lang(chat_id), email=email, plan=plan))


# -- /unlink command ---------------------------------------------------------

async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect this Telegram from the linked RUNECLAW account."""
    chat_id = str(update.effective_chat.id)
    user = get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text(t("unlink_not_linked", _user_lang(chat_id)))
        return

    unlink_telegram(user.id)
    await update.message.reply_text(t("unlink_success", _user_lang(chat_id), email=user.email))


# -- /me command -------------------------------------------------------------

@require_registered
async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE,
                 uc: UserContext) -> None:
    """Show the user's account info."""
    pf = uc.portfolio
    s = uc.settings
    await update.message.reply_text(
        t("me_account", _user_lang(str(update.effective_chat.id)),
          email=uc.user.email, plan=uc.user.plan,
          equity=f"{pf['equity']:.2f}", pnl=f"{pf['daily_pnl']:.2f}",
          trades=len(pf['trade_history']), llm=s.llm_provider,
          notif=('on' if s.notifications_on else 'off')),
        parse_mode="HTML",
    )


# -- /sync command -----------------------------------------------------------

@require_registered
async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   uc: UserContext) -> None:
    """Manually sync portfolio data to the website dashboard."""
    try:
        from bot.utils.website_sync import sync_portfolio
        pf = uc.portfolio
        positions = pf.get("positions", [])
        history = pf.get("trade_history", [])
        success = sync_portfolio(uc.user_id, pf["equity"], positions, history)
        if success:
            await update.message.reply_text(
                t("sync_success", _user_lang(str(update.effective_chat.id)),
                  equity=f"{pf['equity']:.2f}", positions=len(positions),
                  trades=len(history), url=REGISTER_URL))
        else:
            await update.message.reply_text(t("sync_failed", _user_lang(str(update.effective_chat.id))))
    except Exception as exc:
        log.error(f"Sync command error: {exc}")
        await update.message.reply_text(t("sync_failed", _user_lang(str(update.effective_chat.id))))

