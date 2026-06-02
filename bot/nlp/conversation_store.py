"""
RUNECLAW Conversation Store — per-user multi-turn memory.

Stores recent messages per user for multi-turn LLM context injection.
In-memory with optional JSONL persistence. Thread-safe.

Design constraints:
  - Max messages per user (default 50) — older messages are pruned
  - Max total users tracked (default 200) — LRU eviction
  - Messages include role, content, timestamp
  - Conversation summarization for long histories
  - No secrets stored — only user text + assistant replies
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.compat import UTC


@dataclass
class Message:
    """A single conversation message."""
    role: str           # "user" or "assistant"
    content: str        # Message text
    timestamp: float    # Unix timestamp
    metadata: dict = field(default_factory=dict)  # Optional: intent, symbol, etc.

    def to_llm_message(self) -> dict:
        """Convert to LLM API message format."""
        return {"role": self.role, "content": self.content}

    def age_seconds(self) -> float:
        return time.time() - self.timestamp


@dataclass
class UserContext:
    """Accumulated context about a user from their conversations."""
    preferred_assets: list[str] = field(default_factory=list)
    last_discussed_asset: str = ""
    interaction_count: int = 0
    first_seen: float = 0.0
    last_active: float = 0.0
    summary: str = ""  # Compressed summary of older conversations

    def update_from_message(self, text: str) -> None:
        """Extract context signals from a user message."""
        from bot.nlp.intent_router import _extract_symbol
        self.interaction_count += 1
        self.last_active = time.time()
        if not self.first_seen:
            self.first_seen = time.time()

        # Track discussed assets
        symbol = _extract_symbol(text)
        if symbol:
            self.last_discussed_asset = symbol
            ticker = symbol.replace("/USDT", "")
            if ticker not in self.preferred_assets:
                self.preferred_assets.append(ticker)
                # Keep only last 10 preferred assets
                if len(self.preferred_assets) > 10:
                    self.preferred_assets = self.preferred_assets[-10:]


class ConversationStore:
    """Per-user conversation memory with LRU eviction.

    Usage:
        store = ConversationStore()
        store.append("12345", "user", "How's BTC doing?")
        store.append("12345", "assistant", "BTC is at $67,000...")
        history = store.get_recent("12345", limit=10)
        context = store.get_context("12345")
    """

    def __init__(
        self,
        max_messages_per_user: int = 50,
        max_users: int = 200,
        persist_path: Optional[str | Path] = None,
        context_window: int = 10,
    ) -> None:
        self._max_messages = max_messages_per_user
        self._max_users = max_users
        self._context_window = context_window  # Default messages to inject
        self._lock = threading.Lock()
        # OrderedDict for LRU eviction
        self._conversations: OrderedDict[str, list[Message]] = OrderedDict()
        self._user_contexts: dict[str, UserContext] = {}
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path:
            self._load()

    def append(self, user_id: str, role: str, content: str,
               metadata: Optional[dict] = None) -> None:
        """Append a message to a user's conversation history."""
        if not content or not content.strip():
            return

        msg = Message(
            role=role,
            content=content.strip(),
            timestamp=time.time(),
            metadata=metadata or {},
        )

        with self._lock:
            # Move user to end (most recently active)
            if user_id in self._conversations:
                self._conversations.move_to_end(user_id)
            else:
                self._conversations[user_id] = []

            self._conversations[user_id].append(msg)

            # Prune oldest messages if over limit
            if len(self._conversations[user_id]) > self._max_messages:
                self._conversations[user_id] = \
                    self._conversations[user_id][-self._max_messages:]

            # LRU eviction of oldest users
            while len(self._conversations) > self._max_users:
                self._conversations.popitem(last=False)

            # Update user context
            if user_id not in self._user_contexts:
                self._user_contexts[user_id] = UserContext()
            if role == "user":
                self._user_contexts[user_id].update_from_message(content)

        if self._persist_path:
            self._persist_message(user_id, msg)

    def get_recent(self, user_id: str, limit: Optional[int] = None) -> list[Message]:
        """Get recent messages for a user."""
        limit = limit or self._context_window
        with self._lock:
            msgs = self._conversations.get(user_id, [])
            return list(msgs[-limit:])

    def get_recent_as_llm_messages(self, user_id: str,
                                    limit: Optional[int] = None) -> list[dict]:
        """Get recent messages formatted for LLM API (list of {role, content})."""
        return [m.to_llm_message() for m in self.get_recent(user_id, limit)]

    def get_context(self, user_id: str) -> Optional[UserContext]:
        """Get accumulated user context."""
        with self._lock:
            return self._user_contexts.get(user_id)

    def clear_user(self, user_id: str) -> None:
        """Clear all conversation history for a user."""
        with self._lock:
            self._conversations.pop(user_id, None)
            self._user_contexts.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all conversation data."""
        with self._lock:
            self._conversations.clear()
            self._user_contexts.clear()

    def user_count(self) -> int:
        """Number of users with conversation history."""
        with self._lock:
            return len(self._conversations)

    def message_count(self, user_id: str) -> int:
        """Number of stored messages for a user."""
        with self._lock:
            return len(self._conversations.get(user_id, []))

    def build_context_prompt(self, user_id: str, portfolio_summary: str = "",
                              engine_state: str = "") -> str:
        """Build a context block to inject into the system prompt.

        Returns a string with user-specific context that makes the
        conversation feel continuous and personalized.
        """
        ctx = self.get_context(user_id)
        if not ctx:
            return ""

        parts = []

        if ctx.last_discussed_asset:
            parts.append(
                f"Last discussed asset: {ctx.last_discussed_asset}")
        if ctx.preferred_assets:
            assets = ", ".join(ctx.preferred_assets[-5:])
            parts.append(f"User's frequently discussed assets: {assets}")
        if ctx.interaction_count > 1:
            parts.append(
                f"This user has sent {ctx.interaction_count} messages "
                f"(returning user).")
        if portfolio_summary:
            parts.append(f"Current portfolio: {portfolio_summary}")
        if engine_state:
            parts.append(f"Engine state: {engine_state}")
        if ctx.summary:
            parts.append(f"Previous conversation summary: {ctx.summary}")

        if not parts:
            return ""

        return "\n\nUser context:\n" + "\n".join(f"- {p}" for p in parts)

    # ── Persistence ──────────────────────────────────────────────

    def _persist_message(self, user_id: str, msg: Message) -> None:
        """Append a message to JSONL file."""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "user_id": user_id,
                "role": msg.role,
                "content": msg.content[:2000],  # Cap stored content
                "timestamp": msg.timestamp,
                "metadata": msg.metadata,
            }
            with open(self._persist_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Non-critical — memory store is primary

    def _load(self) -> None:
        """Load conversation history from JSONL file."""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        uid = entry["user_id"]
                        msg = Message(
                            role=entry["role"],
                            content=entry["content"],
                            timestamp=entry.get("timestamp", 0),
                            metadata=entry.get("metadata", {}),
                        )
                        if uid not in self._conversations:
                            self._conversations[uid] = []
                        self._conversations[uid].append(msg)

                        if uid not in self._user_contexts:
                            self._user_contexts[uid] = UserContext()
                        if msg.role == "user":
                            self._user_contexts[uid].update_from_message(
                                msg.content)
                    except (KeyError, json.JSONDecodeError):
                        continue

            # Prune loaded data to limits
            for uid in list(self._conversations.keys()):
                if len(self._conversations[uid]) > self._max_messages:
                    self._conversations[uid] = \
                        self._conversations[uid][-self._max_messages:]
        except OSError:
            pass

    def stats(self) -> dict:
        """Return store statistics."""
        with self._lock:
            total_msgs = sum(
                len(msgs) for msgs in self._conversations.values())
            return {
                "users": len(self._conversations),
                "total_messages": total_msgs,
                "max_users": self._max_users,
                "max_messages_per_user": self._max_messages,
                "context_window": self._context_window,
            }
