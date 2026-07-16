"""Conversation JSONL compaction.

The persist file was append-only and never pruned — a long-lived bot
re-parsed an ever-growing history on every restart while keeping only
max_messages per user in memory. On load, once the file outgrows the
threshold, it is rewritten from the retained in-memory state.
"""

from __future__ import annotations

from bot.nlp.conversation_store import ConversationStore


def _lines(path):
    with open(path) as f:
        return [ln for ln in f if ln.strip()]


def test_compacts_oversized_file_on_load(tmp_path, monkeypatch):
    path = tmp_path / "conv.jsonl"
    monkeypatch.setattr(ConversationStore, "COMPACT_THRESHOLD_LINES", 100)

    store = ConversationStore(persist_path=str(path), max_messages_per_user=10)
    for i in range(150):
        store.append("u1", "user", f"message {i}")
    assert len(_lines(path)) == 150            # append-only while running

    reloaded = ConversationStore(persist_path=str(path), max_messages_per_user=10)
    kept = _lines(path)
    assert len(kept) == 10                     # rewritten from retained state
    recent = reloaded.get_recent("u1", limit=10)
    assert recent[-1].content == "message 149" # newest survive
    assert recent[0].content == "message 140"


def test_small_file_left_untouched(tmp_path, monkeypatch):
    path = tmp_path / "conv.jsonl"
    monkeypatch.setattr(ConversationStore, "COMPACT_THRESHOLD_LINES", 100)
    store = ConversationStore(persist_path=str(path), max_messages_per_user=10)
    for i in range(5):
        store.append("u1", "user", f"m{i}")
    before = _lines(path)
    ConversationStore(persist_path=str(path), max_messages_per_user=10)
    assert _lines(path) == before              # under threshold: no rewrite


def test_compaction_preserves_multiple_users(tmp_path, monkeypatch):
    path = tmp_path / "conv.jsonl"
    monkeypatch.setattr(ConversationStore, "COMPACT_THRESHOLD_LINES", 50)
    store = ConversationStore(persist_path=str(path), max_messages_per_user=5)
    for i in range(40):
        store.append("alice", "user", f"a{i}")
        store.append("bob", "user", f"b{i}")
    reloaded = ConversationStore(persist_path=str(path), max_messages_per_user=5)
    assert reloaded.get_recent("alice", limit=5)[-1].content == "a39"
    assert reloaded.get_recent("bob", limit=5)[-1].content == "b39"
    assert len(_lines(path)) == 10             # 5 per user retained
