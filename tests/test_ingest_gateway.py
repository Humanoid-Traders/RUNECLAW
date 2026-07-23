"""NEWS-3: personal ingest (share-to-agent) — store + gateway trust model.

Pins that shared notes are PRIVATE per user (a caller can only ever touch their
own), encrypted at rest, bounded, and that the gateway save→list→delete round
trips return previews only — never leaking one user's text to another.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet

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
    def __init__(self, users):
        self.users = FakeUsers(users)
        self._limiter = SimpleNamespace(allow=lambda uid: True)

    def _allowlist_ids(self):
        return set()


class FakeEngine:
    _pending_ideas: dict = {}


AUTHED = {"7": {"authorized": True, "role": "trader"},
          "8": {"authorized": True, "role": "trader"}}


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
    import bot.db.models as m
    monkeypatch.setattr(m, "DB_PATH", Path(tmp_path / "t.db"))
    monkeypatch.setattr(m, "_LLM_CIPHER", None)
    m.init_db()
    return m


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)


@contextlib.asynccontextmanager
async def client_for(handler):
    app = ug.build_gateway(FakeEngine(), handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


# ── pure store ───────────────────────────────────────────────────────────────

class TestStore:
    def test_encrypted_at_rest_and_round_trip(self, isolated_db):
        m = isolated_db
        uid = m.create_user("a@x.io", "password12")
        nid = m.add_user_ingest_note(uid, "Newsletter", "BTC ETF record inflows", "Bankless")
        assert nid
        got = m.list_user_ingest_notes(uid)
        assert got[0]["body"] == "BTC ETF record inflows"
        with m.get_db() as db:
            raw = db.execute("SELECT body FROM user_ingest_notes WHERE id=?", (nid,)).fetchone()["body"]
        assert "BTC ETF" not in raw          # ciphertext at rest

    def test_empty_body_is_rejected(self, isolated_db):
        uid = isolated_db.create_user("b@x.io", "password12")
        assert isolated_db.add_user_ingest_note(uid, "t", "   ") is None

    def test_per_user_isolation(self, isolated_db):
        m = isolated_db
        a = m.create_user("c@x.io", "password12")
        b = m.create_user("d@x.io", "password12")
        nid = m.add_user_ingest_note(a, "secret", "A's private note", "")
        assert m.list_user_ingest_notes(b) == []          # B sees nothing of A's
        assert m.delete_user_ingest_note(b, nid) is False  # B cannot delete A's
        assert len(m.list_user_ingest_notes(a)) == 1       # A's note untouched

    def test_cap_prunes_oldest(self, isolated_db):
        m = isolated_db
        uid = m.create_user("e@x.io", "password12")
        for i in range(m.INGEST_MAX_NOTES + 5):
            m.add_user_ingest_note(uid, f"n{i}", f"body {i}", "")
        assert len(m.list_user_ingest_notes(uid, limit=m.INGEST_MAX_NOTES)) == m.INGEST_MAX_NOTES


# ── gateway ──────────────────────────────────────────────────────────────────

class TestGateway:
    @pytest.mark.asyncio
    async def test_save_list_delete_roundtrip_private(self, isolated_db):
        async with client_for(FakeHandler(AUTHED)) as c:
            r = await c.post("/ingest", json={"telegram_id": "7", "title": "N",
                                              "body": "shared text body", "source": "X"}, headers=HDRS)
            assert r.status == 200 and (await r.json())["saved"] is True
            r = await c.get("/ingest?telegram_id=7", headers=HDRS)
            d = await r.json()
            assert d["private"] is True and len(d["notes"]) == 1
            note = d["notes"][0]
            assert note["title"] == "N" and "shared text" in note["preview"]
            # a DIFFERENT user sees none of it
            r2 = await c.get("/ingest?telegram_id=8", headers=HDRS)
            assert (await r2.json())["notes"] == []
            # delete it (scoped to the owner)
            r = await c.post("/ingest/delete", json={"telegram_id": "7", "id": note["id"]}, headers=HDRS)
            assert (await r.json())["deleted"] is True
            r = await c.get("/ingest?telegram_id=7", headers=HDRS)
            assert (await r.json())["notes"] == []

    @pytest.mark.asyncio
    async def test_empty_body_is_400(self, isolated_db):
        async with client_for(FakeHandler(AUTHED)) as c:
            r = await c.post("/ingest", json={"telegram_id": "7", "body": "  "}, headers=HDRS)
            assert r.status == 400

    @pytest.mark.asyncio
    async def test_unauthorized_id_guarded(self, isolated_db):
        async with client_for(FakeHandler(AUTHED)) as c:
            r = await c.post("/ingest", json={"telegram_id": "404", "body": "x"}, headers=HDRS)
            assert r.status in (401, 403)
