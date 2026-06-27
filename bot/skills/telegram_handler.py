"""
RUNECLAW Telegram Handler v6 — MuleRun War Room edition.
War Room branding, tactical signal cards, risk control panel,
strategy mode selector, emergency stop, and Telegram Mini App link.
File-backed user management with roles and admin commands.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from bot.compat import UTC
from typing import Optional

# Module logger. Several exception/admin paths referenced bare `os`/`logger`
# without these being in scope — latent NameErrors (flagged by ruff F821).
logger = logging.getLogger(__name__)

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
from bot.skills.user_middleware import cmd_link as _cmd_link, cmd_unlink as _cmd_unlink, cmd_me as _cmd_me, cmd_sync as _cmd_sync
from bot.utils.logger import audit, system_log, _redact_string
from bot.utils.user_store import UserStore
from bot.utils.i18n import t, get_user_lang, set_user_lang, SUPPORTED_LANGS
from bot.nlp.intent_router import IntentRouter
from bot.nlp.conversation_store import ConversationStore
from bot.core.proactive_monitor import ProactiveMonitor
from bot.marketing.channel_forwarder import ChannelForwarder
from bot.formatters.rich_cards import (
    display_symbol,
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

# Prefixes for orphan-adopted and diagnostic-injected trades.
# Used throughout handlers to exclude these from user-facing stats.
_ORPHAN_PREFIXES = ("TI-adopted", "TI-injected")


def _sanitize_chat_input(text: str) -> str:
    """Sanitize free-form user text before sending to LLM.

    - Strips prompt-injection patterns FIRST
    - Then truncates to 500 characters

    DEFENSE-IN-DEPTH ONLY (RC-AUD-014): this denylist is thin and trivially
    bypassable. It is NOT a security boundary. The real boundary is the
    execution gate — LLM chat output has no execution authority; trades still
    require confirm_trade -> compliance -> executor with numeric re-validation.
    Keep it light so it never blocks legitimate trading commands.
    """
    sanitized = _INJECTION_PATTERNS.sub("[filtered]", text)
    truncated = sanitized[:_MAX_CHAT_INPUT_LEN]
    return truncated.strip()


def _sanitize_history_for_llm(history: list[dict]) -> list[dict]:
    """Sanitize replayed USER turns before they reach the LLM.

    RC-AUD-014: conversation-memory replay (``get_recent_as_llm_messages``)
    returns raw user text that was stored unsanitized. This closes that path by
    applying the same sanitizer to ``role == "user"`` turns on read; assistant
    turns are left intact. Defense-in-depth only — see ``_sanitize_chat_input``;
    the real boundary remains the execution gate.
    """
    out: list[dict] = []
    for m in history:
        if isinstance(m, dict) and m.get("role") == "user":
            out.append({**m, "content": _sanitize_chat_input(m.get("content", ""))})
        else:
            out.append(m)
    return out


# ── War Room main menu keyboard ─────────────────────────────

_KB_WARROOM = InlineKeyboardMarkup([
    [InlineKeyboardButton("Scan Market", callback_data="open_warroom"),
     InlineKeyboardButton("Latest Signal", callback_data="latest_signal")],
    [InlineKeyboardButton("Positions", callback_data="positions"),
     InlineKeyboardButton("Performance", callback_data="performance")],
    [InlineKeyboardButton("Orders", callback_data="orders"),
     InlineKeyboardButton("Risk", callback_data="risk_control")],
    [InlineKeyboardButton("Stop Bot", callback_data="risk_emergency_stop")],
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
        app.bot_data["telegram_handler"] = self
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
            ("orders", self._cmd_orders),
            ("performance", self._cmd_performance),
            ("pause", self._cmd_pause),
            ("resume", self._cmd_resume),
            ("emergency_stop", self._cmd_emergency_stop),
            ("closeall", self._cmd_close_all),
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
            ("sync", _cmd_sync),
            ("lang", self._cmd_lang),
            ("autoconfirm", self._cmd_autoconfirm),
            ("forcescan", self._cmd_forcescan),
            ("session", self._cmd_session),
            ("montecarlo", self._cmd_montecarlo),
            ("attribution", self._cmd_attribution),
            ("equitycurve", self._cmd_equitycurve),
            ("crossasset", self._cmd_crossasset),
            ("slippage", self._cmd_slippage),
            ("sweep", self._cmd_sweep),
            ("zones", self._cmd_zones),
            ("squeeze", self._cmd_squeeze),
            ("holdtime", self._cmd_holdtime),
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
        # Audit F-15: scrub secrets from every outgoing message. Many handlers
        # interpolate raw str(exc) into replies; the logger redacts its own
        # output but the Telegram send path did not, so a credential-bearing
        # ccxt/auth error could reach the chat unredacted. This is the single
        # chokepoint for all outbound text.
        if text:
            try:
                text = _redact_string(text)
            except Exception:
                pass
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
            return await self.registry.dispatch("check_risk", self.engine, mode="status", **kw)
        elif pane == "risk":
            return await self.registry.dispatch("check_risk", self.engine, mode="risk", **kw)
        elif pane == "portfolio":
            return await self.registry.dispatch("get_portfolio", self.engine, **kw)
        elif pane == "macro":
            return await self.registry.dispatch("macro_calendar", self.engine, **kw)
        elif pane == "learning":
            return await self.registry.dispatch("learning", self.engine, **kw)
        elif pane == "scan":
            return await self.registry.dispatch("scan_market", self.engine, **kw)
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
                live_closed_all = executor.closed_positions if executor else []
                live_open = executor.open_positions if executor else []
                # Exclude adopted orphan trades from stats
                live_closed = [t for t in live_closed_all
                               if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
                total_trades = len(live_closed)
                wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
                win_rate_val = wins / total_trades if total_trades > 0 else 0
                total_pnl = sum(t.pnl_usd or 0 for t in live_closed)
                total_fees = sum(t.commission or 0 for t in live_closed)
                portfolio_summary = (
                    f"{len(live_open)} open positions, "
                    f"equity ~${eq_display:,.2f}, "
                    f"net PnL ${total_pnl:+,.2f} (fees ${total_fees:.2f}), "
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
                # Filter out canceled/expired limit orders (never-filled, $0 PnL)
                _ntr = {"canceled", "cancelled", "expired", "price_drift", "rejected"}
                live_closed = [t for t in executor.closed_positions
                               if getattr(t, "close_reason", "") not in _ntr]
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
                        user_name: str = "",
                        is_admin: bool = False) -> str:
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

        # Build personalized system prompt.
        # RC-AUD-014: the display name is user-influenced (Telegram first_name)
        # and reaches the system prompt via build_context_prompt — sanitize it
        # (defense-in-depth; the real boundary is the execution gate).
        system_prompt = self._build_chat_system_prompt(
            user_id,
            user_name=_sanitize_chat_input(user_name) if user_name else user_name)

        # Get conversation history for multi-turn context
        history = []
        if user_id:
            history = self.conversations.get_recent_as_llm_messages(
                user_id, limit=8)
            # RC-AUD-014: sanitize replayed user turns. The stored history holds
            # raw user text (stored unsanitized), so without this the
            # conversation-memory replay path bypasses the call-site
            # sanitization of the live question. Defense-in-depth only — the
            # real boundary is the execution gate.
            history = _sanitize_history_for_llm(history)

        # Build fallback chain: chat tier → fallback providers → primary
        import os
        configs_to_try = []

        # 1. Primary chat tier config
        chat_cfg = resolve_tier_config(LLMTier.CHAT, active_cfg, is_admin=is_admin)
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

        # ── Custom limit price input ──────────────────────────
        # If user is in "set limit" mode, capture the price they type
        caller_uid = str(uid)
        if hasattr(self, '_pending_limit_input') and caller_uid in self._pending_limit_input:
            pending_info = self._pending_limit_input[caller_uid]
            # Expire stale limit-price requests after 5 minutes
            if time.time() - pending_info.get("timestamp", 0) > 300:
                del self._pending_limit_input[caller_uid]
                pending_info = None
        if hasattr(self, '_pending_limit_input') and caller_uid in self._pending_limit_input:
            pending_info = self._pending_limit_input[caller_uid]
            # Try to parse as a number
            try:
                custom_price = float(text.replace("$", "").replace(",", "").strip())
                if custom_price <= 0:
                    raise ValueError("Price must be positive")

                trade_id = pending_info["trade_id"]
                pair = pending_info["pair"]
                direction = pending_info["direction"]

                # Update the idea's entry price
                idea = self.engine._pending_ideas.get(trade_id)
                if not idea:
                    del self._pending_limit_input[caller_uid]
                    await self._send(update, "<b>Trade expired.</b> Run a new scan.")
                    return

                old_price = idea.entry_price
                idea.entry_price = custom_price
                # Force limit order type
                idea.order_type = "limit"

                # Clean up
                del self._pending_limit_input[caller_uid]

                # Show confirmation and execute
                await self._send(update,
                    f"\U0001f4b0 <b>Limit set: {pair} {direction}</b>\n"
                    f"Entry: <code>${old_price:,.4f}</code> \u2192 <code>${custom_price:,.4f}</code>\n\n"
                    f"\u2705 <b>Confirmed — executing...</b>")

                # H-18 FIX: LIVE mode — check per-user live trading permission
                if CONFIG.is_live() and not self._is_admin(update):
                    caller_uid_str = str(update.effective_user.id) if update.effective_user else ""
                    if not self.users.can_trade_live(caller_uid_str):
                        await self._send(update,
                            "\U0001f512 <b>Live trading not enabled</b>\n\n"
                            "Ask an admin to grant you live trading access with /grant_live.")
                        return

                result = await self.engine.confirm_trade(trade_id, user_id=caller_uid)
                await self._send(update, result)
                return

            except ValueError:
                # Not a valid number — cancel the limit input mode
                if text.lower() in ("cancel", "no", "back", "nevermind"):
                    del self._pending_limit_input[caller_uid]
                    await self._send(update, "Limit price cancelled. Use the buttons to confirm or skip.")
                    return
                # Otherwise try to parse, maybe they typed something weird
                await self._send(update,
                    f"\u26a0\ufe0f <b>Invalid price:</b> <code>{html.escape(text[:30])}</code>\n\n"
                    f"Type a number (e.g. <code>84.07</code>) or <code>cancel</code>.")
                return

        # ── Manual trade via natural language ──────────────────────
        # Intercept "buy SOL 71 sl 70 tp 76" or "trade short ETH 1721 sl 1695 tp 1842"
        # before the intent router can misroute it
        _trade_text = text.lower().strip()
        if _trade_text.startswith("trade "):
            _trade_text = _trade_text[6:].strip()
        _trade_prefixes = ("buy ", "long ", "short ", "sell ")
        if any(_trade_text.startswith(p) for p in _trade_prefixes) and " sl " in _trade_text:
            # Looks like a manual trade command — delegate to _cmd_trade
            # Simulate the /trade command by prepending it
            original_text = update.message.text
            update.message.text = f"/trade {_trade_text}"
            await self._cmd_trade(update, ctx)
            update.message.text = original_text  # restore
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
                    result = await self.registry.dispatch("deepscan",
                        self.engine, timeframe="4h")
                elif intent.skill == "scan_full":
                    result = await self.registry.dispatch("deepscan",
                        self.engine, timeframe="4h")
                else:
                    result = await self.registry.dispatch("pro_scan",
                        self.engine, mode=mode, user_id=tg_id)
                await self._send(update, result)
                return

            # ── Orders intent → direct command ──
            if intent.skill == "get_orders":
                await self._cmd_orders(update, ctx)
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

                # For analyze_asset: track pending ideas so we can attach signal card
                ids_before = set()
                if intent.skill == "analyze_asset":
                    ids_before = set(idea.id for idea in self.engine.pending_ideas)

                try:
                    result = await skill.execute(self.engine, user_id=tg_id, **intent.kwargs)
                    # Store skill result as assistant message (truncated)
                    self.conversations.append(tg_id, "assistant",
                                               f"[{intent.skill}] executed successfully",
                                               metadata={"skill": intent.skill})

                    # For analyze_asset: check if a new trade idea was created
                    if intent.skill == "analyze_asset" and ids_before is not None:
                        new_idea = None
                        for idea in self.engine.pending_ideas:
                            if idea.id not in ids_before:
                                new_idea = idea
                                break
                        if new_idea:
                            uid = update.effective_user.id if update.effective_user else ""
                            kb = InlineKeyboardMarkup([[
                                InlineKeyboardButton("Take it",
                                    callback_data=f"confirm:{new_idea.id}:{uid}"),
                                InlineKeyboardButton("Limit",
                                    callback_data=f"setlimit:{new_idea.id}:{uid}"),
                                InlineKeyboardButton("Skip",
                                    callback_data=f"reject:{new_idea.id}:{uid}"),
                            ]])
                            # Try to send signal card image
                            card_sent = False
                            try:
                                from bot.formatters.signal_card import signal_card_from_idea
                                png = signal_card_from_idea(new_idea, rank=1)
                                if png:
                                    pair = display_symbol(new_idea.asset)
                                    d = new_idea.direction.value if hasattr(new_idea.direction, "value") else str(new_idea.direction)
                                    st = getattr(new_idea, 'strategy_type', '').upper()
                                    st_str = f" [{st}]" if st else ""
                                    cap = f"<b>{pair} {d}</b>{st_str} | Conf {new_idea.confidence*100:.0f}%"
                                    card_sent = await self._send_photo(update, png, cap, reply_markup=kb)
                            except Exception:
                                pass
                            # Send text result (with or without card)
                            if card_sent:
                                await self._send(update, result)
                            else:
                                await self._send(update, result, reply_markup=kb)
                            return

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
            _sanitize_chat_input(text), user_id=tg_id, user_name=user_name,
            is_admin=self._is_admin(update))

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

    @staticmethod
    def _uid_matches(caller_uid: str | None, expected_uid: str | None) -> bool:
        """Check if caller matches expected UID(s).

        expected_uid may be a single ID or comma-separated list (from auto-scan
        where CONFIG.telegram.chat_id contains multiple IDs).  Returns True if
        caller is in the list, or if expected_uid is empty/None (allow all).
        """
        if not expected_uid:
            return True
        if not caller_uid:
            return False
        return caller_uid in {s.strip() for s in expected_uid.split(",") if s.strip()}

    def _allowlist_ids(self) -> set[str]:
        """Telegram IDs permitted to use the bot (audit F-2).

        Sourced from TELEGRAM_CHAT_ID (the operator; may be comma-separated for
        multi-channel auto-scan) plus ADMIN_TELEGRAM_IDS. An EMPTY set means no
        allowlist is configured (e.g. an unconfigured demo / paper setup), in
        which case the allowlist is NOT enforced and the prior open-registration
        behavior is preserved — live mode already requires TELEGRAM_CHAT_ID via
        is_live(), so a live bot always has a non-empty allowlist.
        """
        ids: set[str] = set()
        for raw in (CONFIG.telegram.chat_id, CONFIG.telegram.admin_ids):
            if raw:
                ids |= {s.strip() for s in str(raw).split(",") if s.strip()}
        return ids

    def _is_allowlisted(self, update: Update) -> bool:
        """True if the caller may use the bot. Audit F-2: closes the
        open-self-registration hole where any /start made a stranger an
        authorized trader (able to /halt, /reset, /mode, emergency-stop)."""
        allow = self._allowlist_ids()
        if not allow:
            return True  # no allowlist configured -> preserve open/demo behavior
        return self._get_tg_id(update) in allow

    def _is_admin(self, update: Update) -> bool:
        """Check if the user is an admin (user-store role OR ADMIN_TELEGRAM_IDS)."""
        tg_id = self._get_tg_id(update)
        # Primary: user store role
        user = self.users.get(tg_id)
        if user is not None and user.get("role") == "admin":
            return True
        # Fallback: explicit ADMIN_TELEGRAM_IDS env var
        admin_ids_raw = CONFIG.telegram.admin_ids
        if admin_ids_raw:
            admin_ids = {s.strip() for s in admin_ids_raw.split(",") if s.strip()}
            if tg_id in admin_ids:
                return True
        return False

    def _check_auth(self, update: Update) -> bool:
        """Check if user is authorized (any role except pending).

        Audit F-2: a non-allowlisted caller is never authorized, regardless of
        user-store state. This is the gate for inline-keyboard callbacks
        (emergency-stop / pause / mode) which do not go through _guard.
        """
        if not self._is_allowlisted(update):
            return False
        tg_id = self._get_tg_id(update)
        return self.users.is_authorized(tg_id)

    async def _guard(self, update: Update, command: str = "") -> bool:
        """Auth + rate limit + role permission check."""
        tg_id = self._get_tg_id(update)
        user = self.users.get(tg_id)

        # Audit F-2: hard allowlist gate. Only TELEGRAM_CHAT_ID / ADMIN_TELEGRAM_IDS
        # may reach any privileged command; the user store's auto-approval can no
        # longer grant a stranger access to a live bot.
        if not self._is_allowlisted(update):
            await self._send(update,
                "\U0001f512 <b>Access restricted</b>\n\n"
                "This bot is locked to its configured operator.\n"
                f"Your Telegram ID: <code>{tg_id}</code>")
            return False

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
        # H-16 FIX: rate limit /start
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return  # rate limited
        now = datetime.now(UTC).strftime("%H:%M UTC")
        user_tg = update.effective_user
        tg_id = self._get_tg_id(update)
        user_name = html.escape(user_tg.first_name) if user_tg else "Trader"

        # Auto-register on first contact
        record = self.users.register(tg_id, name=user_name)

        if not record.get("authorized", False):
            lang = get_user_lang(self.users, tg_id)
            msg = t("welcome_pending", lang, name=user_name, tg_id=tg_id)
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
            # Count filled vs pending separately
            _all_tracked = list(executor._positions.values())
            _filled_count = sum(1 for p in _all_tracked if p.status == "open")
            _pending_count = sum(1 for p in _all_tracked if p.status == "pending_fill")

            # Cross-check with exchange for accurate pending order count
            # The bot's internal count can be stale after restarts
            try:
                _ex = await executor._get_exchange()
                _ex_orders = await _ex.fetch_open_orders(
                    params={"productType": "USDT-FUTURES"})
                # Only count limit orders (not SL/TP trigger orders)
                _ex_limit_orders = [
                    o for o in (_ex_orders or [])
                    if (o.get("type") or "").lower() == "limit"
                ]
                _exchange_pending = len(_ex_limit_orders)
                if _exchange_pending != _pending_count:
                    _pending_count = _exchange_pending
            except Exception:
                pass  # Fall back to internal count

            # Fallback: if no locally-tracked positions, check exchange directly
            # This catches orphan positions (opened but lost from local state)
            if open_pos == 0:
                try:
                    _ex = await executor._get_exchange()
                    _ex_pos = await _ex.fetch_positions()
                    _ex_open = [p for p in (_ex_pos or [])
                                if isinstance(p, dict) and float(p.get("contracts") or 0) > 0]
                    if _ex_open:
                        open_pos = len(_ex_open)
                except Exception:
                    pass

            live_closed = executor.closed_positions
            # Exclude adopted orphan trades and injected diagnostic artifacts from win rate
            user_closed = [t for t in live_closed
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
            if user_closed:
                wins = sum(1 for t in user_closed if (t.pnl_usd or 0) > 0)
                win_rate = f"{wins / len(user_closed) * 100:.0f}"
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

        # Get user language preference
        lang = get_user_lang(self.users, tg_id)

        # Bilingual status labels
        status_label_zh = t("status_active", "zh") if not cb_active else t("status_paused", "zh")
        trade_mode_zh = t("mode_live", "zh") if can_live else t("mode_paper", "zh")
        pending_str = f' | Pending orders: <code>{_pending_count}</code>' if _pending_count > 0 else ''
        pending_str_zh = f' | 掛單: <code>{_pending_count}</code>' if _pending_count > 0 else ''

        # Format win rate with % sign
        wr_display = f"{win_rate}%" if win_rate != "N/A" else "N/A"

        body = t('welcome_ready', lang,
                 name=user_name,
                 status_icon=status_icon,
                 status_label=status_label,
                 status_label_zh=status_label_zh,
                 mode=mode,
                 equity=equity,
                 filled=_filled_count,
                 pending_str=pending_str,
                 pending_str_zh=pending_str_zh,
                 win_rate=wr_display,
                 tier=tier_label,
                 trade_mode=trade_mode,
                 trade_mode_zh=trade_mode_zh,
                 time=time)

        msg = f"<b>RUNECLAW</b>\n{SEP}\n\n{body}"
        await self._send(update, msg, reply_markup=_KB_WARROOM)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw help — organized command reference."""
        # H-16 FIX: rate limit /help
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return  # rate limited
        tg_id = self._get_tg_id(update)
        is_auth = self.users.is_authorized(tg_id)
        user = self.users.get(tg_id)
        role = user.get("role", "pending") if user else "pending"
        lang = get_user_lang(self.users, tg_id)

        _sep = "\u2500" * 20
        _pending_zh = "等待審核中 \u2014 請使用 /start 註冊"
        _pending_en = "Status: pending approval \u2014 use /start to register"

        if not is_auth:
            await self._send(update,
                f"\u2694\ufe0f <b>RUNECLAW</b>\n"
                f"{_sep}\n"
                f"<i>{_pending_zh if lang == 'zh' else _pending_en}</i>")
            return

        tier_label = self.users.tier_label(tg_id)
        can_live = self.users.can_trade_live(tg_id)
        trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"

        msg = (
            f"{t('help_title', lang)}\n"
            f"{_sep}\n"
            f"{tier_label} | {trade_mode}\n\n"
            f"{t('help_tip', lang)}\n\n"
            f"{t('help_market', lang)}\n\n"
            f"{t('help_trading', lang)}\n\n"
            f"{t('help_portfolio', lang)}\n\n"
            f"{t('help_strategy', lang)}\n\n"
            f"{t('help_tools', lang)}\n\n"
            f"{t('help_controls', lang)}\n\n"
            f"{t('help_account', lang)}\n\n"
            f"{t('help_ai', lang)}\n"
        )

        # Live trading (show for users with live access)
        if can_live or role == "admin":
            msg += f"\n{t('help_live', lang)}\n"

        # Admin section
        if role == "admin":
            msg += (
                f"\n{t('help_admin', lang)}\n"
                f"/stockscan \u2014 {'股市掃描' if lang == 'zh' else 'stock market scan'}\n"
                f"/channel \u2014 {'管理自動發佈' if lang == 'zh' else 'manage auto-posting'}\n"
                f"/broadcast \u2014 {'群組廣播' if lang == 'zh' else 'send message to groups'}\n"
            )

        await self._send(update, msg)

    # ── Language command ──────────────────────────────────────

    async def _cmd_lang(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch between English and Traditional Chinese."""
        if not await self._guard(update, "lang"):
            return
        tg_id = self._get_tg_id(update)
        current_lang = get_user_lang(self.users, tg_id)

        args = ctx.args or []
        if args:
            new_lang = args[0].lower().strip()
            # Accept various inputs
            lang_map = {
                "en": "en", "english": "en", "eng": "en",
                "zh": "zh", "zh-tw": "zh", "chinese": "zh",
                "中文": "zh", "繁體": "zh", "繁中": "zh", "繁體中文": "zh",
            }
            new_lang = lang_map.get(new_lang, new_lang)
            if new_lang in SUPPORTED_LANGS:
                set_user_lang(self.users, tg_id, new_lang)
                await self._send(update, t("lang_switched", new_lang))
                return

        # No args or invalid — show buttons
        buttons = [
            [InlineKeyboardButton("English", callback_data="lang:en"),
             InlineKeyboardButton("繁體中文", callback_data="lang:zh")],
        ]
        await self._send(update,
            f"🌐 {t('lang_prompt', current_lang)}\n\n"
            f"Current / 目前: <b>{SUPPORTED_LANGS.get(current_lang, 'English')}</b>",
            reply_markup=InlineKeyboardMarkup(buttons))

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

        # L-13 FIX: validate Telegram ID format
        if not target_id.isdigit():
            await self._send(update, "Invalid Telegram ID format.")
            return

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
        """Switch asset universe: /mode solana | /mode all | /mode stocks | /mode metals | etc."""
        if not await self._guard(update, "mode"):
            return

        args = (update.message.text or "").split()
        valid_modes = {"all_markets", "all", "solana", "stocks", "hybrid", "metals",
                       "commodities", "etfs", "pre_ipo", "tradfi"}

        if len(args) < 2 or args[1].lower() not in valid_modes:
            from bot.config import RUNTIME
            current = RUNTIME.asset_universe
            icons = {
                "all_markets": "\U0001f310", "solana": "\u2600\ufe0f",
                "all": "\U0001f30d", "stocks": "\U0001f4c8",
                "hybrid": "\U0001f500", "metals": "\u2699\ufe0f", "commodities": "\U0001f6e2\ufe0f",
                "etfs": "\U0001f4ca", "pre_ipo": "\U0001f680", "tradfi": "\U0001f3e6",
            }
            icon = icons.get(current, "\U0001f30d")
            lines = [
                f"\U0001f504 <b>ASSET UNIVERSE</b>\n",
                f"Current: {icon} <b>{current.upper()}</b>\n",
                "<b>Multi-Asset:</b>",
                "  <code>/mode all_markets</code> \u2014 EVERYTHING: crypto + all TradFi futures",
                "",
                "<b>Crypto:</b>",
                "  <code>/mode all</code> \u2014 all Bitget USDT spot pairs",
                "  <code>/mode solana</code> \u2014 Solana ecosystem tokens",
                "",
                "<b>TradFi Perpetuals (Futures):</b>",
                "  <code>/mode stocks</code> \u2014 US stock tokenized perps",
                "  <code>/mode hybrid</code> \u2014 crypto + stocks combined",
                "  <code>/mode metals</code> \u2014 Gold, Silver, Platinum, Copper",
                "  <code>/mode commodities</code> \u2014 WTI Oil, Brent, Natural Gas",
                "  <code>/mode etfs</code> \u2014 ETF perpetuals (XLK, KWEB, etc.)",
                "  <code>/mode pre_ipo</code> \u2014 Pre-IPO (OpenAI, Anthropic)",
                "  <code>/mode tradfi</code> \u2014 ALL TradFi combined",
            ]
            if current == "solana":
                from bot.config import SOLANA_ECOSYSTEM_SYMBOLS
                tokens = ", ".join(s.replace("/USDT", "") for s in SOLANA_ECOSYSTEM_SYMBOLS)
                lines.append(f"\nTokens: <i>{tokens}</i>")
            elif current == "stocks":
                from bot.config import US_STOCK_SYMBOLS
                tickers = ", ".join(s.replace("/USDT", "") for s in US_STOCK_SYMBOLS)
                lines.append(f"\nStocks: <i>{tickers}</i>")
            elif current in ("metals", "commodities", "etfs", "pre_ipo", "tradfi"):
                from bot.config import (
                    METAL_PERPETUALS, COMMODITY_PERPETUALS,
                    PRE_IPO_PERPETUALS, ETF_PERPETUALS, TRADFI_PERPETUALS,
                )
                perp_map = {
                    "metals": METAL_PERPETUALS,
                    "commodities": COMMODITY_PERPETUALS,
                    "pre_ipo": PRE_IPO_PERPETUALS,
                    "etfs": ETF_PERPETUALS,
                    "tradfi": TRADFI_PERPETUALS,
                }
                symbols = perp_map.get(current, [])
                names = ", ".join(s.split("/")[0] for s in symbols)
                lines.append(f"\nAssets: <i>{names}</i>")
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
        elif new_mode == "metals":
            from bot.config import METAL_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in METAL_PERPETUALS)
            await self._send(update, (
                "\u2699\ufe0f <b>METALS MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(METAL_PERPETUALS)} metal perpetual contracts (USDT-M Futures):\n"
                f"<i>{names}</i>\n\n"
                "These are commodity-backed perpetuals tradeable 24/7.\n"
                "Lower volume threshold applied for less liquid metals.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "commodities":
            from bot.config import COMMODITY_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in COMMODITY_PERPETUALS)
            await self._send(update, (
                "\U0001f6e2\ufe0f <b>COMMODITIES MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(COMMODITY_PERPETUALS)} energy commodity perpetuals:\n"
                f"<i>{names}</i>\n\n"
                "WTI Oil, Brent Crude, Natural Gas — USDT-M Futures.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "etfs":
            from bot.config import ETF_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in ETF_PERPETUALS)
            await self._send(update, (
                "\U0001f4ca <b>ETF MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(ETF_PERPETUALS)} ETF perpetual contracts:\n"
                f"<i>{names}</i>\n\n"
                "Tech, Defense, China Internet, Treasury, HK, India ETFs.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "pre_ipo":
            from bot.config import PRE_IPO_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in PRE_IPO_PERPETUALS)
            await self._send(update, (
                "\U0001f680 <b>PRE-IPO MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(PRE_IPO_PERPETUALS)} pre-IPO stock perpetuals:\n"
                f"<i>{names}</i>\n\n"
                "Pre-IPO tech company tokens on Bitget — high volatility, use caution.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "tradfi":
            from bot.config import TRADFI_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in TRADFI_PERPETUALS)
            await self._send(update, (
                "\U0001f3e6 <b>TRADFI MODE ACTIVE</b>\n\n"
                f"Scanner covers ALL {len(TRADFI_PERPETUALS)} TradFi perpetuals:\n"
                f"<i>{names}</i>\n\n"
                "Metals + Commodities + ETFs + Pre-IPO combined.\n"
                "All USDT-M Futures.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "all_markets":
            from bot.config import TRADFI_PERPETUALS
            await self._send(update, (
                "\U0001f310 <b>ALL MARKETS MODE ACTIVE</b>\n\n"
                "Scanner now covers <b>everything</b> in one scan:\n"
                "\u2022 All Bitget crypto spot pairs\n"
                f"\u2022 {len(TRADFI_PERPETUALS)} TradFi futures (metals, oil, ETFs, pre-IPO)\n\n"
                "Results are categorized by asset class.\n"
                "Spot + Futures fetched in parallel.\n\n"
                "Use <code>/mode all</code> for crypto-only."
            ))
        else:
            await self._send(update, (
                "\U0001f30d <b>CRYPTO-ONLY MODE</b>\n\n"
                "Scanner now covers all Bitget USDT spot pairs.\n"
                "Use <code>/mode all_markets</code> for everything or "
                "<code>/mode solana</code> for Solana."
            ))

    # ── Live Trading Commands ─────────────────────────────────

    async def _cmd_autoconfirm(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/autoconfirm — view or set auto-confirm threshold.

        Usage:
          /autoconfirm         — show current threshold
          /autoconfirm 0.75    — set to 75% confidence
          /autoconfirm off     — disable (set to 1.0)
        """
        if not await self._guard(update, "admin"):
            return

        from bot.config import RUNTIME
        args = ctx.args or []

        if not args:
            # Show current state
            threshold = CONFIG.auto_confirm_threshold
            if threshold >= 1.0:
                status = "\U0001f534 <b>OFF</b> — all trades require manual confirmation"
            else:
                status = f"\U0001f7e2 <b>ON</b> — trades with confidence \u2265 <b>{threshold*100:.0f}%</b> auto-execute"
            await self._send(update,
                f"\U0001f916 <b>Auto-Confirm Status</b>\n\n"
                f"{status}\n\n"
                f"<b>Commands:</b>\n"
                f"\u2022 <code>/autoconfirm 0.75</code> — auto-confirm \u2265 75%\n"
                f"\u2022 <code>/autoconfirm off</code> — disable\n"
                f"\u2022 <code>/autoconfirm 0.60</code> — aggressive (60%+)")
            return

        arg = args[0].lower()
        if arg in ("off", "disable", "manual"):
            # Use RUNTIME to override the frozen CONFIG value
            RUNTIME.auto_confirm_threshold = 1.0
            audit(system_log, "Auto-confirm DISABLED via /autoconfirm off",
                  action="autoconfirm", result="DISABLED",
                  data={"user": self._get_tg_id(update)})
            await self._send(update,
                "\U0001f534 <b>Auto-Confirm DISABLED</b>\n\n"
                "All trades now require manual confirmation.")
            return

        try:
            new_threshold = float(arg)
            if new_threshold < 0.5 or new_threshold > 1.0:
                await self._send(update,
                    "\u274c Threshold must be between 0.50 and 1.00\n"
                    "Example: <code>/autoconfirm 0.75</code>")
                return
            RUNTIME.auto_confirm_threshold = new_threshold
            audit(system_log, f"Auto-confirm threshold set to {new_threshold}",
                  action="autoconfirm", result="SET",
                  data={"user": self._get_tg_id(update), "threshold": new_threshold})
            await self._send(update,
                f"\U0001f916 <b>Auto-Confirm Updated</b>\n\n"
                f"Threshold: <b>{new_threshold*100:.0f}%</b>\n"
                f"Trades with confidence \u2265 {new_threshold*100:.0f}% will auto-execute.\n"
                f"Lower confidence trades still require manual confirmation.")
        except ValueError:
            await self._send(update,
                "\u274c Invalid value. Use a number (0.50-1.00) or 'off'.\n"
                "Example: <code>/autoconfirm 0.75</code>")

    async def _cmd_forcescan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/forcescan — force immediate scan bypassing cooldown and pending gates."""
        if not await self._guard(update, "admin"):
            return

        await self._send(update,
            "\U0001f50d <b>Force scan starting...</b>\n"
            "Clearing pending ideas, bypassing cooldown.")

        try:
            result = await self.engine.force_scan()
        except Exception as exc:
            await self._send(update,
                f"\u274c <b>Force scan failed:</b> {exc}")
            return

        if result.get("error"):
            await self._send(update,
                f"\u274c <b>Scan error:</b> {result['error']}")
            return

        lines = [
            "\u2705 <b>Force Scan Complete</b>",
            "",
            f"\U0001f4e1 Signals found: <b>{result.get('signals', 0)}</b>",
            f"\U0001f4a1 Ideas generated: <b>{result.get('ideas', 0)}</b>",
            f"\U0001f916 Auto-confirmed: <b>{result.get('auto_confirmed', 0)}</b>",
            f"\u23f3 Pending confirmation: <b>{result.get('pending', 0)}</b>",
        ]
        if result.get('cleared_pending', 0) > 0:
            lines.append(f"\U0001f9f9 Cleared old pending: <b>{result['cleared_pending']}</b>")

        await self._send(update, "\n".join(lines))

    async def _cmd_session(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/session — show current trading session and its risk adjustments."""
        try:
            from bot.core.session_aware import get_current_session
            session = get_current_session()
        except Exception as exc:
            await self._send(update, f"\u274c Session check failed: {exc}")
            return

        # Session name styling
        session_icons = {
            "asian": "\U0001f30f",
            "london": "\U0001f1ec\U0001f1e7",
            "london_ny_overlap": "\U0001f525",
            "new_york": "\U0001f1fa\U0001f1f8",
            "late_ny": "\U0001f319",
        }
        icon = session_icons.get(session.session_name, "\U0001f554")

        lines = [
            f"{icon} <b>Current Session: {session.session_name.replace('_', ' ').title()}</b>",
            "",
            f"\U0001f4ca {session.description}",
            "",
            f"Position size: <b>{session.size_multiplier:.0%}</b> of normal",
            f"SL width: <b>{session.sl_width_multiplier:.0%}</b> of normal",
            f"Confidence adj: <b>{session.confidence_adjustment:+.1%}</b>",
            f"Peak liquidity: <b>{'Yes' if session.is_peak_liquidity else 'No'}</b>",
        ]
        if session.is_weekend_risk:
            lines.extend([
                "",
                "\u26a0\ufe0f <b>WEEKEND RISK ACTIVE</b>",
                "Position sizes reduced, SL widened for gap protection.",
            ])

        await self._send(update, "\n".join(lines))

    async def _cmd_montecarlo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Run Monte Carlo risk simulation on trade history."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id

        try:
            from bot.core.monte_carlo import run_monte_carlo
            trades = self.engine.portfolio._history
            closed_pnls = [t.pnl for t in trades if t.closed_at is not None]

            if len(closed_pnls) < 5:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\u26a0\ufe0f Need at least 5 closed trades for Monte Carlo simulation.",
                )
                return

            equity = self.engine.portfolio.balance
            result = run_monte_carlo(closed_pnls, starting_equity=equity, num_simulations=5000)

            if result is None:
                await context.bot.send_message(chat_id=chat_id, text="\u274c Monte Carlo simulation failed.")
                return

            lines = [
                "\U0001f3b2 <b>Monte Carlo Risk Simulation</b>",
                "\u2500" * 28,
                "",
                f"\U0001f4ca <b>{result.num_simulations:,} simulations</b> on <b>{result.num_trades}</b> trades",
                "",
                "<b>Max Drawdown Distribution:</b>",
                f"  50th: <code>{result.dd_50th:.1f}%</code>",
                f"  75th: <code>{result.dd_75th:.1f}%</code>",
                f"  90th: <code>{result.dd_90th:.1f}%</code>",
                f"  95th: <code>{result.dd_95th:.1f}%</code> \u2190 key metric",
                f"  99th: <code>{result.dd_99th:.1f}%</code>",
                "",
                "<b>Return Distribution:</b>",
                f"  Worst 5%:  <code>{result.return_5th:+.1f}%</code>",
                f"  Median:    <code>{result.return_median:+.1f}%</code>",
                f"  Best 5%:   <code>{result.return_95th:+.1f}%</code>",
                "",
                f"\U0001f480 Probability of ruin: <code>{result.probability_of_ruin:.1%}</code>",
                f"\u26a0\ufe0f Risk rating: <b>{result.risk_rating}</b>",
            ]
            if result.recommended_size_mult < 1.0:
                lines.append(f"\U0001f4c9 Suggested size reduction: <b>{result.recommended_size_mult:.0%}</b>")
            else:
                lines.append("\u2705 Current sizing is within acceptable risk bounds")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Monte Carlo Engine"])

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"\u274c Monte Carlo error: {exc}")

    async def _cmd_attribution(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show trade signal attribution — which indicators contribute to wins."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id

        try:
            from bot.core.metrics import MetricsEngine
            trades = self.engine.portfolio._history
            _me = MetricsEngine()
            attribution = _me.compute_attribution(trades)

            if not attribution:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\u26a0\ufe0f No signal attribution data yet. Need closed trades with signal tracking.",
                )
                return

            lines = [
                "\U0001f4ca <b>Signal Attribution Report</b>",
                "\u2500" * 28,
                "",
            ]

            # Sort by edge score
            sorted_signals = sorted(attribution.items(), key=lambda x: x[1].get("edge_score", 0), reverse=True)

            for name, stats in sorted_signals[:15]:
                wr = stats.get("win_rate", 0) * 100
                total = stats.get("total", 0)
                avg = stats.get("avg_pnl", 0)
                edge = stats.get("edge_score", 0)
                emoji = "\u2705" if wr >= 55 else "\u26a0\ufe0f" if wr >= 45 else "\u274c"
                lines.append(f"{emoji} <b>{name}</b>: {wr:.0f}% WR ({total} trades) avg=${avg:.2f} edge={edge:.1f}")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Attribution Engine"])

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"\u274c Attribution error: {exc}")

    async def _cmd_equitycurve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show equity curve circuit breaker status."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id

        try:
            risk = self.engine.risk
            eq_mult = risk.equity_curve_size_multiplier
            in_recovery = risk.in_drawdown_recovery

            if eq_mult <= 0:
                status = "\U0001f6d1 PAUSED — equity below 2\u03c3 of MA"
                status_emoji = "\U0001f6d1"
            elif eq_mult < 1.0:
                status = f"\u26a0\ufe0f HALVED — equity below MA (sizing at {eq_mult:.0%})"
                status_emoji = "\u26a0\ufe0f"
            else:
                status = "\u2705 HEALTHY — equity above MA"
                status_emoji = "\u2705"

            lines = [
                "\U0001f4c8 <b>Equity Curve Health</b>",
                "\u2500" * 28,
                "",
                f"Status: {status}",
                f"Size multiplier: <code>{eq_mult:.0%}</code>",
                f"Equity snapshots: <code>{len(risk._equity_history)}</code>",
                f"MA period: <code>{CONFIG.risk.equity_curve_ma_period}</code>",
                "",
            ]
            _dr_str = "<b>ACTIVE</b> ⚠️" if in_recovery else "Inactive ✅"
            lines.append(f"Drawdown recovery: {_dr_str}")

            if in_recovery:
                lines.append(f"  Min confidence: <code>{CONFIG.risk.drawdown_recovery_conf_min}</code>")
                lines.append(f"  Size multiplier: <code>{CONFIG.risk.drawdown_recovery_size_mult:.0%}</code>")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Risk Management"])

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"\u274c Error: {exc}")

    async def _cmd_crossasset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show cross-asset correlation context."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            ctx = self.engine.cross_asset.get_context(force=True)
            lines = [
                "\U0001f310 <b>Cross-Asset Context</b>",
                "\u2500" * 28,
                "",
                f"BTC Dominance: <b>{ctx.btc_dominance_trend}</b> ({ctx.btc_dominance_change_1h:+.2f}%)",
                f"ETH/BTC: <b>{ctx.eth_btc_trend}</b> (ratio: {ctx.eth_btc_ratio:.6f})",
                f"Alt-BTC Correlation: <code>{ctx.alt_correlation:.2f}</code>",
                f"Market Regime: <b>{ctx.market_regime.upper()}</b>",
                "",
                f"Confidence adj: <code>{ctx.confidence_adjustment:+.3f}</code>",
                f"Size multiplier: <code>{ctx.size_multiplier:.0%}</code>",
                "",
                f"\U0001f4dd {ctx.description}",
                "",
                "\u2500" * 28,
                "\U0001f43e RUNECLAW Cross-Asset Engine",
            ]
            await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"\u274c Error: {exc}")

    async def _cmd_slippage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show slippage statistics."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            all_stats = self.engine.slippage.get_all_stats()
            if not all_stats:
                await context.bot.send_message(chat_id=chat_id, text="\u26a0\ufe0f No slippage data recorded yet.")
                return

            lines = [
                "\U0001f4ca <b>Slippage Report</b>",
                "\u2500" * 28,
                "",
            ]

            total_lost = 0
            for symbol, stats in sorted(all_stats.items(), key=lambda x: x[1].total_slippage_usd, reverse=True)[:10]:
                lines.append(
                    f"<b>{symbol}</b>: mean={stats.mean_slippage_pct:.3f}% "
                    f"p95={stats.p95_slippage_pct:.3f}% "
                    f"({stats.total_trades} fills, ${stats.total_slippage_usd:.2f} lost)"
                )
                total_lost += stats.total_slippage_usd

            lines.extend([
                "",
                f"\U0001f4b8 Total slippage cost: <b>${total_lost:.2f}</b>",
                "",
                "\u2500" * 28,
                "\U0001f43e RUNECLAW Execution Quality",
            ])

            await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"\u274c Error: {exc}")

    async def _cmd_sweep(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show liquidity sweep detection for a symbol."""
        args = context.args if context.args else []
        symbol = args[0].upper() + "/USDT" if args else "BTC/USDT"

        try:
            exchange = await self.engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=100)
            if not ohlcv or len(ohlcv) < 20:
                await update.message.reply_text(f"Not enough data for {symbol}")
                return

            import numpy as np
            opens = np.array([c[1] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            closes = np.array([c[4] for c in ohlcv])
            volumes = np.array([c[5] for c in ohlcv])

            from bot.core.liquidity_sweep import detect_sweeps
            signals = detect_sweeps(opens, highs, lows, closes, volumes)

            if not signals:
                await update.message.reply_text(f"No liquidity sweeps detected for {symbol}")
                return

            lines = [f"LIQUIDITY SWEEPS -- {symbol}", ""]
            for s in signals[:5]:
                emoji = "UP" if "bullish" in s.sweep_type else "DOWN"
                depth_pct = f"{s.depth_pct:.2f}"
                rev_str = f"{s.reversal_strength:.0%}"
                vol_str = f"{s.volume_ratio:.1f}"
                conf_str = f"{s.confidence:.0%}"
                lines.append(
                    f"[{emoji}] {s.sweep_type.upper()}\n"
                    f"  Level: ${s.level_price:,.4f}\n"
                    f"  Depth: {depth_pct}%  Rev: {rev_str}\n"
                    f"  Vol: {vol_str}x  Conf: {conf_str}\n"
                    f"  Entry: ${s.suggested_entry:,.4f}  SL: ${s.suggested_sl:,.4f}\n"
                )

            await update.message.reply_text("\n".join(lines))
        except Exception as exc:
            await update.message.reply_text(f"Sweep scan error: {exc}")

    async def _cmd_zones(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show supply/demand zones for a symbol."""
        args = context.args if context.args else []
        symbol = args[0].upper() + "/USDT" if args else "BTC/USDT"

        try:
            exchange = await self.engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=200)
            if not ohlcv or len(ohlcv) < 20:
                await update.message.reply_text(f"Not enough data for {symbol}")
                return

            import numpy as np
            opens = np.array([c[1] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            closes = np.array([c[4] for c in ohlcv])
            volumes = np.array([c[5] for c in ohlcv])

            # Compute ATR
            tr = np.maximum(highs[1:] - lows[1:], np.maximum(
                np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))

            from bot.core.supply_demand import detect_zones
            zones = detect_zones(opens, highs, lows, closes, volumes, atr=atr)

            if not zones:
                await update.message.reply_text(f"No active S/D zones for {symbol}")
                return

            lines = [f"SUPPLY/DEMAND ZONES -- {symbol}", ""]
            price = float(closes[-1])
            for z in zones[:8]:
                tag = "DEMAND" if z.zone_type == "demand" else "SUPPLY"
                fresh_label = " [FRESH]" if z.status == "fresh" else f" [{z.status}, {z.retests}x]"
                dist = abs(price - z.midpoint) / price * 100
                lines.append(
                    f"[{tag}]{fresh_label}\n"
                    f"  Range: ${z.zone_low:,.4f} - ${z.zone_high:,.4f}\n"
                    f"  Strength: {z.strength:.0%}  Dist: {dist:.1f}%\n"
                    f"  Departure: {z.departure_pct:.1f}%  Vol: {z.volume_ratio:.1f}x\n"
                )

            await update.message.reply_text("\n".join(lines))
        except Exception as exc:
            await update.message.reply_text(f"Zone scan error: {exc}")

    async def _cmd_squeeze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show volatility squeeze status for a symbol."""
        args = context.args if context.args else []
        symbol = args[0].upper() + "/USDT" if args else "BTC/USDT"

        try:
            exchange = await self.engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=200)
            if not ohlcv or len(ohlcv) < 30:
                await update.message.reply_text(f"Not enough data for {symbol}")
                return

            import numpy as np
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            closes = np.array([c[4] for c in ohlcv])

            from bot.core.smart_exits import detect_squeeze
            sig = detect_squeeze(closes, highs, lows)

            if sig is None:
                await update.message.reply_text(f"Cannot compute squeeze for {symbol}")
                return

            if sig.squeeze_fired:
                status = "SQUEEZE FIRED!"
            elif sig.is_squeezing:
                status = f"SQUEEZING ({sig.squeeze_bars} bars)"
            else:
                status = "No squeeze"
            direction = sig.fire_direction.upper() if sig.squeeze_fired else ""

            lines = [
                f"VOLATILITY SQUEEZE -- {symbol}",
                "",
                f"Status: {status} {direction}",
                f"BB Width: {sig.bb_width_pct:.2f}% (P{sig.bb_width_percentile:.0f})",
                f"Momentum: {sig.momentum:+.2f}%",
                f"Confidence: {sig.confidence:.0%}",
                "",
                sig.description,
            ]

            await update.message.reply_text("\n".join(lines))
        except Exception as exc:
            await update.message.reply_text(f"Squeeze error: {exc}")

    async def _cmd_holdtime(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show hold-time analytics by strategy type."""
        try:
            text = self.engine.hold_analytics.summary()
            await update.message.reply_text(text)
        except Exception as exc:
            await update.message.reply_text(f"Hold-time analysis error: {exc}")

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
                f"\u2022 Default {CONFIG.exchange.default_leverage}x leverage\n\n"
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
            f"{CONFIG.exchange.default_leverage}x leverage.\n\n"
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
            # Filter out adopted/injected trades for consistency with Performance view
            user_closed = [t for t in closed_pos
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
            adopted_closed = [t for t in closed_pos
                              if any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
            realized_pnl = sum(p.pnl_usd or 0 for p in user_closed)
            total_fees = sum(p.commission or 0 for p in user_closed)
            adopted_pnl = sum(p.pnl_usd or 0 for p in adopted_closed)
            exposure = executor.total_exposure_usd

            # PnL sign
            pnl_sign = "+" if realized_pnl >= 0 else ""
            pnl_icon = "\u26aa" if realized_pnl == 0 else ("\U0001f7e2" if realized_pnl > 0 else "\U0001f534")

            # "Used" from exchange only counts filled positions in cross margin.
            # Show the higher of exchange-reported or bot-tracked exposure for accuracy.
            used_display = max(used, exposure)

            # Header
            SEP = "─" * 16
            lines = [
                f"💰 <b>BITGET PORTFOLIO</b>",
                f"{SEP}",
                f"   {pnl_icon}  Net PnL: <code>${pnl_sign}{realized_pnl:.2f}</code> (fees: ${total_fees:.2f})",
                "",
                "💳 <b>Balance</b>",
                f"{SEP}",
                f"- Cash: <code>${free:,.2f}</code>",
                f"- Used: <code>${used_display:,.2f}</code>",
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

            # Footer — use filtered trade count (consistent with Performance)
            n_trades = len(user_closed)
            n_open = len(open_pos)
            trade_word = "trade" if n_trades == 1 else "trades"
            pos_word = f"{n_open} open" if n_open > 0 else "no open positions"
            lines.append("")
            lines.append(f"<i>{n_trades} {trade_word} • {pos_word}</i>")
            if adopted_closed:
                lines.append(
                    f"<i>⚠️ Excluded {len(adopted_closed)} adopted orphan"
                    f"{'s' if len(adopted_closed) != 1 else ''}"
                    f" ({'+' if adopted_pnl >= 0 else ''}{adopted_pnl:.2f})</i>"
                )

            await self._send(update, "\n".join(lines))
        except Exception as exc:
            await self._send(update, f"\u274c Balance fetch failed: {exc}")

    async def _cmd_livepositions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livepositions — show live positions and pending orders separately."""
        if not await self._guard(update, "portfolio"):
            return
        positions = self.engine.live_executor._positions
        filled_pos = [p for p in positions.values() if p.status == "open"]
        pending_pos = [p for p in positions.values() if p.status == "pending_fill"]

        # Fetch current prices for all relevant symbols
        current_prices: dict = {}
        all_pos = filled_pos + pending_pos
        if all_pos:
            try:
                exchange = await self.engine.live_executor._get_exchange()
                for p in all_pos:
                    if p.symbol not in current_prices:
                        try:
                            tk = await exchange.fetch_ticker(p.symbol)
                            current_prices[p.symbol] = float(tk.get("last") or 0)
                        except Exception:
                            current_prices[p.symbol] = 0
            except Exception:
                pass

        # Fallback: check exchange directly if no local positions at all
        if not filled_pos and not pending_pos:
            try:
                exchange = await self.engine.live_executor._get_exchange()
                ex_positions = await exchange.fetch_positions(
                    params={"productType": "USDT-FUTURES"})
                ex_open = [p for p in (ex_positions or [])
                           if isinstance(p, dict) and float(p.get("contracts") or 0) > 0]
                if ex_open:
                    SEP = "\u2500" * 16
                    lines = [f"\U0001f4ca <b>LIVE POSITIONS</b> (from exchange)\n{SEP}\n"]
                    for p in ex_open:
                        sym = p.get("symbol", "???")
                        side = (p.get("side") or "long").upper()
                        dir_icon = "\U0001f7e2" if side == "LONG" else "\U0001f534"
                        contracts = float(p.get("contracts") or 0)
                        entry = float(p.get("entryPrice") or 0)
                        mark = float(p.get("markPrice") or 0)
                        upnl = float(p.get("unrealizedPnl") or 0)
                        lev = int(float(p.get("leverage") or 1))
                        sym_display = sym.replace("/", "").replace(":USDT", "")
                        lines.append(
                            f"{dir_icon} <b>{side} {sym_display}</b> {lev}x\n"
                            f"- Entry: <code>${entry:,.4f}</code>\n"
                            f"- Mark: <code>${mark:,.4f}</code>\n"
                            f"- Qty: <code>{contracts:.6f}</code>\n"
                            f"- uPnL: <code>${upnl:+,.2f}</code>\n"
                        )
                    lines.append(f"\n<i>\u26a0\ufe0f Showing exchange data \u2014 local tracking out of sync</i>")
                    await self._send(update, "\n".join(lines))
                    return
            except Exception:
                pass

        if not filled_pos and not pending_pos:
            await self._send(update, "\U0001f4ad No live positions or pending orders.")
            return

        SEP = "\u2500" * 16
        lines: list = []

        # ── Section 1: Active (filled) positions ──
        if filled_pos:
            lines.append(f"\U0001f4c8 <b>ACTIVE POSITIONS ({len(filled_pos)})</b>\n{SEP}\n")
            for p in filled_pos:
                dir_icon = "\U0001f7e2" if p.direction == "LONG" else "\U0001f534"
                sym_display = p.symbol.replace("/", "").replace(":USDT", "")
                sl_str = f"${p.stop_loss:,.4f}" if p.stop_loss > 0 else "\u26a0\ufe0f NOT SET"
                tp_str = f"${p.take_profit:,.4f}" if p.take_profit > 0 else "\u26a0\ufe0f NOT SET"
                lev = getattr(p, 'leverage', 10)
                cost = getattr(p, 'cost_usd', 0) or 0

                # Calculate uPnL
                cur = current_prices.get(p.symbol, 0)
                upnl_str = ""
                pnl_pct_str = ""
                if cur > 0 and p.entry_price > 0:
                    if p.direction == "LONG":
                        upnl = (cur - p.entry_price) / p.entry_price * cost
                        pnl_pct = (cur - p.entry_price) / p.entry_price * 100 * lev
                    else:
                        upnl = (p.entry_price - cur) / p.entry_price * cost
                        pnl_pct = (p.entry_price - cur) / p.entry_price * 100 * lev
                    sign = "+" if upnl >= 0 else ""
                    upnl_str = f"- uPnL: <code>{sign}${upnl:,.2f}</code> ({sign}{pnl_pct:.1f}%)\n"

                cur_str = f"- Current: <code>${cur:,.4f}</code>\n" if cur > 0 else ""

                lines.append(
                    f"{dir_icon} <b>{p.direction} {sym_display}</b> {lev}x\n"
                    f"- Entry: <code>${p.entry_price:,.4f}</code>\n"
                    f"{cur_str}"
                    f"- Size: <code>${cost:,.2f}</code> | Qty: <code>{p.quantity:.6f}</code>\n"
                    f"- SL: <code>{sl_str}</code>\n"
                    f"- TP: <code>{tp_str}</code>\n"
                    f"{upnl_str}"
                    f"- ID: <code>{p.trade_id}</code>\n"
                )

        # ── Section 2: Pending limit orders ──
        if pending_pos:
            if filled_pos:
                lines.append("")  # spacer
            lines.append(f"\u23f3 <b>PENDING ORDERS ({len(pending_pos)})</b>\n{SEP}\n")
            for p in pending_pos:
                dir_icon = "\U0001f7e2" if p.direction == "LONG" else "\U0001f534"
                sym_display = p.symbol.replace("/", "").replace(":USDT", "")
                sl_str = f"${p.stop_loss:,.4f}" if p.stop_loss > 0 else "\u26a0\ufe0f NOT SET"
                tp_str = f"${p.take_profit:,.4f}" if p.take_profit > 0 else "\u26a0\ufe0f NOT SET"
                lev = getattr(p, 'leverage', 10)
                cost = getattr(p, 'cost_usd', 0) or 0

                # Distance to fill
                cur = current_prices.get(p.symbol, 0)
                dist_str = ""
                if cur > 0 and p.entry_price > 0:
                    dist_pct = abs(cur - p.entry_price) / p.entry_price * 100
                    dist_str = f" ({dist_pct:+.2f}% away)"

                # Time waiting
                age_str = ""
                if hasattr(p, 'opened_at') and p.opened_at:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    delta = now - p.opened_at
                    mins = int(delta.total_seconds() // 60)
                    if mins < 60:
                        age_str = f"- Placed: <code>{mins}m ago</code>\n"
                    else:
                        hrs = mins // 60
                        age_str = f"- Placed: <code>{hrs}h {mins % 60}m ago</code>\n"

                cur_line = f"- Current: <code>${cur:,.4f}</code>{dist_str}\n" if cur > 0 else ""

                lines.append(
                    f"{dir_icon} <b>{p.direction} {sym_display}</b> \u2014 Limit Order\n"
                    f"- Limit: <code>${p.entry_price:,.4f}</code>\n"
                    f"{cur_line}"
                    f"- Size: <code>${cost:,.2f}</code> | Lev: {lev}x\n"
                    f"- SL: <code>{sl_str}</code>\n"
                    f"- TP: <code>{tp_str}</code>\n"
                    f"{age_str}"
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
        """/buy — DISABLED (futures-only mode)."""
        if not await self._guard(update, "admin"):
            return
        await self._send(update,
            "\u274c <b>Spot trading is disabled</b>\n\n"
            "RUNECLAW operates in <b>futures-only mode</b> (USDT-M perpetuals at 5x leverage).\n\n"
            "The bot automatically opens positions via AI analysis. "
            "Use <code>/livepositions</code> to view open positions.")

    async def _cmd_sell(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/sell — DISABLED (futures-only mode)."""
        if not await self._guard(update, "admin"):
            return
        await self._send(update,
            "\u274c <b>Spot trading is disabled</b>\n\n"
            "RUNECLAW operates in <b>futures-only mode</b> (USDT-M perpetuals at 5x leverage).\n\n"
            "Use <code>/liveclose TRADE_ID</code> to close a futures position.")

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

        # Signal card image renderer — sends a styled PNG card for each signal
        _bot_ref = bot
        async def _signal_card_fn(chat_id: str, idea, rank: int = 1,
                                  scan_data: dict = None) -> None:
            try:
                from bot.formatters.signal_card import signal_card_from_idea
                png = signal_card_from_idea(idea, rank=rank, scan_data=scan_data or {})
                if png:
                    import io as _io
                    buf = _io.BytesIO(png)
                    buf.name = "signal.png"
                    uid = CONFIG.telegram.chat_id or chat_id
                    # Build confirm/reject buttons on the card image
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("Take it",
                            callback_data=f"confirm:{idea.id}:{uid}"),
                        InlineKeyboardButton("Limit",
                            callback_data=f"setlimit:{idea.id}:{uid}"),
                        InlineKeyboardButton("Skip",
                            callback_data=f"reject:{idea.id}:{uid}"),
                    ]])
                    pair = idea.asset.replace("/USDT", "")
                    direction = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
                    st = getattr(idea, 'strategy_type', '').upper()
                    st_str = f" [{st}]" if st else ""
                    cap = f"<b>{pair} {direction}</b>{st_str} | Conf {idea.confidence*100:.0f}%"
                    await _bot_ref.send_photo(
                        chat_id=int(chat_id), photo=buf,
                        caption=cap, parse_mode="HTML",
                        reply_markup=kb)
            except Exception as exc:
                system_log.debug("Signal card send failed: %s", exc)

        self._signal_card_fn = _signal_card_fn

        # Hook: forward new signals to marketing channels + send signal card
        _forwarder = self.forwarder
        _original_dispatch = self.monitor._dispatch

        async def _dispatch_with_forward(alert, send_fn):
            await _original_dispatch(alert, send_fn)
            # Send signal card image for trade signals
            if alert.alert_type == "TRADE_SIGNAL" and alert.idea is not None:
                for cid in list(self.monitor._enabled_chats):
                    try:
                        await _signal_card_fn(cid, alert.idea, rank=1)
                    except Exception:
                        pass
                # Forward to marketing channels
                try:
                    await _forwarder.post_signal(alert.idea)
                except Exception:
                    pass

        self.monitor._dispatch = _dispatch_with_forward
        self._monitor_task = asyncio.create_task(self.monitor.run(_send_fn))

        # Register trade-close notification callback
        admin_chat_id = CONFIG.telegram.chat_id
        # Parse comma-separated admin chat IDs into list of ints
        _notify_chat_ids: list[int] = []
        if admin_chat_id:
            for cid in admin_chat_id.split(","):
                cid = cid.strip()
                if cid.isdigit():
                    _notify_chat_ids.append(int(cid))
        async def _on_trade_closed(msg: str) -> None:
            """Send a rich close confirmation to admin when a trade is closed."""
            if not _notify_chat_ids:
                return
            try:
                # Try to render a styled PNG close card
                close_data = getattr(self.engine.live_executor, '_last_close_data', None)
                close_png = None
                if close_data:
                    try:
                        from bot.formatters.signal_card import render_close_card
                        close_png = render_close_card(close_data)
                    except Exception as exc:
                        system_log.debug("Close card render failed: %s", exc)

                if close_png:
                    # Send as photo with brief caption
                    sym = close_data.get("symbol", "").replace("/", "").replace(":USDT", "")
                    direction = close_data.get("direction", "")
                    pnl_usd = close_data.get("pnl_usd", 0)
                    reason = close_data.get("reason", "closed")
                    # Show specific close reason with appropriate emoji
                    if "TP" in reason.upper():
                        pnl_emoji = "\U0001f3af"  # target emoji for TP
                        reason_short = "TP HIT"
                    elif "SL" in reason.upper() or "STOP" in reason.upper():
                        pnl_emoji = "\U0001f6d1"  # stop sign for SL
                        reason_short = "SL HIT"
                    elif "TRAILING" in reason.upper():
                        pnl_emoji = "\U0001f6d1"
                        reason_short = "TRAILING SL"
                    elif "TIME" in reason.upper():
                        pnl_emoji = "\u23f0"  # alarm clock for time stop
                        reason_short = "TIME STOP"
                    elif pnl_usd >= 0:
                        pnl_emoji = "\u2705"
                        reason_short = reason
                    else:
                        pnl_emoji = "\u274c"
                        reason_short = reason
                    cap = (f"{pnl_emoji} <b>{html.escape(sym)}</b> {direction} CLOSED\n"
                           f"PnL: ${pnl_usd:+,.2f} | {html.escape(reason_short)}")
                    for _cid in _notify_chat_ids:
                        try:
                            await bot.send_photo(
                                chat_id=_cid,
                                photo=close_png,
                                caption=cap,
                                parse_mode="HTML")
                        except Exception:
                            pass
                else:
                    # Fallback to text — use reason-specific heading
                    reason = close_data.get("reason", "") if close_data else ""
                    is_win = "+$" in msg
                    if "TP" in reason.upper():
                        emoji = "\U0001f3af"  # 🎯
                        heading = "TP HIT"
                    elif "SL" in reason.upper() or "STOP" in reason.upper():
                        emoji = "\U0001f6d1"  # 🛑
                        heading = "SL HIT"
                    elif "TRAILING" in reason.upper():
                        emoji = "\U0001f6d1"
                        heading = "TRAILING SL"
                    elif "TIME" in reason.upper():
                        emoji = "\u23f0"  # ⏰
                        heading = "TIME STOP"
                    elif is_win:
                        emoji = "\u2705"
                        heading = "Trade Closed"
                    else:
                        emoji = "\u274c"
                        heading = "Trade Closed"
                    sym = close_data.get("symbol", "") if close_data else ""
                    direction = close_data.get("direction", "") if close_data else ""
                    if sym and direction:
                        card = f"{emoji} <b>{html.escape(sym)}</b> {direction} {heading}\n\n"
                    else:
                        card = f"{emoji} <b>{heading}</b>\n\n"
                    for line in msg.strip().split("\n"):
                        card += f"{html.escape(line)}\n"
                    for _cid in _notify_chat_ids:
                        try:
                            await bot.send_message(
                                chat_id=_cid, text=card.strip(),
                                parse_mode="HTML")
                        except Exception:
                            pass
            except Exception as exc:
                system_log.debug("Close notify send failed: %s", exc)

            # Forward trade close to marketing channels
            try:
                await _forwarder.post_trade_closed(msg)
            except Exception:
                pass

        self.engine.set_close_notify_callback(_on_trade_closed)

        # Register limit-fill notification callback
        async def _on_limit_filled(msg: str) -> None:
            """Send a notification when a limit order is filled (position opened)."""
            if not _notify_chat_ids:
                return
            try:
                from datetime import datetime as _dt, timezone as _tz
                card = "\U0001f4e5 <b>TRADE OPENED</b>\n"
                card += "\u2500" * 28 + "\n\n"
                for line in msg.strip().split("\n"):
                    card += f"{html.escape(line)}\n"
                card += "\n" + "\u2500" * 28
                card += f"\n\U0001f43e RUNECLAW | {_dt.now(_tz.utc).strftime('%H:%M')} UTC"
                card += "\n<a href='#'>#RUNECLAW #LimitFill</a>"
                for _cid in _notify_chat_ids:
                    try:
                        await bot.send_message(
                            chat_id=_cid, text=card.strip(),
                            parse_mode="HTML")
                    except Exception:
                        pass
            except Exception as exc:
                system_log.debug("Fill notify send failed: %s", exc)

        self.engine.set_fill_notify_callback(_on_limit_filled)

        # ── Adoption notification ─────────────────────────────────
        async def _on_positions_adopted(adopted_symbols: list[str]) -> None:
            """Notify admin when exchange positions are adopted on startup."""
            try:
                lines = [
                    "\u26a0\ufe0f <b>Adopted Exchange Positions</b>",
                    "",
                    f"Found <b>{len(adopted_symbols)}</b> position(s) on the exchange",
                    "that were not tracked locally:",
                    "",
                ]
                for sym in adopted_symbols:
                    lines.append(f"  \u2022 <code>{html.escape(sym)}</code>")
                lines.extend([
                    "",
                    "These may have been opened in a previous session",
                    "or directly on the exchange.",
                    "",
                    "Use <b>Positions</b> to review. Close any you didn't intend.",
                    "<i>SL/TP may not be set \u2014 check and add manually.</i>",
                ])
                admin_chat_id = os.environ.get("ADMIN_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
                if admin_chat_id:
                    for _cid_str in admin_chat_id.split(","):
                        _cid_str = _cid_str.strip()
                        if _cid_str.isdigit():
                            try:
                                await bot.send_message(
                                    chat_id=int(_cid_str),
                                    text="\n".join(lines),
                                    parse_mode="HTML")
                            except Exception:
                                pass
            except Exception as exc:
                system_log.debug("Adopt notify send failed: %s", exc)

        self.engine.set_adopt_notify_callback(_on_positions_adopted)

        # ── Auto-confirm notification ──────────────────────────────
        async def _on_auto_confirmed(idea, result_msg: str) -> None:
            """Notify admin when a trade is auto-confirmed (high confidence)."""
            try:
                pair = idea.asset.replace("/USDT", "")
                direction = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
                conf = idea.confidence * 100
                from datetime import datetime as _dt, timezone as _tz
                card_lines = [
                    "\U0001f916 <b>AUTO-CONFIRMED TRADE</b>",
                    "\u2500" * 28,
                    "",
                    f"\U0001f4b0 <b>{pair}</b> {direction} | Conf <b>{conf:.0f}%</b>",
                    f"Entry: <code>${idea.entry_price:,.4f}</code>",
                    f"SL: <code>${idea.stop_loss:,.4f}</code> | TP: <code>${idea.take_profit:,.4f}</code>",
                    "",
                ]
                # Add result preview
                first_line = result_msg.strip().split("\n")[0] if result_msg else ""
                if first_line:
                    card_lines.append(f"\u2192 {html.escape(first_line)}")
                card_lines.extend([
                    "",
                    "\u2500" * 28,
                    f"\U0001f43e RUNECLAW | {_dt.now(_tz.utc).strftime('%H:%M')} UTC",
                    "<i>Confidence exceeded auto-confirm threshold</i>",
                ])
                a_chat = os.environ.get("ADMIN_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
                if a_chat:
                    for _cid_str in a_chat.split(","):
                        _cid_str = _cid_str.strip()
                        if _cid_str.isdigit():
                            try:
                                await bot.send_message(
                                    chat_id=int(_cid_str),
                                    text="\n".join(card_lines),
                                    parse_mode="HTML")
                            except Exception:
                                pass
            except Exception as exc:
                system_log.debug("Auto-confirm notify send failed: %s", exc)

        self.engine.set_auto_confirm_notify_callback(_on_auto_confirmed)

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
        # Audit F-12: swapping the analysis LLM / injecting a key affects every
        # trade decision — restrict to admins, not the broad `mode` permission.
        if not self._is_admin(update):
            await self._send(update,
                "\U0001f512 <b>Admin only</b>\n\n"
                "Changing the LLM provider/key is restricted to admins.")
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

        # Warn about key exposure
        await self._send(update,
            "⚠️ <b>Security warning:</b> API keys should only be set in private chats with the bot. "
            "Your message containing the key will be deleted.")

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
        else:
            await self._send(update,
                f"🔴 <b>LLM UPDATE FAILED</b>\n\n"
                f"{html.escape(msg)}")

        # Always try to delete the original message containing the API key
        try:
            await update.message.delete()
        except Exception as del_exc:
            system_log.warning(
                "Failed to delete /setllm message containing API key: %s — "
                "key may be visible in chat history", del_exc)

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
        # Audit F-12: admin-only, mirroring /setllm.
        if not self._is_admin(update):
            await self._send(update,
                "\U0001f512 <b>Admin only</b>\n\n"
                "Resetting the LLM provider/key is restricted to admins.")
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
        # L-14 FIX: key by user_id instead of chat_id to avoid cross-user pane leaks
        user_id = self._get_tg_id(update)
        pane = self._last_pane.get(user_id, "status")
        body = await self._render_pane(pane, user_id=user_id)
        text = body + self._footer()
        await self._send(update, text, reply_markup=_KB_DASH)
        self._last_pane[user_id] = pane

    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "scan"):
            return
        user_id = self._get_tg_id(update)
        result = await self.registry.dispatch("scan_market", self.engine, user_id=user_id)
        await self._send(update, result)

    async def _cmd_analyze(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "analyze"):
            return
        args = ctx.args
        if args:
            raw = args[0].upper().strip()
            # Strip common display suffixes users might copy-paste
            # e.g. "ANTHROPICUSDT:USDT" -> "ANTHROPICUSDT" -> resolve below
            raw = raw.replace(":USDT", "")
            # SEC-H3 FIX: strict symbol validation before reaching CCXT/LLM
            if not _SYMBOL_RE.match(raw):
                await self._send(update,
                    "\U0001f534 Invalid symbol. Use format: <code>BTC</code> or <code>BTC/USDT</code>")
                return
            # Prevent self-referencing pairs like USDT/USDT
            base = raw.split("/")[0]
            if base == "USDT":
                await self._send(update,
                    "\U0001f534 Cannot analyze USDT against itself. Provide a token symbol, e.g. <code>BTC</code>")
                return
            symbol = raw if "/" in raw else f"{raw}/USDT"
        else:
            symbol = "BTC/USDT"

        ids_before = set(idea.id for idea in self.engine.pending_ideas)
        admin = self._is_admin(update)
        await self._send(update, f"\u23f3 <i>Analyzing {html.escape(symbol)}...</i>")

        try:
            result = await self.registry.dispatch("analyze_asset",
                self.engine, symbol=symbol, is_admin=admin)
        except Exception as exc:
            system_log.error("analyze_asset failed for %s: %s", symbol, exc, exc_info=True)
            await self._send(update,
                f"\U0001f534 Analysis failed for <code>{html.escape(symbol)}</code>: {html.escape(str(exc)[:200])}")
            return

        new_idea = None
        for idea in self.engine.pending_ideas:
            if idea.id not in ids_before:
                new_idea = idea
                break

        if new_idea is not None:
            uid = update.effective_user.id if update.effective_user else ""
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Take it", callback_data=f"confirm:{new_idea.id}:{uid}"),
                InlineKeyboardButton("Limit", callback_data=f"setlimit:{new_idea.id}:{uid}"),
                InlineKeyboardButton("Skip", callback_data=f"reject:{new_idea.id}:{uid}"),
            ]])
            # Send signal card image with confirm/reject buttons
            card_sent = False
            if hasattr(self, '_signal_card_fn'):
                try:
                    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                    if chat_id:
                        from bot.formatters.signal_card import signal_card_from_idea
                        png = signal_card_from_idea(new_idea, rank=1)
                        if png:
                            cap = result[:1024] if len(result) <= 1024 else result[:1020] + "..."
                            card_sent = await self._send_photo(update, png, cap, reply_markup=kb)
                except Exception as exc:
                    system_log.debug("Analyze signal card failed: %s", exc)
            if not card_sent:
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
            all_closed = executor.closed_positions

            # Exclude adopted orphan trades and injected diagnostic artifacts
            # so Portfolio matches Performance numbers
            _NON_TRADE_REASONS = {"canceled", "cancelled", "expired", "price_drift", "rejected"}
            live_closed = [t for t in all_closed
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)
                           and getattr(t, "close_reason", "") not in _NON_TRADE_REASONS]
            adopted_trades = [t for t in all_closed
                              if any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]

            # Calculate live PnL from closed positions (net of fees)
            live_total_pnl = sum((p.pnl_usd or 0) for p in live_closed)
            live_total_fees = sum((p.commission or 0) for p in live_closed)
            live_total_gross = sum((p.gross_pnl or p.pnl_usd or 0) for p in live_closed)
            live_unrealized = 0.0
            # Unrealized PnL for open positions
            for lp in live_open:
                if hasattr(lp, 'pnl_usd') and lp.pnl_usd:
                    live_unrealized += lp.pnl_usd

            # Live exposure
            live_exposure = sum(lp.cost_usd for lp in live_open)

            # Count filled vs pending for display
            _filled_count = sum(1 for lp in live_open if lp.status != "pending_fill")
            _pending_count = sum(1 for lp in live_open if lp.status == "pending_fill")
            _pos_display = f"{_filled_count}"
            if _pending_count > 0:
                _pos_display += f" + {_pending_count} pending"

            lines = [
                f"\U0001f4bc <b>YOUR PORTFOLIO</b> (LIVE)",
                sep,
                "",
                f"- Equity: <code>${display_equity:,.2f}</code>",
                f"- Open Positions: <code>{_pos_display}</code>",
                f"- Exposure: <code>${live_exposure:,.2f}</code>",
                f"- Net PnL: <code>${live_total_pnl:+,.2f}</code> {'🟢' if live_total_pnl >= 0 else '🔴'}",
                f"- Fees Paid: <code>${live_total_fees:,.2f}</code>",
            ]
            if live_unrealized != 0:
                lines.append(f"- Unrealized PnL: <code>${live_unrealized:+,.2f}</code> {'🟢' if live_unrealized >= 0 else '🔴'}")

            # Open positions from LiveExecutor
            # Separate filled positions from pending limit orders
            filled_positions = [lp for lp in live_open if lp.status != "pending_fill"]
            pending_limits = [lp for lp in live_open if lp.status == "pending_fill"]

            if filled_positions:
                lines.extend(["", sep, "", "<b>Open Positions:</b>"])
                for lp in filled_positions:
                    d_icon = "🟢" if lp.direction == "LONG" else "🔴"
                    lev_str = f" {lp.leverage}x" if (lp.leverage or 1) > 1 else ""
                    lines.append(
                        f"\n{d_icon} <b>{lp.symbol}</b> {lp.direction}{lev_str}"
                    )
                    lines.append(f"  Entry: <code>${lp.entry_price:,.6f}</code>")
                    lines.append(f"  Size: <code>${lp.cost_usd:,.2f}</code>")
                    if lp.stop_loss:
                        lines.append(f"  SL: <code>${lp.stop_loss:,.6f}</code> | TP: <code>${lp.take_profit:,.6f}</code>")

            if pending_limits:
                lines.extend(["", sep, "", "⏳ <b>Pending Limit Orders:</b>"])
                for lp in pending_limits:
                    d_icon = "🟢" if lp.direction == "LONG" else "🔴"
                    lev_str = f" {lp.leverage}x" if (lp.leverage or 1) > 1 else ""
                    pair = lp.symbol.replace("/", "").replace(":USDT", "")
                    # Calculate time since placed
                    if lp.opened_at:
                        from datetime import datetime, timezone
                        age_secs = (datetime.now(timezone.utc) - lp.opened_at).total_seconds()
                        if age_secs < 3600:
                            age_str = f"{age_secs / 60:.0f}m ago"
                        else:
                            age_str = f"{age_secs / 3600:.1f}h ago"
                    else:
                        age_str = "unknown"
                    lines.append(
                        f"\n{d_icon} <b>{pair}</b> {lp.direction}{lev_str} — LIMIT"
                    )
                    lines.append(f"  Limit: <code>${lp.entry_price:,.6f}</code> | Placed: {age_str}")
                    if lp.stop_loss:
                        lines.append(f"  SL: <code>${lp.stop_loss:,.6f}</code> | TP: <code>${lp.take_profit:,.6f}</code>")

            # Recent closed trades from LiveExecutor
            if live_closed:
                recent = live_closed[-5:]
                lines.extend(["", sep, "", "<b>Recent Trades (net of fees):</b>"])
                for t in recent:
                    pnl_val = t.pnl_usd or 0
                    fee_val = t.commission or 0
                    pnl_icon = "✅" if pnl_val >= 0 else "❌"
                    pair = t.symbol.replace("/", "").replace(":USDT", "")
                    fee_note = f" (fee ${fee_val:.2f})" if fee_val > 0 else ""
                    lines.append(f"  {pnl_icon} {pair} {t.direction} → <code>${pnl_val:+,.2f}</code>{fee_note}")

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
                if adopted_trades:
                    adopted_pnl = sum((t.pnl_usd or 0) for t in adopted_trades)
                    lines.append(
                        f"<i>⚠️ Excluded {len(adopted_trades)} adopted orphans (${adopted_pnl:+,.2f})</i>")
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
        """Manual trade placement: /trade buy SOL 71.42 sl 70.05 tp 76.42 [margin 250]"""
        if not update.message:
            return
        # Audit F-12: route through the standard guard so /trade enforces the
        # allowlist (F-2), the `trade` role permission, and the 24h session
        # staleness check \u2014 the prior inline `authorized`-only check skipped all
        # three, letting any authorized user (incl. a viewer role) queue trades.
        if not await self._guard(update, "trade"):
            return
        tg_id = self._get_tg_id(update)
        uid = str(update.effective_user.id) if update.effective_user else ""

        text = (update.message.text or "").strip()
        # Remove /trade prefix
        args = text.split(None, 1)
        if len(args) < 2:
            await self._send(update,
                "\U0001f4dd <b>Manual Trade</b>\n\n"
                "Format:\n"
                "<code>/trade buy SOL 71.42 sl 70.05 tp 76.42</code>\n"
                "<code>/trade short ETH 1721 sl 1695 tp 1842 margin 250</code>\n\n"
                "\u2022 <code>buy/long</code> = LONG\n"
                "\u2022 <code>sell/short</code> = SHORT\n"
                "\u2022 <code>margin</code> = optional fixed margin in USD")
            return

        body = args[1].strip()
        parsed = self._parse_manual_trade(body)
        if isinstance(parsed, str):
            await self._send(update, f"\u26a0\ufe0f {parsed}")
            return

        direction, symbol, entry, sl, tp, margin_usd = parsed
        pair = f"{symbol}/USDT:USDT"
        display_pair = f"{symbol}/USDT"

        # Build TradeIdea
        from bot.utils.models import TradeIdea, Direction
        try:
            idea = TradeIdea(
                asset=pair,
                direction=Direction.LONG if direction == "LONG" else Direction.SHORT,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                confidence=1.0,
                reasoning="Manual trade placed by user",
                signals_used=["manual"],
                source="manual",
                order_type="limit",
            )
        except ValueError as e:
            await self._send(update, f"\u26a0\ufe0f <b>Invalid trade:</b> {html.escape(str(e))}")
            return

        # Register as pending idea in engine
        self.engine._pending_ideas[idea.id] = idea
        # Store margin override if specified
        if margin_usd and margin_usd > 0:
            if not hasattr(self.engine, '_manual_margin_override'):
                self.engine._manual_margin_override = {}
            self.engine._manual_margin_override[idea.id] = margin_usd

        # Calculate R:R
        rr = idea.risk_reward_ratio
        sl_dist = abs(entry - sl) / entry * 100
        tp_dist = abs(tp - entry) / entry * 100

        margin_text = f"${margin_usd:,.0f}" if margin_usd else "Auto (risk-based)"

        card = (
            f"\U0001f4cb <b>Manual Trade \u2014 {html.escape(display_pair)} {direction}</b>\n"
            f"{'━' * 30}\n"
            f"Entry:  <code>${entry:,.4f}</code>\n"
            f"SL:     <code>${sl:,.4f}</code> ({sl_dist:.1f}%)\n"
            f"TP:     <code>${tp:,.4f}</code> (+{tp_dist:.1f}%)\n"
            f"R:R:    <code>{rr:.2f}</code>\n"
            f"Margin: <code>{margin_text}</code>\n"
            f"Type:   LIMIT\n"
            f"{'━' * 30}\n"
            f"<i>Reduced risk checks for manual orders</i>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2705 Confirm", callback_data=f"confirm:{idea.id}:{uid}"),
             InlineKeyboardButton("\u274c Cancel", callback_data=f"reject:{idea.id}:{uid}")],
        ])

        await self._send(update, card, reply_markup=kb)
        audit(system_log, f"Manual trade created: {idea.id} {direction} {display_pair} entry={entry} sl={sl} tp={tp}",
              action="manual_trade_created", result="PENDING")

    def _parse_manual_trade(self, text: str):
        """Parse manual trade text. Returns (direction, symbol, entry, sl, tp, margin) or error string."""
        # Normalize
        text = text.strip().upper()
        # Pattern: BUY/LONG/SHORT/SELL SYMBOL PRICE SL PRICE TP PRICE [MARGIN AMOUNT]
        pattern = re.compile(
            r'^(BUY|LONG|SHORT|SELL)\s+'
            r'([A-Z0-9]{1,15})\s+'
            r'(\$?[\d,]+\.?\d*)\s+'
            r'SL\s+(\$?[\d,]+\.?\d*)\s+'
            r'TP\s+(\$?[\d,]+\.?\d*)'
            r'(?:\s+MARGIN\s+(\$?[\d,]+\.?\d*))?',
            re.IGNORECASE
        )
        m = pattern.match(text)
        if not m:
            return ("Invalid format. Use:\n"
                    "<code>buy SOL 71.42 sl 70.05 tp 76.42</code>\n"
                    "<code>short ETH 1721 sl 1695 tp 1842 margin 250</code>")

        side = m.group(1).upper()
        direction = "LONG" if side in ("BUY", "LONG") else "SHORT"
        symbol = m.group(2).upper()

        def parse_price(s):
            return float(s.replace("$", "").replace(",", ""))

        try:
            entry = parse_price(m.group(3))
            sl = parse_price(m.group(4))
            tp = parse_price(m.group(5))
            margin = parse_price(m.group(6)) if m.group(6) else None
        except (ValueError, TypeError):
            return "Could not parse prices. Use numbers like <code>71.42</code>"

        if entry <= 0 or sl <= 0 or tp <= 0:
            return "All prices must be positive."
        if margin is not None and margin <= 0:
            return "Margin must be positive."

        # Validate direction
        if direction == "LONG":
            if sl >= entry:
                return f"LONG: SL (${sl:,.4f}) must be below entry (${entry:,.4f})"
            if tp <= entry:
                return f"LONG: TP (${tp:,.4f}) must be above entry (${entry:,.4f})"
        else:
            if sl <= entry:
                return f"SHORT: SL (${sl:,.4f}) must be above entry (${entry:,.4f})"
            if tp >= entry:
                return f"SHORT: TP (${tp:,.4f}) must be below entry (${entry:,.4f})"

        # Symbol sanity
        if not re.match(r'^[A-Z0-9]{1,15}$', symbol):
            return f"Invalid symbol: {symbol}"

        return (direction, symbol, entry, sl, tp, margin)

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
        result = await self.registry.dispatch("rejected_trades", self.engine, user_id=self._get_tg_id(update))
        await self._send(update, result)

    async def _cmd_whynot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/whynot [symbol] — explain why a trade was rejected by risk."""
        if not await self._guard(update, "rejected"):
            return
        args = ctx.args or []
        symbol = args[0].upper().strip() if args else ""
        # H-17 FIX: validate symbol format before passing to skill
        if symbol and not _SYMBOL_RE.match(symbol):
            await self._send(update, "Invalid symbol format.")
            return
        result = await self.registry.dispatch("whynot",
            self.engine, symbol=symbol)
        await self._send(update, result)

    async def _cmd_halt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "halt"):
            return
        result = await self.registry.dispatch("halt", self.engine)
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
        result = await self.registry.dispatch("macro_calendar", self.engine)
        await self._send(update, result)

    async def _cmd_backtest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "backtest"):
            return
        args = ctx.args or []
        bars = args[0] if args else "720"
        seed = args[1] if len(args) > 1 else "42"
        await self._send(update,
            f"\u23f3 <i>Backtest running  \u2022  {bars} bars  \u2022  seed {seed}</i>")
        result = await self.registry.dispatch("run_backtest",
            self.engine, bars=bars, seed=seed)
        await self._send(update, result)

    async def _cmd_walkforward(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "walkforward"):
            return
        args = ctx.args or []
        bars = args[0] if args else "1440"
        folds = args[1] if len(args) > 1 else "3"
        await self._send(update, "\u23f3 <i>Walk-forward running...</i>")
        result = await self.registry.dispatch("walk_forward",
            self.engine, bars=bars, folds=folds)
        await self._send(update, result)

    async def _cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show weekly trade journal review."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            review = self.engine.journal.get_weekly_review()

            if review.get("trades", 0) == 0:
                await ctx.bot.send_message(chat_id=chat_id, text="\u26a0\ufe0f No trades in the last 7 days.")
                return

            lines = [
                "\U0001f4d3 <b>Weekly Trade Review</b>",
                "\u2500" * 28,
                "",
                f"Period: {review['period']}",
                f"Trades: <b>{review['trades']}</b> ({review['wins']}W / {review['losses']}L)",
                f"Win Rate: <b>{review['win_rate']:.0f}%</b>",
                f"Total PnL: <b>${review['total_pnl']:+.2f}</b>",
                f"Avg R-Multiple: <code>{review['avg_r_multiple']:+.2f}</code>",
                f"Avg Hold: <code>{review['avg_holding_hours']:.1f}h</code>",
                "",
                f"\U0001f3c6 Best: {review['best_trade']['symbol']} ${review['best_trade']['pnl']:+.2f} ({review['best_trade']['r']:.1f}R)",
                f"\U0001f4a9 Worst: {review['worst_trade']['symbol']} ${review['worst_trade']['pnl']:+.2f} ({review['worst_trade']['r']:.1f}R)",
            ]

            # Top lessons
            if review.get("top_lessons"):
                lines.extend(["", "<b>Recurring Lessons:</b>"])
                for lesson, count in review["top_lessons"][:3]:
                    lines.append(f"  \u2022 {lesson} ({count}x)")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Trade Journal"])

            await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await ctx.bot.send_message(chat_id=chat_id, text=f"\u274c Error: {exc}")

    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "costs"):
            return
        result = await self.registry.dispatch("costs", self.engine, user_id=self._get_tg_id(update))
        await self._send(update, result)

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "run"):
            return
        strategy = " ".join(ctx.args) if ctx.args else ""
        if strategy:
            await self._send(update,
                f"\u23f3 <i>Running {html.escape(strategy)}...</i>")
        result = await self.registry.dispatch("run_strategy",
            self.engine, strategy=strategy)
        await self._send(update, result)

    async def _cmd_momentum(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Shortcut for /run momentum."""
        if not await self._guard(update, "run"):
            return
        await self._send(update, "\u23f3 <i>Running Momentum Hunter...</i>")
        result = await self.registry.dispatch("run_strategy",
            self.engine, strategy="momentum")
        await self._send(update, result)

    async def _cmd_dip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Shortcut for /run dip."""
        if not await self._guard(update, "run"):
            return
        await self._send(update, "\u23f3 <i>Running Dip Sniper (all symbols)...</i>")
        result = await self.registry.dispatch("run_strategy",
            self.engine, strategy="dip")
        await self._send(update, result)

    async def _cmd_scalp(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Scalp scan: 5m candles, tight SL, top-3 by volume."""
        if not await self._guard(update, "scan"):
            return
        await self._send(update, "\u26a1 <i>Scalp scan — 5M candles, tight zones...</i>")
        try:
            result = await self.registry.dispatch("pro_scan",
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
            result = await self.registry.dispatch("pro_scan",
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
            result = await self.registry.dispatch("pro_scan",
                self.engine, mode="swing", user_id=self._get_tg_id(update))
            await self._send(update, result)
        except ValueError as ve:
            # TradeIdea validation errors (SL=entry, etc.) — report but don't crash
            system_log.warning(f"Swing scan validation error: {ve}")
            await self._send(update,
                f"<b>Swing scan:</b> skipped — invalid setup generated "
                f"(SL too close to entry). Try again or use /scan.")
        except Exception as exc:
            system_log.error(f"Swing scan error: {exc}", exc_info=True)
            await self._send(update, f"\U0001f534 <b>Swing scan error:</b> <code>{html.escape(str(exc)[:200])}</code>")

    async def _cmd_playbook(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw-style full system playbook briefing."""
        if not await self._guard(update, "playbook"):
            return
        await self._send(update, "📋 <i>Assembling playbook...</i>")
        try:
            result = await self.registry.dispatch("playbook", self.engine, user_id=self._get_tg_id(update))
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
                self.registry.dispatch("deepscan",
                    self.engine, timeframe=tf),
                timeout=120,  # 2 minute max
            )
            if result:
                # Try to render a card image from structured hits
                card_sent = False
                try:
                    hits = getattr(self.engine, '_last_deepscan_hits', None)
                    if hits:
                        from bot.formatters.signal_card import render_scan_results_card
                        # Convert deepscan hits to scan card format
                        setups = []
                        for h in hits[:6]:
                            price = h["price"]
                            atr = price * 0.02  # approximate ATR
                            direction = "LONG" if h.get("rsi", 50) < 50 or h.get("chg", 0) > 0 else "SHORT"
                            if direction == "LONG":
                                entry = round(price - atr * 0.3, 8)
                                sl_val = round(price - atr * 2.5, 8)
                                tp_val = round(price + atr * 3.0, 8)
                            else:
                                entry = round(price + atr * 0.3, 8)
                                sl_val = round(price + atr * 2.5, 8)
                                tp_val = round(price - atr * 3.0, 8)
                            sl_dist = abs(entry - sl_val) / entry * 100 if entry > 0 else 0
                            tp_dist = abs(tp_val - entry) / entry * 100 if entry > 0 else 0
                            rr = tp_dist / sl_dist if sl_dist > 0 else 0
                            setups.append({
                                "sym": h["symbol"],
                                "dir": direction,
                                "price": price,
                                "entry": entry,
                                "sl": sl_val,
                                "tp": tp_val,
                                "rr": rr,
                                "rsi": h.get("rsi", 0),
                                "vol_ratio": 2.5 if h.get("vol_spike") else 1.0,
                                "score": min(h.get("score", 0) / 10.0, 1.0),
                            })
                        now_str = datetime.now(UTC).strftime('%H:%M UTC')
                        card_png = render_scan_results_card(
                            setups, scan_label=f"DEEP SCAN {tf.upper()}",
                            timestamp=now_str)
                        if card_png:
                            import io as _io
                            buf = _io.BytesIO(card_png)
                            buf.name = "deepscan.png"
                            chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                            if chat_id:
                                await update.get_bot().send_photo(
                                    chat_id=int(chat_id), photo=buf,
                                    caption=f"🔬 <b>RUNECLAW Deep Scan</b> — {tf.upper()} — {now_str}",
                                    parse_mode="HTML")
                                card_sent = True
                except Exception as exc:
                    system_log.warning("Deepscan card render failed: %s", exc)

                # Send text result (full details + patterns)
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
        result = await self.registry.dispatch("learning", self.engine)
        await self._send(update, result)

    async def _cmd_patterns(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "patterns"):
            return
        result = await self.registry.dispatch("patterns", self.engine)
        await self._send(update, result)

    async def _cmd_proposals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "proposals"):
            return
        result = await self.registry.dispatch("proposals", self.engine)
        await self._send(update, result)

    async def _cmd_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, "optimize"):
            return
        result = await self.registry.dispatch("optimize", self.engine)
        await self._send(update, result)

    # ── War Room commands ────────────────────────────────────────

    async def _cmd_latest_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show all pending trade signals with action buttons.

        If no signals are pending, auto-triggers a fresh scan cycle
        so the user always sees current opportunities.
        """
        if not await self._guard(update, "scan"):
            return

        # Filter to only show ideas above the display threshold (default 70%)
        from bot.config import CONFIG
        _display_min = CONFIG.risk.signal_display_min_confidence
        pending = [i for i in self.engine.pending_ideas if i.confidence >= _display_min]

        # If nothing pending above threshold, auto-trigger a scan first
        if not pending:
            await self._send(update,
                "\U0001f50d <b>No signals queued — running fresh scan...</b>")
            try:
                result = await self.engine.force_scan()
                pending = [i for i in self.engine.pending_ideas if i.confidence >= _display_min]
                if not pending:
                    sig_count = result.get("signals", 0)
                    auto_count = result.get("auto_confirmed", 0)
                    msg = f"No trade setups above {_display_min:.0%} confidence found."
                    if sig_count > 0:
                        msg += f"\n\n\U0001f4e1 Scanned {sig_count} pairs"
                        if auto_count > 0:
                            msg += f" — {auto_count} were auto-confirmed"
                        msg += " but none passed confidence threshold."
                    msg += "\n\nTry <code>/fullscan</code> for deep multi-symbol analysis."
                    await self._send(update, msg)
                    return
            except Exception as exc:
                await self._send(update,
                    f"Scan failed: {exc}\nTry <code>/fullscan</code> instead.")
                return

        uid = update.effective_user.id if update.effective_user else ""

        # Show ALL pending ideas, not just the last one
        await self._send(update,
            f"\U0001f4a1 <b>{len(pending)} Trade Setup{'s' if len(pending) > 1 else ''} Found</b>\n"
            f"{'━' * 28}")

        for i, idea in enumerate(pending, 1):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Take it", callback_data=f"confirm:{idea.id}:{uid}"),
                InlineKeyboardButton("Limit", callback_data=f"setlimit:{idea.id}:{uid}"),
                InlineKeyboardButton("Skip", callback_data=f"reject:{idea.id}:{uid}"),
            ]])

            d_icon = "\U0001f7e2" if idea.direction.value == "LONG" else "\U0001f534"
            entry, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
            sl_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0
            tp_pct = abs(tp - entry) / entry * 100 if entry > 0 else 0
            rr = idea.risk_reward_ratio
            pair = idea.asset.replace("/USDT", "")
            _otype = getattr(idea, 'order_type', 'market').upper()
            _otype_tag = f" {_otype}" if _otype == "LIMIT" else ""
            _st = getattr(idea, 'strategy_type', '').upper()
            _st_tag = f" [{_st}]" if _st else ""

            # Try to send signal card image if available
            card_sent = False
            if hasattr(self, '_signal_card_fn') and self._signal_card_fn:
                try:
                    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                    if chat_id:
                        await self._signal_card_fn(chat_id, idea, rank=i)
                        card_sent = True
                except Exception:
                    pass

            if not card_sent:
                # Text fallback
                msg = (
                    f"{d_icon} <b>#{i} {html.escape(pair)}</b> — {idea.direction.value}{_st_tag}{_otype_tag}\n"
                    f"Entry: <code>${entry:,.4f}</code> | SL: <code>${sl:,.4f}</code> (-{sl_pct:.1f}%) | TP: <code>${tp:,.4f}</code> (+{tp_pct:.1f}%)\n"
                    f"R:R 1:{rr:.1f} | Conf <b>{idea.confidence:.0%}</b>\n"
                    f"<i>{html.escape(idea.reasoning[:150])}</i>"
                )
                await self._send(update, msg, reply_markup=kb)

            # Rate limit: avoid flooding Telegram
            if i < len(pending):
                import asyncio
                await asyncio.sleep(0.3)

    async def _cmd_orders(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show open/pending orders on Bitget exchange."""
        if not await self._guard(update, "portfolio"):
            return

        await self._send(update, "<i>Fetching open orders from Bitget...</i>")

        try:
            exchange = await self.engine.live_executor._get_exchange()

            # Fetch all open orders (limit orders, trigger orders, SL/TP)
            open_orders = await exchange.fetch_open_orders(
                params={"productType": "USDT-FUTURES"})

            if not open_orders:
                await self._send(update,
                    "<b>Open Orders</b>\n\n"
                    "No pending orders on Bitget right now.\n\n"
                    "<i>Tip: Use the \"Limit\" button when confirming a trade to set a custom limit price.</i>")
                return

            # Group by type
            limit_orders = []
            sl_orders = []
            tp_orders = []
            other_orders = []

            from bot.config import CONFIG
            expire_sec = CONFIG.limit_orders.expire_seconds
            now_utc = datetime.now(UTC)

            for o in open_orders:
                otype = (o.get("type") or "").lower()
                sym = display_symbol(o.get("symbol", ""))
                side = (o.get("side") or "").upper()
                price = float(o.get("price") or 0)
                amount = float(o.get("amount") or o.get("remaining") or 0)
                trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                filled = float(o.get("filled") or 0)
                status = o.get("status", "open")
                oid = o.get("id", "")[:12]
                created = o.get("datetime", "")[:16] if o.get("datetime") else ""

                # Calculate time remaining until expiry
                ttl_str = ""
                raw_dt = o.get("datetime") or ""
                if raw_dt and otype == "limit":
                    try:
                        from datetime import datetime as _dt
                        created_dt = _dt.fromisoformat(raw_dt.replace("Z", "+00:00"))
                        age_sec = (now_utc - created_dt).total_seconds()
                        remaining = max(0, expire_sec - age_sec)
                        if remaining <= 0:
                            ttl_str = " | \u23f0 expiring..."
                        else:
                            hrs = int(remaining // 3600)
                            mins = int((remaining % 3600) // 60)
                            if hrs > 0:
                                ttl_str = f" | \u23f0 {hrs}h {mins}m left"
                            else:
                                ttl_str = f" | \u23f0 {mins}m left"
                    except Exception:
                        pass

                entry = {
                    "sym": sym, "side": side, "price": price,
                    "trigger": trigger, "amount": amount, "filled": filled,
                    "status": status, "oid": oid, "created": created, "type": otype,
                    "ttl_str": ttl_str,
                }

                if "stop" in otype or "loss" in otype:
                    sl_orders.append(entry)
                elif "take" in otype or "profit" in otype:
                    tp_orders.append(entry)
                elif otype == "limit":
                    limit_orders.append(entry)
                else:
                    other_orders.append(entry)

            lines = [f"<b>Open Orders ({len(open_orders)})</b>", ""]

            # Fetch current prices for distance-to-fill calculation
            limit_syms = list({o["sym"] for o in limit_orders}) if limit_orders else []
            limit_prices_map: dict[str, float] = {}
            if limit_syms:
                try:
                    # Map display symbols back to exchange symbols for ticker fetch
                    _raw_syms = list({
                        raw_o.get("symbol", "") for raw_o in open_orders
                        if display_symbol(raw_o.get("symbol", "")) in limit_syms
                    })
                    if _raw_syms:
                        _tickers = await exchange.fetch_tickers(_raw_syms)
                        for _s, _t in _tickers.items():
                            limit_prices_map[display_symbol(_s)] = float(_t.get("last") or 0)
                except Exception:
                    pass

            if limit_orders:
                lines.append(f"<b>\U0001f4cb Limit Orders ({len(limit_orders)}):</b>")
                lines.append("")
                for o in limit_orders:
                    d_icon = "\U0001f7e2" if o["side"] == "BUY" else "\U0001f534"
                    dir_label = "LONG" if o["side"] == "BUY" else "SHORT"
                    fill_str = f" ({o['filled']:.4f} filled)" if o["filled"] > 0 else ""
                    cur_price = limit_prices_map.get(o["sym"], 0)

                    lines.append(f"{d_icon} <b>{o['sym']} {dir_label}</b> \u2014 Limit Order")
                    lines.append(f"  \U0001f4cd Limit: <code>${o['price']:,.4f}</code>{fill_str}")
                    if cur_price > 0:
                        dist = ((cur_price - o['price']) / cur_price) * 100
                        fill_hint = "\u2b07\ufe0f" if (o["side"] == "BUY" and cur_price > o['price']) else (
                            "\u2b06\ufe0f" if (o["side"] != "BUY" and cur_price < o['price']) else "\u2705")
                        lines.append(f"  \U0001f4b2 Current: <code>${cur_price:,.4f}</code>  {fill_hint} {dist:+.2f}% to fill")
                    lines.append(f"  \U0001f4b0 Qty: <code>{o['amount']:.4f}</code>{o['ttl_str']}")
                    lines.append(f"  ID: <code>{o['oid']}</code>")
                    if o['created']:
                        lines.append(f"  \u23f3 Placed: {o['created']}")
                    lines.append("")

            if sl_orders:
                lines.append(f"<b>Stop-Loss Orders ({len(sl_orders)}):</b>")
                for o in sl_orders:
                    trigger_str = f"trigger ${o['trigger']:,.4f}" if o['trigger'] > 0 else ""
                    lines.append(
                        f"  \U0001f6d1 <b>{o['sym']}</b> {o['side']} {trigger_str}")
                lines.append("")

            if tp_orders:
                lines.append(f"<b>Take-Profit Orders ({len(tp_orders)}):</b>")
                for o in tp_orders:
                    trigger_str = f"trigger ${o['trigger']:,.4f}" if o['trigger'] > 0 else ""
                    lines.append(
                        f"  \U0001f3af <b>{o['sym']}</b> {o['side']} {trigger_str}")
                lines.append("")

            if other_orders:
                lines.append(f"<b>Other ({len(other_orders)}):</b>")
                for o in other_orders:
                    lines.append(
                        f"  <b>{o['sym']}</b> {o['side']} {o['type']} "
                        f"@ <code>${o['price']:,.4f}</code>")
                lines.append("")

            lines.append(f"<i>Source: Bitget USDT-M Futures</i>")

            # ── Render orders card image ──
            card_sent = False
            try:
                from bot.formatters.signal_card import render_orders_card
                all_display_orders = limit_orders + sl_orders + tp_orders + other_orders
                card_data = []
                for o in all_display_orders[:6]:
                    cur_price = limit_prices_map.get(o["sym"], 0)
                    dist = ((cur_price - o['price']) / cur_price * 100) if cur_price > 0 and o['price'] > 0 else 0
                    card_data.append({
                        "sym": o["sym"],
                        "side": o["side"],
                        "price": o["price"],
                        "current_price": cur_price,
                        "amount": o["amount"],
                        "ttl_str": o.get("ttl_str", ""),
                        "oid": o["oid"],
                        "created": o.get("created", ""),
                        "type": o["type"],
                        "dist_pct": dist,
                    })
                now_str = datetime.now(UTC).strftime('%H:%M UTC')
                card_png = render_orders_card(card_data, timestamp=now_str)
                if card_png:
                    import io as _io
                    buf = _io.BytesIO(card_png)
                    buf.name = "orders.png"
                    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                    if chat_id:
                        await update.get_bot().send_photo(
                            chat_id=int(chat_id), photo=buf,
                            caption=f"\U0001f4cb <b>Open Orders</b> — {now_str}",
                            parse_mode="HTML")
                        card_sent = True
            except Exception as exc:
                system_log.warning("Orders card render failed: %s", exc)

            if not card_sent:
                await self._send(update, "\n".join(lines))
            # Always send text as well for copy-paste of IDs
            if card_sent:
                # Send compact text with order IDs only
                id_lines = [f"<b>Order IDs</b> (for cancel):"]
                for o in all_display_orders[:6]:
                    dir_l = "LONG" if o["side"] == "BUY" else "SHORT"
                    id_lines.append(f"  {o['sym']} {dir_l} — <code>{o['oid']}</code>")
                await self._send(update, "\n".join(id_lines))

        except Exception as exc:
            logger.error(f"Orders fetch error: {exc}", exc_info=True)
            await self._send(update,
                f"\U0001f534 <b>Failed to fetch orders:</b> <code>{html.escape(str(exc)[:200])}</code>")

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
                prices: dict[str, float] = {}
                try:
                    exchange = await self.engine.live_executor._get_exchange()
                    for p in live_positions:
                        if p.symbol not in prices:
                            try:
                                tk = await exchange.fetch_ticker(p.symbol)
                                last = float(tk.get("last") or 0)
                                if last > 0:
                                    prices[p.symbol] = last
                            except Exception:
                                pass
                except Exception:
                    pass

                for pos in live_positions:
                    last_price = prices.get(pos.symbol, pos.entry_price)
                    if pos.direction == "LONG":
                        pnl_pct_raw = ((last_price - pos.entry_price) / pos.entry_price) * 100
                        upnl_usd = (last_price - pos.entry_price) * pos.quantity
                    else:
                        pnl_pct_raw = ((pos.entry_price - last_price) / pos.entry_price) * 100
                        upnl_usd = (pos.entry_price - last_price) * pos.quantity
                    from datetime import datetime, timezone
                    hold_h = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
                    cost = pos.cost_usd if pos.cost_usd > 0 else pos.entry_price * pos.quantity
                    notional = last_price * pos.quantity
                    leverage = getattr(pos, 'leverage', 0) or (notional / cost if cost > 0 else 1.0)
                    pnl_pct = pnl_pct_raw * leverage
                    sl_dist = abs(last_price - pos.stop_loss) / last_price * 100 if last_price else 0
                    tp_dist = abs(pos.take_profit - last_price) / last_price * 100 if last_price else 0
                    risk_left = abs(last_price - pos.stop_loss) if pos.stop_loss else 0
                    reward_left = abs(pos.take_profit - last_price) if pos.take_profit else 0
                    rr_live = reward_left / risk_left if risk_left > 0 else 0
                    positions_data.append({
                        "pair": pos.symbol.replace("/", "").replace(":USDT", ""),
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
                        "status": getattr(pos, "status", "open"),
                        "strategy_type": getattr(pos, "strategy_type", "swing"),
                    })
            else:
                # No locally-tracked positions — fall back to exchange API
                # to catch orphans (positions opened outside bot or lost on restart)
                try:
                    exchange = await self.engine.live_executor._get_exchange()
                    ex_positions = await exchange.fetch_positions()
                    open_ex = [p for p in (ex_positions or [])
                               if isinstance(p, dict) and float(p.get("contracts") or 0) > 0]
                    if open_ex:
                        syms = [p.get("symbol", "") for p in open_ex]
                        tickers = await exchange.fetch_tickers(syms)
                        prices = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                        # Try to fetch open trigger/conditional orders for SL/TP
                        sl_tp_map = {}  # symbol -> {"sl": price, "tp": price}
                        try:
                            open_orders = await exchange.fetch_open_orders()
                            for o in (open_orders or []):
                                osym = o.get("symbol", "")
                                otype = (o.get("type") or "").lower()
                                oside = (o.get("side") or "").lower()
                                trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                                if trigger <= 0:
                                    continue
                                if osym not in sl_tp_map:
                                    sl_tp_map[osym] = {"sl": 0, "tp": 0}
                                # For a LONG: sell stop = SL, sell limit/take-profit = TP
                                # For a SHORT: buy stop = SL, buy limit/take-profit = TP
                                if "stop" in otype or "loss" in otype:
                                    sl_tp_map[osym]["sl"] = trigger
                                elif "take" in otype or "profit" in otype:
                                    sl_tp_map[osym]["tp"] = trigger
                                elif oside == "sell":
                                    # Closing sell = likely SL or TP for a long
                                    # Use price relative to entry to guess
                                    sl_tp_map[osym].setdefault("_sells", []).append(trigger)
                                elif oside == "buy":
                                    sl_tp_map[osym].setdefault("_buys", []).append(trigger)
                        except Exception:
                            pass  # Orders fetch not critical
                        from datetime import datetime, timezone
                        for p in open_ex:
                            sym = p.get("symbol", "")
                            side = (p.get("side") or "long").upper()
                            contracts = float(p.get("contracts") or 0)
                            entry_price = float(p.get("entryPrice") or p.get("info", {}).get("openPriceAvg") or 0)
                            notional = float(p.get("notional") or 0)
                            margin = float(p.get("initialMargin") or p.get("collateral") or 0)
                            lev = float(p.get("leverage") or 1)
                            unrealized = float(p.get("unrealizedPnl") or 0)
                            last_price = prices.get(sym, entry_price)
                            pnl_pct = (unrealized / margin * 100) if margin > 0 else 0
                            # SL/TP from conditional orders
                            sym_orders = sl_tp_map.get(sym, {})
                            sl_price = sym_orders.get("sl", 0)
                            tp_price = sym_orders.get("tp", 0)
                            # Timestamp handling
                            ts = p.get("timestamp")
                            if ts:
                                opened = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                                hold_h = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
                            else:
                                hold_h = 0.0
                            sl_dist = abs(last_price - sl_price) / last_price * 100 if sl_price and last_price else 0
                            tp_dist = abs(tp_price - last_price) / last_price * 100 if tp_price and last_price else 0
                            positions_data.append({
                                "pair": sym.replace("/", "").replace(":USDT", ""),
                                "direction": side,
                                "entry": round(entry_price, 6),
                                "current": round(last_price, 6),
                                "pnl_pct": round(pnl_pct, 2),
                                "pnl_usd": round(unrealized, 4),
                                "sl": round(sl_price, 6),
                                "tp": round(tp_price, 6),
                                "sl_dist_pct": round(sl_dist, 2),
                                "tp_dist_pct": round(tp_dist, 2),
                                "size_usd": round(margin, 2),
                                "notional_usd": round(notional, 2),
                                "leverage": round(lev, 2),
                                "rr_live": 0,
                                "quantity": contracts,
                                "comm_pct": CONFIG.risk.commission_pct,
                                "hold_hours": round(hold_h, 1),
                                "sl_order": "exchange" if sl_price > 0 else "none",
                                "tp_order": "exchange" if tp_price > 0 else "none",
                                "trade_id": sym,
                                "untracked": True,
                                "status": "open",
                            })
                except Exception as exc:
                    logger.warning("Exchange position fallback failed: %s", exc)
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
                        pnl_pct_raw = ((last_price - pos.entry_price) / pos.entry_price) * 100
                    else:
                        pnl_pct_raw = ((pos.entry_price - last_price) / pos.entry_price) * 100
                    pos_lev = getattr(pos, 'leverage', 1) or 1
                    pnl_pct = pnl_pct_raw * pos_lev
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

        # ── Split into filled positions vs pending orders ──
        filled_positions = [p for p in positions_data if p.get("status", "open") != "pending_fill"]
        pending_orders = [p for p in positions_data if p.get("status") == "pending_fill"]

        if not filled_positions and not pending_orders:
            await self._send(update,
                "No open positions or pending orders right now.\n"
                "Say \"scan\" or \"analyze BTC\" to find setups.")
            return

        from bot.formatters.signal_card import render_position_card

        # ── SECTION 1: Open Positions (filled) ──
        if filled_positions:
            total_pnl = sum(p.get("pnl_pct", 0) for p in filled_positions)
            pnl_icon = "\U0001f7e2" if total_pnl > 0 else "\U0001f534" if total_pnl < 0 else ""
            header = (f"\U0001f4ca <b>OPEN POSITIONS ({len(filled_positions)})</b> "
                      f"{pnl_icon} {total_pnl:+.2f}% total")
            await self._send(update, header)
        elif not pending_orders:
            await self._send(update, "No open positions right now.")

        for pos in filled_positions:
            tid = pos.get('trade_id', pos['pair'])
            pair = pos.get("pair", "N/A")
            direction = pos.get("direction", "LONG")
            entry = pos.get("entry", 0)
            current = pos.get("current", entry)
            pnl_pct = pos.get("pnl_pct", 0)
            pnl_usd = pos.get("pnl_usd", 0)
            sl = pos.get("sl", 0)
            tp = pos.get("tp", 0)
            sl_dist = pos.get("sl_dist_pct", 0)
            tp_dist = pos.get("tp_dist_pct", 0)
            size_usd = pos.get("size_usd", 0)
            leverage = pos.get("leverage", 1)
            rr_live = pos.get("rr_live", 0)
            hold_h = pos.get("hold_hours", 0)
            sl_order = pos.get("sl_order", "")
            tp_order = pos.get("tp_order", "")
            comm_pct = pos.get("comm_pct", CONFIG.risk.commission_pct)

            # Hold time display
            if hold_h < 1:
                hold_str = f"{hold_h * 60:.0f}m"
            elif hold_h < 24:
                hold_str = f"{hold_h:.1f}h"
            else:
                hold_str = f"{hold_h / 24:.1f}d"

            # Fee calculations
            entry_fee = size_usd * (comm_pct / 100.0)
            exit_notional = pos.get("notional_usd", current * pos.get("quantity", 0))
            exit_fee = exit_notional * (comm_pct / 100.0)
            total_fees = entry_fee + exit_fee
            funding_sessions = hold_h / 8.0
            funding_paid = size_usd * (0.01 / 100.0) * funding_sessions
            net_pnl = pnl_usd - total_fees - funding_paid

            sl_tag = "on exchange" if sl_order == "exchange" else "bot-managed"
            tp_tag = "on exchange" if tp_order == "exchange" else "bot-managed"

            pos_card_data = {
                "symbol": pair.replace("USDT", "/USDT") if "USDT" in pair else pair,
                "direction": direction,
                "is_live": CONFIG.is_live(),
                "entry": entry,
                "now": current,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "net_pnl": net_pnl,
                "fees": total_fees + funding_paid,
                "size_usd": size_usd,
                "leverage": leverage,
                "hold_time": hold_str,
                "rr": rr_live,
                "sl": sl,
                "tp": tp,
                "sl_pct": sl_dist,
                "tp_pct": tp_dist,
                "sl_status": sl_tag,
                "tp_status": tp_tag,
            }

            try:
                card_png = render_position_card(pos_card_data)
            except Exception as exc:
                system_log.debug("Position card render failed for %s: %s", pair, exc)
                card_png = None

            d_emoji = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
            pnl_emoji = "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{pair}", callback_data=f"pos_details_{tid}"),
                InlineKeyboardButton("Close", callback_data=f"pos_close_{tid}"),
            ]])

            if card_png:
                mode_tag = "LIVE" if CONFIG.is_live() else "PAPER"
                st_tag = pos.get("strategy_type", "").upper()
                st_str = f" [{st_tag}]" if st_tag else ""
                cap = (f"<b>{html.escape(pair)}</b> {mode_tag}\n"
                       f"{d_emoji} {direction}{st_str} | {pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})")
                await self._send_photo(update, card_png, cap, reply_markup=kb)
            else:
                # Fallback to text if PNG render fails
                msg = render_open_positions([pos])
                await self._send(update, msg, reply_markup=kb)

        # ── SECTION 2: Pending Orders (unfilled limit orders) ──
        if pending_orders:
            from datetime import datetime, timezone
            pend_header = (f"\u2694\ufe0f <b>PENDING ORDERS ({len(pending_orders)})</b>")
            await self._send(update, pend_header)

            for po in pending_orders:
                pair = po.get("pair", "N/A")
                direction = po.get("direction", "LONG")
                limit_price = po.get("entry", 0)
                current = po.get("current", limit_price)
                sl = po.get("sl", 0)
                tp = po.get("tp", 0)
                size_usd = po.get("size_usd", 0)
                notional_usd = po.get("notional_usd", size_usd)
                leverage = po.get("leverage", 1)
                tid = po.get("trade_id", pair)
                hold_h = po.get("hold_hours", 0)
                quantity = po.get("quantity", 0)
                comm_pct = po.get("comm_pct", CONFIG.risk.commission_pct)
                sl_order = po.get("sl_order", "")
                tp_order = po.get("tp_order", "")

                # Distance from current price to limit
                if limit_price > 0 and current > 0:
                    dist_pct = ((current - limit_price) / current) * 100
                else:
                    dist_pct = 0

                # SL/TP distances from limit price (where it will fill)
                if sl > 0 and limit_price > 0:
                    sl_dist_pct = abs(limit_price - sl) / limit_price * 100
                else:
                    sl_dist_pct = 0
                if tp > 0 and limit_price > 0:
                    tp_dist_pct = abs(tp - limit_price) / limit_price * 100
                else:
                    tp_dist_pct = 0

                # R:R at fill
                risk_at_fill = abs(limit_price - sl) if sl > 0 else 0
                reward_at_fill = abs(tp - limit_price) if tp > 0 else 0
                rr_at_fill = reward_at_fill / risk_at_fill if risk_at_fill > 0 else 0

                # Fee estimate — fees are charged on notional, not margin
                entry_notional = notional_usd if notional_usd > 0 else (limit_price * quantity if quantity else size_usd * leverage)
                entry_fee = entry_notional * (comm_pct / 100.0)
                exit_notional = entry_notional  # assume same notional on exit
                exit_fee = exit_notional * (comm_pct / 100.0)
                total_fees = entry_fee + exit_fee

                d_icon = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
                dir_label = "LONG" if direction == "LONG" else "SHORT"

                # Age display
                if hold_h < 1:
                    age_str = f"{hold_h * 60:.0f}m"
                elif hold_h < 24:
                    age_str = f"{hold_h:.1f}h"
                else:
                    age_str = f"{hold_h / 24:.1f}d"

                # Fill direction hint
                if direction == "LONG":
                    fill_hint = "\u2b07\ufe0f" if current > limit_price else "\u2705"
                else:
                    fill_hint = "\u2b06\ufe0f" if current < limit_price else "\u2705"

                sl_tag = "on exchange" if sl_order == "exchange" else "bot-managed"
                tp_tag = "on exchange" if tp_order == "exchange" else "bot-managed"
                strategy_type = po.get("strategy_type", "swing").upper()

                lines = [
                    f"{d_icon} <b>{html.escape(pair)} {dir_label}</b> \u2014 Limit Order \u2022 {strategy_type}",
                    f"",
                    f"\U0001f4cd <b>Limit Price:</b> <code>${limit_price:,.4f}</code>",
                    f"\U0001f4b2 <b>Current:</b>    <code>${current:,.4f}</code>  {fill_hint} {dist_pct:+.2f}% to fill",
                    f"",
                    f"\U0001f4b0 <b>Size:</b> <code>${size_usd:,.2f}</code> margin | <b>{leverage:.0f}x</b> leverage",
                ]
                if quantity > 0:
                    lines.append(f"   Qty: <code>{quantity:.4f}</code> contracts")

                lines.append(f"")

                if sl > 0:
                    lines.append(
                        f"\U0001f6d1 <b>SL:</b> <code>${sl:,.4f}</code>  ({sl_dist_pct:.2f}% from entry) [{sl_tag}]")
                if tp > 0:
                    lines.append(
                        f"\U0001f3af <b>TP:</b> <code>${tp:,.4f}</code>  ({tp_dist_pct:.2f}% from entry) [{tp_tag}]")
                if rr_at_fill > 0:
                    lines.append(f"\u2696\ufe0f <b>R:R at fill:</b> 1:{rr_at_fill:.1f}")

                lines.append(f"")
                lines.append(f"\U0001f4b8 <b>Est. fees:</b> ${total_fees:.4f} (entry + exit)")
                lines.append(f"\u23f3 <b>Waiting:</b> {age_str}")

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Cancel", callback_data=f"pos_close_{tid}"),
                ]])

                await self._send(update, "\n".join(lines), reply_markup=kb)

        elif not filled_positions:
            await self._send(update, "No pending orders.")

    async def _cmd_performance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Performance summary — per-user."""
        if not await self._guard(update, "portfolio"):
            return
        user_id = self._get_tg_id(update)

        # LIVE mode: use real trade data from executor + exchange fallback
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            executor = self.engine.live_executor
            live_closed = executor.closed_positions

            # ── Exchange trade history fallback ──
            # If local closed_trades is empty, try to fetch recent trades
            # from the exchange to capture trades closed outside the bot
            if not live_closed:
                try:
                    exchange = await executor._get_exchange()
                    # Fetch recent closed orders across major pairs
                    import time as _time
                    since_ms = int((_time.time() - 7 * 86400) * 1000)  # last 7 days
                    ex_trades = await exchange.fetch_my_trades(symbol=None, since=since_ms, limit=50)
                    if ex_trades:
                        from bot.core.live_executor import LivePosition
                        # Group trades by order to reconstruct PnL
                        _trade_pnl_map: dict[str, float] = {}
                        _trade_sym_map: dict[str, str] = {}
                        for t in ex_trades:
                            oid = t.get("order", t.get("id", "unknown"))
                            info = t.get("info", {})
                            pnl = float(info.get("profit", 0) or 0)
                            _trade_pnl_map[oid] = _trade_pnl_map.get(oid, 0) + pnl
                            _trade_sym_map[oid] = t.get("symbol", "UNKNOWN")
                        # Create synthetic LivePosition entries for display
                        for oid, pnl in _trade_pnl_map.items():
                            if pnl == 0:
                                continue  # skip zero-PnL (likely open leg)
                            sym = _trade_sym_map.get(oid, "UNKNOWN")
                            lp = LivePosition(
                                trade_id=f"EX-{oid}",
                                symbol=sym,
                                side="long",
                                entry_price=0,
                                qty=0,
                                cost_usd=0,
                                leverage=1,
                                sl_price=None,
                                tp_price=None,
                            )
                            lp.status = "closed"
                            lp.pnl_usd = pnl
                            live_closed.append(lp)
                        if live_closed:
                            audit(system_log, f"Performance: loaded {len(live_closed)} trades from exchange history",
                                  action="perf_exchange_fallback", result="OK")
                except Exception as exc:
                    audit(system_log, f"Performance exchange fallback error: {exc}",
                          action="perf_exchange_fallback", result="ERROR")

            # ── Separate adopted/injected vs user-initiated trades ──
            # Exclude: TI-adopted (orphan positions), TI-injected (diagnostic artifacts),
            # canceled/expired/price_drift (never-filled limit orders with $0 PnL)
            _NON_TRADE_REASONS_PERF = {"canceled", "cancelled", "expired", "price_drift", "rejected"}
            user_trades = [t for t in live_closed
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)
                           and getattr(t, "close_reason", "") not in _NON_TRADE_REASONS_PERF]
            adopted_trades = [t for t in live_closed
                              if any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
            adopted_pnl = sum((t.pnl_usd or 0) for t in adopted_trades)

            total_trades = len(user_trades)
            wins = sum(1 for t in user_trades if (t.pnl_usd or 0) > 0)
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            total_pnl = sum((t.pnl_usd or 0) for t in user_trades)

            # ── Date-filtered PnL ──
            from datetime import datetime as _dt, timedelta as _td
            from bot.compat import UTC as _UTC
            _now = _dt.now(_UTC)
            _today_start = _now.replace(hour=0, minute=0, second=0, microsecond=0)
            _week_start = _today_start - _td(days=7)

            today_pnl = 0.0
            week_pnl = 0.0
            trades_today = 0
            for t in user_trades:
                closed_at = getattr(t, "closed_at", None)
                if closed_at:
                    if isinstance(closed_at, str):
                        try:
                            closed_at = _dt.fromisoformat(closed_at)
                        except (ValueError, TypeError):
                            closed_at = None
                    if closed_at is not None:
                        # Ensure timezone-aware
                        if closed_at.tzinfo is None:
                            closed_at = closed_at.replace(tzinfo=_UTC)
                        pnl = t.pnl_usd or 0
                        if closed_at >= _today_start:
                            today_pnl += pnl
                            trades_today += 1
                        if closed_at >= _week_start:
                            week_pnl += pnl
                        continue
                # Fallback: if no closed_at, count in total only
            # If no date info at all, fall back to total for both
            if today_pnl == 0 and week_pnl == 0 and total_pnl != 0:
                week_pnl = total_pnl
                trades_today = total_trades

            best_pair = "N/A"
            worst_pair = "N/A"
            if user_trades:
                sorted_t = sorted(user_trades, key=lambda t: (t.pnl_usd or 0))
                worst_pair = sorted_t[0].symbol.replace("/USDT", "").replace(":USDT", "")
                best_pair = sorted_t[-1].symbol.replace("/USDT", "").replace(":USDT", "")
            data = {
                "today_pnl": round(today_pnl, 2),
                "week_pnl": round(week_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "win_rate": win_rate,
                "trades_today": trades_today,
                "total_trades": total_trades,
                "best_pair": best_pair,
                "worst_pair": worst_pair,
                "adopted_count": len(adopted_trades),
                "adopted_pnl": round(adopted_pnl, 2),
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

    async def _cmd_close_all(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /closeall — close all open positions on exchange."""
        if not self._is_admin(update):
            await self._send(update, "🔒 Admin only.")
            return
        if not CONFIG.is_live() or not hasattr(self.engine, 'live_executor'):
            await self._send(update, "No live executor available.")
            return

        await self._send(update, "⏳ Closing all positions...")
        try:
            results = await self.engine.live_executor.close_all_positions(reason="admin_closeall")
            msg = "⛔ <b>Close All Results:</b>\n\n" + "\n".join(
                f"• {r[:120]}" for r in results[:10])
            await self._send(update, msg)
        except Exception as exc:
            await self._send(update, f"❌ Close all failed: {exc}")

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
            closed = [t for t in executor.closed_positions
                       if not any(getattr(t, "trade_id", "").startswith(p)
                                  for p in _ORPHAN_PREFIXES)]
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
                worst_trade = sorted_t[0].symbol.replace("/USDT", "").replace(":USDT", "")
                worst_pnl = round(sorted_t[0].pnl_usd or 0, 2)
                best_trade = sorted_t[-1].symbol.replace("/USDT", "").replace(":USDT", "")
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
                worst_trade = sorted_t[0].asset.replace("/USDT", "").replace(":USDT", "")
                worst_pnl = round(sorted_t[0].pnl, 2)
                best_trade = sorted_t[-1].asset.replace("/USDT", "").replace(":USDT", "")
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
        """Show active strategy and regime-based routing."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            from bot.core.strategy_router import select_strategy, strategy_summary
            regime = self.engine.risk._current_regime
            vol_state = self.engine.risk._current_vol_state
            profile = select_strategy(regime, vol_state)

            lines = [
                "\U0001f3af <b>Strategy Router</b>",
                "\u2500" * 28,
                "",
                f"Current Regime: <b>{regime}</b>",
                f"Volatility: <b>{vol_state}</b>",
                "",
                f"Active Strategy: <b>{profile.name}</b>",
                f"Type: <code>{profile.strategy_type}</code>",
                f"SL: <code>{profile.sl_atr_mult}x ATR</code>",
                f"TP: <code>{profile.tp_atr_mult}x ATR</code>",
                f"Size: <code>{profile.size_multiplier:.0%}</code>",
                f"Min Confidence: <code>{profile.min_confidence:.0%}</code>",
                "",
                f"\U0001f4dd {profile.description}",
                "",
                "\u2500" * 28,
                "\U0001f43e RUNECLAW Strategy Engine",
            ]

            await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await ctx.bot.send_message(chat_id=chat_id, text=f"\u274c Error: {exc}")

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

        # M-18 FIX: rate limit callback buttons
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return  # rate limited

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

        # ── Audit F-11: destructive callbacks require role permission ──
        # _check_auth (allowlist-gated) above stops strangers; this stops an
        # authorized non-privileged user from pausing, emergency-stopping, or
        # switching strategy mode via an inline button.
        _DESTRUCTIVE_CB_PERM = {
            "risk_safe_mode": "halt", "risk_pause": "halt",
            "risk_emergency_stop": "halt", "emergency_confirm": "halt",
        }
        _required_perm = _DESTRUCTIVE_CB_PERM.get(data)
        if _required_perm is None and data.startswith("mode_"):
            _required_perm = "mode"
        if _required_perm and not self.users.has_permission(self._get_tg_id(update), _required_perm):
            role = (self.users.get(self._get_tg_id(update)) or {}).get("role", "pending")
            await self._send(update,
                f"\U0001f512 Your role (<code>{role}</code>) cannot perform this action.",
                edit=True)
            audit(system_log, f"Destructive callback denied: {data}",
                  action="callback_denied", result="DENIED",
                  data={"data": data, "role": role})
            return

        # ── Language switch callback ─────────────────────────
        if data.startswith("lang:"):
            new_lang = data.split(":", 1)[1]
            tg_id = self._get_tg_id(update)
            if new_lang in SUPPORTED_LANGS:
                set_user_lang(self.users, tg_id, new_lang)
                try:
                    await query.edit_message_text(
                        t("lang_switched", new_lang),
                        parse_mode="HTML")
                except Exception:
                    pass
            return

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

        if data == "orders":
            await self._cmd_orders(update, ctx)
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

            # GETCLAW: Close all open positions on exchange
            close_msgs = []
            if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
                try:
                    close_msgs = await self.engine.live_executor.close_all_positions(
                        reason="emergency_stop")
                except Exception as exc:
                    close_msgs = [f"Failed to close positions: {exc}"]

            close_summary = ""
            if close_msgs:
                close_summary = "\n\n<b>Position closes:</b>\n" + "\n".join(
                    f"• {m[:100]}" for m in close_msgs[:10])

            await self._send(update,
                f"⛔ <b>EMERGENCY STOP</b>\n\n"
                f"• Circuit breaker: ON\n"
                f"• Pending ideas: cleared\n"
                f"• Exchange positions: close attempted{close_summary}\n\n"
                f"Say \"resume\" when ready to restart.",
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
            # M-21 FIX: validate strategy mode against allowed values
            VALID_MODES = {"defensive", "balanced", "aggressive", "manual"}
            mode = data.removeprefix("mode_")
            if mode not in VALID_MODES:
                await self._send(update, "Invalid strategy mode.", edit=True)
                return
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
                ident_clean = ident.replace("/", "").replace(":USDT", "")
                for lp in self.engine.live_executor.open_positions:
                    if is_trade_id:
                        if lp.trade_id == ident:
                            pos_match = lp
                            is_live_pos = True
                            pair = lp.symbol.replace("/", "").replace(":USDT", "")
                            break
                    else:
                        lp_clean = lp.symbol.replace("/", "").replace(":USDT", "")
                        if lp_clean == ident_clean:
                            pos_match = lp
                            is_live_pos = True
                            pair = lp_clean
                            break

            if pos_match is None:
                for p in portfolio.open_positions:
                    if p.asset.replace("/", "").replace(":USDT", "") == pair:
                        pos_match = p
                        break

            # Fallback: check exchange directly for untracked positions
            is_untracked = False
            if pos_match is None and CONFIG.is_live():
                try:
                    exchange_fallback = await self.engine.live_executor._get_exchange()
                    ex_positions = await exchange_fallback.fetch_positions()
                    ident_clean = ident.replace("/", "").replace(":USDT", "")
                    for ep in (ex_positions or []):
                        if not isinstance(ep, dict):
                            continue
                        contracts = float(ep.get("contracts") or 0)
                        if contracts <= 0:
                            continue
                        ep_sym = ep.get("symbol", "")
                        ep_clean = ep_sym.replace("/", "").replace(":USDT", "")
                        if ep_clean == ident_clean or ep_sym == ident:
                            # Build a lightweight mock object for rendering
                            from types import SimpleNamespace
                            from datetime import datetime, timezone
                            ts = ep.get("timestamp")
                            opened = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)
                            pos_match = SimpleNamespace(
                                entry_price=float(ep.get("entryPrice") or ep.get("info", {}).get("openPriceAvg") or 0),
                                quantity=contracts,
                                direction=(ep.get("side") or "long").upper(),
                                stop_loss=0,
                                take_profit=0,
                                opened_at=opened,
                                cost_usd=float(ep.get("initialMargin") or ep.get("collateral") or 0),
                                leverage=float(ep.get("leverage") or 1),
                                sl_order_id=None,
                                tp_order_id=None,
                                trade_id=ep_sym,
                                symbol=ep_sym,
                            )
                            is_live_pos = True
                            is_untracked = True
                            pair = ep_clean
                            break
                except Exception:
                    pass

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

                # ── Render styled position card PNG ──
                pos_card_png = None
                try:
                    from bot.formatters.signal_card import render_position_card
                    pos_card_data = {
                        "symbol": symbol,
                        "direction": _dir,
                        "is_live": is_live_pos,
                        "entry": _entry,
                        "now": last_px,
                        "pnl_pct": pnl_pct,
                        "pnl_usd": pnl_usd,
                        "net_pnl": net_pnl,
                        "fees": total_fees + funding_paid,
                        "size_usd": sz,
                        "leverage": leverage,
                        "hold_time": hold_str,
                        "rr": rr_live,
                        "sl": _sl,
                        "tp": _tp,
                        "sl_pct": sl_dist,
                        "tp_pct": tp_dist,
                        "sl_status": sl_tag,
                        "tp_status": tp_tag,
                    }
                    if adata:
                        pos_card_data["rsi"] = adata.get("rsi", 0)
                        rsi_val = adata.get("rsi", 0)
                        pos_card_data["rsi_label"] = (
                            "overbought" if rsi_val > 70
                            else "oversold" if rsi_val < 30
                            else "neutral"
                        )
                        pos_card_data["structure"] = adata.get("structure", "")
                    pos_card_png = render_position_card(pos_card_data)
                except Exception as exc:
                    system_log.debug("Position card render failed: %s", exc)

                # Try to build a position chart
                chart_png = None
                try:
                    from bot.skills.chart_renderer import build_position_chart
                    chart_png = await build_position_chart(
                        None, symbol, entry=_entry, sl=_sl, tp=_tp)
                except Exception as exc:
                    system_log.warning("build_position_chart failed for %s: %s", symbol, exc)

                if pos_card_png:
                    # Send the styled position card as a photo with buttons
                    mode_tag = "LIVE" if is_live_pos else "PAPER"
                    cap = (f"<b>{html.escape(pair)}</b> {mode_tag}\n"
                           f"{d_emoji} {_dir} | {pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})")
                    await self._send_photo(update, pos_card_png, cap, reply_markup=kb)
                    # Also send chart below if available
                    if chart_png:
                        chart_cap = (f"<b>{html.escape(pair)}</b> · 1h\n"
                                     f"Entry <code>{_entry:,.6f}</code> | "
                                     f"Now <code>{last_px:,.6f}</code>")
                        await self._send_photo(update, chart_png, chart_cap)
                elif chart_png:
                    card_text = "\n".join(lines)
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
                    f"\u2705 <b>{html.escape(pair)}</b> — position closed.\n\n"
                    "Say \"positions\" to see current state.",
                    edit=True)
                # Remove stale buttons
                try:
                    if update.callback_query and update.callback_query.message:
                        await update.callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
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
                    if is_trade_id:
                        matched = lp.trade_id == ident
                    else:
                        lp_clean = lp.symbol.replace("/", "").replace(":USDT", "")
                        ident_clean = ident.replace("/", "").replace(":USDT", "")
                        matched = lp_clean == ident_clean
                    if matched:
                        pair = lp.symbol.replace("/", "").replace(":USDT", "")
                        try:
                            result = await executor.close_position(lp.trade_id, "manual_nlp")
                            live_closed = True
                            # Render styled PNG close card
                            close_data = getattr(executor, '_last_close_data', None)
                            close_png = None
                            if close_data:
                                try:
                                    from bot.formatters.signal_card import render_close_card
                                    close_png = render_close_card(close_data)
                                except Exception:
                                    pass

                            if close_png:
                                pnl_val = close_data.get("pnl_usd", 0)
                                pnl_emoji = "\u2705" if pnl_val >= 0 else "\u274c"
                                cap = (f"{pnl_emoji} <b>{html.escape(pair)}</b> CLOSED\n"
                                       f"PnL: ${pnl_val:+,.2f} | {html.escape(close_data.get('reason', 'manual'))}")
                                await self._send_photo(update, close_png, cap)
                            else:
                                # Fallback to text
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
                            # Remove buttons from the original details message
                            try:
                                if update.callback_query and update.callback_query.message:
                                    await update.callback_query.message.edit_reply_markup(reply_markup=None)
                            except Exception:
                                pass
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

            # LIVE mode fallback: close untracked exchange positions directly
            if CONFIG.is_live() and not live_closed:
                try:
                    exchange = await self.engine.live_executor._get_exchange()
                    ex_positions = await exchange.fetch_positions()
                    for ep in (ex_positions or []):
                        if not isinstance(ep, dict):
                            continue
                        contracts = float(ep.get("contracts") or 0)
                        if contracts <= 0:
                            continue
                        ep_sym = ep.get("symbol", "")
                        ep_clean = ep_sym.replace("/", "").replace(":USDT", "")
                        ident_clean = ident.replace("/", "").replace(":USDT", "")
                        if ep_clean == ident_clean or ep_sym == ident:
                            # Found it on exchange — close directly
                            side = (ep.get("side") or "long").upper()
                            close_side = "sell" if side == "LONG" else "buy"
                            entry_price = float(ep.get("entryPrice") or 0)
                            margin = float(ep.get("initialMargin") or ep.get("collateral") or 0)
                            leverage = int(float(ep.get("leverage") or 1))
                            close_params = {"productType": "USDT-FUTURES"}
                            hedge = getattr(self.engine.live_executor, '_hedge_mode', False)
                            if hedge:
                                close_params["tradeSide"] = "close"
                            try:
                                order = await exchange.create_order(
                                    symbol=ep_sym, type="market",
                                    side=close_side, amount=contracts,
                                    params=close_params,
                                )
                                # Get fill price — try order response, then fetch ticker
                                fill_price = float(order.get("average") or order.get("price") or 0)
                                if fill_price <= 0:
                                    try:
                                        ticker = await exchange.fetch_ticker(ep_sym)
                                        fill_price = float(ticker.get("last") or 0)
                                    except Exception:
                                        fill_price = entry_price  # last resort

                                # Calculate PnL
                                if side == "LONG":
                                    gross_pnl = (fill_price - entry_price) * contracts
                                else:
                                    gross_pnl = (entry_price - fill_price) * contracts
                                comm_pct = CONFIG.risk.commission_pct
                                commission = (entry_price * contracts + fill_price * contracts) * (comm_pct / 100.0)
                                net_pnl = gross_pnl - commission

                                # Record trade in closed_trades.json via executor
                                # First, check if this position was already closed by reconciliation
                                # to avoid double-counting with a different trade_id.
                                from datetime import datetime, timezone
                                from bot.core.live_executor import LivePosition
                                already_recorded = False
                                for ct in self.engine.live_executor._closed_trades:
                                    ct_clean = ct.symbol.replace("/", "").replace(":USDT", "")
                                    if ct_clean == ep_clean and ct.direction == side:
                                        # Check if closed within the last 5 minutes
                                        ct_closed = ct.closed_at
                                        if ct_closed and (datetime.now(timezone.utc) - ct_closed).total_seconds() < 300:
                                            already_recorded = True
                                            break
                                if not already_recorded:
                                    ts = ep.get("timestamp")
                                    opened_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)
                                    closed_pos = LivePosition(
                                        trade_id=f"TI-manual-{ep_clean}-{int(datetime.now(timezone.utc).timestamp())}",
                                        symbol=ep_sym,
                                        direction=side,
                                        entry_price=entry_price,
                                        quantity=contracts,
                                        cost_usd=margin,
                                        stop_loss=0,
                                        take_profit=0,
                                        leverage=leverage,
                                        status="closed",
                                        close_price=fill_price,
                                        gross_pnl=round(gross_pnl, 4),
                                        commission=round(commission, 4),
                                        pnl_usd=round(net_pnl, 4),
                                        opened_at=opened_at,
                                        closed_at=datetime.now(timezone.utc),
                                    )
                                    self.engine.live_executor._append_closed_trade(closed_pos)

                                pnl_emoji = "\U0001f7e2" if net_pnl >= 0 else "\U0001f534"
                                lines = [
                                    f"\u2705 <b>{html.escape(ep_clean)} — Position Closed</b>",
                                    "",
                                    f"Entry <code>{entry_price:,.6f}</code> / Exit <code>{fill_price:,.6f}</code>",
                                    f"Size <code>${margin:,.2f}</code> | {leverage}x",
                                    f"{pnl_emoji} Net PnL: <code>${net_pnl:+,.2f}</code> (fees ${commission:.2f})",
                                ]
                                await self._send(update, "\n".join(lines), edit=True)
                                # Remove buttons
                                try:
                                    if update.callback_query and update.callback_query.message:
                                        await update.callback_query.message.edit_reply_markup(reply_markup=None)
                                except Exception:
                                    pass
                            except Exception as e:
                                await self._send(update,
                                    f"Couldn't close {html.escape(ep_clean)} on exchange.\n\n"
                                    f"{html.escape(str(e)[:200])}\n"
                                    "Try closing it on the exchange directly.",
                                    edit=True)
                            live_closed = True
                            break
                except Exception as exc:
                    logger.warning("Exchange direct close fallback failed: %s", exc)

            if live_closed:
                return

            if not live_closed:
                # Paper mode close
                for pos in list(portfolio.open_positions):
                    if pos.asset.replace("/", "").replace(":USDT", "") == pair:
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
                    f"\u2705 <b>{html.escape(pair)}</b> — already closed.\n\n"
                    "Say \"positions\" to see current state.",
                    edit=True)
                # Remove stale buttons
                try:
                    if update.callback_query and update.callback_query.message:
                        await update.callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return

        # ── Legacy pane callbacks (backward compat) ──────────

        if data.startswith("pane:"):
            pane = data.split(":", 1)[1]
            if pane == "refresh":
                pane = self._last_pane.get(self._get_tg_id(update), "status")
            self._last_pane[self._get_tg_id(update)] = pane
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
            self._last_pane[self._get_tg_id(update)] = pane
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

        # ── Scan skill callbacks (scan_confirm: / scan_reject: / scan_limit:) ──
        if data.startswith("scan_confirm:") or data.startswith("scan_reject:") or data.startswith("scan_limit:"):
            await _scan_callback(update, ctx)
            return

        # ── Trade confirm/reject ─────────────────────────────

        # ── Set custom limit price ──
        if data.startswith("setlimit:"):
            parts = data.split(":")
            trade_id = parts[1]
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if expected_uid and not self._uid_matches(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>", edit=True)
                return

            # Look up the idea to show current entry
            idea = self.engine._pending_ideas.get(trade_id)
            if not idea:
                await self._send(update,
                    "<b>Trade expired.</b> Run a new scan.", edit=True)
                return

            pair = display_symbol(idea.asset)
            direction = idea.direction.value if hasattr(idea.direction, 'value') else str(idea.direction)

            # Store that this user is waiting to type a limit price
            if not hasattr(self, '_pending_limit_input'):
                self._pending_limit_input: dict = {}
            self._pending_limit_input[caller_uid] = {
                "trade_id": trade_id,
                "asset": idea.asset,
                "pair": pair,
                "direction": direction,
                "current_entry": idea.entry_price,
                "timestamp": time.time(),
            }

            await self._send(update,
                f"\U0001f4b0 <b>Set limit price for {pair} {direction}</b>\n\n"
                f"Current entry: <code>${idea.entry_price:,.4f}</code>\n"
                f"SL: <code>${idea.stop_loss:,.4f}</code> | TP: <code>${idea.take_profit:,.4f}</code>\n\n"
                f"Type your limit price (e.g. <code>84.07</code> or <code>0.0522</code>):",
                edit=True)
            return

        if data.startswith("confirm:"):
            parts = data.split(":")
            trade_id = parts[1]

            # Double-tap guard: skip if this trade was already confirmed
            if not hasattr(self, '_confirmed_ids'):
                self._confirmed_ids: set[str] = set()
            if trade_id in self._confirmed_ids:
                try:
                    await query.answer("Already confirmed")
                except Exception:
                    pass
                return
            self._confirmed_ids.add(trade_id)
            # Cap the set to prevent unbounded growth
            if len(self._confirmed_ids) > 100:
                self._confirmed_ids = set(list(self._confirmed_ids)[-50:])

            # M3 FIX: validate callback belongs to requesting user.
            # RC-AUD-004: fail-closed. Every legitimate confirm button is built as
            # "confirm:<id>:<uid>" (see button construction sites), so a missing
            # owner tag means a crafted/replayed callback — deny rather than allow.
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if not expected_uid or not self._uid_matches(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who requested this trade can approve it.",
                    edit=True)
                audit(system_log,
                      f"Callback IDOR blocked: caller={caller_uid} expected={expected_uid}",
                      action="callback_idor_block", result="DENIED")
                return

            # H-18 FIX: LIVE mode — check per-user live trading permission
            if CONFIG.is_live() and not self._is_admin(update):
                caller_uid_str = str(update.effective_user.id) if update.effective_user else ""
                if not self.users.can_trade_live(caller_uid_str):
                    await self._send(update,
                        "\U0001f512 <b>Live trading not enabled</b>\n\n"
                        "Ask an admin to grant you live trading access with /grant_live.",
                        edit=True)
                    audit(system_log,
                          f"Non-admin trade confirm blocked: caller={caller_uid_str}",
                          action="admin_gate", result="DENIED")
                    return

            try:
                result = await self.engine.confirm_trade(trade_id, user_id=caller_uid or "")
            except Exception as exc:
                audit(system_log, f"confirm_trade raised: {exc}",
                      action="confirm_trade", result="ERROR")
                await self._send(update,
                    f"\u274c <b>Trade execution failed:</b> {exc}", edit=True)
                return

            # ── Auto re-analyze on price drift ──
            # If price moved since analysis, rebuild the idea at current price and retry once
            if "price drifted" in result.lower() and "re-analyze" in result.lower():
                original_idea = self.engine._last_confirmed_idea
                if original_idea:
                    try:
                        await self._send(update,
                            f"\u26a0\ufe0f <b>Price moved — auto re-analyzing {original_idea.asset}...</b>")
                        exchange = await self.engine.scanner._get_exchange()
                        ticker = await exchange.fetch_ticker(original_idea.asset)
                        new_price = float(ticker.get("last", 0))
                        if new_price > 0:
                            d = original_idea.direction
                            from bot.utils.models import Direction
                            is_long = d == Direction.LONG
                            new_sl = round(new_price * (0.97 if is_long else 1.03), 6)
                            new_tp = round(new_price * (1.06 if is_long else 0.94), 6)
                            from bot.utils.models import TradeIdea
                            new_idea = TradeIdea(
                                asset=original_idea.asset, direction=d,
                                entry_price=new_price, stop_loss=new_sl, take_profit=new_tp,
                                confidence=original_idea.confidence,
                                reasoning=f"Auto re-analyzed after price drift",
                                source="auto_reanalyze")
                            ohlcv = await exchange.fetch_ohlcv(original_idea.asset, "4h", limit=30)
                            h = [c[2] for c in ohlcv]; l = [c[3] for c in ohlcv]; cl = [c[4] for c in ohlcv]
                            import numpy as _np
                            h_a, l_a, c_a = _np.array(h, dtype=float), _np.array(l, dtype=float), _np.array(cl, dtype=float)
                            tr = _np.maximum(h_a[1:] - l_a[1:], _np.maximum(abs(h_a[1:] - c_a[:-1]), abs(l_a[1:] - c_a[:-1])))
                            atr2 = float(_np.mean(tr[-14:])) if len(tr) >= 14 else float(_np.mean(tr))
                            retry_id = new_idea.id
                            self.engine._pending_ideas[retry_id] = new_idea
                            self.engine._pending_atr[retry_id] = atr2
                            result = await self.engine.confirm_trade(retry_id, user_id=caller_uid or "")
                    except Exception as retry_exc:
                        audit(system_log, f"Auto re-analyze failed: {retry_exc}",
                              action="auto_reanalyze", result="ERROR")

            # Detect failure by checking for known error prefixes
            _fail_prefixes = (
                "EXECUTION FAILED:", "INSUFFICIENT FUNDS:", "INVALID ORDER:",
                "BLOCKED:", "PREFLIGHT FAILED:", "Risk re-check FAILED",
                "Trade not found", "not found", "expired", "No pending",
                "Trade REJECTED", "Trade HALTED", "Execution denied",
            )
            # Case-insensitive prefix check: catches both "Trade REJECTED" and
            # "Trade rejected" (post-critique, manual reject, etc.)
            result_lower = result.lower()
            is_failure = any(result_lower.startswith(p.lower()) for p in _fail_prefixes)
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

            # Double-tap guard
            if not hasattr(self, '_confirmed_ids'):
                self._confirmed_ids: set[str] = set()
            if trade_id in self._confirmed_ids:
                try:
                    await query.answer("Already processed")
                except Exception:
                    pass
                return
            self._confirmed_ids.add(trade_id)

            # M3 FIX: validate callback belongs to requesting user.
            # RC-AUD-004: fail-closed — a missing owner tag means a crafted
            # callback (legitimate buttons are always "reject:<id>:<uid>").
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if not expected_uid or not self._uid_matches(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who requested this trade can reject it.",
                    edit=True)
                audit(system_log,
                      f"Callback IDOR blocked: caller={caller_uid} expected={expected_uid}",
                      action="callback_idor_block", result="DENIED")
                return
            try:
                result = self.engine.reject_trade(trade_id)
            except Exception as exc:
                audit(system_log, f"reject_trade raised: {exc}",
                      action="reject_trade", result="ERROR")
                await self._send(update,
                    f"\u274c <b>Trade execution failed:</b> {exc}", edit=True)
                return
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
