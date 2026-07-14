"""
Web user gateway (bot/web/user_gateway.py) — the website's chat + manual-trade
surface inside the bot process.

Pins the trust model:
  * fail-closed service auth (no/short/wrong WEB_GATEWAY_SECRET -> 403)
  * web chat is admin/operator-only (v1 product decision)
  * user re-authorization mirrors TelegramHandler._guard (registered +
    authorized + role permission + allowlist)
  * trades ride the exact Telegram path: shared parser -> pending idea ->
    engine.confirm_trade; the gateway adds proposer isolation so a web user
    can only confirm/cancel their OWN manual proposals
  * live mode requires _can_trade_live (web can never bypass the operator
    allowlist)
"""

import contextlib
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeUsers:
    def __init__(self, users=None, perms=True, live=False):
        self._users = dict(users or {})
        self._perms = perms
        self._live = live

    def get(self, tg):
        return self._users.get(str(tg))

    def has_permission(self, tg, cmd):
        return self._perms

    def can_trade_live(self, tg):
        return self._live


class FakeIntent:
    def __init__(self, skill="", confidence=0.0):
        self.skill = skill
        self.confidence = confidence
        self.kwargs = {}
        self.source = "rules"
        self.is_social = False

    @property
    def matched(self):
        return bool(self.skill)


class FakeSkill:
    async def execute(self, engine, **kw):
        return "<b>portfolio reply</b>"


class FakeConvos:
    def __init__(self):
        self.appended = []

    def append(self, uid, role, content, metadata=None):
        self.appended.append((str(uid), role, content))

    def get_recent(self, uid, limit=None):
        return []


class FakeHandler:
    def __init__(self, *, users=None, perms=True, live=False,
                 intent=None, skills=None, allowlist=()):
        self.users = FakeUsers(users, perms=perms, live=live)
        self._limiter = SimpleNamespace(allow=lambda uid: True)
        self.intent_router = SimpleNamespace(
            classify_rules=lambda text: intent or FakeIntent())
        self.registry = SimpleNamespace(get=lambda name: (skills or {}).get(name))
        self.conversations = FakeConvos()
        self._allowlist = set(map(str, allowlist))

    def _allowlist_ids(self):
        return self._allowlist

    def _can_trade_live(self, tg):
        return self.users.can_trade_live(tg)

    async def _llm_chat(self, q, user_id="", user_name="", is_admin=False):
        return "llm answer"


class FakeEngine:
    def __init__(self, operator_ids=()):
        self._pending_ideas = {}
        self._manual_margin_override = {}
        self._ops = set(map(str, operator_ids))
        self.confirm_calls = []

    def _is_operator_user(self, uid):
        return str(uid) in self._ops

    async def confirm_trade(self, trade_id, user_id=""):
        self.confirm_calls.append((trade_id, user_id))
        self._pending_ideas.pop(trade_id, None)
        return "✅ executed"


AUTHED = {"7": {"authorized": True, "role": "trader"}}


@contextlib.asynccontextmanager
async def gateway_client(engine, handler):
    app = ug.build_gateway(engine, handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


# ── Service auth: fail-closed secret ────────────────────────────────────────

async def test_no_secret_configured_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", "")
    async with gateway_client(FakeEngine(), FakeHandler()) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "gateway_disabled"


async def test_short_secret_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", "short")
    async with gateway_client(FakeEngine(), FakeHandler()) as c:
        r = await c.post("/chat", json={}, headers={"X-Gateway-Secret": "short"})
        assert r.status == 403


async def test_wrong_secret_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with gateway_client(FakeEngine(), FakeHandler()) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers={"X-Gateway-Secret": "x" * 32})
        assert r.status == 403
        r2 = await c.post("/chat", json={"telegram_id": "7", "text": "hi"})
        assert r2.status == 403


# ── Chat: admin-only + user auth + routing ──────────────────────────────────

async def test_chat_non_admin_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=())  # "7" is NOT an operator
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "chat_admin_only"


async def test_chat_unregistered_operator_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=["7"])
    async with gateway_client(engine, FakeHandler(users={})) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "not_authorized"


async def test_chat_intent_dispatches_to_skill(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=["7"])
    handler = FakeHandler(users=AUTHED,
                          intent=FakeIntent("get_portfolio", 1.0),
                          skills={"get_portfolio": FakeSkill()})
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "my portfolio"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["intent"] == "get_portfolio"
        assert data["reply_html"] == "<b>portfolio reply</b>"
        roles = [(role) for _, role, _ in handler.conversations.appended]
        assert roles == ["user", "assistant"]


async def test_chat_falls_back_to_llm(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=["7"])
    handler = FakeHandler(users=AUTHED)  # no intent match
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "how are you"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data == {"reply_html": "llm answer", "intent": "chat"}
        assert len(handler.conversations.appended) == 2


async def test_chat_manual_trade_text_returns_pending_trade(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=["7"])
    handler = FakeHandler(users=AUTHED)
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat",
                         json={"telegram_id": "7",
                               "text": "buy SOL 71 sl 70 tp 76"},
                         headers=HDRS)
        assert r.status == 200
        pt = (await r.json())["pending_trade"]
        assert pt["symbol"] == "SOL"
        assert pt["direction"] == "LONG"
        assert pt["mode"] == "PAPER"
        assert pt["trade_id"] in engine._pending_ideas


async def test_chat_allowlist_blocks_stranger(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=["7"])
    handler = FakeHandler(users=AUTHED, allowlist=["1", "2"])  # "7" not listed
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "not_allowlisted"


# ── Trade: propose / confirm / cancel ───────────────────────────────────────

async def test_propose_structured_registers_pending_idea(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        r = await c.post("/trade/propose",
                         json={"telegram_id": "7", "direction": "LONG",
                               "symbol": "SOL", "entry": 71.0, "sl": 70.0,
                               "tp": 76.0, "margin": 250},
                         headers=HDRS)
        assert r.status == 200
        pt = (await r.json())["pending_trade"]
        assert pt["mode"] == "PAPER" and pt["live_allowed"] is False
        assert pt["rr"] == 5.0
        idea = engine._pending_ideas[pt["trade_id"]]
        assert idea.source == "manual" and idea.order_type == "limit"
        assert engine._manual_margin_override[pt["trade_id"]] == 250


async def test_propose_rejects_wrong_side_sl(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        r = await c.post("/trade/propose",
                         json={"telegram_id": "7", "direction": "LONG",
                               "symbol": "SOL", "entry": 71.0, "sl": 72.0,
                               "tp": 76.0},
                         headers=HDRS)
        assert r.status == 400
        assert (await r.json())["error"] == "invalid_trade"
        assert not engine._pending_ideas


async def test_propose_requires_trade_permission(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=AUTHED, perms=False)) as c:
        r = await c.post("/trade/propose",
                         json={"telegram_id": "7", "direction": "LONG",
                               "symbol": "SOL", "entry": 71.0, "sl": 70.0,
                               "tp": 76.0},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "no_permission"


async def _propose(c, tg="7"):
    r = await c.post("/trade/propose",
                     json={"telegram_id": tg, "direction": "LONG",
                           "symbol": "SOL", "entry": 71.0, "sl": 70.0,
                           "tp": 76.0},
                     headers=HDRS)
    assert r.status == 200
    return (await r.json())["pending_trade"]["trade_id"]


async def test_confirm_executes_via_engine_confirm_trade(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 200
        assert (await r.json())["result_html"] == "✅ executed"
        assert engine.confirm_calls == [(tid, "7")]


async def test_confirm_by_non_proposer_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    users = {"7": {"authorized": True, "role": "trader"},
             "8": {"authorized": True, "role": "trader"}}
    async with gateway_client(engine, FakeHandler(users=users)) as c:
        tid = await _propose(c, tg="7")
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "8", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "not_proposer"
        assert engine.confirm_calls == []


async def test_confirm_cannot_touch_engine_auto_ideas(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    engine._pending_ideas["TI-auto1234"] = SimpleNamespace(source="scan")
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "7", "trade_id": "TI-auto1234"},
                         headers=HDRS)
        assert r.status == 403
        assert engine.confirm_calls == []


async def test_confirm_live_mode_requires_live_gate(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    handler = FakeHandler(users=AUTHED, live=False)  # not live-enabled
    async with gateway_client(engine, handler) as c:
        tid = await _propose(c)
        monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
            telegram=SimpleNamespace(admin_ids=""),
            is_live=lambda: True))
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "live_not_enabled"
        assert engine.confirm_calls == []


async def test_cancel_removes_own_manual_idea(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/cancel",
                         json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 200
        assert (await r.json())["cancelled"] is True
        assert tid not in engine._pending_ideas


async def test_history_is_admin_only(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=())
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
        r = await c.get("/chat/history?telegram_id=7", headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "chat_admin_only"


async def test_history_returns_messages_for_admin(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=["7"])
    handler = FakeHandler(users=AUTHED)
    handler.conversations.get_recent = lambda uid, limit=None: [
        SimpleNamespace(role="user", content="hi", timestamp=1.0)]
    async with gateway_client(engine, handler) as c:
        r = await c.get("/chat/history?telegram_id=7", headers=HDRS)
        assert r.status == 200
        msgs = (await r.json())["messages"]
        assert msgs == [{"role": "user", "content": "hi", "timestamp": 1.0}]
