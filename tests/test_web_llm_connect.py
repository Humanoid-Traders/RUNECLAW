"""WEB-1: connect the LLM from the website (the primary surface).

Pins the trust model of the /gateway/llm endpoints:
  * same _guard_user + shared-secret gate as every other endpoint
  * the key goes into the bot's Fernet-encrypted user_settings store and the
    response only ever carries a FINGERPRINT — never the key
  * provider allowlist (no local/keyless providers from the web) and the
    same key-format sanity checks as Telegram /setllm
  * web-only identities ("web:5") map to the negative id space (-5) so they
    can never collide with a Telegram user's settings row
  * ULTRA toggle is ADMIN-only and fails loud without an Anthropic key
  * the chat fallback chain tries the caller's OWN key first (source pin)
"""

from __future__ import annotations

import contextlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet

from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}
GEMINI_KEY = "AIza" + "x" * 32


class FakeUsers:
    def __init__(self, users=None):
        self._users = dict(users or {})

    def get(self, tg):
        return self._users.get(str(tg))

    def register(self, tg, name="", auto_role=""):
        return self._users.setdefault(
            str(tg), {"authorized": True, "role": "trader"})

    def has_permission(self, tg, cmd):
        return True


class FakeHandler:
    def __init__(self, users=None):
        self.users = FakeUsers(users)
        self._limiter = SimpleNamespace(allow=lambda uid: True)

    def _allowlist_ids(self):
        return set()


class FakeEngine:
    _pending_ideas: dict = {}


AUTHED = {"7": {"authorized": True, "role": "trader"},
          "9": {"authorized": True, "role": "admin"}}


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
    import bot.db.models as m
    monkeypatch.setattr(m, "DB_PATH", Path(tmp_path / "t.db"))
    monkeypatch.setattr(m, "_LLM_CIPHER", None)
    m.init_db()
    return m


@contextlib.asynccontextmanager
async def client_for(handler):
    app = ug.build_gateway(FakeEngine(), handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


# ── identity mapping ─────────────────────────────────────────────────────────

def test_settings_user_id_maps_both_id_spaces():
    from bot.db.models import settings_user_id
    assert settings_user_id("12345") == 12345
    assert settings_user_id("web:5") == -5
    assert settings_user_id("web:x") is None
    assert settings_user_id("") is None
    assert settings_user_id("evil:1") is None


# ── status / set / clear ─────────────────────────────────────────────────────

async def test_status_lists_providers_and_no_key(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.get("/llm?telegram_id=7", headers=HDRS)
        assert r.status == 200
        d = await r.json()
        assert d["connected"] is False
        assert d["is_admin"] is False
        assert "ultra" not in d          # ultra state is admin-only info
        ids = [p["id"] for p in d["providers"]]
        assert "gemini" in ids and "anthropic" in ids
        # Local/keyless providers are not connectable from the web.
        assert "ollama" not in ids and "runeclaw" not in ids and "custom" not in ids
        # AI-6: each provider carries a human cost/speed hint so the BYOK panel
        # can show roughly what a user's own key will cost before they connect.
        gem = next(p for p in d["providers"] if p["id"] == "gemini")
        assert gem["cost_label"] == "very low cost"
        assert gem["speed_label"] and gem["free_tier"] is True
        oai = next(p for p in d["providers"] if p["id"] == "openai")
        assert oai["cost_label"] == "premium"


async def test_set_key_stores_encrypted_and_returns_fingerprint_only(
        monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/llm", headers=HDRS, json={
            "telegram_id": "7", "provider": "gemini", "api_key": GEMINI_KEY})
        assert r.status == 200
        d = await r.json()
        assert d["connected"] is True and d["provider"] == "gemini"
        assert GEMINI_KEY not in str(d), "the key must never be echoed back"
        assert d["fingerprint"].startswith(GEMINI_KEY[:6])
        # Encrypted at rest: raw DB cell must not contain the plaintext key.
        import sqlite3
        con = sqlite3.connect(isolated_db.DB_PATH)
        stored = con.execute(
            "SELECT llm_api_key FROM user_settings WHERE user_id=7").fetchone()[0]
        con.close()
        assert stored and GEMINI_KEY not in stored
        # And the status endpoint now reports connected.
        r2 = await c.get("/llm?telegram_id=7", headers=HDRS)
        assert (await r2.json())["connected"] is True


async def test_web_only_identity_uses_negative_id_space(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/llm", headers=HDRS, json={
            "telegram_id": "web:5", "provider": "gemini", "api_key": GEMINI_KEY})
        assert r.status == 200
    s = isolated_db.get_user_settings(-5)
    assert s.llm_api_key == GEMINI_KEY
    # The analyzer-side resolver reads the same row for the same identity.
    from bot.core.analyzer import Analyzer
    cfg = Analyzer._resolve_user_llm_config("web:5")
    assert cfg is not None and cfg.api_key == GEMINI_KEY


async def test_bad_provider_and_bad_key_format_rejected(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/llm", headers=HDRS, json={
            "telegram_id": "7", "provider": "ollama", "api_key": "x" * 30})
        assert r.status == 400
        # Only the obviously-broken is refused now — a key with embedded
        # whitespace (a mangled copy/paste), not a per-provider prefix guess.
        r = await c.post("/llm", headers=HDRS, json={
            "telegram_id": "7", "provider": "gemini", "api_key": "AIza with a space"})
        assert r.status == 400
        # …and a too-short token.
        r = await c.post("/llm", headers=HDRS, json={
            "telegram_id": "7", "provider": "gemini", "api_key": "short"})
        assert r.status == 400
        # Nothing was stored on any refusal.
        assert isolated_db.get_user_settings(7).llm_api_key == ""


async def test_valid_keys_of_any_plausible_shape_are_accepted(monkeypatch, isolated_db):
    # Provider key formats drift (OpenAI sk-proj-…, Gemini keys without AIza,
    # OpenRouter sk-or-v1-…, bare Mistral tokens). The connect flow must accept
    # any plausible opaque token and let the live provider call be the arbiter —
    # a stale prefix regex here only false-rejects valid keys (the reported bug).
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    cases = [
        ("openai", "sk-proj-" + "A1b2C3d4" * 8),        # modern project key
        ("gemini", "ya29." + "Z" * 40),                  # non-AIza Google token
        ("mistral", "b" * 32),                           # bare 32-char token
        ("openrouter", "sk-or-v1-" + "0" * 40),          # OpenRouter shape
    ]
    async with client_for(FakeHandler(AUTHED)) as c:
        for prov, key in cases:
            r = await c.post("/llm", headers=HDRS, json={
                "telegram_id": "7", "provider": prov, "api_key": key})
            assert r.status == 200, f"{prov} key of shape {key[:10]}… was rejected"
            # each overwrites the same row — the stored key is the one just sent.
            assert isolated_db.get_user_settings(7).llm_api_key == key


async def test_clear_disconnects(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        await c.post("/llm", headers=HDRS, json={
            "telegram_id": "7", "provider": "gemini", "api_key": GEMINI_KEY})
        r = await c.post("/llm/clear", headers=HDRS, json={"telegram_id": "7"})
        assert r.status == 200 and (await r.json())["connected"] is False
    assert isolated_db.get_user_settings(7).llm_api_key == ""


async def test_gateway_secret_required(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.get("/llm?telegram_id=7")          # no secret header
        assert r.status == 403


# ── ULTRA toggle (admin-only) ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_ultra():
    yield
    from bot.llm.provider import set_ultra_mode
    set_ultra_mode(False)


async def test_ultra_is_admin_only(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/llm/ultra", headers=HDRS,
                         json={"telegram_id": "7", "enabled": True})
        assert r.status == 403


async def test_ultra_admin_toggle_roundtrip(monkeypatch, isolated_db):
    from bot.llm.provider import is_ultra_mode
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-web-ultra-" + "0" * 24)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/llm/ultra", headers=HDRS,
                         json={"telegram_id": "9", "enabled": True})
        assert r.status == 200
        d = await r.json()
        assert d["ok"] is True and d["ultra"] is True and is_ultra_mode()
        # Admin status now reports the ultra state.
        st = await (await c.get("/llm?telegram_id=9", headers=HDRS)).json()
        assert st["is_admin"] is True and st["ultra"] is True
        r = await c.post("/llm/ultra", headers=HDRS,
                         json={"telegram_id": "9", "enabled": False})
        assert (await r.json())["ultra"] is False and not is_ultra_mode()


async def test_ultra_fails_loud_without_anthropic_key(monkeypatch, isolated_db):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with client_for(FakeHandler(AUTHED)) as c:
        r = await c.post("/llm/ultra", headers=HDRS,
                         json={"telegram_id": "9", "enabled": True})
        assert r.status == 400
        assert (await r.json())["ok"] is False


# ── chat uses the caller's own key first ─────────────────────────────────────

def test_chat_fallback_chain_tries_own_key_first():
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._llm_chat)
    own = src.find('"own_key"')
    chat_tier = src.find('"chat_tier"')
    assert 0 < own < chat_tier, "the caller's own key must be tried before tier routing"
    # Never for anonymous public chat.
    assert "not public" in src[max(0, own - 600):own]
