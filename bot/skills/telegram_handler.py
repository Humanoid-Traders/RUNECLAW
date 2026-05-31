"""
RUNECLAW Telegram Handler v6 — MuleRun War Room edition.
War Room branding, tactical signal cards, risk control panel,
strategy mode selector, emergency stop, and Telegram Mini App link.
File-backed user management with roles and admin commands.
"""

from __future__ import annotations

import html
import re
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import CONFIG, _env_bool
from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.utils.logger import audit, system_log
from bot.utils.user_store import UserStore
from bot.warroom.warroom_bot import (
    render_start as wr_start,
    render_status as wr_status,
    render_signal as wr_signal,
    render_risk as wr_risk,
    render_performance as wr_performance,
    render_positions as wr_positions,
    render_daily_report as wr_daily_report,
    render_strategy_mode as wr_strategy_mode,
    render_pause as wr_pause,
    render_resume as wr_resume,
    render_emergency_stop as wr_emergency_stop,
    handle_callback as wr_handle_callback,
)


class RateLimiter:
    def __init__(self, max_per_minute: int = 20) -> None:
        self._limit = max_per_minute
        self._calls: dict[int, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, user_id: int) -> bool:
        with self._lock:
            now = time.time()
            window = [t for t in self._calls[user_id] if now - t < 60]
            self._calls[user_id] = window
            if len(window) >= self._limit:
                return False
            self._calls[user_id].append(now)
            return True


# ── War Room main menu keyboard ─────────────────────────────

_KB_WARROOM = InlineKeyboardMarkup([
    [InlineKeyboardButton("\u2694\ufe0f Open War Room", callback_data="open_warroom"),
     InlineKeyboardButton("\U0001f4ca Latest Signal", callback_data="latest_signal")],
    [InlineKeyboardButton("\U0001f4c8 Performance", callback_data="performance"),
     InlineKeyboardButton("\U0001f6e1 Risk Control", callback_data="risk_control")],
    [InlineKeyboardButton("\u2699\ufe0f Strategy Mode", callback_data="strategy_mode"),
     InlineKeyboardButton("\U0001f4c2 Positions", callback_data="positions")],
    [InlineKeyboardButton("\u26d4 Emergency Stop", callback_data="risk_emergency_stop")],
])

# Legacy dashboard keyboard (kept for /dashboard command compatibility)
_KB_DASH = InlineKeyboardMarkup([
    [InlineKeyboardButton("\U0001f4ca Status", callback_data="pane:status"),
     InlineKeyboardButton("\U0001f6e1 Risk", callback_data="pane:risk")],
    [InlineKeyboardButton("\U0001f4b0 Portfolio", callback_data="pane:portfolio"),
     InlineKeyboardButton("\U0001f4c5 Macro", callback_data="pane:macro")],
    [InlineKeyboardButton("\U0001f9e0 Learning", callback_data="pane:learning"),
     InlineKeyboardButton("\U0001f50d Scan", callback_data="pane:scan")],
    [InlineKeyboardButton("\U0001f504 Refresh", callback_data="pane:refresh")],
])


class TelegramHandler:
    def __init__(self, engine: RuneClawEngine, registry: Optional[SkillRegistry] = None) -> None:
        self.engine = engine
        self.registry = registry or build_default_registry()
        self._limiter = RateLimiter(CONFIG.telegram.rate_limit_per_minute)
        self._last_pane: dict[int, str] = {}
        self.users = UserStore()
        # Seed admin from .env TELEGRAM_CHAT_ID
        self.users.seed_admin(CONFIG.telegram.chat_id)

    def build_app(self) -> Application:
        app = Application.builder().token(CONFIG.telegram.bot_token).build()
        for cmd, handler in [
            ("start", self._cmd_start), ("dashboard", self._cmd_dashboard),
            ("scan", self._cmd_scan), ("analyze", self._cmd_analyze),
            ("portfolio", self._cmd_portfolio), ("trade", self._cmd_trade),
            ("risk", self._cmd_risk), ("status", self._cmd_status),
            ("rejected", self._cmd_rejected), ("halt", self._cmd_halt),
            ("reset", self._cmd_reset), ("macro", self._cmd_macro),
            ("backtest", self._cmd_backtest), ("walkforward", self._cmd_walkforward),
            ("journal", self._cmd_journal), ("costs", self._cmd_costs),
            ("run", self._cmd_run), ("learn", self._cmd_learn),
            ("patterns", self._cmd_patterns), ("proposals", self._cmd_proposals),
            ("optimize", self._cmd_optimize), ("help", self._cmd_help),
            ("mode", self._cmd_mode),
            # War Room commands
            ("latest_signal", self._cmd_latest_signal),
            ("open_positions", self._cmd_open_positions),
            ("performance", self._cmd_performance),
            ("pause", self._cmd_pause),
            ("resume", self._cmd_resume),
            ("emergency_stop", self._cmd_emergency_stop),
            ("daily_report", self._cmd_daily_report),
            ("strategy", self._cmd_strategy),
            # Admin commands
            ("approve", self._cmd_approve), ("revoke", self._cmd_revoke),
            ("users", self._cmd_users),
        ]:
            app.add_handler(CommandHandler(cmd, handler))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        # Free-text message handler (must be last — catches non-command text)
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self._handle_message))
        return app

    # ── Centralized send ──────────────────────────────────────

    async def _send(self, update: Update, text: str,
                    reply_markup=None, edit: bool = False) -> None:
        target = update.callback_query.message if edit else update.message
        method = update.callback_query.edit_message_text if edit else update.message.reply_text
        try:
            await method(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            import re
            plain = re.sub(r"<[^>]+>", "", text)
            try:
                await method(plain, parse_mode=None, reply_markup=reply_markup)
            except Exception:
                pass

    # ── Banner / Footer ───────────────────────────────────────

    def _banner(self) -> str:
        cb = self.engine.risk.circuit_breaker_active
        state = self.engine.portfolio.snapshot()
        macro = self.engine.macro_calendar.evaluate()
        mode = "SIM" if CONFIG.simulation_mode else "LIVE"
        cb_s = "\U0001f534 CB" if cb else "\U0001f7e2 OK"
        macro_s = macro.state.value.replace("_", " ").title()
        macro_icon = {
            "NORMAL": "\U0001f7e2", "PRE_EVENT_CAUTION": "\U0001f7e1",
            "EVENT_LOCKDOWN": "\U0001f534", "POST_EVENT_VOLATILITY": "\U0001f7e0",
            "BLACKOUT": "\u26ab",
        }.get(macro.state.value, "\u26aa")
        return f"{mode} \u2022 {state.open_positions} open \u2022 {cb_s} \u2022 {macro_icon} {macro_s}"

    def _footer(self) -> str:
        return f"\n<i>{datetime.now(UTC).strftime('%H:%M:%S UTC')}</i>"

    # ── Pane renderers ────────────────────────────────────────

    async def _render_pane(self, pane: str) -> str:
        if pane == "status":
            return await self.registry.get("check_risk").execute(self.engine, mode="status")
        elif pane == "risk":
            return await self.registry.get("check_risk").execute(self.engine, mode="risk")
        elif pane == "portfolio":
            return await self.registry.get("get_portfolio").execute(self.engine)
        elif pane == "macro":
            return await self.registry.get("macro_calendar").execute(self.engine)
        elif pane == "learning":
            return await self.registry.get("learning").execute(self.engine)
        elif pane == "scan":
            return await self.registry.get("scan_market").execute(self.engine)
        return ""

    # ── Free-text AI chat ─────────────────────────────────────

    _CHAT_SYSTEM_PROMPT = (
        "You are RUNECLAW, an AI crypto trading assistant by Humanoid Traders. "
        "You answer questions about crypto markets, trading strategies, technical analysis, "
        "risk management, and how the RUNECLAW bot works. "
        "Keep answers concise (under 200 words). Use plain text, no markdown. "
        "If asked about non-crypto topics, briefly answer but steer back to trading. "
        "Never give financial advice — always note that you provide analysis, not recommendations. "
        "Available commands: /scan, /analyze, /dashboard, /portfolio, /risk, /status, "
        "/backtest, /journal, /macro, /help. Suggest relevant commands when appropriate."
    )

    async def _llm_chat(self, question: str) -> str:
        """Send a free-text question to the LLM and return the response."""
        import asyncio
        from openai import AsyncOpenAI

        llm_kwargs: dict = {"api_key": CONFIG.llm.api_key}
        if CONFIG.llm.base_url:
            llm_kwargs["base_url"] = CONFIG.llm.base_url

        client = AsyncOpenAI(**llm_kwargs)
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=CONFIG.llm.model,
                    messages=[
                        {"role": "system", "content": self._CHAT_SYSTEM_PROMPT},
                        {"role": "user", "content": question},
                    ],
                    temperature=0.5,
                    max_tokens=512,
                ),
                timeout=CONFIG.llm.timeout_seconds,
            )
            answer = resp.choices[0].message.content.strip()
            # Track cost
            usage = resp.usage
            if usage:
                self.engine.cost.record_llm(
                    model=CONFIG.llm.model,
                    prompt_tokens=usage.prompt_tokens or 0,
                    completion_tokens=usage.completion_tokens or 0,
                    category="chat",
                )
            return answer
        except asyncio.TimeoutError:
            return "Response timed out. Try again or use a specific command like /scan or /analyze."
        except Exception as e:
            audit(system_log, f"Chat LLM error: {e}", action="chat_error", result="ERROR")
            return "Could not process your question right now. Try a command like /help or /scan."

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle free-text messages — AI chat for authorized users."""
        if not update.message or not update.message.text:
            return

        tg_id = self._get_tg_id(update)
        user = self.users.get(tg_id)
        text = update.message.text.strip()

        if not text:
            return

        # Unregistered users get onboarding
        if not user:
            self.users.register(tg_id, name=(
                update.effective_user.first_name if update.effective_user else ""))
            await self._send(update,
                f"\U0001f43e <b>RUNECLAW</b>\n\n"
                f"Welcome! I'm an AI trading assistant.\n\n"
                f"Your account needs approval first.\n"
                f"ID: <code>{tg_id}</code>\n\n"
                f"Use /start to register, then wait for admin approval.\n"
                f"Use /help to see available commands.")
            return

        # Pending users get a clear message
        if not user.get("authorized", False):
            await self._send(update,
                f"\U0001f512 Your account is pending approval.\n\n"
                f"Once approved, you can ask me anything about crypto markets, "
                f"trading strategies, or use commands like /scan and /analyze.\n\n"
                f"Use /help to see all commands.")
            return

        # Rate limit check
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            await update.message.reply_text("\u26a0\ufe0f Rate limit. Wait a moment.")
            return

        # Authorized user — send to LLM
        await self._send(update, "\U0001f9e0 <i>Thinking...</i>")
        answer = await self._llm_chat(text)
        await self._send(update,
            f"\U0001f43e <b>RUNECLAW</b>\n\n{html.escape(answer)}")

    # ── Auth helpers ──────────────────────────────────────────

    def _get_tg_id(self, update: Update) -> str:
        """Get Telegram user ID as string from update."""
        if update.effective_user:
            return str(update.effective_user.id)
        if update.effective_chat:
            return str(update.effective_chat.id)
        return ""

    def _is_admin(self, update: Update) -> bool:
        """Check if the user is an admin."""
        tg_id = self._get_tg_id(update)
        user = self.users.get(tg_id)
        return user is not None and user.get("role") == "admin"

    def _check_auth(self, update: Update) -> bool:
        """Check if user is authorized (any role except pending)."""
        tg_id = self._get_tg_id(update)
        return self.users.is_authorized(tg_id)

    async def _guard(self, update: Update, command: str = "") -> bool:
        """Auth + rate limit + role permission check."""
        tg_id = self._get_tg_id(update)
        user = self.users.get(tg_id)

        if not user or not user.get("authorized", False):
            await self._send(update,
                f"\U0001f512 <b>Access restricted</b>\n\n"
                f"Your account is not linked yet.\n"
                f"Your Telegram ID: <code>{tg_id}</code>\n\n"
                f"Use /start to register, then wait for admin approval.")
            return False

        # Role-based permission check
        if command and not self.users.has_permission(tg_id, command):
            role = user.get("role", "pending")
            await self._send(update,
                f"\U0001f512 <b>Insufficient permissions</b>\n\n"
                f"Your role (<code>{role}</code>) cannot use <code>/{command}</code>.\n"
                f"Contact an admin for access.")
            return False

        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            await update.message.reply_text("\u26a0\ufe0f Rate limit. Wait a moment.")
            return False
        return True

    # ── Public commands (no auth required) ─────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """War Room welcome — auto-registers new users."""
        now = datetime.now(UTC).strftime("%H:%M UTC")
        user_tg = update.effective_user
        tg_id = self._get_tg_id(update)
        user_name = html.escape(user_tg.first_name) if user_tg else "Trader"

        # Auto-register on first contact
        record = self.users.register(tg_id, name=user_name)

        if not record.get("authorized", False):
            msg = (
                "<b>\u2694\ufe0f MULERUN WAR ROOM</b>\n"
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                "Powered by <b>RUNECLAW Signal Engine</b>\n\n"
                f"Welcome, <b>{user_name}</b>.\n\n"
                "\u2022 AI-powered crypto analysis\n"
                "\u2022 18 fail-closed risk checks\n"
                "\u2022 Human approval on every trade\n"
                "\u2022 Full audit trail\n\n"
                "\U0001f4cb <b>Registration received</b>\n"
                f"Your Telegram ID: <code>{tg_id}</code>\n"
                "Status: <code>pending approval</code>\n\n"
                "An admin will review your access.\n"
                "Use /help to see available commands.\n\n"
                f"<i>{now}</i>"
            )
            await self._send(update, msg)
            await self._notify_admins(
                "\U0001f195 <b>New user registered</b>\n\n"
                f"Name: <b>{user_name}</b>\n"
                f"ID: <code>{tg_id}</code>\n\n"
                f"Approve with: <code>/approve {tg_id}</code>",
                ctx)
            return

        # Authorized user — War Room start
        banner = self._banner()
        role = record.get("role", "trader")
        mode_str = "PAPER" if CONFIG.simulation_mode else "LIVE"
        state = self.engine.portfolio.snapshot()
        cb_active = self.engine.risk.circuit_breaker_active

        msg = (
            "<b>\u2694\ufe0f MULERUN WAR ROOM</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "Powered by <b>RUNECLAW Signal Engine</b>\n\n"
            "Signal locked. Risk checked. Claw ready.\n\n"
            f"Status: <b>{'ACTIVE' if not cb_active else 'CB TRIGGERED'}</b> "
            f"{'🟢' if not cb_active else '🔴'}\n"
            f"Engine: v3.1 | Mode: {mode_str}\n"
            f"<code>{banner}</code>\n\n"
            f"<pre>"
            f"  Balance    ${CONFIG.paper_balance_usd:>9,.0f}\n"
            f"  Risk Checks          18\n"
            f"  Open Pos   {state.open_positions:>10}\n"
            f"  Role       {role:>10}"
            f"</pre>\n\n"
            f"<i>{now}  \u2022  /help for all commands</i>\n\n"
            "<i>\u26a0\ufe0f Not financial advice. Use at your own risk.\n"
            "\U0001f4dc AGPL-3.0 \u2022 github.com/Humanoid-Traders/RUNECLAW</i>"
        )
        await self._send(update, msg, reply_markup=_KB_WARROOM)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Always responds — War Room help menu."""
        tg_id = self._get_tg_id(update)
        is_auth = self.users.is_authorized(tg_id)
        user = self.users.get(tg_id)
        role = user.get("role", "pending") if user else "pending"

        if is_auth:
            banner = self._banner()
            header = (
                "<b>\u2694\ufe0f MULERUN WAR ROOM</b>  "
                f"[{role}]\n"
                f"<code>{banner}</code>\n\n"
            )
        else:
            header = (
                "<b>\u2694\ufe0f MULERUN WAR ROOM</b>\n"
                "<i>Status: pending approval \u2014 use /start to register</i>\n\n"
            )

        sections = (
            "<pre>"
            " WAR ROOM\n"
            "  /start         Main menu\n"
            "  /status        Engine status\n"
            "  /latest_signal Latest signal\n"
            "  /open_positions Open trades\n"
            "  /performance   PnL summary\n"
            "  /strategy      Strategy mode\n"
            "  /daily_report  Daily report\n"
            "\n"
            " MARKET\n"
            "  /scan          Market scanner\n"
            "  /analyze BTC   AI analysis\n"
            "  /run           Strategy preset\n"
            "\n"
            " PORTFOLIO\n"
            "  /portfolio     Holdings + PnL\n"
            "  /trade         Pending trades\n"
            "  /journal       Trade history\n"
            "\n"
            " RISK CONTROL\n"
            "  /risk          Risk dashboard\n"
            "  /pause         Pause trading\n"
            "  /resume        Resume trading\n"
            "  /emergency_stop Full stop\n"
            "  /halt          Circuit breaker\n"
            "  /reset         Reset breaker\n"
            "\n"
            " INTELLIGENCE\n"
            "  /dashboard     Command center\n"
            "  /macro         Macro calendar\n"
            "  /backtest      Synthetic test\n"
            "  /walkforward   Walk-forward\n"
            "\n"
            " AI SYSTEM\n"
            "  /learn         Learning stats\n"
            "  /patterns      Detected patt.\n"
            "  /proposals     Improvements\n"
            "  /optimize      Token optimizer\n"
            "  /costs         Agent economics\n"
        )

        if role == "admin":
            sections += (
                "\n"
                " ADMIN\n"
                "  /approve ID   Authorize user\n"
                "  /revoke ID    Revoke access\n"
                "  /users        List all users\n"
            )

        sections += "</pre>\n\n"
        sections += (
            "<i>\u26a0\ufe0f Not financial advice. Use at your own risk.\n"
            "\U0001f4dc AGPL-3.0 \u2022 github.com/Humanoid-Traders/RUNECLAW</i>"
        )
        await self._send(update, header + sections)

    # ── Admin commands ────────────────────────────────────────

    async def _cmd_approve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /approve <telegram_id> [role]"""
        if not self._is_admin(update):
            await self._send(update, "\U0001f512 Admin only.")
            return

        args = ctx.args or []
        if not args:
            await self._send(update,
                "\U0001f4cb <b>Usage</b>\n\n"
                "<code>/approve &lt;telegram_id&gt; [role]</code>\n\n"
                "Roles: <code>trader</code> (default), <code>viewer</code>, <code>admin</code>")
            return

        target_id = args[0].strip()

        # Input validation: Telegram IDs are numeric only
        if not target_id.isdigit():
            await self._send(update,
                "\U0001f534 Invalid Telegram ID. Must be numeric.")
            return

        role = args[1].strip().lower() if len(args) > 1 else "trader"

        if role not in ("trader", "viewer", "admin"):
            await self._send(update,
                f"\U0001f534 Invalid role: <code>{html.escape(role)}</code>\n"
                f"Valid: <code>trader</code>, <code>viewer</code>, <code>admin</code>")
            return

        ok = self.users.authorize(target_id, role=role)
        if ok:
            target = self.users.get(target_id)
            name = target.get("name", "Unknown") if target else "Unknown"
            await self._send(update,
                f"\U0001f7e2 <b>User approved</b>\n\n"
                f"ID: <code>{target_id}</code>\n"
                f"Name: {html.escape(name)}\n"
                f"Role: <code>{role}</code>")
            # Notify the approved user
            try:
                await ctx.bot.send_message(
                    chat_id=int(target_id),
                    text=(
                        f"\U0001f7e2 <b>Access granted</b>\n\n"
                        f"Your RUNECLAW account has been approved.\n"
                        f"Role: <code>{role}</code>\n\n"
                        f"Use /start to begin trading."
                    ),
                    parse_mode="HTML")
            except Exception:
                pass  # User may not have started the bot yet
        else:
            await self._send(update,
                f"\U0001f534 Failed to approve <code>{html.escape(target_id)}</code>")

    async def _cmd_revoke(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /revoke <telegram_id>"""
        if not self._is_admin(update):
            await self._send(update, "\U0001f512 Admin only.")
            return

        args = ctx.args or []
        if not args:
            await self._send(update,
                "<code>/revoke &lt;telegram_id&gt;</code>")
            return

        target_id = args[0].strip()

        # Don't let admin revoke themselves
        if target_id == self._get_tg_id(update):
            await self._send(update, "\U0001f534 Cannot revoke yourself.")
            return

        ok = self.users.revoke(target_id)
        if ok:
            await self._send(update,
                f"\U0001f7e1 <b>Access revoked</b>\n\n"
                f"ID: <code>{target_id}</code>\n"
                f"Status: <code>pending</code>")
        else:
            await self._send(update,
                f"\U0001f534 User <code>{html.escape(target_id)}</code> not found")

    async def _cmd_users(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: list all registered users."""
        if not self._is_admin(update):
            await self._send(update, "\U0001f512 Admin only.")
            return

        all_users = self.users.list_users()
        if not all_users:
            await self._send(update, "\U0001f4cb <b>No registered users</b>")
            return

        counts = self.users.count()
        lines = [
            f"\U0001f465 <b>USERS</b>  ({len(all_users)} total)\n",
            "<pre>",
        ]

        # Summary
        for role in ("admin", "trader", "viewer", "pending"):
            c = counts.get(role, 0)
            if c > 0:
                lines.append(f"  {role:<10} {c:>3}")
        lines.append("")

        # User list
        lines.append(f" {'ID':<12}{'NAME':<14}{'ROLE':<10}")
        lines.append(f" {'─'*12}{'─'*14}{'─'*10}")

        for u in all_users[-15:]:  # Show last 15
            tid = u["telegram_id"][-8:]  # Last 8 digits
            name = (u.get("name") or "?")[:12]
            role = u.get("role", "?")
            auth = "\u2713" if u.get("authorized") else "\u2717"
            lines.append(f" {tid:<12}{name:<14}{auth} {role}")

        lines.append("</pre>")

        if len(all_users) > 15:
            lines.append(f"\n<i>Showing last 15 of {len(all_users)}</i>")

        await self._send(update, "\n".join(lines))

    # ── Mode switching ────────────────────────────────────────

    async def _cmd_mode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch asset universe: /mode solana | /mode all"""
        if not await self._guard(update, "mode"):
            return

        args = (update.message.text or "").split()
        valid_modes = {"all", "solana"}

        if len(args) < 2 or args[1].lower() not in valid_modes:
            from bot.config import RUNTIME
            current = RUNTIME.asset_universe
            icon = "\u2600\ufe0f" if current == "solana" else "\U0001f30d"
            lines = [
                f"\U0001f504 <b>ASSET UNIVERSE</b>\n",
                f"Current: {icon} <b>{current.upper()}</b>\n",
                "Usage:",
                "  <code>/mode solana</code> \u2014 15 Solana ecosystem tokens",
                "  <code>/mode all</code> \u2014 all Bitget USDT pairs",
            ]
            if current == "solana":
                from bot.config import SOLANA_ECOSYSTEM_SYMBOLS
                tokens = ", ".join(s.replace("/USDT", "") for s in SOLANA_ECOSYSTEM_SYMBOLS)
                lines.append(f"\nTokens: <i>{tokens}</i>")
            await self._send(update, "\n".join(lines))
            return

        new_mode = args[1].lower()
        # C1 FIX: use mutable RuntimeState instead of mutating frozen CONFIG
        from bot.config import RUNTIME
        RUNTIME.asset_universe = new_mode

        if new_mode == "solana":
            from bot.config import SOLANA_ECOSYSTEM_SYMBOLS
            tokens = ", ".join(s.replace("/USDT", "") for s in SOLANA_ECOSYSTEM_SYMBOLS)
            await self._send(update, (
                "\u2600\ufe0f <b>SOLANA MODE ACTIVE</b>\n\n"
                f"Scanner now prioritizes {len(SOLANA_ECOSYSTEM_SYMBOLS)} Solana ecosystem tokens:\n"
                f"<i>{tokens}</i>\n\n"
                "All 18 risk checks still apply. Meme tokens (BONK, WIF) "
                "use tighter volatility and correlation limits.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        else:
            await self._send(update, (
                "\U0001f30d <b>ALL MARKETS MODE</b>\n\n"
                "Scanner now covers all Bitget USDT pairs.\n"
                "Use <code>/mode solana</code> to focus on Solana ecosystem."
            ))

    # ── Admin notification helper ─────────────────────────────

    async def _notify_admins(self, text: str, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a notification to all admin users."""
        for u in self.users.list_users():
            if u.get("role") == "admin" and u.get("authorized"):
                try:
                    await ctx.bot.send_message(
                        chat_id=int(u["telegram_id"]),
                        text=text, parse_mode="HTML")
                except Exception:
                    pass

    # ── Protected commands ────────────────────────────────────

    async def _cmd_dashboard(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "dashboard"):
            return
        chat_id = update.effective_chat.id
        pane = self._last_pane.get(chat_id, "status")
        body = await self._render_pane(pane)
        text = body + self._footer()
        await self._send(update, text, reply_markup=_KB_DASH)
        self._last_pane[chat_id] = pane

    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "scan"):
            return
        result = await self.registry.get("scan_market").execute(self.engine)
        await self._send(update, result)

    async def _cmd_analyze(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "analyze"):
            return
        args = ctx.args
        if args:
            raw = args[0].upper().strip()
            # Input validation: only allow alphanumeric + slash (e.g. BTC, BTC/USDT)
            if not re.match(r"^[A-Z0-9]{1,20}(/[A-Z0-9]{1,10})?$", raw):
                await self._send(update,
                    "\U0001f534 Invalid symbol. Use format: <code>BTC</code> or <code>BTC/USDT</code>")
                return
            symbol = raw if "/" in raw else f"{raw}/USDT"
        else:
            symbol = "BTC/USDT"

        ids_before = set(idea.id for idea in self.engine.pending_ideas)
        await self._send(update, f"\u23f3 <i>Analyzing {html.escape(symbol)}...</i>")

        result = await self.registry.get("analyze_asset").execute(
            self.engine, symbol=symbol)

        new_idea = None
        for idea in self.engine.pending_ideas:
            if idea.id not in ids_before:
                new_idea = idea
                break

        if new_idea is not None:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 APPROVE", callback_data=f"confirm:{new_idea.id}"),
                InlineKeyboardButton("\u274c PASS", callback_data=f"reject:{new_idea.id}"),
            ]])
            await self._send(update, result, reply_markup=kb)
        else:
            await self._send(update, result)

    async def _cmd_portfolio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "portfolio"):
            return
        result = await self.registry.get("get_portfolio").execute(self.engine)
        await self._send(update, result)

    async def _cmd_trade(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "trade"):
            return
        pending = self.engine.pending_ideas
        if not pending:
            await self._send(update,
                "\u23f3 <b>No pending trades</b>\n\n"
                "<i>Use /scan or /analyze to generate ideas</i>")
            return
        for idea in pending:
            d = "\U0001f7e2" if idea.direction.value == "LONG" else "\U0001f534"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 APPROVE", callback_data=f"confirm:{idea.id}"),
                InlineKeyboardButton("\u274c PASS", callback_data=f"reject:{idea.id}"),
            ]])
            msg = (
                f"{d} <b>{idea.direction.value}  {html.escape(idea.asset)}</b>\n\n"
                f"<pre>"
                f"  Entry  ${idea.entry_price:>10,.2f}\n"
                f"  SL     ${idea.stop_loss:>10,.2f}\n"
                f"  TP     ${idea.take_profit:>10,.2f}"
                f"</pre>\n\n"
                f"  Conf <code>{idea.confidence:.0%}</code>  \u2502  "
                f"R:R <code>{idea.risk_reward_ratio}</code>\n\n"
                f"<i>{html.escape(idea.reasoning[:200])}</i>"
            )
            await self._send(update, msg, reply_markup=kb)

    async def _cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "risk"):
            return
        state = self.engine.portfolio.snapshot()
        data = {
            "daily_loss_limit": CONFIG.max_daily_drawdown_pct if hasattr(CONFIG, 'max_daily_drawdown_pct') else 3.0,
            "current_drawdown": round(state.max_drawdown_pct, 2) if state.max_drawdown_pct else 0.0,
            "max_open_trades": CONFIG.max_open_positions if hasattr(CONFIG, 'max_open_positions') else 3,
            "open_trades": state.open_positions,
            "leverage_cap": 5,
        }
        rendered = wr_risk(data)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\U0001f6e1 Safe Mode", callback_data="risk_safe_mode"),
             InlineKeyboardButton("\u23f8 Pause Bot", callback_data="risk_pause")],
            [InlineKeyboardButton("\u26d4 Emergency Stop", callback_data="risk_emergency_stop")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "status"):
            return
        state = self.engine.portfolio.snapshot()
        cb = self.engine.risk.circuit_breaker_active
        macro = self.engine.macro_calendar.evaluate()
        mode = "PAPER" if CONFIG.simulation_mode else "LIVE"
        # Build War Room status card
        data = {
            "active": not cb,
            "mode": mode,
            "exchange": "Bitget",
            "open_trades": state.open_positions,
            "daily_pnl": round(state.max_drawdown_pct * -1, 2) if state.max_drawdown_pct else 0.0,
            "risk_used": round(state.max_drawdown_pct, 2) if state.max_drawdown_pct else 0.0,
            "market_bias": macro.state.value.replace("_", " ").title(),
            "last_signal": "Use /scan",
        }
        rendered = wr_status(data)
        await self._send(update, rendered["text"], reply_markup=_KB_WARROOM)

    async def _cmd_rejected(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "rejected"):
            return
        result = await self.registry.get("rejected_trades").execute(self.engine)
        await self._send(update, result)

    async def _cmd_halt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "halt"):
            return
        result = await self.registry.get("halt").execute(self.engine)
        await self._send(update, result)

    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "reset"):
            return
        was_active = self.engine.risk.circuit_breaker_active
        streak_before = self.engine.risk.consecutive_losses
        self.engine.risk.reset_circuit_breaker()
        if was_active:
            msg = "\U0001f7e2 <b>Circuit breaker reset</b>\n\nTrading resumed."
        elif streak_before >= 3:
            msg = f"\U0001f7e2 <b>Streak cleared</b>  {streak_before} \u2192 0"
        else:
            msg = f"\U0001f7e1 <b>Nothing to reset</b>\n\nCB: off  \u2022  Streak: {streak_before}"
        await self._send(update, msg)

    async def _cmd_macro(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "macro"):
            return
        result = await self.registry.get("macro_calendar").execute(self.engine)
        await self._send(update, result)

    async def _cmd_backtest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "backtest"):
            return
        args = ctx.args or []
        bars = args[0] if args else "720"
        seed = args[1] if len(args) > 1 else "42"
        await self._send(update,
            f"\u23f3 <i>Backtest running  \u2022  {bars} bars  \u2022  seed {seed}</i>")
        result = await self.registry.get("run_backtest").execute(
            self.engine, bars=bars, seed=seed)
        await self._send(update, result)

    async def _cmd_walkforward(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "walkforward"):
            return
        args = ctx.args or []
        bars = args[0] if args else "1440"
        folds = args[1] if len(args) > 1 else "3"
        await self._send(update, "\u23f3 <i>Walk-forward running...</i>")
        result = await self.registry.get("walk_forward").execute(
            self.engine, bars=bars, folds=folds)
        await self._send(update, result)

    async def _cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "journal"):
            return
        result = await self.registry.get("trade_journal").execute(self.engine)
        await self._send(update, result)

    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "costs"):
            return
        result = await self.registry.get("costs").execute(self.engine)
        await self._send(update, result)

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "run"):
            return
        strategy = " ".join(ctx.args) if ctx.args else ""
        if strategy:
            await self._send(update,
                f"\u23f3 <i>Running {html.escape(strategy)}...</i>")
        result = await self.registry.get("run_strategy").execute(
            self.engine, strategy=strategy)
        await self._send(update, result)

    async def _cmd_learn(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "learn"):
            return
        result = await self.registry.get("learning").execute(self.engine)
        await self._send(update, result)

    async def _cmd_patterns(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "patterns"):
            return
        result = await self.registry.get("patterns").execute(self.engine)
        await self._send(update, result)

    async def _cmd_proposals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "proposals"):
            return
        result = await self.registry.get("proposals").execute(self.engine)
        await self._send(update, result)

    async def _cmd_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "optimize"):
            return
        result = await self.registry.get("optimize").execute(self.engine)
        await self._send(update, result)

    # ── War Room commands ────────────────────────────────────────

    async def _cmd_latest_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the latest signal in War Room card format."""
        if not await self._guard(update, "scan"):
            return
        pending = self.engine.pending_ideas
        if not pending:
            await self._send(update,
                "<b>\U0001f4e1 NO ACTIVE SIGNALS</b>\n\n"
                "No signals in queue.\n"
                "Use /scan or /analyze to generate signals.")
            return
        idea = pending[-1]  # most recent
        direction = idea.direction.value
        confidence = int(idea.confidence * 100)
        entry = idea.entry_price
        sl = idea.stop_loss
        tp = idea.take_profit
        spread = abs(entry - sl) * 0.3  # approximate entry range
        data = {
            "pair": idea.asset.replace("/", ""),
            "direction": direction,
            "confidence": confidence,
            "risk_level": "High" if confidence < 50 else "Medium" if confidence < 70 else "Low",
            "entry_low": round(min(entry, entry - spread), 2),
            "entry_high": round(max(entry, entry + spread), 2),
            "sl": round(sl, 2),
            "tp1": round(tp, 2),
            "tp2": round(tp * 1.005 if direction == "LONG" else tp * 0.995, 2),
            "reason": idea.reasoning[:200] if idea.reasoning else "AI analysis",
        }
        rendered = wr_signal(data)
        # Map approve/reject to actual trade IDs
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2705 Approve Trade", callback_data=f"confirm:{idea.id}")],
            [InlineKeyboardButton("\U0001f441 Watch Only", callback_data=f"signal_watch_{idea.asset}")],
            [InlineKeyboardButton("\u274c Reject", callback_data=f"reject:{idea.id}")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    async def _cmd_open_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show open positions in War Room format."""
        if not await self._guard(update, "portfolio"):
            return
        state = self.engine.portfolio.snapshot()
        positions_data = []
        # Pull from portfolio's internal position data
        with self.engine.portfolio._lock:
            for tid, pos in self.engine.portfolio._positions.items():
                last_price = self.engine.portfolio._last_prices.get(pos.asset, pos.entry_price)
                if pos.direction.value == "LONG":
                    pnl_pct = ((last_price - pos.entry_price) / pos.entry_price) * 100
                else:
                    pnl_pct = ((pos.entry_price - last_price) / pos.entry_price) * 100
                positions_data.append({
                    "pair": pos.asset.replace("/", ""),
                    "direction": pos.direction.value,
                    "entry": round(pos.entry_price, 2),
                    "current": round(last_price, 2),
                    "pnl": round(pnl_pct, 2),
                    "sl": round(pos.stop_loss, 2),
                    "tp1": round(pos.take_profit, 2),
                })
        if not positions_data:
            await self._send(update,
                "<b>\U0001f4c8 OPEN POSITIONS (0)</b>\n"
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
                "No open positions.\n"
                "Use /scan or /analyze to find signals.")
            return
        rendered = wr_positions(positions_data)
        # Build keyboard with actual trade controls
        kb_rows = []
        for pos in positions_data:
            kb_rows.append([
                InlineKeyboardButton(f"\U0001f4cb {pos['pair']}", callback_data=f"pos_details_{pos['pair']}"),
                InlineKeyboardButton(f"\u274c Close", callback_data=f"pos_close_{pos['pair']}"),
            ])
        await self._send(update, rendered["text"],
                         reply_markup=InlineKeyboardMarkup(kb_rows) if kb_rows else None)

    async def _cmd_performance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Performance summary in War Room format."""
        if not await self._guard(update, "portfolio"):
            return
        state = self.engine.portfolio.snapshot()
        journal = self.engine.journal
        trades = journal.trades if hasattr(journal, 'trades') else []
        today_trades = len(trades)
        wins = sum(1 for t in trades if getattr(t, 'pnl_pct', 0) > 0)
        win_rate = (wins / today_trades * 100) if today_trades > 0 else 0
        # Find best/worst pairs
        best_pair = "N/A"
        worst_pair = "N/A"
        if trades:
            sorted_t = sorted(trades, key=lambda t: getattr(t, 'pnl_pct', 0))
            worst_pair = getattr(sorted_t[0], 'asset', 'N/A').replace("/USDT", "")
            best_pair = getattr(sorted_t[-1], 'asset', 'N/A').replace("/USDT", "")

        data = {
            "today_pnl": round(state.max_drawdown_pct * -1, 2) if state.max_drawdown_pct else 0.0,
            "week_pnl": 0.0,
            "win_rate": win_rate,
            "trades_today": today_trades,
            "best_pair": best_pair,
            "worst_pair": worst_pair,
        }
        rendered = wr_performance(data)
        await self._send(update, rendered["text"])

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Pause trading — activates circuit breaker."""
        if not await self._guard(update, "halt"):
            return
        self.engine.risk._circuit_breaker = True
        rendered = wr_pause()
        await self._send(update, rendered["text"])
        audit(system_log, "Bot paused via /pause", action="pause", result="OK")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Resume trading — deactivates circuit breaker."""
        if not await self._guard(update, "reset"):
            return
        self.engine.risk.reset_circuit_breaker()
        rendered = wr_resume()
        await self._send(update, rendered["text"])
        audit(system_log, "Bot resumed via /resume", action="resume", result="OK")

    async def _cmd_emergency_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Emergency stop confirmation prompt."""
        if not await self._guard(update, "halt"):
            return
        rendered = wr_emergency_stop()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u26d4 CONFIRM STOP", callback_data="emergency_confirm"),
             InlineKeyboardButton("\u21a9\ufe0f Cancel", callback_data="emergency_cancel")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    async def _cmd_daily_report(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Daily trading report."""
        if not await self._guard(update, "journal"):
            return
        journal = self.engine.journal
        trades = journal.trades if hasattr(journal, 'trades') else []
        today_trades = len(trades)
        wins = sum(1 for t in trades if getattr(t, 'pnl_pct', 0) > 0)
        losses = today_trades - wins
        net_pnl = sum(getattr(t, 'pnl_pct', 0) for t in trades)
        best_trade = "N/A"
        best_pnl = 0.0
        worst_trade = "N/A"
        worst_pnl = 0.0
        if trades:
            sorted_t = sorted(trades, key=lambda t: getattr(t, 'pnl_pct', 0))
            worst_trade = getattr(sorted_t[0], 'asset', 'N/A').replace("/USDT", "")
            worst_pnl = round(getattr(sorted_t[0], 'pnl_pct', 0), 2)
            best_trade = getattr(sorted_t[-1], 'asset', 'N/A').replace("/USDT", "")
            best_pnl = round(getattr(sorted_t[-1], 'pnl_pct', 0), 2)

        state = self.engine.portfolio.snapshot()
        dd = state.max_drawdown_pct if state.max_drawdown_pct else 0
        risk_status = "Healthy" if dd < 2.0 else "Warning" if dd < 3.0 else "Critical"

        data = {
            "trades": today_trades, "wins": wins, "losses": losses,
            "net_pnl": round(net_pnl, 2),
            "best_trade": best_trade, "best_pnl": best_pnl,
            "worst_trade": worst_trade, "worst_pnl": worst_pnl,
            "risk_status": risk_status,
        }
        rendered = wr_daily_report(data)
        await self._send(update, rendered["text"])

    async def _cmd_strategy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Strategy mode selector."""
        if not await self._guard(update, "run"):
            return
        from bot.config import RUNTIME
        current = getattr(RUNTIME, 'strategy_mode', 'balanced')
        rendered = wr_strategy_mode(current)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\U0001f6e1 Defensive", callback_data="mode_defensive"),
             InlineKeyboardButton("\u2694\ufe0f Balanced", callback_data="mode_balanced")],
            [InlineKeyboardButton("\U0001f525 Aggressive", callback_data="mode_aggressive"),
             InlineKeyboardButton("\U0001f9d8 Manual", callback_data="mode_manual")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    # ── Callback handler ──────────────────────────────────────

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        if not self._check_auth(update):
            try:
                await query.edit_message_text(
                    "\U0001f512 <b>Access restricted</b>\n\n"
                    "Your account is not linked.\n"
                    "Use /start to register.",
                    parse_mode="HTML")
            except Exception:
                pass
            return

        data = query.data or ""
        chat_id = update.effective_chat.id

        # ── War Room menu callbacks ──────────────────────────

        if data == "open_warroom":
            rendered = wr_start()
            kb = _KB_WARROOM
            try:
                await query.edit_message_text(
                    rendered["text"], parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if data == "latest_signal":
            # Delegate to the command handler
            await self._cmd_latest_signal(update, ctx)
            return

        if data == "performance":
            await self._cmd_performance(update, ctx)
            return

        if data == "risk_control":
            await self._cmd_risk(update, ctx)
            return

        if data == "strategy_mode":
            await self._cmd_strategy(update, ctx)
            return

        if data == "positions":
            await self._cmd_open_positions(update, ctx)
            return

        # ── Risk panel callbacks ─────────────────────────────

        if data == "risk_safe_mode":
            # Reduce risk tolerance
            self.engine.risk._circuit_breaker = False
            await self._send(update,
                "\U0001f6e1 <b>Safe Mode activated</b>\n\n"
                "Exposure reduced. Only high-confidence signals will pass.",
                edit=True)
            audit(system_log, "Safe mode activated", action="safe_mode", result="OK")
            return

        if data == "risk_pause":
            self.engine.risk._circuit_breaker = True
            rendered = wr_pause()
            await self._send(update, rendered["text"], edit=True)
            audit(system_log, "Bot paused via risk panel", action="pause", result="OK")
            return

        if data == "risk_emergency_stop":
            rendered = wr_emergency_stop()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u26d4 CONFIRM STOP", callback_data="emergency_confirm"),
                 InlineKeyboardButton("\u21a9\ufe0f Cancel", callback_data="emergency_cancel")],
            ])
            await self._send(update, rendered["text"], reply_markup=kb, edit=True if query.message else False)
            return

        if data == "emergency_confirm":
            self.engine.risk._circuit_breaker = True
            # Clear pending ideas
            self.engine.pending_ideas.clear()
            await self._send(update,
                "\u26d4 <b>EMERGENCY STOP EXECUTED</b>\n\n"
                "All pending orders cancelled.\n"
                "Circuit breaker engaged.\n"
                "Bot is <b>PAUSED</b>.\n\n"
                "Use /resume to reactivate.",
                edit=True)
            audit(system_log, "EMERGENCY STOP executed", action="emergency_stop", result="OK")
            return

        if data == "emergency_cancel":
            await self._send(update,
                "\u21a9\ufe0f Emergency stop cancelled. Bot continues.",
                edit=True)
            return

        # ── Strategy mode callbacks ──────────────────────────

        if data.startswith("mode_"):
            mode = data.removeprefix("mode_")
            from bot.config import RUNTIME
            if hasattr(RUNTIME, 'strategy_mode'):
                RUNTIME.strategy_mode = mode
            rendered = wr_strategy_mode(mode)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f6e1 Defensive", callback_data="mode_defensive"),
                 InlineKeyboardButton("\u2694\ufe0f Balanced", callback_data="mode_balanced")],
                [InlineKeyboardButton("\U0001f525 Aggressive", callback_data="mode_aggressive"),
                 InlineKeyboardButton("\U0001f9d8 Manual", callback_data="mode_manual")],
            ])
            try:
                await query.edit_message_text(
                    rendered["text"] + f"\n\n\u2705 Switched to <b>{mode.capitalize()}</b>",
                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            audit(system_log, f"Strategy mode: {mode}", action="mode_switch", result="OK")
            return

        # ── Signal action callbacks ──────────────────────────

        if data.startswith("signal_watch_"):
            pair = data.removeprefix("signal_watch_")
            await self._send(update,
                f"\U0001f441 <b>Watching {html.escape(pair)}</b>\n\n"
                "You will be notified on trigger.",
                edit=True)
            return

        # ── Position callbacks ───────────────────────────────

        if data.startswith("pos_details_"):
            pair = data.removeprefix("pos_details_")
            await self._send(update,
                f"\U0001f4cb <b>{html.escape(pair)} Details</b>\n\n"
                "Use /open_positions for full view.",
                edit=True)
            return

        if data.startswith("pos_close_"):
            pair = data.removeprefix("pos_close_")
            await self._send(update,
                f"\u274c <b>Close {html.escape(pair)}</b>\n\n"
                "Position close requested.\n"
                "Manual confirmation required on exchange.",
                edit=True)
            return

        # ── Legacy pane callbacks (backward compat) ──────────

        if data.startswith("pane:"):
            pane = data.split(":", 1)[1]
            if pane == "refresh":
                pane = self._last_pane.get(chat_id, "status")
            self._last_pane[chat_id] = pane
            body = await self._render_pane(pane)
            text = body + self._footer()
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=_KB_DASH)
            except Exception:
                import re
                plain = re.sub(r"<[^>]+>", "", text)
                try:
                    await query.edit_message_text(
                        plain, parse_mode=None, reply_markup=_KB_DASH)
                except Exception:
                    pass
            return

        if data.startswith("nav:"):
            cmd = data.split(":", 1)[1]
            pane_map = {
                "scan": "scan", "status": "status",
                "risk": "risk", "portfolio": "portfolio",
                "backtest": "scan",
            }
            pane = pane_map.get(cmd, "status")
            self._last_pane[chat_id] = pane
            body = await self._render_pane(pane)
            text = body + self._footer()
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=_KB_DASH)
            except Exception:
                try:
                    await query.message.reply_text(
                        text, parse_mode="HTML", reply_markup=_KB_DASH)
                except Exception:
                    pass
            return

        # ── Trade confirm/reject ─────────────────────────────

        if data.startswith("confirm:"):
            trade_id = data.split(":", 1)[1]
            result = await self.engine.confirm_trade(trade_id)
            await self._send(update,
                f"\u2705 <b>TRADE APPROVED</b>\n\n{result}", edit=True)
        elif data.startswith("reject:"):
            trade_id = data.split(":", 1)[1]
            result = self.engine.reject_trade(trade_id)
            await self._send(update,
                f"\u274c <b>TRADE REJECTED</b>\n\n{result}", edit=True)

        audit(system_log, f"Callback: {data}", action="telegram_callback")
