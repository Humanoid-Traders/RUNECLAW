"""
RUNECLAW Channel Forwarder — auto-post signals, results, and reports
to a public Telegram group/channel.

Posts are cleaned up: no confirm/reject buttons, no internal IDs,
no sensitive data. Public-facing marketing content only.
"""

from __future__ import annotations

import html
import json
import threading
from datetime import datetime
from pathlib import Path

from bot.compat import UTC
from bot.utils.logger import audit, system_log

# Persistent config file for group chat IDs
_CONFIG_PATH = Path("data/channel_config.json")


class ChannelForwarder:
    """Forwards cleaned-up bot content to public Telegram groups/channels."""

    def __init__(self) -> None:
        self._bot = None
        self._group_ids: set[int] = set()
        self._lock = threading.Lock()
        self._enabled = True
        self._load_config()

    # ── Config persistence ────────────────────────────────────

    def _load_config(self) -> None:
        if _CONFIG_PATH.exists():
            try:
                with open(_CONFIG_PATH) as f:
                    data = json.load(f)
                self._group_ids = set(data.get("group_ids", []))
                self._enabled = data.get("enabled", True)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_config(self) -> None:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CONFIG_PATH.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({
                "group_ids": list(self._group_ids),
                "enabled": self._enabled,
            }, f, indent=2)
        tmp.rename(_CONFIG_PATH)

    # ── Setup ─────────────────────────────────────────────────

    def set_bot(self, bot) -> None:
        """Set the Telegram bot instance (called from start_monitor)."""
        self._bot = bot

    def add_group(self, chat_id: int) -> None:
        """Register a group/channel for auto-posting."""
        with self._lock:
            self._group_ids.add(chat_id)
            self._save_config()
        audit(system_log, f"Marketing group added: {chat_id}",
              action="channel_add", result="OK")

    def remove_group(self, chat_id: int) -> None:
        """Remove a group from auto-posting."""
        with self._lock:
            self._group_ids.discard(chat_id)
            self._save_config()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
            self._save_config()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def group_count(self) -> int:
        return len(self._group_ids)

    @property
    def group_ids(self) -> list[int]:
        return list(self._group_ids)

    # ── Auto-detect group from message ────────────────────────

    def detect_group(self, chat_id: int, chat_type: str, chat_title: str = "") -> bool:
        """Auto-detect and register a group when the bot receives a message.
        Returns True if a new group was detected."""
        if chat_type not in ("group", "supergroup", "channel"):
            return False
        if chat_id in self._group_ids:
            return False
        self.add_group(chat_id)
        system_log.info("Auto-detected marketing group: %s (%s)", chat_title, chat_id)
        return True

    # ── Post methods ──────────────────────────────────────────

    async def _post(self, text: str, disable_notification: bool = False) -> None:
        """Post to all registered groups."""
        if not self._bot or not self._enabled or not self._group_ids:
            return
        for gid in list(self._group_ids):
            try:
                await self._bot.send_message(
                    chat_id=gid, text=text, parse_mode="HTML",
                    disable_notification=disable_notification,
                    disable_web_page_preview=True)
            except Exception as exc:
                system_log.debug("Channel post to %s failed: %s", gid, exc)

    async def post_signal(self, idea) -> None:
        """Post a new trade signal to the group (no buttons)."""
        if not self._enabled or not self._group_ids:
            return
        try:
            d = "\U0001f7e2 LONG" if idea.direction.value == "LONG" else "\U0001f534 SHORT"
            asset = idea.asset
            risk_amt = abs(idea.entry_price - idea.stop_loss)
            reward_amt = abs(idea.take_profit - idea.entry_price)
            rr = reward_amt / risk_amt if risk_amt > 0 else 0
            sl_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
            tp_pct = abs(idea.take_profit - idea.entry_price) / idea.entry_price * 100
            now = datetime.now(UTC).strftime("%H:%M UTC")

            _sep = "\u2500" * 18
            msg = (
                f"\U0001f4e1 <b>RUNECLAW SIGNAL</b>\n"
                f"{_sep}\n\n"
                f"{d} <b>{asset}</b>\n\n"
                f"Entry: <code>${idea.entry_price:,.4f}</code>\n"
                f"Stop Loss: <code>${idea.stop_loss:,.4f}</code> ({sl_pct:.1f}%)\n"
                f"Take Profit: <code>${idea.take_profit:,.4f}</code> ({tp_pct:.1f}%)\n"
                f"R:R: <code>{rr:.1f}x</code>\n"
                f"Confidence: <code>{idea.confidence:.0%}</code>\n\n"
                f"{_sep}\n"
                f"\U0001f916 AI-generated signal | {now}\n"
                f"#RUNECLAW #{asset.split('/')[0] if '/' in asset else asset}"
            )
            await self._post(msg)
        except Exception as exc:
            system_log.debug("post_signal error: %s", exc)

    async def post_trade_opened(self, idea, mode: str = "PAPER") -> None:
        """Post when a trade is confirmed and opened."""
        if not self._enabled or not self._group_ids:
            return
        try:
            d = "\U0001f7e2 LONG" if idea.direction.value == "LONG" else "\U0001f534 SHORT"
            asset = idea.asset
            now = datetime.now(UTC).strftime("%H:%M UTC")
            mode_icon = "\U0001f525" if mode == "LIVE" else "\U0001f4dd"

            _sep = "\u2500" * 18
            msg = (
                f"\u2705 <b>TRADE OPENED</b>\n"
                f"{_sep}\n\n"
                f"{d} <b>{asset}</b> | {mode_icon} {mode}\n\n"
                f"Entry: <code>${idea.entry_price:,.4f}</code>\n"
                f"Stop Loss: <code>${idea.stop_loss:,.4f}</code>\n"
                f"Take Profit: <code>${idea.take_profit:,.4f}</code>\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW | {now}\n"
                f"#RUNECLAW #{asset.split('/')[0] if '/' in asset else asset}"
            )
            await self._post(msg)
        except Exception as exc:
            system_log.debug("post_trade_opened error: %s", exc)

    async def post_trade_closed(self, close_msg: str) -> None:
        """Post a trade close result to the group."""
        if not self._enabled or not self._group_ids:
            return
        try:
            lines = close_msg.strip().split("\n")
            is_win = "+$" in close_msg or "+" in close_msg.split("PnL")[-1] if "PnL" in close_msg else False
            emoji = "\U0001f3c6" if is_win else "\U0001f4c9"
            now = datetime.now(UTC).strftime("%H:%M UTC")

            _sep = "\u2500" * 18
            body = "\n".join(html.escape(line) for line in lines)
            msg = (
                f"{emoji} <b>TRADE CLOSED</b>\n"
                f"{_sep}\n\n"
                f"{body}\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW | {now}\n"
                f"#RUNECLAW #TradeResult"
            )
            await self._post(msg)
        except Exception as exc:
            system_log.debug("post_trade_closed error: %s", exc)

    async def post_daily_report(self, report_text: str) -> None:
        """Post a daily performance summary."""
        if not self._enabled or not self._group_ids:
            return
        try:
            now = datetime.now(UTC).strftime("%Y-%m-%d")
            _sep = "\u2500" * 18
            msg = (
                f"\U0001f4ca <b>DAILY REPORT \u2014 {now}</b>\n"
                f"{_sep}\n\n"
                f"{report_text}\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW AI Trading\n"
                f"#RUNECLAW #DailyReport"
            )
            await self._post(msg)
        except Exception as exc:
            system_log.debug("post_daily_report error: %s", exc)

    async def post_scan_result(self, scan_text: str) -> None:
        """Post a market scan summary."""
        if not self._enabled or not self._group_ids:
            return
        try:
            now = datetime.now(UTC).strftime("%H:%M UTC")
            _sep = "\u2500" * 18
            msg = (
                f"\U0001f50d <b>MARKET SCAN</b>\n"
                f"{_sep}\n\n"
                f"{scan_text}\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW | {now}\n"
                f"#RUNECLAW #MarketScan"
            )
            await self._post(msg, disable_notification=True)
        except Exception as exc:
            system_log.debug("post_scan_result error: %s", exc)

    async def post_custom(self, text: str) -> None:
        """Post custom content (admin command)."""
        await self._post(text)
