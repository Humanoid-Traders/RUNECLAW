"""AI-4: live, CITED web research enrichment for a coin dossier.

Pins the trust model of POST /gateway/research/web:
  * same _guard_user + shared-secret gate as every endpoint
  * ADMIN-ONLY — real-time web_search bills the operator's Anthropic key, so a
    non-admin caller is refused (403) and never reaches the LLM
  * the admin path returns the cited synthesis + model transparency + a §4
    advisory disclaimer; a bad symbol is a 400, an LLM failure a soft 502
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}


class FakeUsers:
    def __init__(self, users):
        self._users = dict(users)

    def get(self, tg):
        return self._users.get(str(tg))

    def register(self, tg, name="", auto_role=""):
        return self._users.setdefault(str(tg), {"authorized": True, "role": "trader"})

    def has_permission(self, tg, cmd):
        return True


class FakeHandler:
    """Records the _llm_chat call so we can prove the prompt + admin flag."""

    def __init__(self, users):
        self.users = FakeUsers(users)
        self._limiter = SimpleNamespace(allow=lambda uid: True)
        self.calls: list = []

    def _allowlist_ids(self):
        return set()

    async def _llm_chat(self, prompt, user_id="", is_admin=False,
                        return_meta=False, **kw):
        self.calls.append({"prompt": prompt, "user_id": user_id,
                           "is_admin": is_admin})
        answer = ("• PENDLE launched v3 on 2026-07-01 — https://pendle.finance\n"
                  "🔎 Live web sources:\n• Pendle docs — https://docs.pendle.finance")
        return (answer, {"model": "claude-opus-4-8", "provider": "anthropic"}) \
            if return_meta else answer


class FakeEngine:
    _pending_ideas: dict = {}


AUTHED = {"7": {"authorized": True, "role": "trader"},
          "9": {"authorized": True, "role": "admin"}}


@contextlib.asynccontextmanager
async def client_for(handler):
    app = ug.build_gateway(FakeEngine(), handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)


@pytest.mark.asyncio
async def test_admin_gets_cited_synthesis():
    handler = FakeHandler(AUTHED)
    async with client_for(handler) as c:
        r = await c.post("/research/web",
                         json={"telegram_id": "9", "base": "pendle"}, headers=HDRS)
        assert r.status == 200
        d = await r.json()
        assert d["read_only"] is True
        assert d["base"] == "PENDLE"                 # normalised upper-case
        assert "Live web sources" in d["web_html"]   # citations rode through
        assert d["model"] == "claude-opus-4-8"
        assert "not financial advice" in d["disclaimer"].lower()
    # the LLM was asked WITH web search enabled (is_admin=True), for this coin
    assert handler.calls and handler.calls[0]["is_admin"] is True
    assert "PENDLE" in handler.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_non_admin_is_refused_and_never_hits_the_llm():
    handler = FakeHandler(AUTHED)
    async with client_for(handler) as c:
        r = await c.post("/research/web",
                         json={"telegram_id": "7", "base": "PENDLE"}, headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "admin_only"
    assert handler.calls == []          # a non-admin never spends the AI key


@pytest.mark.asyncio
async def test_missing_base_is_400():
    handler = FakeHandler(AUTHED)
    async with client_for(handler) as c:
        r = await c.post("/research/web",
                         json={"telegram_id": "9", "base": ""}, headers=HDRS)
        assert r.status == 400


@pytest.mark.asyncio
async def test_unauthorized_id_is_guarded():
    handler = FakeHandler(AUTHED)
    async with client_for(handler) as c:
        r = await c.post("/research/web",
                         json={"telegram_id": "404", "base": "BTC"}, headers=HDRS)
        assert r.status in (401, 403)
        assert handler.calls == []
