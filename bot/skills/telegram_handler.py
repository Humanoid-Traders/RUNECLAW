"""
RUNECLAW Telegram Handler -- human interface for the trading bot.
Commands: /scan, /analyze, /portfolio, /trade, /risk, /status, /help
Includes inline keyboard for trade confirmation and rate limiting.
"""

from __future__ import annotations

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

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.utils.logger import audit, system_log


class RateLimiter:
    """Simple per-user rate limiter."""

    def __init__(self, max_per_minute: int = 20) -> None:
        self._limit = max_per_minute
        self._calls: dict[int, list[float]] = defaultdict(list)

    def allow(self, user_id: int) -> bool:
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
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        return app

    # -- Command handlers --

    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_rate(update):
            return
        result = await self.registry.get("scan_market").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f50d *Market Scan*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_analyze(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_rate(update):
            return
        args = ctx.args
        symbol = f"{args[0].upper()}/USDT" if args else "BTC/USDT"
        result = await self.registry.get("analyze_asset").execute(  # type: ignore
            self.engine, symbol=symbol)

        # Add confirm/reject buttons if a trade idea was generated
        pending = self.engine.pending_ideas
        if pending:
            idea = pending[-1]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 Confirm", callback_data=f"confirm:{idea.id}"),
                InlineKeyboardButton("\u274c Reject", callback_data=f"reject:{idea.id}"),
            ]])
            await update.message.reply_text(
                f"\U0001f9e0 *Analysis*\n```\n{result}\n```",
                parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text(f"\U0001f9e0 *Analysis*\n```\n{result}\n```",
                                            parse_mode="Markdown")

    async def _cmd_portfolio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_rate(update):
            return
        result = await self.registry.get("get_portfolio").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f4bc *Portfolio*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_trade(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_rate(update):
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
        if not self._check_rate(update):
            return
        result = await self.registry.get("check_risk").execute(self.engine)  # type: ignore
        await update.message.reply_text(f"\U0001f6e1 *Risk Status*\n```\n{result}\n```",
                                        parse_mode="Markdown")

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_rate(update):
            return
        mode = self.engine.mode.value
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

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "\U0001f43e *RUNECLAW Commands*\n\n"
            "/scan - Scan market for top movers\n"
            "/analyze BTC - AI analysis of an asset\n"
            "/portfolio - View paper portfolio\n"
            "/trade - View & confirm pending trades\n"
            "/risk - Risk metrics & circuit breaker\n"
            "/status - Bot status\n"
            "/help - This message",
            parse_mode="Markdown")

    # -- Callback (inline keyboard) --

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
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

    def _check_rate(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(user_id):
            import asyncio
            asyncio.create_task(
                update.message.reply_text("\u26a0\ufe0f Rate limit exceeded. Please wait.")
            )
            return False
        return True
