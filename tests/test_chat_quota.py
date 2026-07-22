"""Free-tier chat quota — N questions/day, then upgrade prompt.

Locks the spend fence around the operator-funded Grok chat budget: free (basic)
users get a small daily allowance and are then refused (upgrade prompt) without an
LLM call; paid tiers and admin are never limited; the count is per UTC day and
resets on the day boundary; and a write error never crashes the gate.
"""

import inspect

import pytest

from bot.web import chat_quota


@pytest.fixture()
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_quota, "_STORE_PATH", tmp_path / "quota.json")
    monkeypatch.delenv("FREE_CHAT_DAILY_LIMIT", raising=False)
    # The quota is dormant unless the Grok budget it protects is funded — force it
    # ON for these enforcement tests (production activates it via XAI_API_KEY).
    monkeypatch.setenv("FREE_CHAT_QUOTA_ENABLED", "1")
    return tmp_path


def test_quota_dormant_without_funded_grok_budget(monkeypatch, tmp_path):
    # No XAI_API_KEY and no explicit enable → the cap is OFF (nothing to protect);
    # free chat falls back to the genuinely-free tiers, uncapped.
    monkeypatch.setattr(chat_quota, "_STORE_PATH", tmp_path / "q.json")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("FREE_CHAT_QUOTA_ENABLED", raising=False)
    assert chat_quota.quota_enabled() is False
    for _ in range(20):
        assert chat_quota.consume("web:x", "basic")["allowed"] is True


def test_quota_activates_when_grok_key_present(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_quota, "_STORE_PATH", tmp_path / "q.json")
    monkeypatch.delenv("FREE_CHAT_QUOTA_ENABLED", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "xai-abc")
    assert chat_quota.quota_enabled() is True
    # explicit off still wins over key presence.
    monkeypatch.setenv("FREE_CHAT_QUOTA_ENABLED", "off")
    assert chat_quota.quota_enabled() is False


def test_free_user_gets_exactly_the_limit_then_refused(isolated_store):
    uid = "web:1"
    lim = chat_quota.free_daily_limit()
    assert lim == 5                              # documented default
    for i in range(lim):
        r = chat_quota.consume(uid, "basic")
        assert r["allowed"] is True
        assert r["remaining"] == lim - (i + 1)
    # the (lim+1)-th question is refused, and nothing further is counted.
    r = chat_quota.consume(uid, "basic")
    assert r["allowed"] is False and r["remaining"] == 0
    assert chat_quota.consume(uid, "basic")["allowed"] is False


def test_paid_and_admin_tiers_are_exempt(isolated_store):
    for tier in ("pro", "elite", "admin", "premium"):
        for _ in range(50):                      # far past any free limit
            r = chat_quota.consume("web:2", tier)
            assert r["allowed"] is True and r["exempt"] is True
    # exemption never writes a count, so a later downgrade starts fresh.
    assert chat_quota.status("web:2", "basic")["used"] == 0


def test_status_does_not_consume(isolated_store):
    uid = "web:3"
    for _ in range(3):
        assert chat_quota.status(uid, "basic")["used"] == 0
    chat_quota.consume(uid, "basic")
    assert chat_quota.status(uid, "basic")["used"] == 1
    assert chat_quota.status(uid, "basic")["remaining"] == 4


def test_day_rollover_resets_the_count(isolated_store, monkeypatch):
    uid = "web:4"
    monkeypatch.setattr(chat_quota, "_today", lambda: "2026-07-22")
    for _ in range(5):
        chat_quota.consume(uid, "basic")
    assert chat_quota.consume(uid, "basic")["allowed"] is False
    # next UTC day → fresh allowance.
    monkeypatch.setattr(chat_quota, "_today", lambda: "2026-07-23")
    r = chat_quota.consume(uid, "basic")
    assert r["allowed"] is True and r["used"] == 1


def test_limit_is_env_overridable(isolated_store, monkeypatch):
    monkeypatch.setenv("FREE_CHAT_DAILY_LIMIT", "2")
    uid = "web:5"
    assert chat_quota.consume(uid, "basic")["allowed"] is True
    assert chat_quota.consume(uid, "basic")["allowed"] is True
    assert chat_quota.consume(uid, "basic")["allowed"] is False


def test_write_failure_never_raises(isolated_store, monkeypatch):
    # a real disk failure inside _save must be swallowed — the gate never crashes
    # chat. Patch the underlying atomic replace so _save's own guard is exercised.
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(chat_quota.os, "replace", boom)
    r = chat_quota.consume("web:6", "basic")     # must not raise
    assert "allowed" in r


def test_handler_gates_free_chat_and_is_quota_aware():
    from bot.web import user_gateway
    src = inspect.getsource(user_gateway.handle_chat)
    assert "chat_quota" in src                    # the gate is wired in
    assert "quota_exceeded" in src                # refusal intent
    assert "get_tier" in src                      # tier drives exemption
