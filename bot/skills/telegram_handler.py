"""
RUNECLAW Telegram Handler -- human interface for the trading bot.
Commands: /scan, /analyze, /portfolio, /trade, /risk, /macro, /status, /rejected, /halt, /reset, /backtest, /run, /costs, /learn, /patterns, /proposals, /optimize, /help
Includes inline keyboard for trade confirmation and rate limiting.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.config import CONFIG, _env_bool
from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.utils.logger import audit, system_log


class RateLimiter:
    """Simple per-user rate limiter."""

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


class TelegramHandler:
    """Wires Telegram commands to the RuneClaw skill system."""

    def __init__(self, engine: RuneClawEngine, registry: Optional[SkillRegistry] = None) -> None:
        self.engine = engine
        self.registry = registry or build_default_registry()
        self._limiter = RateLimiter(CONFIG.telegram.rate_limit_per_minute)

    def build_app(self) -> Application:
        app = Application.builder().token(CONFIG.telegram.bot_token).build()
        app.add_handler(CommandHandler("scan", self._cmd_scan))
        app.add_handler(CommandHandler("analyze", self._cmd_analyze))
        app.add_handler(CommandHandler("portfolio", self._cmd_portfolio))
        app.add_handler(CommandHandler("trade", self._cmd_trade))
        app.add_handler(CommandHandler("risk", self._cmd_risk))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("rejected", self._cmd_rejected))
        app.add_handler(CommandHandler("halt", self._cmd_halt))
        app.add_handler(CommandHandler("reset", self._cmd_reset))
        app.add_handler(CommandHandler("macro", self._cmd_macro))
        app.add_handler(CommandHandler("backtest", self._cmd_backtest))
        app.add_handler(CommandHandler("walkforward", self._cmd_walkforward))
        app.add_handler(CommandHandler("journal", self._cmd_journal))
        app.add_handler(CommandHandler("costs", self._cmd_costs))
        app.add_handler(CommandHandler("learn", self._cmd_learn))
        app.add_handler(CommandHandler("patterns", self._cmd_patterns))
        app.add_handler(CommandHandler("proposals", self._cmd_proposals))
        app.add_handler(CommandHandler("optimize", self._cmd_optimize))
        app.add_handler(CommandHandler("run", self._cmd_run))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        return app

    # -- Command handlers --

    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("scan_market").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f50d *Market Scan*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_analyze(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        args = ctx.args
        symbol = f"{args[0].upper()}/USDT" if args else "BTC/USDT"

        # C2 fix: snapshot pending IDs before analysis so we can identify the new one
        ids_before = set(idea.id for idea in self.engine.pending_ideas)

        result = await self.registry.get("analyze_asset").execute(  # type: ignore
            self.engine, symbol=symbol)

        # C2 fix: find the newly added idea (if any) rather than using pending[-1]
        new_idea = None
        for idea in self.engine.pending_ideas:
            if idea.id not in ids_before:
                new_idea = idea
                break

        if new_idea is not None:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 Confirm", callback_data=f"confirm:{new_idea.id}"),
                InlineKeyboardButton("\u274c Reject", callback_data=f"reject:{new_idea.id}"),
            ]])
            await update.message.reply_text(
                f"\U0001f9e0 *Analysis*\n```\n{result}\n```",
                parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text(f"\U0001f9e0 *Analysis*\n```\n{result}\n```",
                                            parse_mode="Markdown")

    async def _cmd_portfolio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("get_portfolio").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f4bc *Portfolio*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_trade(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        pending = self.engine.pending_ideas
        if not pending:
            await update.message.reply_text("\U0001f4ad No pending trade ideas.")
            return
        for idea in pending:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 Confirm", callback_data=f"confirm:{idea.id}"),
                InlineKeyboardButton("\u274c Reject", callback_data=f"reject:{idea.id}"),
            ]])
            await update.message.reply_text(
                f"\u23f3 *Pending*: {idea.direction.value} {idea.asset}\n"
                f"Confidence: {idea.confidence:.0%} | R:R {idea.risk_reward_ratio}",
                parse_mode="Markdown", reply_markup=keyboard)

    async def _cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("check_risk").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f6e1 *Risk Status*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        mode = self.engine.state.value
        sim = "SIMULATION" if CONFIG.simulation_mode else "LIVE"
        cb = "TRIPPED" if self.engine.risk.circuit_breaker_active else "OK"
        state = self.engine.portfolio.snapshot()
        await update.message.reply_text(
            f"\U0001f916 *RUNECLAW Status*\n"
            f"Mode: {sim} | Engine: {mode}\n"
            f"Circuit Breaker: {cb}\n"
            f"Equity: ${state.equity_usd:,.2f}\n"
            f"Open Positions: {state.open_positions}",
            parse_mode="Markdown")

    async def _cmd_rejected(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("rejected_trades").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f6ab *Recent Rejections*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_halt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("halt").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f6a8 *EMERGENCY HALT*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Reset circuit breaker. Admin-only, audited."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        was_active = self.engine.risk.circuit_breaker_active
        self.engine.risk.reset_circuit_breaker()
        if was_active:
            await update.message.reply_text(
                "\u2705 Circuit breaker *RESET*. Trading resumed.\n"
                "Loss streak and cooldown cleared. State persisted.",
                parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "\u2139\ufe0f Circuit breaker was not active. No action taken.")

    async def _cmd_macro(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show macro calendar risk state and upcoming events."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("macro_calendar").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f4c5 *Macro Calendar*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_backtest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        args = ctx.args or []
        bars = args[0] if args else "720"
        seed = args[1] if len(args) > 1 else "42"
        await update.message.reply_text("\u23f3 Running backtest... this may take a moment.")
        result = await self.registry.get("run_backtest").execute(  # type: ignore
            self.engine, bars=bars, seed=seed)
        await update.message.reply_text(f"\U0001f4ca *Backtest Results*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_walkforward(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        args = ctx.args or []
        bars = args[0] if args else "1440"
        folds = args[1] if len(args) > 1 else "3"
        await update.message.reply_text("\u23f3 Running walk-forward analysis... this may take a moment.")
        result = await self.registry.get("walk_forward").execute(  # type: ignore
            self.engine, bars=bars, folds=folds)
        await update.message.reply_text(f"\U0001f4c8 *Walk-Forward Results*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("trade_journal").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f4d3 *Trade Journal*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show full agent economics breakdown."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("costs").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f4b0 *Agent Economics*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Execute a predefined trading strategy by natural language name."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        strategy = " ".join(ctx.args) if ctx.args else ""
        if strategy:
            await update.message.reply_text(
                f"\u23f3 Running strategy *{strategy}*... this may take a moment.",
                parse_mode="Markdown")
        result = await self.registry.get("run_strategy").execute(  # type: ignore
            self.engine, strategy=strategy)
        await update.message.reply_text(
            f"\U0001f3af *Strategy Runner*\n```\n{result}\n```",
            parse_mode="Markdown")

    async def _cmd_learn(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show AI learning system dashboard."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("learning").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f9e0 *AI Learning*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_patterns(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show detected market patterns from learning history."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("patterns").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f50d *Patterns*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_proposals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show improvement proposals from AI learning."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("proposals").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f4cb *Proposals*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show LLM token optimization stats."""
        if not await self._check_rate(update):
            return
        if not self._check_auth(update):
            await update.message.reply_text("\u26d4 Unauthorized. Contact the bot owner.")
            return
        result = await self.registry.get("optimize").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\u26a1 *Token Optimizer*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "\U0001f43e *RUNECLAW Commands*\n\n"
            "/scan - Scan market for top movers\n"
            "/analyze BTC - AI analysis of an asset\n"
            "/run - Run a strategy preset (dip, momentum, scalp, full scan)\n"
            "/portfolio - View paper portfolio\n"
            "/trade - View & confirm pending trades\n"
            "/risk - Risk metrics & circuit breaker\n"
            "/macro - Macro calendar & risk state\n"
            "/rejected - Recent risk-rejected trades\n"
            "/backtest - Run backtest (bars seed)\n"
            "/walkforward - Walk-forward analysis (bars folds)\n"
            "/journal - Trade journal with history and PnL\n"
            "/costs - Agent economics & LLM cost breakdown\n"
            "/learn - AI learning dashboard & scores\n"
            "/patterns - Detected market patterns\n"
            "/proposals - Improvement proposals\n"
            "/optimize - LLM token optimizer stats\n"
            "/status - Bot status\n"
            "/halt - Emergency kill-switch\n"
            "/reset - Reset circuit breaker (admin)\n"
            "/help - This message",
            parse_mode="Markdown")

    # -- Callback (inline keyboard) --

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        # H3: auth check on callbacks too
        if not self._check_auth(update):
            await query.edit_message_text("\u26d4 Unauthorized.")
            return

        data = query.data or ""

        if data.startswith("confirm:"):
            trade_id = data.split(":", 1)[1]
            result = await self.engine.confirm_trade(trade_id)
            await query.edit_message_text(f"\u2705 {result}")
        elif data.startswith("reject:"):
            trade_id = data.split(":", 1)[1]
            result = self.engine.reject_trade(trade_id)
            await query.edit_message_text(f"\u274c {result}")

        audit(system_log, f"Callback: {data}", action="telegram_callback")

    # -- Helpers --

    def _check_auth(self, update: Update) -> bool:
        """H3: Verify the message comes from an authorized chat.
        Fail-closed: if TELEGRAM_CHAT_ID is not configured AND
        TELEGRAM_ALLOW_OPEN is not explicitly set, reject all.
        This prevents accidental open-access in production."""
        allowed = CONFIG.telegram.chat_id
        if not allowed:
            # F-02 fix: fail-closed unless explicitly opted in to open mode
            if _env_bool("TELEGRAM_ALLOW_OPEN", False):
                return True  # explicit open-mode opt-in for development
            audit(system_log,
                  "Telegram command rejected: no TELEGRAM_CHAT_ID configured and TELEGRAM_ALLOW_OPEN is not set",
                  action="auth_check", result="REJECTED")
            return False
        chat_id = str(update.effective_chat.id) if update.effective_chat else ""
        # Support comma-separated list of allowed chat IDs
        allowed_ids = {cid.strip() for cid in allowed.split(",") if cid.strip()}
        return chat_id in allowed_ids

    async def _check_rate(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(user_id):
            try:
                await update.message.reply_text("\u26a0\ufe0f Rate limit exceeded.")
            except Exception:
                pass  # best-effort rate limit reply
            return False
        return True
