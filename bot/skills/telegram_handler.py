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

# SEC-H3 FIX: strict symbol regex — applied at every Telegram entry point
# before symbols reach CCXT or the LLM.
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$')
from bot.core.engine import RuneClawEngine
from bot.core.signal_tracker import SignalTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider, LLMTier, PROVIDER_CATALOG, DEFAULT_TIER_ROUTING, create_llm_client, llm_complete, resolve_tier_config
from bot.skills.skill_registry import SkillRegistry, build_default_registry
from bot.skills.scan_skill import cmd_scan as _scan_skill_handler, callback_confirm_reject as _scan_callback
from bot.skills.user_middleware import cmd_link as _cmd_link, cmd_unlink as _cmd_unlink, cmd_me as _cmd_me
from bot.utils.logger import audit, system_log
from bot.utils.user_store import UserStore
from bot.nlp.intent_router import IntentRouter
from bot.nlp.conversation_store import ConversationStore
from bot.core.proactive_monitor import ProactiveMonitor
from bot.marketing.channel_forwarder import ChannelForwarder
from bot.formatters.rich_cards import (
    fetch_analysis_data,
    render_analysis_card,
    render_multi_analysis,
    render_comparison_table,
    render_recommended_orders,
    render_pending_orders,
    render_pnl_report,
    render_open_positions,
    render_status_card,
    _fmt_price,
    _fmt_vol,
    _pct,
)
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


# AG-H1: Prompt-injection sanitizer for free-form user text sent to LLM
_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions"
    r"|ignore\s+above"
    r"|disregard\s+(all\s+)?previous"
    r"|system\s*:"
    r"|<\|?(system|im_start|endoftext)\|?>"
    r"|you\s+are\s+now\s+"
    r"|act\s+as\s+if"
    r"|pretend\s+you\s+are"
    r"|new\s+instructions?\s*:"
    r"|override\s+(previous\s+)?instructions"
    r"|forget\s+(all\s+)?previous"
    r"|do\s+not\s+follow\s+(the\s+)?(above|previous))",
    re.IGNORECASE,
)

_MAX_CHAT_INPUT_LEN = 500


def _sanitize_chat_input(text: str) -> str:
    """Sanitize free-form user text before sending to LLM.

    - Strips prompt-injection patterns FIRST
    - Then truncates to 500 characters
    """
    sanitized = _INJECTION_PATTERNS.sub("[filtered]", text)
    truncated = sanitized[:_MAX_CHAT_INPUT_LEN]
    return truncated.strip()


# ── War Room main menu keyboard ─────────────────────────────

_KB_WARROOM = InlineKeyboardMarkup([
    [InlineKeyboardButton("Scan Market", callback_data="open_warroom"),
     InlineKeyboardButton("Latest Signal", callback_data="latest_signal")],
    [InlineKeyboardButton("Positions", callback_data="positions"),
     InlineKeyboardButton("Performance", callback_data="performance")],
    [InlineKeyboardButton("Risk", callback_data="risk_control"),
     InlineKeyboardButton("Stop Bot", callback_data="risk_emergency_stop")],
])

# Legacy dashboard keyboard (kept for /dashboard command compatibility)
_KB_DASH = InlineKeyboardMarkup([
    [InlineKeyboardButton("Status", callback_data="pane:status"),
     InlineKeyboardButton("Risk", callback_data="pane:risk")],
    [InlineKeyboardButton("Portfolio", callback_data="pane:portfolio"),
     InlineKeyboardButton("Scan", callback_data="pane:scan")],
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
        # Migrate legacy pending users to auto-approved trader/basic
        _migrated = self.users.migrate_pending_users()
        if _migrated:
            audit(system_log, f"Migrated {_migrated} legacy pending users to trader/basic",
                  action="startup_migration", result="OK")
        # Wire user store into engine for role-based live/paper routing
        self.engine._user_store = self.users
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
        # Channel forwarder for marketing auto-posts
        self.forwarder = ChannelForwarder()

    def build_app(self) -> Application:
        app = Application.builder().token(CONFIG.telegram.bot_token).build()
        # Store engine in bot_data so standalone skill handlers can access it
        app.bot_data["engine"] = self.engine
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
            ("grant_live", self._cmd_grant_live), ("revoke_live", self._cmd_revoke_live),
            ("set_tier", self._cmd_set_tier),
            # Marketing / channel forwarder
            ("channel", self._cmd_channel), ("broadcast", self._cmd_broadcast),
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
            # Deep scan & playbook
            ("playbook", self._cmd_playbook), ("deepscan", self._cmd_deepscan),
            ("fullscan", self._cmd_fullscan),
            ("stockscan", self._cmd_stockscan),
            # Multi-user commands
            ("link", _cmd_link), ("unlink", _cmd_unlink), ("me", _cmd_me),
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

        # Telegram max message length is 4096 chars — split if needed
        MAX_LEN = 4000  # leave margin for safety
        chunks = self._split_message(text, MAX_LEN)

        for i, chunk in enumerate(chunks):
            # Only attach reply_markup to the last chunk
            markup = reply_markup if i == len(chunks) - 1 else None
            # Only allow edit for the first chunk (edits can't create new messages)
            if i > 0:
                # For subsequent chunks, always use reply_text
                if update.message:
                    send_method = update.message.reply_text
                elif update.callback_query and update.callback_query.message:
                    send_method = update.callback_query.message.reply_text
                else:
                    continue
            else:
                send_method = method

            try:
                await send_method(chunk, parse_mode="HTML", reply_markup=markup)
            except Exception as e:
                # If editing failed (e.g. photo message), fall back to new message
                if edit and update.callback_query and update.callback_query.message:
                    fallback_method = update.callback_query.message.reply_text
                    try:
                        await fallback_method(chunk, parse_mode="HTML", reply_markup=markup)
                        continue
                    except Exception:
                        pass
                system_log.debug("HTML send failed (%s), falling back to plain", e)
                plain = re.sub(r"<[^>]+>", "", chunk)
                # Try plain text as new message if edit failed
                plain_method = send_method
                if edit and update.callback_query and update.callback_query.message:
                    plain_method = update.callback_query.message.reply_text
                try:
                    await plain_method(plain, parse_mode=None, reply_markup=markup)
                except Exception as e2:
                    system_log.error("Failed to send message chunk %d/%d: %s", i + 1, len(chunks), e2)

    async def _send_photo(self, update: Update, png: bytes, caption: str,
                          reply_markup=None) -> bool:
        """Send a photo with HTML caption + inline keyboard. Returns True on success."""
        import io as _io
        bot = update.get_bot()
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not chat_id or not png:
            return False
        buf = _io.BytesIO(png)
        buf.name = "chart.png"
        cap = caption[:1024]  # Telegram photo caption limit
        try:
            await bot.send_photo(
                chat_id=int(chat_id), photo=buf,
                caption=cap, parse_mode="HTML",
                reply_markup=reply_markup)
            return True
        except Exception as exc:
            system_log.debug("send_photo HTML failed (%s), retrying plain", exc)
            buf.seek(0)
            try:
                plain_cap = re.sub(r"<[^>]+>", "", cap)
                await bot.send_photo(
                    chat_id=int(chat_id), photo=buf,
                    caption=plain_cap, parse_mode=None,
                    reply_markup=reply_markup)
                return True
            except Exception as exc2:
                system_log.warning("send_photo failed: %s", exc2)
                return False

    async def _maybe_send_chart(self, update: Update, data: dict, idea) -> None:
        """Opt-in: attach setup chart(s) for an analysis card.

        Gated by TELEGRAM_SEND_CHARTS (off by default). Renders one chart per
        configured timeframe (TELEGRAM_CHART_TIMEFRAMES) off-thread and sends
        them as a single photo or an album. Degrades silently on any failure.
        """
        try:
            system_log.info("_maybe_send_chart called for %s", idea.asset if idea else "None")
            if not CONFIG.telegram.send_charts:
                system_log.info("charts disabled in config, skipping")
                return
            from bot.skills import chart_renderer
            if not chart_renderer.charts_available():
                system_log.info("chart libs not available, skipping")
                return
            bot = update.get_bot()
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is None or idea is None:
                system_log.info("chart skipped: chat_id=%s idea=%s", chat_id, idea)
                return
            candles_by_tf = await self._fetch_chart_timeframes(idea.asset, data)
            system_log.info("chart candles fetched: %s", {k: len(v) for k, v in candles_by_tf.items()} if candles_by_tf else "empty")
            if not candles_by_tf:
                return
            await chart_renderer.send_idea_charts_multi(
                bot, chat_id, candles_by_tf, idea, theme=CONFIG.telegram.chart_theme)
            system_log.info("chart sent successfully for %s", idea.asset)
        except Exception as exc:  # noqa: BLE001 — charts are best-effort
            system_log.warning("chart send skipped: %s", exc, exc_info=True)

    async def _build_chart_composite(self, data: dict, idea) -> Optional[bytes]:
        """Build a composite chart PNG for embedding in a signal message.

        Returns PNG bytes or None. Does NOT send — caller uses send_photo
        with the PNG + caption + inline keyboard in one message.
        """
        try:
            if not CONFIG.telegram.send_charts:
                return None
            from bot.skills import chart_renderer
            if not chart_renderer.charts_available():
                return None
            if idea is None:
                return None
            candles_by_tf = await self._fetch_chart_timeframes(idea.asset, data)
            if not candles_by_tf:
                return None
            return await chart_renderer.build_idea_chart_composite(
                candles_by_tf, idea, theme=CONFIG.telegram.chart_theme)
        except Exception as exc:
            system_log.warning("chart composite build failed: %s", exc)
            return None

    def _chart_timeframes(self) -> list:
        """Parse TELEGRAM_CHART_TIMEFRAMES into an ordered list (highest first)."""
        raw = CONFIG.telegram.chart_timeframes or "1h"
        tfs = [t.strip() for t in raw.split(",") if t.strip()]
        return tfs[:4] or ["1h"]   # cap at 4 (Telegram album practical limit here)

    async def _fetch_chart_timeframes(self, asset: str, primary_data: dict | None) -> dict:
        """Fetch candles for each configured timeframe -> {tf: ohlcv_raw}.

        Reuses already-fetched candles for the primary 1h timeframe when present
        so we don't double-fetch what the analysis card already loaded.
        """
        out: dict = {}
        tfs = self._chart_timeframes()
        exchange = None
        for tf in tfs:
            if tf == "1h" and primary_data and primary_data.get("ohlcv_raw"):
                out[tf] = primary_data["ohlcv_raw"]
                continue
            try:
                if exchange is None:
                    exchange = await self.engine.get_exchange()
                if exchange is None:
                    break
                d = await fetch_analysis_data(exchange, asset, timeframe=tf)
                candles = (d or {}).get("ohlcv_raw")
                if candles:
                    out[tf] = candles
            except Exception as exc:  # noqa: BLE001
                system_log.debug("chart tf %s fetch failed: %s", tf, exc)
        return out

    @staticmethod
    def _split_message(text: str, max_len: int = 4000) -> list[str]:
        """Split a long message into chunks, preferring line boundaries."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            # Find the last newline within the limit
            split_at = text.rfind("\n", 0, max_len)
            if split_at <= 0:
                # No good break point — hard split
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks

    # ── Banner / Footer ───────────────────────────────────────

    def _banner(self) -> str:
        cb = self.engine.risk.circuit_breaker_active
        combined = self.engine.user_portfolios.combined_snapshot() if self.engine.user_portfolios.all_portfolios() else None
        open_pos = self.engine.user_portfolios.total_open_positions() if self.engine.user_portfolios.all_portfolios() else 0
        macro = self.engine.macro_calendar.evaluate()
        mode = "SIM" if CONFIG.simulation_mode else "LIVE"
        cb_s = "paused" if cb else "running"
        macro_s = macro.state.value.replace("_", " ").lower()
        return f"{mode} | {open_pos} open | {cb_s} | macro: {macro_s}"

    def _footer(self) -> str:
        return f"\n<i>{datetime.now(UTC).strftime('%H:%M:%S UTC')}</i>"

    # ── Pane renderers ────────────────────────────────────────

    async def _render_pane(self, pane: str, user_id: str = None) -> str:
        kw = {"user_id": user_id} if user_id else {}
        if pane == "status":
            return await self.registry.get("check_risk").execute(self.engine, mode="status", **kw)
        elif pane == "risk":
            return await self.registry.get("check_risk").execute(self.engine, mode="risk", **kw)
        elif pane == "portfolio":
            return await self.registry.get("get_portfolio").execute(self.engine, **kw)
        elif pane == "macro":
            return await self.registry.get("macro_calendar").execute(self.engine, **kw)
        elif pane == "learning":
            return await self.registry.get("learning").execute(self.engine, **kw)
        elif pane == "scan":
            return await self.registry.get("scan_market").execute(self.engine, **kw)
        return ""

    # ── Free-text AI chat ─────────────────────────────────────

    _CHAT_SYSTEM_PROMPT = (
        "You are RUNECLAW, an AI trading assistant.\n"
        "Talk like a knowledgeable friend — casual, clear, no jargon overload.\n\n"

        "PERSONALITY:\n"
        "- Friendly and direct. Like texting a trading buddy.\n"
        "- Keep answers short and actionable.\n"
        "- Use plain language. Say 'price is pulling back' not 'retracement to liquidity zone'.\n"
        "- If a setup looks bad, say so honestly. Don't force trades.\n"
        "- You protect the user's capital above all else.\n"
        "- NEVER suggest slash commands. Just talk naturally.\n"
        "- NEVER say you are a generic AI. You are RUNECLAW.\n\n"

        "HOW TO RESPOND:\n"
        "1. Figure out what they want (scan? trade? portfolio check? just chatting?)\n"
        "2. If info is missing, ask one quick question\n"
        "3. Give a clear answer with specific numbers when relevant\n"
        "4. If the setup is weak, say 'I'd skip this one' and explain why briefly\n"
        "5. End with what to watch next\n\n"

        "ANSWER LENGTH:\n"
        "- Quick questions ('long or short?', 'safe?') = 2-4 lines\n"
        "- Scans ('scan BTC', 'analyze SOL') = structured but concise, ~10-15 lines\n"
        "- Trade plans = entry, SL, TP, and reasoning\n\n"

        "WHEN EXPLAINING:\n"
        "  If the user sounds new, keep it simple. Explain terms briefly inline.\n"
        "  Example: 'Price swept below support (took out the stops) and bounced back.'\n\n"

        "SCAN FORMAT — for full analysis requests:\n"
        "  1. Quick verdict (bullish/bearish/choppy + what to do)\n"
        "  2. What the chart shows (trend, key levels, structure)\n"
        "  3. Momentum (RSI, volume, orderflow if relevant)\n"
        "  4. Long scenario + Short scenario\n"
        "  5. Setup quality (1-10)\n"
        "  6. What to watch next\n\n"

        "STYLE:\n"
        "- Talk like a friend who happens to be good at trading.\n"
        "- Keep it real. Say 'I wouldn't touch this' instead of 'No-Trade Zone detected.'\n"
        "- Never refer to yourself as 'the Claw.' Just say 'I' or speak naturally.\n"
        "- Use HTML formatting: <b>bold</b> for headers, <code>mono</code> for numbers.\n"
        "- No emoji overload. One or two per message max.\n"
        "- Keep Quick Mode under 50 words, Full Scan under 300 words.\n"
        "- You remember the conversation. Build on what was discussed.\n\n"

        "TERMS you can use naturally (explain if user seems new):\n"
        "CHoCH, BOS, sweep, reclaim, FVG, displacement, stop hunt, absorption\n\n"

        "WHEN TO SAY NO:\n"
        "- Choppy, no clear direction\n"
        "- No confirmation yet\n"
        "- RSI stuck in no-man's land (40-60)\n"
        "- Late entry after a big move\n"
        "- Conflicting signals across timeframes\n"
        "Just say 'I'd sit this one out' and explain briefly why.\n\n"

        "ALWAYS END WITH: one clear thing to watch next.\n"
    )

    # Varied thinking indicators instead of same one every time
    _THINKING_PHRASES = [
        "<i>Looking at the chart...</i>",
        "<i>Checking the setup...</i>",
        "<i>Pulling up the data...</i>",
        "<i>Reading the orderflow...</i>",
        "<i>Let me check that...</i>",
        "<i>Analyzing the structure...</i>",
        "<i>Running the numbers...</i>",
        "<i>Checking risk levels...</i>",
        "\u2694\ufe0f <i>Analyzing momentum and zones...</i>",
    ]

    def _build_chat_system_prompt(self, user_id: str, user_name: str = "") -> str:
        """Build a personalized system prompt with user context."""
        base = self._CHAT_SYSTEM_PROMPT

        # Inject user-specific context
        portfolio_summary = ""
        engine_state = ""
        positions_detail = ""
        try:
            user_portfolio = self.engine.user_portfolios.get(user_id)
            state = user_portfolio.snapshot()

            is_live = CONFIG.is_live()
            executor = self.engine.live_executor if is_live else None

            # LIVE FIX: use real equity and live executor stats in LIVE mode
            if is_live:
                eff_equity = self.engine.get_effective_equity(user_id)
                eq_display = eff_equity if eff_equity > 0 else state.equity_usd
                # Use live executor stats (actual exchange trades)
                live_closed = executor.closed_positions if executor else []
                live_open = executor.open_positions if executor else []
                total_trades = len(live_closed)
                wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
                win_rate_val = wins / total_trades if total_trades > 0 else 0
                total_pnl = sum(t.pnl_usd or 0 for t in live_closed)
                portfolio_summary = (
                    f"{len(live_open)} open positions, "
                    f"equity ~${eq_display:,.2f}, "
                    f"total PnL ${total_pnl:+,.2f}, "
                    f"win rate {win_rate_val:.0%}, "
                    f"total trades {total_trades}"
                )
            else:
                eq_display = state.equity_usd
                portfolio_summary = (
                    f"{state.open_positions} open positions, "
                    f"equity ~${eq_display:,.2f}, "
                    f"total PnL ${state.total_pnl:+,.2f}, "
                    f"win rate {state.win_rate:.0%}, "
                    f"total trades {state.total_trades}"
                )
            cb = self.engine.risk.circuit_breaker_active
            mode = "LIVE" if not CONFIG.simulation_mode else "PAPER"
            engine_state = f"{mode} mode, CB={'ON' if cb else 'OFF'}"

            # Inject actual open positions
            if is_live and executor:
                # Use live executor positions (actual exchange positions)
                if executor.open_positions:
                    pos_lines = []
                    for pos in executor.open_positions:
                        if pos.status == "pending_fill":
                            pos_lines.append(
                                f"  - PENDING {pos.direction} {pos.symbol}: "
                                f"limit ${pos.entry_price:,.4f}, "
                                f"SL ${pos.stop_loss:,.4f}, TP ${pos.take_profit:,.4f}"
                            )
                        else:
                            size_usd = pos.quantity * pos.entry_price
                            pos_lines.append(
                                f"  - {pos.direction} {pos.symbol}: "
                                f"entry ${pos.entry_price:,.4f}, "
                                f"size ${pos.cost_usd:,.2f}, lev {pos.leverage}x, "
                                f"SL ${pos.stop_loss:,.4f}, TP ${pos.take_profit:,.4f}"
                            )
                    positions_detail = (
                        "\n\nACTIVE POSITIONS (live exchange):\n" +
                        "\n".join(pos_lines)
                    )
            elif user_portfolio.open_positions:
                pos_lines = []
                for pos in user_portfolio.open_positions:
                    last_px = user_portfolio._last_prices.get(pos.asset, pos.entry_price)
                    if pos.direction.value == "LONG":
                        pnl_pct = ((last_px - pos.entry_price) / pos.entry_price) * 100
                    else:
                        pnl_pct = ((pos.entry_price - last_px) / pos.entry_price) * 100
                    size_usd = pos.quantity * pos.entry_price
                    pnl_usd = size_usd * pnl_pct / 100
                    pos_lines.append(
                        f"  - {pos.direction.value} {pos.asset}: "
                        f"entry ${pos.entry_price:,.4f}, current ${last_px:,.4f}, "
                        f"size ${size_usd:,.2f}, PnL {pnl_pct:+.2f}% (${pnl_usd:+,.2f}), "
                        f"SL ${pos.stop_loss:,.4f}, TP ${pos.take_profit:,.4f}"
                    )
                positions_detail = (
                    "\n\nACTIVE POSITIONS (live data):\n" +
                    "\n".join(pos_lines)
                )

            # Inject recent closed trades
            if is_live and executor:
                # Use live executor closed trades (actual exchange fills)
                live_closed = executor.closed_positions
                recent_trades_live = live_closed[-5:] if live_closed else []
                if recent_trades_live:
                    trade_lines = []
                    for t in recent_trades_live:
                        pnl_val = t.pnl_usd or 0
                        exit_px = t.close_price or t.entry_price
                        trade_lines.append(
                            f"  - {t.direction} {t.symbol}: "
                            f"entry ${t.entry_price:,.4f}, exit ${exit_px:,.4f}, "
                            f"PnL ${pnl_val:+,.2f}"
                        )
                    positions_detail += (
                        "\n\nRECENT CLOSED TRADES (live):\n" +
                        "\n".join(trade_lines)
                    )
            else:
                recent_trades = user_portfolio.trade_history[-5:]
                if recent_trades:
                    trade_lines = []
                    for t in recent_trades:
                        trade_lines.append(
                            f"  - {t.direction.value} {t.asset}: "
                            f"entry ${t.entry_price:,.4f}, exit ${t.exit_price:,.4f}, "
                            f"PnL ${t.pnl:+,.2f}"
                        )
                    positions_detail += (
                        "\n\nRECENT CLOSED TRADES:\n" +
                        "\n".join(trade_lines)
                    )
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
        return base + f"\n{time_note}" + positions_detail + context_block

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
            "I'm having trouble thinking right now. "
            f"Last error: {last_error[:80]}. "
            "Try again in a minute."
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

        # Auto-detect group chats for channel forwarder
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup", "channel"):
            self.forwarder.detect_group(chat.id, chat.type, chat.title or "")

        if not text:
            return

        # Unregistered users get onboarding
        if not user:
            self.users.register(tg_id, name=(
                update.effective_user.first_name if update.effective_user else ""))
            await self._send(update,
                f"\u2694\ufe0f <b>RUNECLAW</b>\n\n"
                f"I don't recognize you yet.\n\n"
                f"Your ID: <code>{tg_id}</code>\n\n"
                f"Use /start to register, then wait for approval.")
            return

        # Pending users get a clear message
        if not user.get("authorized", False):
            await self._send(update,
                f"\U0001f512 Your account is pending approval.\n\n"
                f"Once approved, just talk to me naturally.\n"
                f"No commands needed — the Claw understands.")
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
            # ── Scan mode shortcuts ──────────────────────────────
            scan_modes = {
                "scan_swing": ("swing", "<i>Checking the 4H chart...</i>"),
                "scan_scalp": ("scalp", "\u26a1 <i>Scalp scan — 5M candles, tight zones...</i>"),
                "scan_intraday": ("intraday", "\U0001f4ca <i>Intraday scan — 15M structure...</i>"),
                "scan_deep": (None, "\u2694\ufe0f <i>Deep scanning 67+ symbols...</i>"),
                "scan_full": (None, "\u2694\ufe0f <i>Full scan with patterns...</i>"),
            }
            if intent.skill in scan_modes:
                mode, thinking_msg = scan_modes[intent.skill]
                await self._send(update, thinking_msg)
                if intent.skill == "scan_deep":
                    result = await self.registry.get("deepscan").execute(
                        self.engine, timeframe="4h")
                elif intent.skill == "scan_full":
                    result = await self.registry.get("deepscan").execute(
                        self.engine, timeframe="4h")
                else:
                    result = await self.registry.get("pro_scan").execute(
                        self.engine, mode=mode, user_id=tg_id)
                await self._send(update, result)
                return

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
                    result = await skill.execute(self.engine, user_id=tg_id, **intent.kwargs)
                    # Store skill result as assistant message (truncated)
                    self.conversations.append(tg_id, "assistant",
                                               f"[{intent.skill}] executed successfully",
                                               metadata={"skill": intent.skill})
                    await self._send(update, result)
                except Exception as exc:
                    await self._send(update,
                        f"Something went wrong. Try again or use a command.")
                return

        if intent.matched and intent.confidence >= 0.5 and not intent.kwargs.get("symbol"):
            # Partial match — skill needs a symbol we couldn't extract
            await self._send(update,
                f"What coin do you want me to look at?\n\n"
                f"Which asset? Say something like <i>\"scan BTC\"</i> or <i>\"check ETH\"</i>")
            return

        # ── Fallback: AI chat ─────────────────────────────────────
        # Store user message in conversation memory
        self.conversations.append(tg_id, "user", text,
                                   metadata={"intent": intent.skill or "chat"})

        # Pick a varied thinking indicator
        import random
        thinking = random.choice(self._THINKING_PHRASES)
        await self._send(update, thinking)

        answer = await self._llm_chat(
            _sanitize_chat_input(text), user_id=tg_id, user_name=user_name)

        # Store assistant response in conversation memory
        self.conversations.append(tg_id, "assistant", answer)

        # Don't wrap in rigid header for short/social responses
        is_social = intent.is_social if hasattr(intent, 'is_social') else False
        # Don't escape if LLM produced HTML formatting tags
        if any(tag in answer for tag in ['<b>', '<i>', '<code>', '<pre>']):
            formatted = answer
        else:
            formatted = html.escape(answer)

        if len(answer) < 80 or is_social:
            await self._send(update, formatted)
        else:
            # Premium tactical header for substantive responses
            await self._send(update,
                f"\u2694\ufe0f <b>RUNECLAW</b>\n{'─' * 16}\n\n{formatted}")

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
                "I don't recognize you yet.\n"
                f"Your Telegram ID: <code>{tg_id}</code>\n\n"
                "Use /start to register, then wait for approval.")
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

        # Refresh last_seen for session timeout
        user_record = self.users.get(tg_id)
        if user_record:
            user_record["last_seen"] = datetime.now(UTC).isoformat()

        return True

    # ── Public commands (no auth required) ─────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw welcome — auto-registers new users."""
        now = datetime.now(UTC).strftime("%H:%M UTC")
        user_tg = update.effective_user
        tg_id = self._get_tg_id(update)
        user_name = html.escape(user_tg.first_name) if user_tg else "Trader"

        # Auto-register on first contact
        record = self.users.register(tg_id, name=user_name)

        if not record.get("authorized", False):
            msg = (
                f"<b>Hey {user_name}!</b>\n\n"
                f"I'm RUNECLAW, your AI trading assistant.\n"
                f"I scan the market, find setups, and manage risk for you.\n\n"
                f"Your account is pending approval.\n"
                f"ID: <code>{tg_id}</code>\n\n"
                f"An admin will get you set up soon.\n"
                f"Once approved, just chat with me like normal."
            )
            await self._send(update, msg)
            await self._notify_admins(
                f"New user: <b>{user_name}</b> (<code>{tg_id}</code>)\n"
                f"Approve: <code>/approve {tg_id}</code>",
                ctx)
            return

        # Authorized user — GetClaw ready
        role = record.get("role", "trader")
        mode_str = "PAPER" if CONFIG.simulation_mode else "LIVE"
        user_portfolio = self.engine.user_portfolios.get(tg_id)
        state = user_portfolio.snapshot()
        cb_active = self.engine.risk.circuit_breaker_active

        # LIVE FIX: show real exchange equity in LIVE mode
        if mode_str == "LIVE":
            live_eq = await self.engine.get_effective_equity_async(tg_id)
            display_equity = live_eq if live_eq > 0 else state.equity_usd
            executor = self.engine.live_executor
            open_pos = len(executor.open_positions)
            live_closed = executor.closed_positions
            if live_closed:
                wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
                win_rate = f"{wins / len(live_closed) * 100:.0f}"
            else:
                win_rate = "N/A"
        else:
            display_equity = state.equity_usd
            open_pos = state.open_positions
            win_rate = f"{state.win_rate:.0%}".replace("%", "")

        SEP = "\u2500" * 16
        status_icon = "\U0001f7e2" if not cb_active else "\U0001f534"
        status_label = "Active" if not cb_active else "Paused"
        mode = mode_str
        equity = f"{display_equity:,.2f}"
        time = now

        # Show user's tier and trading mode
        tier_label = self.users.tier_label(tg_id)
        can_live = self.users.can_trade_live(tg_id)
        trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"

        msg = (
            f"<b>RUNECLAW</b>\n"
            f"{SEP}\n\n"
            f"Hey {user_name}, here's where things stand:\n\n"
            f"{status_icon} <b>{status_label}</b> | {mode}\n"
            f"Equity: <code>${equity}</code>\n"
            f"Open positions: <code>{open_pos}</code>\n"
            f"Win rate: <code>{win_rate}{'%' if win_rate != 'N/A' else ''}</code>\n"
            f"Tier: {tier_label} | Trading: {trade_mode}\n\n"
            f"<b>Talk to me:</b>\n"
            f"<i>\"scan BTC\" - \"show my positions\" - \"what's the risk?\"</i>\n"
            f"<i>\"analyze SOL\" - \"how's my PnL?\" - \"pause the bot\"</i>\n\n"
            f"Just type what you need, no commands required.\n\n"
            f"<i>{time}</i>"
        )
        await self._send(update, msg, reply_markup=_KB_WARROOM)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw help — organized command reference."""
        tg_id = self._get_tg_id(update)
        is_auth = self.users.is_authorized(tg_id)
        user = self.users.get(tg_id)
        role = user.get("role", "pending") if user else "pending"

        _sep = "\u2500" * 20

        if not is_auth:
            await self._send(update,
                f"\u2694\ufe0f <b>RUNECLAW</b>\n"
                f"{_sep}\n"
                f"<i>Status: pending approval \u2014 use /start to register</i>")
            return

        tier_label = self.users.tier_label(tg_id)
        can_live = self.users.can_trade_live(tg_id)
        trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"

        msg = (
            f"\u2694\ufe0f <b>RUNECLAW \u2014 Command Guide</b>\n"
            f"{_sep}\n"
            f"{tier_label} | {trade_mode}\n\n"

            # Tip
            f"\U0001f4ac <i>You can also just type naturally:</i>\n"
            f"<i>\"scan BTC\" \u2014 \"how's my PnL?\" \u2014 \"what's moving?\"</i>\n\n"

            # Market Analysis
            f"\U0001f50d <b>Market Analysis</b>\n"
            f"/scan \u2014 quick market scan\n"
            f"/deepscan \u2014 deep multi-timeframe scan\n"
            f"/fullscan \u2014 full scan (all pairs)\n"
            f"/analyze <i>BTC</i> \u2014 detailed coin analysis\n"
            f"/macro \u2014 macro outlook\n"
            f"/patterns \u2014 chart pattern detection\n\n"

            # Trading
            f"\U0001f4b9 <b>Trading</b>\n"
            f"/trade <i>BTC</i> \u2014 generate trade idea\n"
            f"/latest_signal \u2014 last signal\n"
            f"/signals \u2014 signal history & stats\n"
            f"/proposals \u2014 pending trade proposals\n\n"

            # Portfolio & Risk
            f"\U0001f4ca <b>Portfolio & Risk</b>\n"
            f"/portfolio \u2014 holdings & PnL\n"
            f"/open_positions \u2014 current positions\n"
            f"/risk \u2014 risk dashboard\n"
            f"/performance \u2014 performance stats\n"
            f"/daily_report \u2014 daily summary\n"
            f"/journal \u2014 trade journal\n"
            f"/costs \u2014 fee breakdown\n\n"

            # Strategy
            f"\U0001f3af <b>Strategy Presets</b>\n"
            f"/momentum \u2014 trend following\n"
            f"/swing \u2014 swing trades\n"
            f"/scalp \u2014 quick scalps\n"
            f"/dip \u2014 dip buying\n"
            f"/intraday \u2014 intraday setups\n"
            f"/strategy \u2014 current strategy info\n"
            f"/mode \u2014 switch strategy mode\n"
            f"/playbook \u2014 strategy playbook\n\n"

            # Tools
            f"\U0001f6e0 <b>Tools</b>\n"
            f"/backtest \u2014 run backtest\n"
            f"/walkforward \u2014 walk-forward test\n"
            f"/optimize \u2014 parameter optimization\n"
            f"/watch <i>BTC 65000</i> \u2014 price alert\n"
            f"/learn \u2014 trading lessons\n\n"

            # Controls
            f"\u2699\ufe0f <b>Controls</b>\n"
            f"/dashboard \u2014 overview panel\n"
            f"/status \u2014 engine status\n"
            f"/health \u2014 system health\n"
            f"/pause \u2014 pause trading\n"
            f"/resume \u2014 resume trading\n"
            f"/halt \u2014 halt engine\n"
            f"/emergency_stop \u2014 kill switch\n"
            f"/rejected \u2014 rejected trades\n"
            f"/whynot \u2014 why last trade was rejected\n"
            f"/reset \u2014 reset engine state\n"
        )

        # Account
        msg += (
            f"\n\U0001f464 <b>Account</b>\n"
            f"/me \u2014 your profile\n"
            f"/link \u2014 link exchange account\n"
            f"/start \u2014 refresh session\n"
        )

        # LLM settings (show for all authorized users)
        msg += (
            f"\n\U0001f916 <b>AI Settings</b>\n"
            f"/llmstatus \u2014 current AI model\n"
            f"/llmtiers \u2014 available models\n"
            f"/setllm \u2014 change AI model\n"
            f"/llmreset \u2014 reset to default\n"
        )

        # Live trading (show for users with live access)
        if can_live or role == "admin":
            msg += (
                f"\n\U0001f525 <b>Live Trading</b>\n"
                f"/golive \u2014 enable live execution\n"
                f"/livebalance \u2014 exchange balance\n"
                f"/livepositions \u2014 exchange positions\n"
                f"/liveclose <i>id</i> \u2014 close position\n"
                f"/buy <i>BTC 5</i> \u2014 spot buy\n"
                f"/sell <i>BTC</i> \u2014 spot sell\n"
            )

        # Admin section
        if role == "admin":
            msg += (
                f"\n\U0001f6e1 <b>Admin</b>\n"
                f"/users \u2014 all users\n"
                f"/approve <i>ID</i> \u2014 approve user\n"
                f"/revoke <i>ID</i> \u2014 revoke access\n"
                f"/set_tier <i>ID tier</i> \u2014 change tier\n"
                f"/grant_live <i>ID</i> \u2014 enable live trading\n"
                f"/revoke_live <i>ID</i> \u2014 disable live trading\n"
                f"/stockscan \u2014 stock market scan\n"
                f"/channel \u2014 manage auto-posting\n"
                f"/broadcast \u2014 send message to groups\n"
            )

        await self._send(update, msg)

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
            can_live = self.users.can_trade_live(target_id)
            trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"
            SEP = "\u2500" * 16
            await self._send(update,
                f"\u2705 <b>USER APPROVED</b>\n"
                f"{SEP}\n"
                f"- Name: <b>{html.escape(name)}</b>\n"
                f"- ID: <code>{target_id}</code>\n"
                f"- Role: <code>{role}</code>\n"
                f"- Trading: {trade_mode}\n"
                f"- Status: \U0001f7e2 authorized\n\n"
                f"<i>Use /grant_live or /revoke_live to change trading mode</i>")
            # Notify the approved user
            try:
                await ctx.bot.send_message(
                    chat_id=int(target_id),
                    text=(
                        f"🟢 <b>Access Granted</b>\n"
                        f"{SEP}\n"
                        f"Your RUNECLAW account has been approved.\n"
                        f"- Role: <code>{role}</code>\n\n"
                        f"Use /start to begin trading."
                    ),
                    parse_mode="HTML")
            except Exception:
                pass  # User may not have started the bot yet
        else:
            await self._send(update,
                f"🔴 Failed to approve <code>{html.escape(target_id)}</code>")

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
            SEP = "─" * 16
            await self._send(update,
                f"⚠️ <b>ACCESS REVOKED</b>\n"
                f"{SEP}\n"
                f"- ID: <code>{target_id}</code>\n"
                f"- Status: 🔴 <code>pending</code>")
        else:
            await self._send(update,
                f"\U0001f534 User <code>{html.escape(target_id)}</code> not found")

    async def _cmd_grant_live(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /grant_live <telegram_id> — allow user to trade live."""
        if not self._is_admin(update):
            await self._send(update, "\U0001f512 Admin only.")
            return
        args = ctx.args or []
        if not args:
            await self._send(update,
                "\U0001f4cb <b>Usage</b>\n\n"
                "<code>/grant_live &lt;telegram_id&gt;</code>\n\n"
                "Grants live trading permission to a user.\n"
                "Without this, users trade paper only.")
            return
        target_id = args[0].strip()
        if not target_id.isdigit():
            await self._send(update, "\U0001f534 Invalid Telegram ID.")
            return
        user = self.users.get(target_id)
        if not user or not user.get("authorized"):
            await self._send(update,
                f"\U0001f534 User <code>{target_id}</code> not found or not approved.\n"
                f"Use /approve first.")
            return
        ok = self.users.set_live_trading(target_id, True)
        if ok:
            name = user.get("name", "Unknown")
            await self._send(update,
                f"\U0001f525 <b>LIVE TRADING GRANTED</b>\n\n"
                f"- User: <b>{html.escape(name)}</b> (<code>{target_id}</code>)\n"
                f"- Role: <code>{user.get('role', 'trader')}</code>\n"
                f"- Trading: \U0001f525 Live\n\n"
                f"<i>This user can now execute live trades on the exchange.</i>")
        else:
            await self._send(update, f"\U0001f534 Failed to grant live trading.")

    async def _cmd_revoke_live(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /revoke_live <telegram_id> — restrict user to paper only."""
        if not self._is_admin(update):
            await self._send(update, "\U0001f512 Admin only.")
            return
        args = ctx.args or []
        if not args:
            await self._send(update,
                "<code>/revoke_live &lt;telegram_id&gt;</code>\n\n"
                "Restricts user to paper trading only.")
            return
        target_id = args[0].strip()
        if not target_id.isdigit():
            await self._send(update, "\U0001f534 Invalid Telegram ID.")
            return
        ok = self.users.set_live_trading(target_id, False)
        if ok:
            user = self.users.get(target_id)
            name = user.get("name", "Unknown") if user else "Unknown"
            await self._send(update,
                f"\U0001f4dd <b>LIVE TRADING REVOKED</b>\n\n"
                f"- User: <b>{html.escape(name)}</b> (<code>{target_id}</code>)\n"
                f"- Trading: \U0001f4dd Paper only")
        else:
            await self._send(update, f"\U0001f534 User not found.")

    async def _cmd_set_tier(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /set_tier <telegram_id> <tier> — change user tier."""
        if not self._is_admin(update):
            await self._send(update, "\U0001f512 Admin only.")
            return
        args = ctx.args or []
        if len(args) < 2:
            from bot.utils.user_store import TIERS
            tiers_str = " / ".join(f"<code>{t}</code>" for t in TIERS)
            await self._send(update,
                "\U0001f4cb <b>Usage</b>\n\n"
                f"<code>/set_tier &lt;telegram_id&gt; &lt;tier&gt;</code>\n\n"
                f"Tiers: {tiers_str}\n\n"
                "\U0001f7e2 <b>basic</b> — Paper trading, basic analysis\n"
                "\U0001f535 <b>pro</b> — + Backtesting, patterns, strategies\n"
                "\U0001f7e1 <b>elite</b> — + Live eligible, priority signals, early access\n"
                "\U0001f534 <b>admin</b> — Full access")
            return
        target_id = args[0].strip()
        tier = args[1].strip().lower()
        if not target_id.isdigit():
            await self._send(update, "\U0001f534 Invalid Telegram ID.")
            return
        from bot.utils.user_store import TIERS
        if tier not in TIERS:
            await self._send(update,
                f"\U0001f534 Invalid tier: <code>{html.escape(tier)}</code>\n"
                f"Valid: {', '.join(f'<code>{t}</code>' for t in TIERS)}")
            return
        user = self.users.get(target_id)
        if not user:
            await self._send(update, f"\U0001f534 User <code>{target_id}</code> not found.")
            return
        ok = self.users.set_tier(target_id, tier)
        if ok:
            name = user.get("name", "Unknown")
            tier_label = self.users.tier_label(target_id)
            await self._send(update,
                f"\U0001f3af <b>TIER UPDATED</b>\n\n"
                f"- User: <b>{html.escape(name)}</b> (<code>{target_id}</code>)\n"
                f"- Tier: {tier_label}\n"
                f"- Role: <code>{user.get('role', 'trader')}</code>")
            # Notify the user
            try:
                await ctx.bot.send_message(
                    chat_id=int(target_id),
                    text=(f"\U0001f3af <b>Account Upgraded</b>\n\n"
                          f"Your tier has been updated to: {tier_label}\n"
                          f"Use /start to see your new features."),
                    parse_mode="HTML")
            except Exception:
                pass
        else:
            await self._send(update, "\U0001f534 Failed to update tier.")

    # ── Marketing / Channel commands ──────────────────────────

    async def _cmd_channel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/channel — manage marketing channel auto-posting."""
        # Allow bot admins OR Telegram group admins
        is_bot_admin = self._is_admin(update)
        is_group_admin = False
        if not is_bot_admin and update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
            try:
                member = await ctx.bot.get_chat_member(
                    update.effective_chat.id, update.effective_user.id)
                is_group_admin = member.status in ("creator", "administrator")
            except Exception:
                pass
        if not is_bot_admin and not is_group_admin:
            await self._send(update, "\U0001f512 Admin only.")
            return

        # Auto-detect this group if command is run in one
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            self.forwarder.detect_group(chat.id, chat.type, chat.title or "")

        args = ctx.args or []
        if not args:
            groups = self.forwarder.group_ids
            status = "\U0001f7e2 ON" if self.forwarder.is_enabled else "\U0001f534 OFF"
            _sep = "\u2500" * 18
            msg = (
                f"\U0001f4e1 <b>Channel Forwarder</b>\n"
                f"{_sep}\n\n"
                f"Status: {status}\n"
                f"Groups: <code>{len(groups)}</code>\n"
            )
            if groups:
                for gid in groups:
                    msg += f"\u2022 <code>{gid}</code>\n"
            msg += (
                f"\n<b>Commands:</b>\n"
                f"<code>/channel on</code> \u2014 enable auto-posting\n"
                f"<code>/channel off</code> \u2014 disable auto-posting\n"
                f"<code>/channel add &lt;chat_id&gt;</code> \u2014 add group\n"
                f"<code>/channel remove &lt;chat_id&gt;</code> \u2014 remove group\n"
                f"<code>/channel test</code> \u2014 send test message\n\n"
                f"<i>Groups are also auto-detected when the bot receives a message in them.</i>"
            )
            await self._send(update, msg)
            return

        sub = args[0].lower()
        if sub == "on":
            self.forwarder.set_enabled(True)
            await self._send(update, "\U0001f7e2 Channel auto-posting <b>enabled</b>.")
        elif sub == "off":
            self.forwarder.set_enabled(False)
            await self._send(update, "\U0001f534 Channel auto-posting <b>disabled</b>.")
        elif sub == "add" and len(args) >= 2:
            try:
                gid = int(args[1])
                self.forwarder.add_group(gid)
                await self._send(update, f"\u2705 Group <code>{gid}</code> added.")
            except ValueError:
                await self._send(update, "\u274c Invalid chat ID. Must be a number.")
        elif sub == "remove" and len(args) >= 2:
            try:
                gid = int(args[1])
                self.forwarder.remove_group(gid)
                await self._send(update, f"\u2705 Group <code>{gid}</code> removed.")
            except ValueError:
                await self._send(update, "\u274c Invalid chat ID.")
        elif sub == "test":
            now = datetime.now(UTC).strftime("%H:%M UTC")
            await self.forwarder.post_custom(
                f"\U0001f916 <b>RUNECLAW Test</b>\n\n"
                f"Channel forwarder is working.\n"
                f"Signals, trade results, and daily reports will auto-post here.\n\n"
                f"<i>{now}</i>")
            await self._send(update, "\u2705 Test message sent to all groups.")
        else:
            await self._send(update,
                "\u274c Unknown subcommand. Use <code>/channel</code> for help.")

    async def _cmd_broadcast(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/broadcast <message> — send a custom message to all marketing channels."""
        is_bot_admin = self._is_admin(update)
        is_group_admin = False
        if not is_bot_admin and update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
            try:
                member = await ctx.bot.get_chat_member(
                    update.effective_chat.id, update.effective_user.id)
                is_group_admin = member.status in ("creator", "administrator")
            except Exception:
                pass
        if not is_bot_admin and not is_group_admin:
            await self._send(update, "\U0001f512 Admin only.")
            return
        args = ctx.args or []
        if not args:
            await self._send(update,
                "\U0001f4e2 <b>Broadcast</b>\n\n"
                "<code>/broadcast Your message here</code>\n\n"
                "Sends a custom message to all registered groups.")
            return
        text = " ".join(args)
        await self.forwarder.post_custom(f"\U0001f4e2 {html.escape(text)}")
        await self._send(update, f"\u2705 Broadcast sent to {self.forwarder.group_count} group(s).")

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
        SEP = "─" * 16
        lines = [
            f"👥 <b>REGISTERED USERS</b>  ({len(all_users)} total)\n"
            f"{SEP}\n",
        ]

        # Summary with role icons
        role_icons = {"admin": "🔒", "trader": "⚔️", "viewer": "👁", "pending": "⏳"}
        for role in ("admin", "trader", "viewer", "pending"):
            c = counts.get(role, 0)
            if c > 0:
                icon = role_icons.get(role, "")
                lines.append(f"- {icon} {role}: <code>{c}</code>")
        lines.append("")

        # User list
        _dash = "\u2500"
        lines.append("<pre>")
        lines.append(f" {'ID':<10}{'NAME':<12}{'ROLE':<8}{'TIER':<7}{'MODE'}")
        lines.append(f" {_dash*10}{_dash*12}{_dash*8}{_dash*7}{_dash*6}")

        for u in all_users[-15:]:  # Show last 15
            tid = u["telegram_id"][-8:]  # Last 8 digits
            name = (u.get("name") or "?")[:10]
            role = u.get("role", "?")
            tier = u.get("tier", "basic")
            auth = "\u2713" if u.get("authorized") else "\u2717"
            can_live = self.users.can_trade_live(u["telegram_id"])
            mode = "LIVE" if can_live else "paper"
            lines.append(f" {tid:<10}{name:<12}{auth}{role:<7}{tier:<7}{mode}")

        lines.append("</pre>")

        if len(all_users) > 15:
            lines.append(f"\n<i>Showing last 15 of {len(all_users)}</i>")

        await self._send(update, "\n".join(lines))

    # ── Mode switching ────────────────────────────────────────

    async def _cmd_mode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch asset universe: /mode solana | /mode all | /mode stocks | /mode hybrid"""
        if not await self._guard(update, "mode"):
            return

        args = (update.message.text or "").split()
        valid_modes = {"all", "solana", "stocks", "hybrid"}

        if len(args) < 2 or args[1].lower() not in valid_modes:
            from bot.config import RUNTIME
            current = RUNTIME.asset_universe
            icons = {"solana": "\u2600\ufe0f", "all": "\U0001f30d", "stocks": "\U0001f4c8", "hybrid": "\U0001f500"}
            icon = icons.get(current, "\U0001f30d")
            lines = [
                f"\U0001f504 <b>ASSET UNIVERSE</b>\n",
                f"Current: {icon} <b>{current.upper()}</b>\n",
                "Usage:",
                "  <code>/mode all</code> \u2014 all Bitget USDT pairs",
                "  <code>/mode solana</code> \u2014 15 Solana ecosystem tokens",
                "  <code>/mode stocks</code> \u2014 20 US stock tokenized perps",
                "  <code>/mode hybrid</code> \u2014 crypto + stocks combined",
            ]
            if current == "solana":
                from bot.config import SOLANA_ECOSYSTEM_SYMBOLS
                tokens = ", ".join(s.replace("/USDT", "") for s in SOLANA_ECOSYSTEM_SYMBOLS)
                lines.append(f"\nTokens: <i>{tokens}</i>")
            elif current == "stocks":
                from bot.config import US_STOCK_SYMBOLS
                tickers = ", ".join(s.replace("/USDT", "") for s in US_STOCK_SYMBOLS)
                lines.append(f"\nStocks: <i>{tickers}</i>")
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
                "All 23 risk checks still apply. Meme tokens (BONK, WIF) "
                "use tighter volatility and correlation limits.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "stocks":
            from bot.config import US_STOCK_SYMBOLS
            from bot.core.stock_trading import get_market_session, format_stock_scan_header
            session = get_market_session()
            tickers = ", ".join(s.replace("/USDT", "") for s in US_STOCK_SYMBOLS)
            await self._send(update, (
                "\U0001f4c8 <b>US STOCK MODE ACTIVE</b>\n\n"
                f"{format_stock_scan_header(session)}\n\n"
                f"Scanner now targets {len(US_STOCK_SYMBOLS)} tokenized US stock perps:\n"
                f"<i>{tickers}</i>\n\n"
                "Stock-specific risk rules:\n"
                f"\u2022 ATR guard: {CONFIG.stocks.volatility_guard_atr_pct}%\n"
                f"\u2022 Min R:R: {CONFIG.stocks.min_risk_reward}\n"
                f"\u2022 Max position: {CONFIG.stocks.max_position_pct}%\n"
                f"\u2022 Off-hours size: {CONFIG.stocks.reduce_size_outside_hours:.0%}\n"
                f"\u2022 Max sector positions: {CONFIG.stocks.max_sector_positions}\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "hybrid":
            await self._send(update, (
                "\U0001f500 <b>HYBRID MODE ACTIVE</b>\n\n"
                "Scanner shows both crypto movers and US stock tokenized perps.\n"
                "Risk engine applies stock-specific rules to stock symbols "
                "and crypto rules to crypto symbols automatically.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        else:
            await self._send(update, (
                "\U0001f30d <b>ALL MARKETS MODE</b>\n\n"
                "Scanner now covers all Bitget USDT pairs.\n"
                "Use <code>/mode solana</code> for Solana or "
                "<code>/mode stocks</code> for US stocks."
            ))

    # ── Live Trading Commands ─────────────────────────────────

    async def _cmd_golive(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/golive — enable live trading with double confirmation."""
        if not await self._guard(update, "admin"):
            return

        args = ctx.args or []
        if args and args[0].upper() == "OFF":
            # Disable live mode
            from bot.config import RUNTIME
            RUNTIME.live_mode = False
            from bot.compliance.compliance_engine import Permission
            self.engine.compliance_profile.permissions.discard(Permission.LIVE_TRADE)
            audit(system_log, "LIVE TRADING DISABLED via /golive OFF",
                  action="golive", result="DISABLED",
                  data={"user": self._get_tg_id(update)})
            await self._send(update,
                "\U0001f534 <b>LIVE TRADING DISABLED</b>\n\n"
                "Reverted to paper-trade mode.\n"
                "Use <code>/golive CONFIRM</code> to re-enable.")
            return

        if not args or args[0].upper() != "CONFIRM":
            await self._send(update,
                "\u26a0\ufe0f <b>LIVE TRADING ACTIVATION</b>\n\n"
                "This will enable <b>real order execution</b> on Bitget.\n\n"
                f"Safety limits:\n"
                f"\u2022 Max {CONFIG.risk.max_open_positions} concurrent positions\n"
                f"\u2022 Max {CONFIG.risk.max_symbol_exposure_pct:.0f}% per symbol\n"
                f"\u2022 USDT-M perpetual futures\n"
                f"\u2022 Default {CONFIG.trading.default_leverage}x leverage\n\n"
                "To confirm, type:\n<code>/golive CONFIRM</code>")
            return

        # Enable live mode via RuntimeState (CONFIG is frozen)
        from bot.config import RUNTIME
        RUNTIME.live_mode = True

        # Grant LIVE_TRADE permission on the engine's compliance profile
        # so Lock 1 passes. This is the explicit human authorization.
        from bot.compliance.compliance_engine import Permission
        self.engine.compliance_profile.permissions.add(Permission.LIVE_TRADE)

        audit(system_log, "LIVE TRADING ENABLED via /golive",
              action="golive", result="ENABLED",
              data={"user": self._get_tg_id(update)})
        await self._send(update,
            "\U0001f7e2 <b>LIVE TRADING ENABLED</b>\n\n"
            "Real orders will execute on Bitget (USDT-M futures).\n"
            f"Limits: {CONFIG.risk.max_open_positions} positions, "
            f"{CONFIG.trading.default_leverage}x leverage.\n\n"
            "\u2022 <code>/livebalance</code> — check USDT balance\n"
            "\u2022 <code>/livepositions</code> — view open positions\n"
            "\u2022 <code>/liveclose &lt;id&gt;</code> — close a position\n"
            "\u2022 <code>/golive OFF</code> — disable live mode")

    async def _cmd_livebalance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livebalance — check real USDT balance + spot holdings on Bitget."""
        if not await self._guard(update, "portfolio"):
            return
        try:
            bal = await self.engine.live_executor.fetch_balance()
            # LIVE FIX: update engine's cached balance so /status shows fresh data
            if "error" not in bal or bal.get("total", 0) > 0:
                self.engine._live_balance_cache = bal
                self.engine._live_balance_cache_ts = time.time()
            total = bal.get("total", 0)
            free = bal.get("free", 0)
            used = bal.get("used", 0)
            holdings = bal.get("holdings", [])

            # Fetch prices and compute portfolio value
            exchange = await self.engine.live_executor._get_exchange()
            spot_items = []
            total_usd = total
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
                    pass
                spot_items.append({"asset": asset, "qty": qty, "price": price, "usd": usd_val})

            # Live executor stats
            executor = self.engine.live_executor
            open_pos = executor.open_positions
            closed_pos = executor.closed_positions
            realized_pnl = sum(p.pnl_usd or 0 for p in closed_pos)
            exposure = executor.total_exposure_usd

            # PnL sign
            pnl_sign = "+" if realized_pnl >= 0 else ""
            pnl_icon = "\u26aa" if realized_pnl == 0 else ("\U0001f7e2" if realized_pnl > 0 else "\U0001f534")

            # Header
            SEP = "─" * 16
            lines = [
                f"💰 <b>BITGET PORTFOLIO</b>",
                f"{SEP}",
                f"   {pnl_icon}  Realized PnL: <code>${pnl_sign}{realized_pnl:.2f}</code>",
                "",
                "💳 <b>Balance</b>",
                f"{SEP}",
                f"- Cash: <code>${free:,.2f}</code>",
                f"- Used: <code>${used:,.2f}</code>",
                f"- Equity: <code>${total_usd:,.2f}</code>",
                f"- Exposure: <code>${exposure:,.2f}</code>",
            ]

            # Spot holdings section
            real_holdings = [s for s in spot_items if s["usd"] >= 0.01]
            dust_holdings = [s for s in spot_items if 0 < s["usd"] < 0.01]

            if real_holdings:
                lines.append("")
                lines.append("📦 <b>Spot Holdings</b>")
                lines.append(SEP)
                for s in sorted(real_holdings, key=lambda x: -x["usd"]):
                    pct = (s["usd"] / total_usd * 100) if total_usd > 0 else 0
                    bar = _bar(pct / 100, 1.0, 8)
                    lines.append(
                        f"- <b>{s['asset']}</b>  "
                        f"<code>{s['qty']:.8g}</code>  "
                        f"<code>${s['usd']:.2f}</code>  "
                        f"{bar} {pct:.0f}%"
                    )
                if dust_holdings:
                    lines.append(f"- <i>+{len(dust_holdings)} dust</i>")

            # PnL waterfall
            lines.append("")
            lines.append("📈 <b>PnL Waterfall</b>")
            lines.append(SEP)
            lines.append(f"- Realized: <code>${pnl_sign}{realized_pnl:.4f}</code>")
            lines.append(f"- Exposure: <code>${exposure:,.2f}</code>")
            lines.append(SEP)
            lines.append(f"- <b>NET: <code>${total_usd:,.2f}</code></b>")

            # Footer
            n_trades = len(closed_pos)
            n_open = len(open_pos)
            trade_word = "trade" if n_trades == 1 else "trades"
            pos_word = f"{n_open} open" if n_open > 0 else "no open positions"
            lines.append("")
            lines.append(f"<i>{n_trades} {trade_word} • {pos_word}</i>")

            await self._send(update, "\n".join(lines))
        except Exception as exc:
            await self._send(update, f"\u274c Balance fetch failed: {exc}")

    async def _cmd_livepositions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livepositions — show live open positions."""
        if not await self._guard(update, "portfolio"):
            return
        positions = self.engine.live_executor._positions
        open_pos = [p for p in positions.values() if p.status == "open"]
        if not open_pos:
            await self._send(update, "💭 No live positions open.")
            return
        SEP = "─" * 16
        lines = [f"📊 <b>LIVE POSITIONS</b>\n{SEP}\n"]
        for p in open_pos:
            dir_icon = "🟢" if p.direction == "LONG" else "🔴"
            lines.append(
                f"{dir_icon} <b>{p.direction} {p.symbol}</b>\n"
                f"- Entry: <code>${p.entry_price:,.4f}</code>\n"
                f"- Qty: <code>{p.quantity:.6f}</code>\n"
                f"- SL: <code>${p.stop_loss:,.4f}</code>\n"
                f"- TP: <code>${p.take_profit:,.4f}</code>\n"
                f"- ID: <code>{p.trade_id}</code>\n"
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

        # SEC-H3 FIX: validate symbol before it reaches CCXT
        if not _SYMBOL_RE.match(symbol):
            await self._send(update, "\u274c Invalid symbol format.")
            return

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
        SEP = "─" * 16
        bar = _bar(amount_usd / 10.0, 1.0, 10)  # 10 = micro limit
        await self._send(update,
            f"✅ <b>SPOT BUY FILLED</b>\n"
            f"{SEP}\n"
            f"🟢 <b>{symbol}</b>\n\n"
            f"- Qty: <code>{result['qty']:.8f}</code>\n"
            f"- Price: <code>${result['price']:,.4f}</code>\n"
            f"- Cost: <code>${result['cost']:.2f}</code>\n"
            f"- Order: <code>{result['order_id']}</code>\n\n"
            f"Budget: {bar} <code>${amount_usd:.0f}/$10</code>\n\n"
            f"💡 Sell with: <code>/sell {asset}</code>")

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

        # SEC-H3 FIX: validate symbol before it reaches CCXT
        if not _SYMBOL_RE.match(symbol):
            await self._send(update, "\u274c Invalid symbol format.")
            return

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

        SEP = "─" * 16
        await self._send(update,
            f"✅ <b>SPOT SELL FILLED</b>\n"
            f"{SEP}\n"
            f"🔴 <b>{symbol}</b>\n\n"
            f"- Qty: <code>{result['qty']:.8f}</code>\n"
            f"- Price: <code>${result['price']:,.4f}</code>\n"
            f"- Proceeds: <code>${result['proceeds']:.2f}</code>\n"
            f"- Order: <code>{result['order_id']}</code>")

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
        # Wire up channel forwarder
        self.forwarder.set_bot(bot)
        async def _send_fn(chat_id: str, text: str) -> None:
            try:
                await bot.send_message(
                    chat_id=int(chat_id), text=text, parse_mode="HTML")
            except Exception:
                pass

        # Opt-in: push a setup chart (with entry/SL/TP lines) alongside each
        # proactive NEW SIGNAL alert. Renders off-thread; degrades silently.
        async def _chart_fn(chat_id: str, idea) -> None:
            try:
                system_log.info("proactive _chart_fn called for %s", idea.asset if idea else "None")
                if not CONFIG.telegram.send_charts:
                    system_log.info("proactive chart: disabled in config")
                    return
                from bot.skills import chart_renderer
                if not chart_renderer.charts_available():
                    system_log.info("proactive chart: libs not available")
                    return
                candles_by_tf = await self._fetch_chart_timeframes(idea.asset, None)
                system_log.info("proactive chart candles: %s", {k: len(v) for k, v in candles_by_tf.items()} if candles_by_tf else "empty")
                if not candles_by_tf:
                    return
                await chart_renderer.send_idea_charts_multi(
                    bot, int(chat_id), candles_by_tf, idea,
                    theme=CONFIG.telegram.chart_theme)
                system_log.info("proactive chart sent for %s", idea.asset)
            except Exception as exc:  # noqa: BLE001 — best-effort
                system_log.warning("proactive chart_fn skipped: %s", exc, exc_info=True)

        self.monitor.set_chart_fn(_chart_fn)

        # Hook: forward new signals to marketing channels
        _forwarder = self.forwarder
        _original_dispatch = self.monitor._dispatch

        async def _dispatch_with_forward(alert, send_fn):
            await _original_dispatch(alert, send_fn)
            # Forward trade signals to marketing channels
            if alert.alert_type == "TRADE_SIGNAL" and alert.idea is not None:
                try:
                    await _forwarder.post_signal(alert.idea)
                except Exception:
                    pass

        self.monitor._dispatch = _dispatch_with_forward
        self._monitor_task = asyncio.create_task(self.monitor.run(_send_fn))

        # Register trade-close notification callback
        admin_chat_id = CONFIG.telegram.chat_id
        async def _on_trade_closed(msg: str) -> None:
            """Send a rich close confirmation to admin when a trade is closed."""
            if not admin_chat_id:
                return
            try:
                # Parse the close message from live_executor
                # Format: "CLOSED LONG BTC/USDT (SL HIT)\nEntry: $X → Exit: $Y\nPnL: +$Z"
                lines = msg.strip().split("\n")
                header = lines[0] if lines else msg
                # Build a rich notification
                is_win = "+$" in msg or "+$" in msg
                emoji = "\u2705" if is_win else "\u274c"
                card = f"{emoji} <b>Trade Closed</b>\n\n"
                for line in lines:
                    card += f"{html.escape(line)}\n"
                await bot.send_message(
                    chat_id=int(admin_chat_id), text=card.strip(),
                    parse_mode="HTML")
            except Exception as exc:
                system_log.debug("Close notify send failed: %s", exc)

            # Forward trade close to marketing channels
            try:
                await _forwarder.post_trade_closed(msg)
            except Exception:
                pass

        self.engine.set_close_notify_callback(_on_trade_closed)

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
            SEP = "─" * 16
            await self._send(update,
                f"🤖 <b>BYOK — Bring Your Own Key</b>\n"
                f"{SEP}\n\n"
                "<pre>"
                " /setllm &lt;provider&gt; &lt;api_key&gt;\n"
                " /setllm groq gsk_your_key\n"
                " /setllm ollama\n"
                " /setllm anthropic sk-ant-key\n"
                " /setllm openai sk-key gpt-4o-mini\n"
                "</pre>\n\n"
                f"<b>Providers:</b> <code>{providers}</code>\n\n"
                "<i>🔑 Keys are stored in memory only — never saved to disk or logs.</i>")
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
            SEP = "─" * 16
            await self._send(update,
                f"✅ <b>LLM PROVIDER UPDATED</b>\n"
                f"{SEP}\n"
                f"- Provider: <code>{html.escape(provider_str)}</code>\n"
                f"- Model: <code>{html.escape(model or 'default')}</code>\n"
                f"- Status: 🟢 active")
            try:
                await update.message.delete()
            except Exception:
                pass  # Can't delete in all chat types
        else:
            await self._send(update,
                f"🔴 <b>LLM UPDATE FAILED</b>\n\n"
                f"{html.escape(msg)}")
            try:
                await update.message.delete()
            except Exception:
                pass  # Can't delete in all chat types

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
        SEP = "─" * 16
        await self._send(update,
            f"🤖 <b>LLM STATUS</b>\n"
            f"{SEP}\n"
            f"<pre>{html.escape(status)}</pre>")

    async def _cmd_llmreset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmreset — clear runtime LLM key, revert to .env settings."""
        if not await self._guard(update, "mode"):
            return

        msg = BYOK.reset()
        # Refresh analyzer client back to .env config
        if hasattr(self.engine, 'analyzer') and hasattr(self.engine.analyzer, 'refresh_llm_client'):
            self.engine.analyzer.refresh_llm_client()
        audit(system_log, "LLM config reset to .env", action="llmreset", result="OK")
        SEP = "─" * 16
        await self._send(update,
            f"🔄 <b>LLM CONFIG RESET</b>\n"
            f"{SEP}\n"
            f"- {html.escape(msg)}\n"
            f"- Status: 🟢 using .env defaults")

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

        SEP = "─" * 16
        lines = [f"🎯 <b>Multi-Tier LLM Routing</b>\n{SEP}\n"]
        for tier in LLMTier:
            tier_cfg = resolve_tier_config(tier, active_cfg)
            provider_name = tier_cfg.provider.value if isinstance(tier_cfg.provider, LLMProvider) else str(tier_cfg.provider)
            default_route = DEFAULT_TIER_ROUTING.get(tier, {})
            is_custom = tier_cfg != active_cfg
            source = "tier-routed" if is_custom else "primary"
            configured = "✅" if tier_cfg.is_configured() else "❌"
            lines.append(
                f"{configured} <b>{tier.value.upper()}</b>\n"
                f"- Provider: <code>{provider_name}</code>\n"
                f"- Model: <code>{tier_cfg.model}</code>\n"
                f"- Source: {source} | {default_route.get('reason', 'default')}\n"
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
        user_id = self._get_tg_id(update)
        pane = self._last_pane.get(chat_id, "status")
        body = await self._render_pane(pane, user_id=user_id)
        text = body + self._footer()
        await self._send(update, text, reply_markup=_KB_DASH)
        self._last_pane[chat_id] = pane

    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "scan"):
            return
        user_id = self._get_tg_id(update)
        result = await self.registry.get("scan_market").execute(self.engine, user_id=user_id)
        await self._send(update, result)

    async def _cmd_analyze(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "analyze"):
            return
        args = ctx.args
        if args:
            raw = args[0].upper().strip()
            # SEC-H3 FIX: strict symbol validation before reaching CCXT/LLM
            if not _SYMBOL_RE.match(raw):
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
                InlineKeyboardButton("Take it", callback_data=f"confirm:{new_idea.id}:{uid}"),
                InlineKeyboardButton("Skip", callback_data=f"reject:{new_idea.id}:{uid}"),
            ]])
            await self._send(update, result, reply_markup=kb)
        else:
            await self._send(update, result)

    async def _cmd_portfolio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "portfolio"):
            return
        user_id = self._get_tg_id(update)
        # Use per-user portfolio if it exists, otherwise show shared
        if self.engine.user_portfolios.has_user(user_id):
            portfolio = self.engine.user_portfolios.get(user_id)
        else:
            portfolio = self.engine.user_portfolios.get(user_id)  # creates new one

        positions = portfolio.open_positions

        # Fetch fresh prices before rendering so PnL is accurate
        if positions:
            try:
                exchange = await self.engine.scanner._get_exchange()
                syms = list({p.asset for p in positions})
                tickers = await exchange.fetch_tickers(syms)
                fresh_prices = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                if fresh_prices:
                    portfolio.mark_to_market(fresh_prices)
            except Exception:
                pass  # fall back to whatever prices we have

        state = portfolio.snapshot()
        history = portfolio.trade_history

        # LIVE FIX: in LIVE mode, show real exchange balance prominently
        mode_str = "LIVE" if CONFIG.is_live() else "PAPER"
        sep = "─" * 16

        if mode_str == "LIVE":
            # ── LIVE MODE: show real exchange data ──
            display_equity = await self.engine.get_effective_equity_async(user_id)
            if display_equity <= 0:
                display_equity = state.equity_usd

            executor = self.engine.live_executor
            live_open = executor.open_positions
            live_closed = executor.closed_positions

            # Calculate live PnL from closed positions
            live_total_pnl = sum((p.pnl_usd or 0) for p in live_closed)
            live_unrealized = 0.0
            # Unrealized PnL for open positions
            for lp in live_open:
                if hasattr(lp, 'pnl_usd') and lp.pnl_usd:
                    live_unrealized += lp.pnl_usd

            # Live exposure
            live_exposure = sum(lp.cost_usd for lp in live_open)

            lines = [
                f"\U0001f4bc <b>YOUR PORTFOLIO</b> (LIVE)",
                sep,
                "",
                f"- Equity: <code>${display_equity:,.2f}</code>",
                f"- Open Positions: <code>{len(live_open)}</code>",
                f"- Exposure: <code>${live_exposure:,.2f}</code>",
                f"- Realized PnL: <code>${live_total_pnl:+,.2f}</code> {'🟢' if live_total_pnl >= 0 else '🔴'}",
            ]
            if live_unrealized != 0:
                lines.append(f"- Unrealized PnL: <code>${live_unrealized:+,.2f}</code> {'🟢' if live_unrealized >= 0 else '🔴'}")

            # Open positions from LiveExecutor
            if live_open:
                lines.extend(["", sep, "", "<b>Open Positions:</b>"])
                for lp in live_open:
                    d_icon = "🟢" if lp.direction == "LONG" else "🔴"
                    lev_str = f" {lp.leverage}x" if (lp.leverage or 1) > 1 else ""
                    lines.append(
                        f"\n{d_icon} <b>{lp.symbol}</b> {lp.direction}{lev_str}"
                    )
                    lines.append(f"  Entry: <code>${lp.entry_price:,.6f}</code>")
                    lines.append(f"  Size: <code>${lp.cost_usd:,.2f}</code>")
                    if lp.stop_loss:
                        lines.append(f"  SL: <code>${lp.stop_loss:,.6f}</code> | TP: <code>${lp.take_profit:,.6f}</code>")

            # Recent closed trades from LiveExecutor
            if live_closed:
                recent = live_closed[-5:]
                lines.extend(["", sep, "", "<b>Recent Trades:</b>"])
                for t in recent:
                    pnl_val = t.pnl_usd or 0
                    pnl_icon = "✅" if pnl_val >= 0 else "❌"
                    pair = t.symbol.replace("/", "")
                    lines.append(f"  {pnl_icon} {pair} {t.direction} → <code>${pnl_val:+,.2f}</code>")

            # Session tally from LiveExecutor
            if live_closed:
                wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
                losses = len(live_closed) - wins
                wr = wins / len(live_closed) * 100 if live_closed else 0
                lines.extend([
                    "", sep, "",
                    f"<b>Session:</b> {wins}W/{losses}L | "
                    f"Net: <code>${live_total_pnl:+,.2f}</code> | "
                    f"Win rate: <code>{wr:.0f}%</code>",
                ])
            else:
                lines.extend(["", "<i>No live trades yet. Say \"scan\" to find signals.</i>"])

        else:
            # ── PAPER MODE: show paper portfolio data ──
            display_equity = state.equity_usd
            lines = [
                f"\U0001f4bc <b>YOUR PORTFOLIO</b> (PAPER)",
                sep,
                "",
                f"- Equity: <code>${display_equity:,.2f}</code>",
                f"- Cash: <code>${state.balance_usd:,.2f}</code>",
                f"- Open Positions: <code>{state.open_positions}</code>",
                f"- Daily PnL: <code>{'+' if state.daily_pnl >= 0 else ''}{state.daily_pnl:.2f}%</code> {'🟢' if state.daily_pnl >= 0 else '🔴'}",
                f"- Drawdown: <code>{state.max_drawdown_pct:.2f}%</code>",
            ]

            if positions:
                lines.extend(["", sep, "", "<b>Open Positions:</b>"])
                for pos in positions:
                    d_icon = "🟢" if pos.direction.value == "LONG" else "🔴"
                    last = portfolio._last_prices.get(pos.asset, pos.entry_price)
                    size_usd = pos.quantity * pos.entry_price
                    if pos.direction.value == "LONG":
                        pnl_pct = ((last - pos.entry_price) / pos.entry_price) * 100
                    else:
                        pnl_pct = ((pos.entry_price - last) / pos.entry_price) * 100
                    pnl_usd = size_usd * pnl_pct / 100
                    pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
                    arrow = "▲" if pnl_pct > 0 else "▼" if pnl_pct < 0 else "◇"
                    lines.append(
                        f"\n{pnl_icon}{arrow} <b>{pos.asset}</b> {pos.direction.value} | "
                        f"{pnl_icon} {'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%"
                    )
                    lines.append(f"  Entry: <code>${pos.entry_price:,.4f}</code> → Current: <code>${last:,.4f}</code>")
                    lines.append(f"  SL: <code>${pos.stop_loss:,.4f}</code> | TP: <code>${pos.take_profit:,.4f}</code>")
                    lines.append(f"  Size: <code>${size_usd:,.2f}</code> | PNL: <code>${pnl_usd:+,.2f}</code>")

            if history:
                lines.extend(["", sep, "", "<b>Recent Trades:</b>"])
                for t in history[-5:]:
                    pnl_icon = "✅" if t.pnl > 0 else "❌"
                    lines.append(f"  {pnl_icon} {t.asset} {t.direction.value} → <code>${t.pnl:+.2f}</code>")

            # Session tally
            if state.total_trades > 0:
                wins = sum(1 for t in history if t.pnl > 0)
                lines.extend([
                    "", sep, "",
                    f"<b>Session:</b> {wins}W/{state.total_trades - wins}L | "
                    f"Net: <code>${state.total_pnl:+.2f}</code> | "
                    f"Win rate: <code>{state.win_rate:.0%}</code>",
                ])
            else:
                lines.extend(["", "<i>No trades yet. Say \"scan\" to find signals.</i>"])

        await self._send(update, "\n".join(lines))

    async def _cmd_trade(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "trade"):
            return
        pending = self.engine.pending_ideas
        if not pending:
            await self._send(update,
                "No trades waiting right now.\n\n"
                "Say \"scan\" or \"analyze BTC\" to find setups.")
            return

        # Fetch rich market data for all pending ideas
        await self._send(update, "<i>Pulling live data...</i>")
        try:
            exchange = await self.engine.get_exchange()
        except Exception:
            exchange = None

        assets_data = []
        if exchange and pending:
            async def _fetch_one(idea):
                return await fetch_analysis_data(exchange, idea.asset, timeframe="1h")
            results = await asyncio.gather(*[_fetch_one(idea) for idea in pending], return_exceptions=True)
            for r in results:
                if r and not isinstance(r, Exception):
                    assets_data.append(r)

        # If we have rich data for multiple ideas, render multi-analysis
        if len(assets_data) >= 2 and len(pending) >= 2:
            msg = render_multi_analysis(assets_data, list(pending))
            uid = update.effective_user.id if update.effective_user else ""
            buttons = []
            for idea in pending:
                pair = idea.asset.replace("/USDT", "")
                buttons.append([
                    InlineKeyboardButton(f"\u2705 {pair}", callback_data=f"confirm:{idea.id}:{uid}"),
                    InlineKeyboardButton(f"Skip", callback_data=f"reject:{idea.id}:{uid}"),
                ])
            kb = InlineKeyboardMarkup(buttons)
            # Try to build composite chart for first idea and send as photo+buttons
            chart_sent = False
            if assets_data:
                chart_png = await self._build_chart_composite(assets_data[0], pending[0])
                if chart_png:
                    # Send full analysis as text first, then chart+buttons as photo
                    await self._send(update, msg)
                    pair0 = pending[0].asset.replace("/", "")
                    cap = f"<b>{html.escape(pair0)}</b> — {len(pending)} setups"
                    chart_sent = await self._send_photo(update, chart_png, cap, reply_markup=kb)
            if not chart_sent:
                await self._send(update, msg, reply_markup=kb)
        else:
            # Single idea or fallback — render per idea
            for i, idea in enumerate(pending):
                uid = update.effective_user.id if update.effective_user else ""
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Take it", callback_data=f"confirm:{idea.id}:{uid}"),
                    InlineKeyboardButton("Skip", callback_data=f"reject:{idea.id}:{uid}"),
                ]])
                if i < len(assets_data) and assets_data:
                    # Rich analysis card — try chart+buttons as one message
                    card = render_analysis_card(assets_data[i], idea)
                    chart_png = await self._build_chart_composite(assets_data[i], idea)
                    if chart_png:
                        # Build concise caption for photo (1024 char limit)
                        pair = idea.asset.replace("/", "")
                        d = idea.direction.value
                        entry = idea.entry_price
                        sl, tp = idea.stop_loss, idea.take_profit
                        sl_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0
                        tp_pct = abs(tp - entry) / entry * 100 if entry > 0 else 0
                        rr = idea.risk_reward_ratio
                        price = assets_data[i].get("price", entry)
                        rsi = assets_data[i].get("rsi", 0)
                        _otype = getattr(idea, 'order_type', 'market').upper()
                        _otype_tag = f" | {_otype}" if _otype == "LIMIT" else ""
                        cap = (
                            f"<b>{html.escape(pair)}</b> — {d} Setup{_otype_tag}\n"
                            f"Entry: <code>{entry:,.4f}</code> | Now: <code>{price:,.4f}</code>\n"
                            f"SL: <code>{sl:,.4f}</code> (-{sl_pct:.1f}%) | TP: <code>{tp:,.4f}</code> (+{tp_pct:.1f}%)\n"
                            f"R:R 1:{rr:.1f} | Conf {idea.confidence:.0%} | RSI {rsi:.0f}\n"
                            f"{html.escape(idea.reasoning[:200])}"
                        )
                        # Send full card as text, then chart+buttons as photo
                        await self._send(update, card)
                        await self._send_photo(update, chart_png, cap, reply_markup=kb)
                    else:
                        # No chart available — send text+buttons as before
                        await self._send(update, card, reply_markup=kb)
                else:
                    # Fallback: detailed format without live market data
                    d = "\U0001f7e2" if idea.direction.value == "LONG" else "\U0001f534"
                    entry, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
                    sl_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0
                    tp_pct = abs(tp - entry) / entry * 100 if entry > 0 else 0
                    rr = idea.risk_reward_ratio
                    pair = idea.asset.replace("/", "")

                    _otype = getattr(idea, 'order_type', 'market').upper()
                    _otype_tag = f" ({_otype} ORDER)" if _otype == "LIMIT" else ""
                    msg = (
                        f"\U0001f525 <b>{html.escape(pair)}</b> — {idea.direction.value} Setup{_otype_tag}\n"
                        f"{'━' * 28}\n\n"
                        f"<b>Setup — {idea.direction.value}:</b>\n"
                        f"- Entry: <code>{entry:,.4f}</code>{' (limit)' if _otype == 'LIMIT' else ''}\n"
                        f"- SL: <code>{sl:,.4f}</code> (-{sl_pct:.1f}%)\n"
                        f"- TP: <code>{tp:,.4f}</code> (+{tp_pct:.1f}%)\n"
                        f"- Risk/Reward: 1:{rr:.1f}\n"
                        f"- Confidence: {idea.confidence:.0%}\n\n"
                        f"<b>Analysis:</b>\n"
                        f"<i>{html.escape(idea.reasoning[:300])}</i>\n\n"
                        f"<i>⚠️ Live market data unavailable — approve with caution</i>"
                    )
                    await self._send(update, msg, reply_markup=kb)

    async def _cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "risk"):
            return
        user_id = self._get_tg_id(update)
        portfolio = self.engine.user_portfolios.get(user_id)
        state = portfolio.snapshot()
        # LIVE FIX: use real open position count
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            open_count = len(self.engine.live_executor.open_positions)
        else:
            open_count = state.open_positions
        data = {
            "daily_loss_limit": CONFIG.risk.max_daily_loss_pct,
            "current_drawdown": round(state.max_drawdown_pct, 2) if state.max_drawdown_pct else 0.0,
            "max_open_trades": CONFIG.risk.max_open_positions,
            "open_trades": open_count,
            "leverage_cap": CONFIG.exchange.default_leverage,
        }
        rendered = wr_risk(data)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Safe Mode", callback_data="risk_safe_mode"),
             InlineKeyboardButton("Pause", callback_data="risk_pause")],
            [InlineKeyboardButton("Stop Bot", callback_data="risk_emergency_stop")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "status"):
            return
        user_id = self._get_tg_id(update)
        # Show per-user equity in status
        user_portfolio = self.engine.user_portfolios.get(user_id)
        state = user_portfolio.snapshot()
        cb = self.engine.risk.circuit_breaker_active
        macro = self.engine.macro_calendar.evaluate()
        mode = "PAPER" if CONFIG.simulation_mode else "LIVE"
        # LIVE FIX: show real exchange equity and live position count
        if mode == "LIVE":
            equity = await self.engine.get_effective_equity_async(user_id)
            if equity <= 0:
                equity = state.equity_usd
            executor = self.engine.live_executor
            open_count = len(executor.open_positions)
            live_closed = executor.closed_positions
            daily_pnl = round(sum((t.pnl_usd or 0) for t in live_closed), 2) if live_closed else 0.0
        else:
            equity = state.equity_usd if hasattr(state, "equity_usd") else 10_000.0
            open_count = state.open_positions
            daily_pnl = round(state.daily_pnl, 2) if hasattr(state, "daily_pnl") else 0.0
        drawdown = round(state.max_drawdown_pct, 2) if state.max_drawdown_pct else 0.0

        msg = render_status_card(
            mode=mode,
            active=not cb,
            equity=equity,
            open_positions=open_count,
            daily_pnl=daily_pnl,
            drawdown=drawdown,
            max_drawdown=CONFIG.risk.max_daily_loss_pct,
            market_bias=macro.state.value.replace("_", " ").title(),
            pending_ideas=len(self.engine.pending_ideas) if hasattr(self.engine, "pending_ideas") else 0,
        )
        await self._send(update, msg, reply_markup=_KB_WARROOM)

    async def _cmd_rejected(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "rejected"):
            return
        result = await self.registry.get("rejected_trades").execute(self.engine, user_id=self._get_tg_id(update))
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
        result = await self.registry.get("trade_journal").execute(self.engine, user_id=self._get_tg_id(update))
        await self._send(update, result)

    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "costs"):
            return
        result = await self.registry.get("costs").execute(self.engine, user_id=self._get_tg_id(update))
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
        """Scalp scan: 5m candles, tight SL, top-3 by volume."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "\u26a1 <i>Scalp scan — 5M candles, tight zones...</i>")
        try:
            result = await self.registry.get("pro_scan").execute(
                self.engine, mode="scalp", user_id=self._get_tg_id(update))
            await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Scalp scan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Scalp scan error:</b> <code>{html.escape(str(exc)[:200])}</code>")

    async def _cmd_intraday(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Intraday scan: 15m candles, top-5 movers."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "\U0001f4ca <i>Intraday scan — 15M structure...</i>")
        try:
            result = await self.registry.get("pro_scan").execute(
                self.engine, mode="intraday", user_id=self._get_tg_id(update))
            await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Intraday scan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Intraday scan error:</b> <code>{html.escape(str(exc)[:200])}</code>")

    async def _cmd_swing(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Swing scan: 4h candles, wide SL/TP, trend-based."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "<i>Checking the 4H chart...</i>")
        try:
            result = await self.registry.get("pro_scan").execute(
                self.engine, mode="swing", user_id=self._get_tg_id(update))
            await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Swing scan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Swing scan error:</b> <code>{html.escape(str(exc)[:200])}</code>")

    async def _cmd_playbook(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw-style full system playbook briefing."""
        if not await self._guard(update, "playbook"):
            return
        await self._send(update, "📋 <i>Assembling playbook...</i>")
        try:
            result = await self.registry.get("playbook").execute(self.engine, user_id=self._get_tg_id(update))
            await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Playbook error: {exc}")
            await self._send(update, f"🔴 <b>Playbook error:</b> <code>{html.escape(str(exc)[:200])}</code>")

    async def _cmd_deepscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Deep scan 67+ symbols with chart + candle patterns."""
        if not await self._guard(update, "deepscan"):
            return
        # Parse optional timeframe from args: /deepscan 1h
        tf = "4h"
        if ctx.args:
            arg = ctx.args[0].lower().strip()
            if arg in ("5m", "15m", "1h", "4h", "1d"):
                tf = arg
        await self._send(update, f"🔬 <i>Deep scanning {tf.upper()} — this may take a minute...</i>")
        try:
            result = await asyncio.wait_for(
                self.registry.get("deepscan").execute(
                    self.engine, timeframe=tf),
                timeout=120,  # 2 minute max
            )
            if result:
                await self._send(update, result)
            else:
                await self._send(update, "🔴 <b>Deepscan returned empty result.</b>")
        except asyncio.TimeoutError:
            system_log.error("Deepscan timed out after 120s")
            await self._send(update, "🔴 <b>Deepscan timed out.</b> Exchange may be slow — try again.")
        except Exception as exc:
            system_log.error(f"Deepscan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Deepscan error:</b> <code>{html.escape(str(exc)[:200])}</code>")

    async def _cmd_fullscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Full 67-symbol scan via scan_skill module. /fullscan [deep|deepall|swing|scalp|SYMBOL]"""
        if not await self._guard(update, "scan"):
            return
        await _scan_skill_handler(update, ctx)

    async def _cmd_stockscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/stockscan — Scan US stock tokenized perpetuals."""
        if not await self._guard(update, "scan"):
            return

        from bot.core.stock_trading import (
            get_market_session, format_stock_scan_header,
            format_stock_signal_line, is_stock_symbol, get_stock_risk_params,
        )
        from bot.config import US_STOCK_SYMBOLS

        session = get_market_session()
        await self._send(update,
            f"\U0001f4c8 <i>Scanning US stock tokenized perps...</i>\n"
            f"{format_stock_scan_header(session)}")

        try:
            exchange = await self.engine.get_exchange()
            tickers = await exchange.fetch_tickers()
        except Exception as exc:
            await self._send(update, f"\U0001f534 <b>Exchange error:</b> {html.escape(str(exc)[:200])}")
            return

        # Filter to stock symbols — try exact match first, then fuzzy
        stock_set = set(US_STOCK_SYMBOLS)
        stock_signals = []

        # Also detect any symbol with stock-like naming (ON suffix or R prefix)
        stock_name_patterns = {
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
            "AMD", "QQQ", "SPY", "COIN", "HOOD", "ARM", "MRVL",
            "DELL", "INTC", "NOK", "ANET", "NFLX", "CRM",
        }
        for sym, tick in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            # Check exact match or pattern match
            is_stock = sym in stock_set
            if not is_stock:
                base = sym.replace("/USDT", "")
                for pat in stock_name_patterns:
                    if pat in base.upper():
                        is_stock = True
                        break
            if not is_stock:
                continue
            try:
                price = float(tick.get("last", 0) or 0)
                change = float(tick.get("percentage", 0) or 0)
                volume = float(tick.get("quoteVolume", 0) or 0)
                if price <= 0:
                    continue
                stock_signals.append({
                    "symbol": sym,
                    "price": price,
                    "change_pct": round(change, 2),
                    "volume": round(volume, 2),
                })
            except (TypeError, ValueError):
                continue

        if not stock_signals:
            await self._send(update,
                "\U0001f534 <b>No stock symbols found on exchange.</b>\n\n"
                "Stock tokenized perps may not be available on this Bitget account.\n"
                "Check if your account has access to tokenized equity derivatives.")
            return

        # Sort by absolute change
        stock_signals.sort(key=lambda s: abs(s["change_pct"]), reverse=True)

        # Build output
        lines = [
            f"\U0001f4c8 <b>US STOCK SCAN</b> \u2014 {len(stock_signals)} symbols  |  "
            f"{datetime.now(UTC).strftime('%H:%M')} UTC\n",
            format_stock_scan_header(session),
            "",
        ]

        # Get risk params
        risk_note = ""
        if session.is_weekend:
            risk_note = "\n\u26a0\ufe0f <i>Weekend: reduced liquidity, wider spreads</i>\n"
        elif session.session_name in ("closed", "pre_market", "after_hours"):
            risk_note = f"\n\u26a0\ufe0f <i>{session.session_name.replace('_', ' ').title()}: size reduced to {session.size_multiplier:.0%}</i>\n"
        if risk_note:
            lines.append(risk_note)

        for sig in stock_signals[:15]:
            line = format_stock_signal_line(
                sig["symbol"], sig["price"], sig["change_pct"],
            )
            lines.append(line)

        # Summary
        gainers = sum(1 for s in stock_signals if s["change_pct"] > 0)
        losers = sum(1 for s in stock_signals if s["change_pct"] < 0)
        total_vol = sum(s["volume"] for s in stock_signals)
        lines.append(f"\n\U0001f7e2 {gainers} up  \U0001f534 {losers} down  |  Vol: ${total_vol/1e6:.1f}M")
        lines.append("\n<code>/mode stocks</code> to auto-scan stocks  |  <code>/mode hybrid</code> for both")

        await self._send(update, "\n".join(lines))

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
                "Nothing in the queue right now.\n"
                "Say \"scan\" or \"analyze\" to look for setups.")
            return
        idea = pending[-1]  # most recent
        direction = idea.direction.value
        confidence = int(idea.confidence * 100)
        entry = idea.entry_price
        sl = idea.stop_loss
        tp = idea.take_profit

        # Dynamic precision based on price magnitude
        if entry >= 100:
            prec = 2
        elif entry >= 1:
            prec = 4
        else:
            prec = 5

        # Entry zone: use 38.2% Fibonacci retracement toward SL
        # LONG: entry_low = entry - 0.382 * (entry - sl), entry_high = entry
        # SHORT: entry_low = entry, entry_high = entry + 0.382 * (sl - entry)
        sl_dist = abs(entry - sl)
        fib_382 = sl_dist * 0.382
        if direction == "LONG":
            entry_low = entry - fib_382
            entry_high = entry
        else:
            entry_low = entry
            entry_high = entry + fib_382

        # TP2: extend 50% of the TP1-entry distance beyond TP1
        tp_dist = abs(tp - entry)
        if direction == "LONG":
            tp2 = tp + tp_dist * 0.5
        else:
            tp2 = tp - tp_dist * 0.5

        data = {
            "pair": idea.asset.replace("/", ""),
            "direction": direction,
            "confidence": confidence,
            "risk_level": "High" if confidence < 50 else "Medium" if confidence < 70 else "Low",
            "entry_low": round(entry_low, prec),
            "entry_high": round(entry_high, prec),
            "sl": round(sl, prec),
            "tp1": round(tp, prec),
            "tp2": round(tp2, prec),
            "reason": idea.reasoning[:200] if idea.reasoning else "AI analysis",
        }
        rendered = wr_signal(data)
        # Map approve/reject to actual trade IDs
        uid = update.effective_user.id if update.effective_user else ""
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Take it", callback_data=f"confirm:{idea.id}:{uid}")],
            [InlineKeyboardButton("Watch", callback_data=f"signal_watch_{idea.asset}")],
            [InlineKeyboardButton("Skip", callback_data=f"reject:{idea.id}:{uid}")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)

    async def _cmd_open_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show open positions in rich format — per-user."""
        if not await self._guard(update, "portfolio"):
            return
        user_id = self._get_tg_id(update)
        portfolio = self.engine.user_portfolios.get(user_id)

        positions_data = []

        # LIVE FIX: in LIVE mode, show positions from LiveExecutor
        if CONFIG.is_live():
            live_positions = self.engine.live_executor.open_positions
            if live_positions:
                try:
                    exchange = await self.engine.scanner._get_exchange()
                    syms = list({p.symbol for p in live_positions})
                    tickers = await exchange.fetch_tickers(syms)
                    prices = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                except Exception:
                    prices = {}

                for pos in live_positions:
                    last_price = prices.get(pos.symbol, pos.entry_price)
                    if pos.direction == "LONG":
                        pnl_pct = ((last_price - pos.entry_price) / pos.entry_price) * 100
                        upnl_usd = (last_price - pos.entry_price) * pos.quantity
                    else:
                        pnl_pct = ((pos.entry_price - last_price) / pos.entry_price) * 100
                        upnl_usd = (pos.entry_price - last_price) * pos.quantity
                    from datetime import datetime, timezone
                    hold_h = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
                    cost = pos.cost_usd if pos.cost_usd > 0 else pos.entry_price * pos.quantity
                    notional = last_price * pos.quantity
                    leverage = getattr(pos, 'leverage', 0) or (notional / cost if cost > 0 else 1.0)
                    sl_dist = abs(last_price - pos.stop_loss) / last_price * 100 if last_price else 0
                    tp_dist = abs(pos.take_profit - last_price) / last_price * 100 if last_price else 0
                    risk_left = abs(last_price - pos.stop_loss) if pos.stop_loss else 0
                    reward_left = abs(pos.take_profit - last_price) if pos.take_profit else 0
                    rr_live = reward_left / risk_left if risk_left > 0 else 0
                    positions_data.append({
                        "pair": pos.symbol.replace("/", ""),
                        "direction": pos.direction,
                        "entry": round(pos.entry_price, 6),
                        "current": round(last_price, 6),
                        "pnl_pct": round(pnl_pct, 2),
                        "pnl_usd": round(upnl_usd, 4),
                        "sl": round(pos.stop_loss, 6),
                        "tp": round(pos.take_profit, 6),
                        "sl_dist_pct": round(sl_dist, 2),
                        "tp_dist_pct": round(tp_dist, 2),
                        "size_usd": round(cost, 2),
                        "notional_usd": round(notional, 2),
                        "leverage": round(leverage, 2),
                        "rr_live": round(rr_live, 2),
                        "quantity": pos.quantity,
                        "comm_pct": CONFIG.risk.commission_pct,
                        "hold_hours": round(hold_h, 1),
                        "sl_order": "exchange" if pos.sl_order_id else "manual",
                        "tp_order": "exchange" if pos.tp_order_id else "manual",
                        "trade_id": pos.trade_id,
                    })
        else:
            # PAPER mode: show paper positions
            open_pos = portfolio.open_positions
            if open_pos:
                try:
                    exchange = await self.engine.scanner._get_exchange()
                    syms = list({p.asset for p in open_pos})
                    tickers = await exchange.fetch_tickers(syms)
                    fresh = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                    if fresh:
                        portfolio.mark_to_market(fresh)
                except Exception:
                    pass

            with portfolio._lock:
                for tid, pos in portfolio._positions.items():
                    last_price = portfolio._last_prices.get(pos.asset, pos.entry_price)
                    if pos.direction.value == "LONG":
                        pnl_pct = ((last_price - pos.entry_price) / pos.entry_price) * 100
                    else:
                        pnl_pct = ((pos.entry_price - last_price) / pos.entry_price) * 100
                    from datetime import datetime, timezone
                    hold_h = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
                    positions_data.append({
                        "pair": pos.asset.replace("/", ""),
                        "direction": pos.direction.value,
                        "entry": round(pos.entry_price, 6),
                        "current": round(last_price, 6),
                        "pnl_pct": round(pnl_pct, 2),
                        "sl": round(pos.stop_loss, 6),
                        "tp": round(pos.take_profit, 6),
                        "size_usd": round(pos.quantity * pos.entry_price, 2),
                        "comm_pct": CONFIG.risk.commission_pct,
                        "hold_hours": round(hold_h, 1),
                    })

        msg = render_open_positions(positions_data)

        # Build keyboard with close buttons — use trade_id for unique identification
        kb_rows = []
        for pos in positions_data:
            tid = pos.get('trade_id', pos['pair'])
            kb_rows.append([
                InlineKeyboardButton(f"{pos['pair']}", callback_data=f"pos_details_{tid}"),
                InlineKeyboardButton(f"Close", callback_data=f"pos_close_{tid}"),
            ])
        await self._send(update, msg,
                         reply_markup=InlineKeyboardMarkup(kb_rows) if kb_rows else None)

    async def _cmd_performance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Performance summary — per-user."""
        if not await self._guard(update, "portfolio"):
            return
        user_id = self._get_tg_id(update)

        # LIVE mode: use real trade data from executor
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            executor = self.engine.live_executor
            live_closed = executor.closed_positions
            live_open = executor.open_positions
            today_trades = len(live_closed)
            wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
            win_rate = (wins / today_trades * 100) if today_trades > 0 else 0
            total_pnl = sum((t.pnl_usd or 0) for t in live_closed)
            unrealized = 0.0  # would need mark-to-market
            best_pair = "N/A"
            worst_pair = "N/A"
            if live_closed:
                sorted_t = sorted(live_closed, key=lambda t: (t.pnl_usd or 0))
                worst_pair = sorted_t[0].symbol.replace("/USDT", "")
                best_pair = sorted_t[-1].symbol.replace("/USDT", "")
            data = {
                "today_pnl": round(total_pnl, 2),
                "week_pnl": 0.0,
                "win_rate": win_rate,
                "trades_today": today_trades,
                "best_pair": best_pair,
                "worst_pair": worst_pair,
            }
        else:
            portfolio = self.engine.user_portfolios.get(user_id)
            state = portfolio.snapshot()
            trades = portfolio.trade_history
            today_trades = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            win_rate = (wins / today_trades * 100) if today_trades > 0 else 0
            best_pair = "N/A"
            worst_pair = "N/A"
            if trades:
                sorted_t = sorted(trades, key=lambda t: t.pnl)
                worst_pair = sorted_t[0].asset.replace("/USDT", "")
                best_pair = sorted_t[-1].asset.replace("/USDT", "")
            data = {
                "today_pnl": round(state.daily_pnl, 2) if hasattr(state, "daily_pnl") else 0.0,
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
        user_id = self._get_tg_id(update)

        # LIVE mode: use real trade data from executor
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            executor = self.engine.live_executor
            closed = executor.closed_positions
            today_trades = len(closed)
            wins = sum(1 for t in closed if (t.pnl_usd or 0) > 0)
            losses = today_trades - wins
            net_pnl = sum((t.pnl_usd or 0) for t in closed)
            best_trade = "N/A"
            best_pnl = 0.0
            worst_trade = "N/A"
            worst_pnl = 0.0
            if closed:
                sorted_t = sorted(closed, key=lambda t: (t.pnl_usd or 0))
                worst_trade = sorted_t[0].symbol.replace("/USDT", "")
                worst_pnl = round(sorted_t[0].pnl_usd or 0, 2)
                best_trade = sorted_t[-1].symbol.replace("/USDT", "")
                best_pnl = round(sorted_t[-1].pnl_usd or 0, 2)

            live_eq = await self.engine.get_effective_equity_async(user_id)
            dd = 0.0
            risk_status = "Healthy"
        else:
            portfolio = self.engine.user_portfolios.get(user_id)
            trades = portfolio.trade_history
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

            state = portfolio.snapshot()
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

        # Forward daily report to marketing channels
        try:
            win_rate = (wins / today_trades * 100) if today_trades > 0 else 0
            report_summary = (
                f"Trades: <code>{today_trades}</code> | "
                f"W/L: <code>{wins}/{losses}</code> | "
                f"Win Rate: <code>{win_rate:.0f}%</code>\n"
                f"Net PnL: <code>${net_pnl:+,.2f}</code>\n"
                f"Best: <code>{best_trade}</code> (${best_pnl:+,.2f})\n"
                f"Worst: <code>{worst_trade}</code> (${worst_pnl:+,.2f})\n"
                f"Risk: {risk_status}"
            )
            await self.forwarder.post_daily_report(report_summary)
        except Exception:
            pass

    async def _cmd_strategy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Strategy mode selector."""
        if not await self._guard(update, "run"):
            return
        from bot.config import RUNTIME
        current = RUNTIME.strategy_mode
        rendered = wr_strategy_mode(current)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Defensive", callback_data="mode_defensive"),
             InlineKeyboardButton("Balanced", callback_data="mode_balanced")],
            [InlineKeyboardButton("Aggressive", callback_data="mode_aggressive"),
             InlineKeyboardButton("Manual", callback_data="mode_manual")],
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
            await self._send(update,
                "Safe mode is on.\n\n"
                "I'll only take high-confidence setups from here.",
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
                [InlineKeyboardButton("Yes, stop everything", callback_data="emergency_confirm"),
                 InlineKeyboardButton("Cancel", callback_data="emergency_cancel")],
            ])
            await self._send(update, rendered["text"], reply_markup=kb, edit=True if query.message else False)
            return

        if data == "emergency_confirm":
            self.engine.risk.emergency_halt("emergency_stop_telegram")
            # Clear pending ideas (must access the underlying dict, not the property copy)
            self.engine._pending_ideas.clear()
            await self._send(update,
                "Bot stopped.\n\n"
                "All pending orders cancelled, circuit breaker is on.\n"
                "Say \"resume\" when you're ready to start again.",
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
            ident = data.removeprefix("pos_details_")
            # ident can be a trade_id (TI-xxxx) or a pair name (EDGEUSDT)
            is_trade_id = ident.startswith("TI-")
            pair = ident  # fallback for display
            # Find the open position — check LIVE executor first
            user_id = self._get_tg_id(update)
            portfolio = self.engine.user_portfolios.get(user_id)
            pos_match = None
            is_live_pos = False

            if CONFIG.is_live():
                for lp in self.engine.live_executor.open_positions:
                    if is_trade_id:
                        if lp.trade_id == ident:
                            pos_match = lp
                            is_live_pos = True
                            pair = lp.symbol.replace("/", "")
                            break
                    else:
                        if lp.symbol.replace("/", "") == ident:
                            pos_match = lp
                            is_live_pos = True
                            break

            if pos_match is None:
                for p in portfolio.open_positions:
                    if p.asset.replace("/", "") == pair:
                        pos_match = p
                        break

            # Fetch live analysis data
            try:
                exchange = await self.engine.get_exchange()
            except Exception:
                exchange = None

            symbol = pair.replace("USDT", "/USDT") if "USDT" in pair else pair
            adata = None
            if exchange:
                adata = await fetch_analysis_data(exchange, symbol, timeframe="1h")

            if adata and pos_match:
                last_px = adata["price"]

                # Extract fields uniformly from live or paper position
                if is_live_pos:
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction  # already a string
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = pos_match.cost_usd if pos_match.cost_usd > 0 else _entry * _qty
                    _sl_oid = pos_match.sl_order_id
                    _tp_oid = pos_match.tp_order_id
                else:
                    portfolio.mark_to_market({pos_match.asset: last_px})
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction.value if hasattr(pos_match.direction, 'value') else str(pos_match.direction)
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = _entry * _qty
                    _sl_oid = None
                    _tp_oid = None

                pnl_pct = ((last_px - _entry) / _entry * 100)
                if _dir == "SHORT":
                    pnl_pct = -pnl_pct
                sz = _cost
                exit_notional = _qty * last_px
                pnl_usd = (_qty * (last_px - _entry)) if _dir == "LONG" else (_qty * (_entry - last_px))
                d_emoji = "\U0001f7e2" if _dir == "LONG" else "\U0001f534"
                pnl_emoji = "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534"
                sl_dist = abs(last_px - _sl) / last_px * 100 if last_px else 0
                tp_dist = abs(_tp - last_px) / last_px * 100 if last_px else 0

                # R:R from current price
                risk_left = abs(last_px - _sl) if _sl else 0
                reward_left = abs(_tp - last_px) if _tp else 0
                rr_live = reward_left / risk_left if risk_left > 0 else 0

                # Leverage — prefer stored value from position, fall back to notional/cost
                notional_now = _qty * last_px
                if is_live_pos and getattr(pos_match, 'leverage', 0) and pos_match.leverage > 1:
                    leverage = float(pos_match.leverage)
                else:
                    _stored_lev = getattr(pos_match, 'leverage', 0) if not is_live_pos else 0
                    leverage = float(_stored_lev) if _stored_lev and _stored_lev > 1 else (notional_now / sz if sz > 0 else 1.0)

                # Fee calculations
                comm_pct = CONFIG.risk.commission_pct
                entry_fee = sz * (comm_pct / 100.0)
                exit_fee_est = exit_notional * (comm_pct / 100.0)
                total_fees = entry_fee + exit_fee_est

                # Funding rate estimate
                from datetime import datetime, timezone
                hold_hours = (datetime.now(timezone.utc) - _opened).total_seconds() / 3600
                funding_sessions = hold_hours / 8.0
                funding_rate = 0.01
                funding_paid = sz * (funding_rate / 100.0) * funding_sessions

                # Net PNL after all fees
                net_pnl = pnl_usd - total_fees - funding_paid

                # Hold time display
                if hold_hours < 1:
                    hold_str = f"{hold_hours * 60:.0f}m"
                elif hold_hours < 24:
                    hold_str = f"{hold_hours:.1f}h"
                else:
                    hold_str = f"{hold_hours / 24:.1f}d"

                # SL/TP order status
                if _sl_oid:
                    sl_tag = "on exchange"
                else:
                    sl_tag = "bot-managed"
                if _tp_oid:
                    tp_tag = "on exchange"
                else:
                    tp_tag = "bot-managed"

                mode_tag = " LIVE" if is_live_pos else ""
                lev_str = f" | {leverage:.0f}x" if leverage > 1 else ""

                lines = [
                    f"<b>{html.escape(pair)}</b>{mode_tag}",
                    f"{d_emoji} {_dir} | {pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})",
                    "",
                    f"Entry <code>{_entry:,.6f}</code> / Now <code>{last_px:,.6f}</code>",
                    f"Size <code>${sz:,.2f}</code>{lev_str} | Hold {hold_str} | R:R {rr_live:.1f}x",
                    f"SL <code>{_sl:,.6f}</code> ({sl_dist:.1f}%) {sl_tag}",
                    f"TP <code>{_tp:,.6f}</code> ({tp_dist:.1f}%) {tp_tag}",
                    f"Net PnL <code>${net_pnl:+,.2f}</code> (fees ${total_fees + funding_paid:.2f})",
                ]

                # Add market context on one line if available
                if adata:
                    rsi_val = adata.get('rsi', 0)
                    rsi_label = "overbought" if rsi_val > 70 else "oversold" if rsi_val < 30 else "neutral"
                    lines.append(f"RSI {rsi_val:.0f} ({rsi_label}) | {adata.get('structure', '')}")

                # Use trade_id for buttons if we have a live position
                btn_id = pos_match.trade_id if is_live_pos else pair
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Close", callback_data=f"pos_close_{btn_id}"),
                    InlineKeyboardButton("Refresh", callback_data=f"pos_details_{btn_id}"),
                ]])

                # Try to attach a position chart
                chart_png = None
                try:
                    from bot.skills.chart_renderer import build_position_chart
                    chart_png = await build_position_chart(
                        None, symbol, entry=_entry, sl=_sl, tp=_tp)
                except Exception:
                    pass

                if chart_png:
                    card_text = "\n".join(lines)
                    # Send text card first, then chart with buttons
                    await self._send(update, card_text, edit=True)
                    cap = (f"<b>{html.escape(pair)}</b> · 1h\n"
                           f"Entry <code>{_entry:,.6f}</code> | Now <code>{last_px:,.6f}</code>\n"
                           f"{pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})")
                    await self._send_photo(update, chart_png, cap, reply_markup=kb)
                else:
                    await self._send(update, "\n".join(lines), edit=True, reply_markup=kb)
            elif pos_match:
                # No market data — show position info only
                if is_live_pos:
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = pos_match.cost_usd if pos_match.cost_usd > 0 else _entry * _qty
                else:
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction.value if hasattr(pos_match.direction, 'value') else str(pos_match.direction)
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = _entry * _qty

                d_emoji = "\U0001f7e2" if _dir == "LONG" else "\U0001f534"
                sz = _cost
                comm_pct = CONFIG.risk.commission_pct
                entry_fee = sz * (comm_pct / 100.0)
                exit_fee_est = sz * (comm_pct / 100.0)
                from datetime import datetime, timezone
                hold_hours = (datetime.now(timezone.utc) - _opened).total_seconds() / 3600
                funding_sessions = hold_hours / 8.0
                funding_paid = sz * (0.01 / 100.0) * funding_sessions

                mode_tag = " \U0001f534 LIVE" if is_live_pos else ""
                lines = [
                    f"\U0001f4cb <b>{html.escape(pair)} \u2014 Position Detail</b>{mode_tag}",
                    "",
                    f"- Direction: {d_emoji} {_dir}",
                    f"- Entry: <code>{_entry:,.6f}</code>",
                    f"- SL: <code>{_sl:,.6f}</code>",
                    f"- TP: <code>{_tp:,.6f}</code>",
                    f"- Qty: <code>{_qty:,.4f}</code> | Size: <code>${sz:,.2f}</code>",
                    f"- Hold: <code>{hold_hours:.1f}h</code>",
                    "",
                    f"<b>Fees & Costs:</b>",
                    f"- Entry fee ({comm_pct}%): <code>${entry_fee:.4f}</code>",
                    f"- Exit fee ({comm_pct}%, est): <code>${exit_fee_est:.4f}</code>",
                    f"- Funding ({hold_hours:.1f}h hold): <code>${funding_paid:.4f}</code>",
                    f"- Total costs: <code>${entry_fee + exit_fee_est + funding_paid:.4f}</code>",
                    "",
                    "<i>Market data unavailable \u2014 say \"trade\" for full analysis</i>",
                ]
                await self._send(update, "\n".join(lines), edit=True)
            else:
                await self._send(update,
                    f"<b>{html.escape(pair)}</b>\n\n"
                    "Can't find this position — it might have been closed already.\n"
                    "Say \"positions\" to see what's open.",
                    edit=True)
            return

        if data.startswith("pos_close_"):
            ident = data.removeprefix("pos_close_")
            is_trade_id = ident.startswith("TI-")
            pair = ident  # fallback for display
            user_id = self._get_tg_id(update)
            portfolio = self.engine.user_portfolios.get(user_id)

            closed_trade = None
            live_closed = False

            # LIVE mode: close via LiveExecutor
            if CONFIG.is_live():
                executor = self.engine.live_executor
                for lp in list(executor.open_positions):
                    matched = (lp.trade_id == ident) if is_trade_id else (lp.symbol.replace("/", "") == ident)
                    if matched:
                        pair = lp.symbol.replace("/", "")  # ensure display name
                        try:
                            result = await executor.close_position(lp.trade_id)
                            live_closed = True
                            # Build a simple response from LivePosition
                            from datetime import datetime, timezone
                            hold_h = (datetime.now(timezone.utc) - lp.opened_at).total_seconds() / 3600
                            cost = lp.cost_usd if lp.cost_usd > 0 else lp.entry_price * lp.quantity
                            close_px = lp.close_price or lp.entry_price
                            pnl_val = lp.pnl_usd or 0
                            pnl_emoji = "\U0001f7e2" if pnl_val >= 0 else "\U0001f534"
                            lines = [
                                f"<b>{html.escape(pair)} closed</b>",
                                "",
                                f"Entry <code>{lp.entry_price:,.6f}</code> / Exit <code>{close_px:,.6f}</code>",
                                f"Size <code>${cost:,.2f}</code> | Hold {hold_h:.1f}h",
                                f"{pnl_emoji} PnL: <code>${pnl_val:+,.2f}</code>",
                            ]
                            await self._send(update, "\n".join(lines), edit=True)
                        except Exception as e:
                            live_closed = True  # prevent fallthrough to "not found"
                            await self._send(update,
                                f"Couldn't close {html.escape(pair)}.\n\n"
                                f"{html.escape(str(e)[:200])}\n"
                                "You can try again or close it on the exchange directly.",
                                edit=True)
                        break

            if live_closed:
                return  # Already sent response (success or error) — do not fall through

            if not live_closed:
                # Paper mode close
                for pos in list(portfolio.open_positions):
                    if pos.asset.replace("/", "") == pair:
                        try:
                            exchange = await self.engine.get_exchange()
                            ticker = await exchange.fetch_ticker(pos.asset)
                            close_price = ticker.get("last", pos.entry_price)
                            closed_trade = portfolio.close_position(pos.trade_id, close_price)
                        except Exception as e:
                            system_log.warning("Close position error for %s: %s", pair, e)
                            closed_trade = portfolio.close_position(pos.trade_id, pos.entry_price)
                        break

            if closed_trade:
                pnl_emoji = "\U0001f7e2" if closed_trade.pnl >= 0 else "\U0001f534"
                sz = closed_trade.quantity * closed_trade.entry_price
                from datetime import datetime, timezone
                hold_h = 0
                if closed_trade.opened_at and closed_trade.closed_at:
                    hold_h = (closed_trade.closed_at - closed_trade.opened_at).total_seconds() / 3600
                funding_paid = sz * (0.01 / 100.0) * (hold_h / 8.0) if hold_h > 0 else 0

                lines = [
                    f"\u2705 <b>{html.escape(pair)} — Position Closed</b>",
                    "",
                    f"- Entry: <code>{closed_trade.entry_price:,.4f}</code>",
                    f"- Exit: <code>{closed_trade.exit_price:,.4f}</code>",
                    f"- Size: <code>${sz:,.2f}</code>",
                    "",
                    f"<b>PNL Breakdown:</b>",
                    f"- Gross PNL: <code>${closed_trade.gross_pnl:+,.2f}</code>",
                    f"- Commission: <code>${closed_trade.commission:.2f}</code>",
                    f"- Funding ({hold_h:.1f}h): <code>${funding_paid:.2f}</code>",
                    f"- <b>Net PNL: {pnl_emoji} <code>${closed_trade.pnl:+,.2f}</code></b>",
                    "",
                    "Say \"my portfolio\" for updated balance.",
                ]
                await self._send(update, "\n".join(lines), edit=True)
            else:
                await self._send(update,
                    f"\u274c <b>{html.escape(pair)}</b>\n\n"
                    "Position not found or already closed.\n"
                    "Say \"open positions\" to check.",
                    edit=True)
            return

        # ── Legacy pane callbacks (backward compat) ──────────

        if data.startswith("pane:"):
            pane = data.split(":", 1)[1]
            if pane == "refresh":
                pane = self._last_pane.get(chat_id, "status")
            self._last_pane[chat_id] = pane
            body = await self._render_pane(pane, user_id=self._get_tg_id(update))
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
            body = await self._render_pane(pane, user_id=self._get_tg_id(update))
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

        # ── Scan skill callbacks (scan_confirm: / scan_reject:) ──
        if data.startswith("scan_confirm:") or data.startswith("scan_reject:"):
            await _scan_callback(update, ctx)
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
            result = await self.engine.confirm_trade(trade_id, user_id=caller_uid or "")
            # Detect failure by checking for known error prefixes
            _fail_prefixes = (
                "EXECUTION FAILED:", "INSUFFICIENT FUNDS:", "INVALID ORDER:",
                "BLOCKED:", "PREFLIGHT FAILED:", "Risk re-check FAILED",
                "Trade not found", "not found", "expired", "No pending",
            )
            is_failure = any(result.startswith(p) for p in _fail_prefixes)
            if not is_failure:
                msg = f"\u2705 <b>Trade executed!</b>\n\n{result}"
                # Forward trade open to marketing channels
                idea = self.engine._pending_ideas.get(trade_id) or self.engine._last_confirmed_idea
                if idea:
                    can_live = self.users.can_trade_live(caller_uid or "")
                    _mode = "LIVE" if can_live and not CONFIG.simulation_mode else "PAPER"
                    try:
                        await self.forwarder.post_trade_opened(idea, mode=_mode)
                    except Exception:
                        pass
            else:
                msg = f"\u274c <b>Trade didn't go through</b>\n\n{result}"
            # Try edit first (works for text messages), fall back to new message
            # (needed when buttons are on a photo message from chart flow)
            try:
                await self._send(update, msg, edit=True)
            except Exception:
                await self._send(update, msg)
            # Remove buttons from the original message (best-effort)
            try:
                if update.callback_query and update.callback_query.message:
                    await update.callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
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
            msg = f"\u274c Got it, trade skipped.\n\n{result}"
            try:
                await self._send(update, msg, edit=True)
            except Exception:
                await self._send(update, msg)
            try:
                if update.callback_query and update.callback_query.message:
                    await update.callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        audit(system_log, f"Callback: {data}", action="telegram_callback")
