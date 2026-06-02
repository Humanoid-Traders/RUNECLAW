"""
RUNECLAW Telegram Handler v6 — MuleRun War Room edition.
War Room branding, tactical signal cards, risk control panel,
strategy mode selector, emergency stop, and Telegram Mini App link.
File-backed user management with roles and admin commands.
"""

from __future__ import annotations

import asyncio
import html
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from bot.compat import UTC
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
from bot.core.signal_tracker import SignalTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider, LLMTier, PROVIDER_CATALOG, DEFAULT_TIER_ROUTING, create_llm_client, llm_complete, resolve_tier_config
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.utils.logger import audit, system_log
from bot.utils.user_store import UserStore
from bot.nlp.intent_router import IntentRouter
from bot.nlp.conversation_store import ConversationStore
from bot.core.proactive_monitor import ProactiveMonitor
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
    _bar,
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
            # F-13 FIX: prune stale user entries to prevent unbounded dict growth
            if len(self._calls) > 500:
                stale = [uid for uid, ts in self._calls.items()
                         if not ts or now - ts[-1] > 300]
                for uid in stale:
                    del self._calls[uid]
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
        self.signal_tracker = SignalTracker()
        self.users = UserStore()
        # Seed admin from .env TELEGRAM_CHAT_ID
        self.users.seed_admin(CONFIG.telegram.chat_id)
        # Natural-language intent router (Move 1)
        self.intent_router = IntentRouter()
        # Conversation memory (Move 3 — multi-turn context)
        self.conversations = ConversationStore(
            max_messages_per_user=50,
            max_users=200,
            persist_path="data/conversations.jsonl",
            context_window=10,
        )
        # Proactive alert monitor (Move 2)
        self.monitor = ProactiveMonitor(engine)

    def build_app(self) -> Application:
        app = Application.builder().token(CONFIG.telegram.bot_token).build()
        for cmd, handler in [
            ("start", self._cmd_start), ("dashboard", self._cmd_dashboard),
            ("scan", self._cmd_scan), ("analyze", self._cmd_analyze),
            ("portfolio", self._cmd_portfolio), ("trade", self._cmd_trade),
            ("risk", self._cmd_risk), ("status", self._cmd_status),
            ("rejected", self._cmd_rejected), ("halt", self._cmd_halt),
            ("reset", self._cmd_reset), ("macro", self._cmd_macro),
            ("whynot", self._cmd_whynot),
            ("backtest", self._cmd_backtest), ("walkforward", self._cmd_walkforward),
            ("journal", self._cmd_journal), ("costs", self._cmd_costs),
            ("run", self._cmd_run), ("learn", self._cmd_learn),
            ("patterns", self._cmd_patterns), ("proposals", self._cmd_proposals),
            ("optimize", self._cmd_optimize), ("help", self._cmd_help),
            # Strategy preset shortcuts (aliases for /run <name>)
            ("momentum", self._cmd_momentum), ("dip", self._cmd_dip),
            ("scalp", self._cmd_scalp),
            ("intraday", self._cmd_intraday),
            ("swing", self._cmd_swing),
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
            # Signal stats
            ("signals", self._cmd_signals),
            # Admin commands
            ("approve", self._cmd_approve), ("revoke", self._cmd_revoke),
            ("users", self._cmd_users),
            # LLM BYOK commands
            ("setllm", self._cmd_setllm), ("llmstatus", self._cmd_llmstatus),
            ("llmreset", self._cmd_llmreset), ("llmtiers", self._cmd_llmtiers),
            # Proactive alerts
            ("watch", self._cmd_watch),
            # Live trading commands
            ("golive", self._cmd_golive), ("livebalance", self._cmd_livebalance),
            ("livepositions", self._cmd_livepositions), ("liveclose", self._cmd_liveclose),
            ("buy", self._cmd_buy), ("sell", self._cmd_sell),
            ("health", self._cmd_health),
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
        # Determine the right send method based on context
        if edit and update.callback_query:
            method = update.callback_query.edit_message_text
        elif update.callback_query and update.callback_query.message:
            # Callback context but not editing — reply to the callback message
            method = update.callback_query.message.reply_text
        elif update.message:
            method = update.message.reply_text
        else:
            return  # No valid target
        try:
            await method(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
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
        "You are RUNECLAW, a sharp crypto trading assistant built by Humanoid Traders. "
        "You talk like a knowledgeable trading buddy — direct, opinionated on markets, "
        "and natural. Not a corporate chatbot.\n\n"
        "Personality:\n"
        "- Confident but honest. If you don't know, say so plainly.\n"
        "- Use short, punchy sentences. Avoid walls of text.\n"
        "- Match the user's energy — if they're casual, be casual. If they ask "
        "a serious technical question, go deep.\n"
        "- Use trader slang naturally when it fits (\"looking heavy\", \"bid is thin\", "
        "\"reclaim that level\") but don't force it.\n"
        "- Refer to the user by name when it feels natural (not every message).\n"
        "- When a user mentions an asset you discussed before, acknowledge it "
        "(\"back to BTC — \", \"still watching SOL?\").\n"
        "- If the user says thanks, goodbye, or just chats — respond warmly and briefly. "
        "Don't force a trading topic.\n\n"
        "Rules:\n"
        "- Keep answers under 150 words unless they ask for detail.\n"
        "- Use plain text. No markdown, no bullet lists unless the user asks for structure.\n"
        "- Never give financial advice. Frame as analysis: \"the chart suggests\" not \"you should buy\".\n"
        "- When relevant, mention a specific command they could use (/scan, /analyze BTC, etc.) "
        "but weave it in naturally, don't list commands robotically.\n"
        "- You remember the conversation. Don't repeat yourself. Build on what was discussed.\n"
    )

    # Varied thinking indicators instead of same one every time
    _THINKING_PHRASES = [
        "\U0001f9e0 <i>Thinking...</i>",
        "\U0001f4ad <i>Let me check...</i>",
        "\u23f3 <i>One sec...</i>",
        "\U0001f50d <i>Looking into it...</i>",
        "\U0001f9e0 <i>On it...</i>",
    ]

    def _build_chat_system_prompt(self, user_id: str, user_name: str = "") -> str:
        """Build a personalized system prompt with user context."""
        base = self._CHAT_SYSTEM_PROMPT

        # Inject user-specific context
        portfolio_summary = ""
        engine_state = ""
        try:
            state = self.engine.portfolio.snapshot()
            portfolio_summary = (
                f"{state.open_positions} open positions, "
                f"balance ~${CONFIG.paper_balance_usd:,.0f}"
            )
            cb = self.engine.risk.circuit_breaker_active
            mode = "LIVE" if not CONFIG.simulation_mode else "PAPER"
            engine_state = f"{mode} mode, CB={'ON' if cb else 'OFF'}"
        except Exception:
            pass

        # Add time awareness
        import datetime as _dt
        hour = _dt.datetime.now(UTC).hour
        if 5 <= hour < 12:
            time_note = "It's morning UTC."
        elif 12 <= hour < 17:
            time_note = "It's afternoon UTC."
        elif 17 <= hour < 22:
            time_note = "It's evening UTC."
        else:
            time_note = "It's late night UTC."

        context_block = self.conversations.build_context_prompt(
            user_id,
            portfolio_summary=portfolio_summary,
            engine_state=engine_state,
            user_name=user_name,
        )
        return base + f"\n{time_note}" + context_block

    async def _llm_chat(self, question: str, user_id: str = "",
                        user_name: str = "") -> str:
        """Send a free-text question to the LLM with multi-turn context.

        Uses CHAT tier routing with automatic fallback chain:
        Groq → Gemini → Anthropic → primary .env provider.
        If all fail, returns a helpful error with the actual reason.
        """
        import asyncio

        # Resolve active LLM config (BYOK runtime > .env)
        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
            timeout_seconds=CONFIG.llm.timeout_seconds,
        )
        active_cfg = BYOK.get_active_config(env_config)

        # Build personalized system prompt
        system_prompt = self._build_chat_system_prompt(
            user_id, user_name=user_name)

        # Get conversation history for multi-turn context
        history = []
        if user_id:
            history = self.conversations.get_recent_as_llm_messages(
                user_id, limit=8)

        # Build fallback chain: chat tier → fallback providers → primary
        import os
        configs_to_try = []

        # 1. Primary chat tier config
        chat_cfg = resolve_tier_config(LLMTier.CHAT, active_cfg)
        if chat_cfg.is_configured():
            configs_to_try.append(("chat_tier", chat_cfg))

        # 2. Fallback providers from env (Gemini, Anthropic, Alibaba)
        _FALLBACK_PROVIDERS = [
            (LLMProvider.GEMINI, "GEMINI_API_KEY", "gemini-2.0-flash"),
            (LLMProvider.ANTHROPIC, "ANTHROPIC_API_KEY", "claude-haiku-4-5"),
            (LLMProvider.ALIBABA, "ALIBABA_API_KEY", "qwen3.6-plus"),
        ]
        for provider, key_env, model in _FALLBACK_PROVIDERS:
            api_key = os.getenv(key_env, "")
            if api_key and not any(
                c.provider == provider for _, c in configs_to_try
            ):
                catalog = PROVIDER_CATALOG.get(provider, {})
                configs_to_try.append(("fallback", LLMConfig(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    base_url=catalog.get("base_url", ""),
                    timeout_seconds=20.0,
                )))

        # 3. Primary config as last resort
        if active_cfg.is_configured() and not any(
            c.provider == active_cfg.provider for _, c in configs_to_try
        ):
            configs_to_try.append(("primary", active_cfg))

        if not configs_to_try:
            return "No LLM configured. Use /setllm to set a provider, or add LLM_API_KEY to .env."

        # Try each config in order
        last_error = ""
        for source, cfg in configs_to_try:
            try:
                client = create_llm_client(cfg)
                if client is None:
                    continue

                answer = await llm_complete(
                    client, cfg, system_prompt, question,
                    history=history)

                # Track cost
                if cfg.sdk_type() != "anthropic" and hasattr(self.engine, 'cost'):
                    history_tokens = sum(len(m.get("content", "")) // 4
                                         for m in history)
                    self.engine.cost.record_llm(
                        model=cfg.model,
                        prompt_tokens=500 + history_tokens,
                        completion_tokens=256,
                        category="chat",
                    )

                if source != "chat_tier":
                    audit(system_log,
                          f"Chat used fallback: {cfg.provider.value}/{cfg.model}",
                          action="chat_fallback", result="OK")

                return answer.strip()

            except asyncio.TimeoutError:
                last_error = f"timeout ({cfg.provider.value})"
                audit(system_log, f"Chat timeout on {cfg.provider.value}",
                      action="chat_timeout", result="FALLBACK")
                continue
            except Exception as e:
                error_str = str(e)
                last_error = f"{cfg.provider.value}: {error_str[:100]}"
                audit(system_log, f"Chat LLM error ({cfg.provider.value}): {e}",
                      action="chat_error", result="FALLBACK")
                continue

        # All providers failed
        audit(system_log, f"All chat LLM providers failed. Last: {last_error}",
              action="chat_error", result="ALL_FAILED")
        return (
            "All my LLM providers are down right now. "
            f"Last error: {last_error[:80]}. "
            "Try again in a minute, or use a command like /scan or /analyze."
        )

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle free-text messages — intent routing + AI chat fallback.

        Move 1: Natural-language intent router. Maps free text to skills
        via rule-based patterns first, then optional LLM classification.
        Falls back to general AI chat if no intent matches.
        """
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

        # ── Intent routing (Move 1) ──────────────────────────────
        # Try to map free text to a skill before falling back to chat
        intent = self.intent_router.classify_rules(text)

        # Get user's display name for personalization
        user_name = ""
        if update.effective_user and update.effective_user.first_name:
            user_name = update.effective_user.first_name

        if intent.matched and intent.confidence >= 0.8:
            # High-confidence match — dispatch to skill
            skill = self.registry.get(intent.skill)
            if skill:
                audit(system_log, f"NL intent routed: '{text[:50]}' -> {intent.skill}",
                      action="intent_dispatch", result=intent.skill,
                      data={"confidence": intent.confidence, "source": intent.source})
                # Store intent-routed message in conversation memory
                self.conversations.append(tg_id, "user", text,
                                           metadata={"intent": intent.skill})
                try:
                    result = await skill.execute(self.engine, **intent.kwargs)
                    # Store skill result as assistant message (truncated)
                    self.conversations.append(tg_id, "assistant",
                                               f"[{intent.skill}] executed successfully",
                                               metadata={"skill": intent.skill})
                    await self._send(update, result)
                except Exception as exc:
                    await self._send(update,
                        f"\u26a0\ufe0f Could not execute: {exc}\n\n"
                        f"Try the direct command instead: /{intent.skill.replace('_', '')}")
                return

        if intent.matched and intent.confidence >= 0.5 and not intent.kwargs.get("symbol"):
            # Partial match — skill needs a symbol we couldn't extract
            await self._send(update,
                f"\U0001f50d I think you want to <b>{intent.explanation.lower()}</b>, "
                f"but I couldn't identify which asset.\n\n"
                f"Try: <code>/analyze BTC</code> or say <i>\"analyze Bitcoin\"</i>")
            return

        # ── Fallback: AI chat ─────────────────────────────────────
        # Store user message in conversation memory
        self.conversations.append(tg_id, "user", text,
                                   metadata={"intent": intent.skill or "chat"})

        # Pick a varied thinking indicator
        import random
        thinking = random.choice(self._THINKING_PHRASES)
        await self._send(update, thinking)

        answer = await self._llm_chat(text, user_id=tg_id, user_name=user_name)

        # Store assistant response in conversation memory
        self.conversations.append(tg_id, "assistant", answer)

        # Don't wrap in rigid header for short/social responses
        if len(answer) < 100 or (intent.is_social if hasattr(intent, 'is_social') else False):
            await self._send(update, html.escape(answer))
        else:
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
                "\U0001f512 <b>Access restricted</b>\n\n"
                "Your account is not linked yet.\n"
                f"Your Telegram ID: <code>{tg_id}</code>\n\n"
                "Use /start to register, then wait for admin approval.")
            return False

        # Role-based permission check
        if command and not self.users.has_permission(tg_id, command):
            role = user.get("role", "pending")
            await self._send(update,
                "\U0001f512 <b>Insufficient permissions</b>\n\n"
                f"Your role (<code>{role}</code>) cannot use <code>/{command}</code>.\n"
                "Contact an admin for access.")
            return False

        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            await self._send(update, "\u26a0\ufe0f Rate limit. Wait a moment.")
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
                "\u2022 21 fail-closed risk checks\n"
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
            "  /signals       Signal history\n"
            "\n"
            " MARKET\n"
            "  /scan          Market scanner\n"
            "  /scalp         Scalp scan (5m)\n"
            "  /intraday      Intraday scan (15m)\n"
            "  /swing         Swing scan (4h)\n"
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
            "  /rejected      Rejected trades\n"
            "  /whynot [SYM]  Why was it rejected\n"
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
            "  /watch on|off  Proactive alerts\n"
            "\n"
            " LLM BYOK\n"
            "  /setllm        Switch provider\n"
            "  /llmstatus     Current LLM\n"
            "  /llmtiers      Tier routing\n"
            "  /llmreset      Reset to .env\n"
            "\n"
            " LIVE TRADING\n"
            "  /golive        Enable live mode\n"
            "  /buy BTC 5     Buy $5 of BTC\n"
            "  /sell BTC      Sell all BTC\n"
            "  /livebalance   Bitget balance\n"
            "  /livepositions Open positions\n"
            "  /liveclose ID  Close position\n"
            "  /health        System health\n"
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
            "\U0001f4ac <i>You can also type naturally:\n"
            "\"how's BTC?\", \"what's moving?\", \"check my portfolio\"</i>\n\n"
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
                "All 21 risk checks still apply. Meme tokens (BONK, WIF) "
                "use tighter volatility and correlation limits.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        else:
            await self._send(update, (
                "\U0001f30d <b>ALL MARKETS MODE</b>\n\n"
                "Scanner now covers all Bitget USDT pairs.\n"
                "Use <code>/mode solana</code> to focus on Solana ecosystem."
            ))

    # ── Live Trading Commands ─────────────────────────────────

    async def _cmd_golive(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/golive — enable live trading with double confirmation."""
        if not await self._guard(update, "admin"):
            return

        args = ctx.args or []
        if not args or args[0].upper() != "CONFIRM":
            await self._send(update,
                "\u26a0\ufe0f <b>LIVE TRADING ACTIVATION</b>\n\n"
                "This will enable <b>real order execution</b> on Bitget.\n\n"
                "Micro-test safety limits:\n"
                "\u2022 Max $10 per position\n"
                "\u2022 Max $50 total exposure\n"
                "\u2022 Max 5 concurrent positions\n"
                "\u2022 Spot market only\n\n"
                "To confirm, type:\n<code>/golive CONFIRM</code>")
            return

        # Enable live mode via RuntimeState (CONFIG is frozen)
        from bot.config import RUNTIME
        RUNTIME.live_mode = True
        audit(system_log, "LIVE TRADING ENABLED via /golive",
              action="golive", result="ENABLED",
              data={"user": self._get_tg_id(update)})
        await self._send(update,
            "\U0001f7e2 <b>LIVE TRADING ENABLED</b>\n\n"
            "Real orders will execute on Bitget.\n"
            "Micro-test limits active ($10/pos, $50 total).\n\n"
            "\u2022 <code>/livebalance</code> — check USDT balance\n"
            "\u2022 <code>/livepositions</code> — view open positions\n"
            "\u2022 <code>/liveclose &lt;id&gt;</code> — close a position\n"
            "\u2022 <code>/golive OFF</code> — disable live mode")

    async def _cmd_livebalance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livebalance — check real USDT balance + spot holdings on Bitget."""
        if not await self._guard(update, "scan"):
            return
        try:
            bal = await self.engine.live_executor.fetch_balance()
            total = bal.get("total", 0)
            free = bal.get("free", 0)
            used = bal.get("used", 0)
            holdings = bal.get("holdings", [])

            lines = [
                "\U0001f4b0 <b>BITGET BALANCE</b>\n",
                f"  USDT Total: <code>${total:.2f}</code>",
                f"  USDT Free:  <code>${free:.2f}</code>",
                f"  USDT Used:  <code>${used:.2f}</code>",
            ]

            if holdings:
                lines.append("\n\U0001f4e6 <b>SPOT HOLDINGS</b>\n")
                # Try to fetch prices for USD value
                exchange = await self.engine.live_executor._get_exchange()
                total_usd = total  # start with USDT
                for h in sorted(holdings, key=lambda x: x["asset"]):
                    asset = h["asset"]
                    qty = h["total"]
                    symbol = f"{asset}/USDT"
                    usd_val = 0.0
                    price = 0.0
                    try:
                        ticker = await exchange.fetch_ticker(symbol)
                        price = float(ticker.get("last", 0))
                        usd_val = qty * price
                        total_usd += usd_val
                    except Exception:
                        pass  # unlisted pair or no ticker

                    if usd_val >= 0.01:
                        lines.append(
                            f"  <b>{asset}</b>  "
                            f"<code>{qty:.8g}</code>  "
                            f"~<code>${usd_val:.2f}</code>"
                        )
                    else:
                        lines.append(
                            f"  <b>{asset}</b>  "
                            f"<code>{qty:.8g}</code>  "
                            f"<i>dust</i>"
                        )

                lines.append(f"\n  \U0001f4b5 <b>Total Value: ~${total_usd:.2f}</b>")
            else:
                lines.append("\n<i>No spot holdings (USDT only)</i>")

            await self._send(update, "\n".join(lines))
        except Exception as exc:
            await self._send(update, f"\u274c Balance fetch failed: {exc}")

    async def _cmd_livepositions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livepositions — show live open positions."""
        if not await self._guard(update, "scan"):
            return
        positions = self.engine.live_executor._positions
        open_pos = [p for p in positions.values() if p.status == "open"]
        if not open_pos:
            await self._send(update, "\U0001f4ad No live positions open.")
            return
        lines = ["\U0001f4ca <b>LIVE POSITIONS</b>\n"]
        for p in open_pos:
            lines.append(
                f"\u2022 <b>{p.direction} {p.symbol}</b>\n"
                f"  Entry: ${p.entry_price:,.4f} | Qty: {p.quantity:.6f}\n"
                f"  SL: ${p.stop_loss:,.4f} | TP: ${p.take_profit:,.4f}\n"
                f"  ID: <code>{p.trade_id}</code>"
            )
        await self._send(update, "\n".join(lines))

    async def _cmd_liveclose(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/liveclose <trade_id> — manually close a live position."""
        if not await self._guard(update, "admin"):
            return
        args = ctx.args or []
        if not args:
            await self._send(update, "Usage: <code>/liveclose TRADE_ID</code>")
            return
        trade_id = args[0]
        result = await self.engine.live_executor.close_position(trade_id, "manual_telegram")
        await self._send(update, f"\U0001f510 {result}")

    async def _cmd_buy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/buy BTC 5 — buy $5 worth of BTC/USDT on Bitget spot."""
        if not await self._guard(update, "admin"):
            return

        args = ctx.args or []
        if len(args) < 1:
            await self._send(update,
                "\U0001f6d2 <b>SPOT BUY</b>\n\n"
                "<b>Usage:</b>\n"
                "<code>/buy BTC 5</code>  — buy $5 of BTC\n"
                "<code>/buy SOL 10</code> — buy $10 of SOL\n"
                "<code>/buy ETH</code>    — buy $5 of ETH (default)\n\n"
                "\u26a0\ufe0f Micro-test limits: $10/trade, $50 total.\n"
                "Requires <code>/golive CONFIRM</code> first.")
            return

        asset = args[0].upper().replace("/USDT", "")
        symbol = f"{asset}/USDT"
        amount_usd = 5.0  # default
        if len(args) >= 2:
            try:
                amount_usd = float(args[1])
            except ValueError:
                await self._send(update, "\u274c Invalid amount. Use: <code>/buy BTC 5</code>")
                return

        if amount_usd <= 0 or amount_usd > 10:
            await self._send(update,
                f"\u274c Amount must be $0.01 – $10.00 (micro-test limit).\n"
                f"You entered: ${amount_usd:.2f}")
            return

        # Check live mode
        from bot.config import RUNTIME
        if not RUNTIME.live_mode:
            await self._send(update,
                "\U0001f512 <b>Live trading is OFF</b>\n\n"
                "Enable with <code>/golive CONFIRM</code> first.")
            return

        await self._send(update,
            f"\u23f3 Placing market BUY: <b>{symbol}</b> — ${amount_usd:.2f}...")

        result = await self.engine.live_executor.buy_spot(symbol, amount_usd)

        if "error" in result:
            await self._send(update,
                f"\u274c <b>BUY FAILED</b>\n\n"
                f"<code>{result['error']}</code>")
            return

        # Success
        bar = _bar(amount_usd / 10.0, 1.0, 10)  # 10 = micro limit
        await self._send(update,
            f"\u2705 <b>SPOT BUY FILLED</b>\n\n"
            f"\U0001f4b0 <b>{symbol}</b>\n"
            f"  Qty:   <code>{result['qty']:.8f}</code>\n"
            f"  Price: <code>${result['price']:,.4f}</code>\n"
            f"  Cost:  <code>${result['cost']:.2f}</code>\n"
            f"  Order: <code>{result['order_id']}</code>\n\n"
            f"  Budget {bar} ${amount_usd:.0f}/$10\n\n"
            f"\U0001f4a1 Sell with: <code>/sell {asset}</code>")

    async def _cmd_sell(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/sell BTC [qty] — sell spot asset on Bitget."""
        if not await self._guard(update, "admin"):
            return

        args = ctx.args or []
        if len(args) < 1:
            await self._send(update,
                "\U0001f4b8 <b>SPOT SELL</b>\n\n"
                "<b>Usage:</b>\n"
                "<code>/sell BTC</code>     — sell all BTC\n"
                "<code>/sell SOL 0.5</code> — sell 0.5 SOL\n\n"
                "\u26a0\ufe0f Requires <code>/golive CONFIRM</code> first.")
            return

        asset = args[0].upper().replace("/USDT", "")
        symbol = f"{asset}/USDT"
        qty = 0.0
        sell_all = True
        if len(args) >= 2:
            try:
                qty = float(args[1])
                sell_all = False
            except ValueError:
                await self._send(update, "\u274c Invalid quantity. Use: <code>/sell BTC 0.001</code>")
                return

        # Check live mode
        from bot.config import RUNTIME
        if not RUNTIME.live_mode:
            await self._send(update,
                "\U0001f512 <b>Live trading is OFF</b>\n\n"
                "Enable with <code>/golive CONFIRM</code> first.")
            return

        action_desc = "all" if sell_all else f"{qty}"
        await self._send(update,
            f"\u23f3 Placing market SELL: <b>{symbol}</b> — {action_desc}...")

        result = await self.engine.live_executor.sell_spot(symbol, qty=qty, sell_all=sell_all)

        if "error" in result:
            await self._send(update,
                f"\u274c <b>SELL FAILED</b>\n\n"
                f"<code>{result['error']}</code>")
            return

        await self._send(update,
            f"\u2705 <b>SPOT SELL FILLED</b>\n\n"
            f"\U0001f4b8 <b>{symbol}</b>\n"
            f"  Qty:      <code>{result['qty']:.8f}</code>\n"
            f"  Price:    <code>${result['price']:,.4f}</code>\n"
            f"  Proceeds: <code>${result['proceeds']:.2f}</code>\n"
            f"  Order:    <code>{result['order_id']}</code>")

    # ── Proactive Alerts (Move 2) ──────────────────────────────

    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show system health status."""
        if not await self._guard(update, "status"):
            return
        text = self.engine.health.format_telegram()
        await self._send(update, text)

    async def _cmd_watch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/watch [on|off|status] — toggle proactive alerts for this chat."""
        if not await self._guard(update, "scan"):
            return

        tg_id = self._get_tg_id(update)
        args = ctx.args or []
        action = args[0].lower() if args else "status"

        if action == "on":
            self.monitor.enable_chat(tg_id)
            await self._send(update,
                "\U0001f514 <b>PROACTIVE ALERTS ON</b>\n\n"
                "I'll push alerts for:\n"
                "\u2022 Volume spikes on scanned assets\n"
                "\u2022 Circuit breaker state changes\n"
                "\u2022 Black-swan anomaly detections\n"
                "\u2022 New trade signals pending confirmation\n"
                "\u2022 Engine state changes (halt/cooldown)\n\n"
                "Use <code>/watch off</code> to disable.")
        elif action == "off":
            self.monitor.disable_chat(tg_id)
            await self._send(update,
                "\U0001f515 <b>PROACTIVE ALERTS OFF</b>\n\n"
                "You won't receive unsolicited alerts.\n"
                "Use <code>/watch on</code> to re-enable.")
        else:
            enabled = self.monitor.is_enabled(tg_id)
            status = "\U0001f7e2 ON" if enabled else "\U0001f534 OFF"
            await self._send(update,
                f"\U0001f514 <b>WATCH STATUS</b>: {status}\n\n"
                f"Active watchers: {self.monitor.enabled_chat_count}\n\n"
                f"Use <code>/watch on</code> or <code>/watch off</code> to toggle.")

    async def start_monitor(self, bot) -> None:
        """Start the proactive monitor background task.
        Called from main.py after the Telegram app is initialized."""
        async def _send_fn(chat_id: str, text: str) -> None:
            try:
                await bot.send_message(
                    chat_id=int(chat_id), text=text, parse_mode="HTML")
            except Exception:
                pass
        self._monitor_task = asyncio.create_task(self.monitor.run(_send_fn))

    async def stop_monitor(self) -> None:
        """Stop the proactive monitor."""
        self.monitor.stop()
        if hasattr(self, '_monitor_task'):
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

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

    # ── LLM BYOK commands ────────────────────────────────────

    async def _cmd_setllm(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/setllm <provider> [api_key] [model] — switch LLM provider at runtime."""
        if not await self._guard(update, "mode"):
            return

        args = ctx.args or []
        if not args:
            providers = ", ".join(p.value for p in LLMProvider if p != LLMProvider.CUSTOM)
            await self._send(update,
                "\U0001f916 <b>BYOK — Bring Your Own Key</b>\n\n"
                "<pre>"
                " /setllm &lt;provider&gt; &lt;api_key&gt;\n"
                " /setllm groq gsk_your_key\n"
                " /setllm ollama\n"
                " /setllm anthropic sk-ant-key\n"
                " /setllm openai sk-key gpt-4o-mini\n"
                "</pre>\n\n"
                f"<b>Providers:</b> <code>{providers}</code>\n\n"
                "<i>Keys are stored in memory only — never saved to disk or logs.</i>")
            return

        provider_str = args[0].lower()
        api_key = args[1] if len(args) > 1 else ""
        model = args[2] if len(args) > 2 else ""

        ok, msg = BYOK.set_provider(provider_str, api_key=api_key, model=model)
        if ok:
            # Refresh the analyzer's LLM client to use new provider
            if hasattr(self.engine, 'analyzer') and hasattr(self.engine.analyzer, 'refresh_llm_client'):
                self.engine.analyzer.refresh_llm_client()
            audit(system_log, f"LLM provider switched to {provider_str}",
                  action="setllm", result="OK",
                  data={"provider": provider_str, "model": model or "default"})
        await self._send(update, html.escape(msg))

    async def _cmd_llmstatus(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmstatus — show current LLM provider and key fingerprint."""
        if not await self._guard(update, "status"):
            return

        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
        )
        status = BYOK.status(env_config)
        await self._send(update, f"<pre>{html.escape(status)}</pre>")

    async def _cmd_llmreset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmreset — clear runtime LLM key, revert to .env settings."""
        if not await self._guard(update, "mode"):
            return

        msg = BYOK.reset()
        # Refresh analyzer client back to .env config
        if hasattr(self.engine, 'analyzer') and hasattr(self.engine.analyzer, 'refresh_llm_client'):
            self.engine.analyzer.refresh_llm_client()
        audit(system_log, "LLM config reset to .env", action="llmreset", result="OK")
        await self._send(update, html.escape(msg))

    async def _cmd_llmtiers(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmtiers — show multi-tier LLM routing configuration."""
        if not await self._guard(update, "status"):
            return

        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
        )
        active_cfg = BYOK.get_active_config(env_config)

        lines = ["\U0001f3af <b>Multi-Tier LLM Routing</b>\n"]
        for tier in LLMTier:
            tier_cfg = resolve_tier_config(tier, active_cfg)
            provider_name = tier_cfg.provider.value if isinstance(tier_cfg.provider, LLMProvider) else str(tier_cfg.provider)
            default_route = DEFAULT_TIER_ROUTING.get(tier, {})
            is_custom = tier_cfg != active_cfg
            source = "tier-routed" if is_custom else "primary"
            configured = "\u2705" if tier_cfg.is_configured() else "\u274c"
            lines.append(
                f"{configured} <b>{tier.value.upper()}</b>: "
                f"<code>{provider_name}</code> / <code>{tier_cfg.model}</code>\n"
                f"   Source: {source} | {default_route.get('reason', 'default')}"
            )

        lines.append(
            "\n<i>Set per-tier routing via env:\n"
            "  LLM_TIER_SCAN_PROVIDER=groq\n"
            "  LLM_TIER_THESIS_PROVIDER=gemini\n"
            "  GEMINI_API_KEY=AIza...</i>"
        )
        await self._send(update, "\n".join(lines))

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
            uid = update.effective_user.id if update.effective_user else ""
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 APPROVE", callback_data=f"confirm:{new_idea.id}:{uid}"),
                InlineKeyboardButton("\u274c PASS", callback_data=f"reject:{new_idea.id}:{uid}"),
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
            uid = update.effective_user.id if update.effective_user else ""
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 APPROVE", callback_data=f"confirm:{idea.id}:{uid}"),
                InlineKeyboardButton("\u274c PASS", callback_data=f"reject:{idea.id}:{uid}"),
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
            "daily_loss_limit": CONFIG.risk.max_daily_loss_pct,
            "current_drawdown": round(state.max_drawdown_pct, 2) if state.max_drawdown_pct else 0.0,
            "max_open_trades": CONFIG.risk.max_open_positions,
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

    async def _cmd_whynot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/whynot [symbol] — explain why a trade was rejected by risk."""
        if not await self._guard(update, "rejected"):
            return
        args = ctx.args or []
        symbol = args[0].upper().strip() if args else ""
        result = await self.registry.get("whynot").execute(
            self.engine, symbol=symbol)
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

    async def _cmd_momentum(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Shortcut for /run momentum."""
        if not await self._guard(update, "run"):
            return
        await self._send(update, "\u23f3 <i>Running Momentum Hunter...</i>")
        result = await self.registry.get("run_strategy").execute(
            self.engine, strategy="momentum")
        await self._send(update, result)

    async def _cmd_dip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Shortcut for /run dip."""
        if not await self._guard(update, "run"):
            return
        await self._send(update, "\u23f3 <i>Running BTC Dip Sniper...</i>")
        result = await self.registry.get("run_strategy").execute(
            self.engine, strategy="dip")
        await self._send(update, result)

    async def _cmd_scalp(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Rich scalp scan: 5m candles, tight SL, top-3 by volume."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "\u26a1 <i>Running Scalp Scan (5m)...</i>")
        result = await self.registry.get("pro_scan").execute(
            self.engine, mode="scalp")
        await self._send(update, result)

    async def _cmd_intraday(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Rich intraday scan: 15m candles, top-5 movers."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "\U0001f4ca <i>Running Intraday Scan (15m)...</i>")
        result = await self.registry.get("pro_scan").execute(
            self.engine, mode="intraday")
        await self._send(update, result)

    async def _cmd_swing(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Rich swing scan: 4h candles, wide SL/TP, trend-based."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "\U0001f30a <i>Running Swing Scan (4h)...</i>")
        result = await self.registry.get("pro_scan").execute(
            self.engine, mode="swing")
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
        uid = update.effective_user.id if update.effective_user else ""
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2705 Approve Trade", callback_data=f"confirm:{idea.id}:{uid}")],
            [InlineKeyboardButton("\U0001f441 Watch Only", callback_data=f"signal_watch_{idea.asset}")],
            [InlineKeyboardButton("\u274c Reject", callback_data=f"reject:{idea.id}:{uid}")],
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
        trades = self.engine.portfolio.trade_history
        today_trades = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = (wins / today_trades * 100) if today_trades > 0 else 0
        # Find best/worst pairs
        best_pair = "N/A"
        worst_pair = "N/A"
        if trades:
            sorted_t = sorted(trades, key=lambda t: t.pnl)
            worst_pair = sorted_t[0].asset.replace("/USDT", "")
            best_pair = sorted_t[-1].asset.replace("/USDT", "")

        data = {
            "today_pnl": round(-state.max_drawdown_pct, 2) if state.max_drawdown_pct else 0.0,
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
        self.engine.risk.emergency_halt("pause_telegram")
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
        trades = self.engine.portfolio.trade_history
        today_trades = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = today_trades - wins
        net_pnl = sum(t.pnl for t in trades)
        best_trade = "N/A"
        best_pnl = 0.0
        worst_trade = "N/A"
        worst_pnl = 0.0
        if trades:
            sorted_t = sorted(trades, key=lambda t: t.pnl)
            worst_trade = sorted_t[0].asset.replace("/USDT", "")
            worst_pnl = round(sorted_t[0].pnl, 2)
            best_trade = sorted_t[-1].asset.replace("/USDT", "")
            best_pnl = round(sorted_t[-1].pnl, 2)

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
        current = RUNTIME.strategy_mode
        rendered = wr_strategy_mode(current)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\U0001f6e1 Defensive", callback_data="mode_defensive"),
             InlineKeyboardButton("\u2694\ufe0f Balanced", callback_data="mode_balanced")],
            [InlineKeyboardButton("\U0001f525 Aggressive", callback_data="mode_aggressive"),
             InlineKeyboardButton("\U0001f9d8 Manual", callback_data="mode_manual")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    # ── Signal stats command ─────────────────────────────────────

    async def _cmd_signals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show per-pair signal stats using SignalTracker."""
        if not await self._guard(update, "scan"):
            return
        text = self.signal_tracker.format_for_telegram()
        await self._send(update, text)

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
            # Safe mode: keep bot running but acknowledge reduced exposure
            await self._send(update,
                "\U0001f6e1 <b>Safe Mode activated</b>\n\n"
                "Exposure reduced. Only high-confidence signals will pass.",
                edit=True)
            audit(system_log, "Safe mode activated", action="safe_mode", result="OK")
            return

        if data == "risk_pause":
            self.engine.risk.emergency_halt("pause_risk_panel")
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
            self.engine.risk.emergency_halt("emergency_stop_telegram")
            # Clear pending ideas (must access the underlying dict, not the property copy)
            self.engine._pending_ideas.clear()
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
            parts = data.split(":")
            trade_id = parts[1]
            # M3 FIX: validate callback belongs to requesting user
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if expected_uid and caller_uid != expected_uid:
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who requested this trade can approve it.",
                    edit=True)
                audit(system_log,
                      f"Callback IDOR blocked: caller={caller_uid} expected={expected_uid}",
                      action="callback_idor_block", result="DENIED")
                return
            result = await self.engine.confirm_trade(trade_id)
            await self._send(update,
                f"\u2705 <b>TRADE APPROVED</b>\n\n{result}", edit=True)
        elif data.startswith("reject:"):
            parts = data.split(":")
            trade_id = parts[1]
            # M3 FIX: validate callback belongs to requesting user
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if expected_uid and caller_uid != expected_uid:
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who requested this trade can reject it.",
                    edit=True)
                audit(system_log,
                      f"Callback IDOR blocked: caller={caller_uid} expected={expected_uid}",
                      action="callback_idor_block", result="DENIED")
                return
            result = self.engine.reject_trade(trade_id)
            await self._send(update,
                f"\u274c <b>TRADE REJECTED</b>\n\n{result}", edit=True)

        audit(system_log, f"Callback: {data}", action="telegram_callback")
