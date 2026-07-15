"""
Web user gateway (bot/web/user_gateway.py) — the website's chat + manual-trade
+ portfolio surface inside the bot process.

Pins the trust model:
  * fail-closed service auth (no/short/wrong WEB_GATEWAY_SECRET -> 403)
  * chat is open to ALL authorized users; the LLM `is_admin` flag reflects the
    caller's REAL role (non-admins must never inherit admin LLM routing)
  * web-only identities ("web:<id>"): auto-provisioned as paper-only traders,
    bypass the Telegram allowlist (they are JWT-authenticated upstream), and
    are STRUCTURALLY locked out of live trading — even a tampered users.json
    entry can't open a live path
  * Telegram-shaped ids keep the exact prior semantics (allowlist, registered
    + authorized, role permission)
  * trades ride the exact Telegram path: shared parser -> pending idea ->
    engine.confirm_trade; proposer isolation
  * /portfolio returns the caller's own paper snapshot
"""

import contextlib
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeUsers:
    """Mirrors UserStore semantics used by the gateway: get/register/
    has_permission/can_trade_live. register() auto-approves as trader
    (paper-only), never overwrites existing records."""

    def __init__(self, users=None, perms=True, live=False):
        self._users = dict(users or {})
        self._perms = perms
        self._live = live
        self.register_calls = []

    def get(self, tg):
        return self._users.get(str(tg))

    def register(self, tg, name="", auto_role=""):
        key = str(tg)
        self.register_calls.append((key, name))
        if key not in self._users:
            self._users[key] = {"authorized": True, "role": "trader",
                                "can_trade_live": False, "name": name}
        return self._users[key]

    def has_permission(self, tg, cmd):
        return self._perms

    def can_trade_live(self, tg):
        if str(tg).startswith("web:"):
            return False
        u = self._users.get(str(tg))
        if u and "can_trade_live" in u:
            return bool(u["can_trade_live"])
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
        self.llm_calls = []  # (question, user_id, is_admin, public)

    def _allowlist_ids(self):
        return self._allowlist

    def _can_trade_live(self, tg):
        return self.users.can_trade_live(tg)

    async def _llm_chat(self, q, user_id="", user_name="", is_admin=False,
                        public=False):
        self.llm_calls.append((q, user_id, is_admin, public))
        return "llm answer"


class FakePortfolio:
    def snapshot(self):
        return SimpleNamespace(equity_usd=10_000.0, balance_usd=9_500.0,
                               total_pnl=120.5, daily_pnl=10.0,
                               win_rate=55.0, total_trades=4)

    @property
    def open_positions(self):
        return []

    @property
    def trade_history(self):
        return []


class FakeEngine:
    def __init__(self, operator_ids=()):
        self._pending_ideas = {}
        self._manual_margin_override = {}
        self._ops = set(map(str, operator_ids))
        self.confirm_calls = []
        self.user_portfolios = SimpleNamespace(get=lambda uid: FakePortfolio())

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


# ── Chat: open to all authorized users ──────────────────────────────────────

async def test_chat_open_to_regular_authorized_user(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine(operator_ids=())  # "7" is NOT an operator/admin
    handler = FakeHandler(users=AUTHED)
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "how are you"},
                         headers=HDRS)
        assert r.status == 200
        assert (await r.json())["reply_html"] == "llm answer"


async def test_chat_unregistered_telegram_id_is_403(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with gateway_client(FakeEngine(), FakeHandler(users={})) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "not_authorized"


async def test_chat_passes_real_admin_flag_to_llm(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
        telegram=SimpleNamespace(admin_ids="99"),
        is_live=lambda: False))
    users = {"7": {"authorized": True, "role": "trader"},
             "99": {"authorized": True, "role": "admin"}}
    handler = FakeHandler(users=users)
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hello there"},
                         headers=HDRS)
        assert r.status == 200
        r = await c.post("/chat", json={"telegram_id": "99", "text": "hello there"},
                         headers=HDRS)
        assert r.status == 200
    admin_flags = {uid: is_admin for _, uid, is_admin, _pub in handler.llm_calls}
    assert admin_flags == {"7": False, "99": True}


async def test_chat_intent_dispatches_to_skill(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users=AUTHED,
                          intent=FakeIntent("get_portfolio", 1.0),
                          skills={"get_portfolio": FakeSkill()})
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "my portfolio"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["intent"] == "get_portfolio"
        assert data["reply_html"] == "<b>portfolio reply</b>"
        roles = [role for _, role, _ in handler.conversations.appended]
        assert roles == ["user", "assistant"]


async def test_chat_manual_trade_text_returns_pending_trade(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=AUTHED)) as c:
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


async def test_chat_allowlist_still_blocks_telegram_stranger(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users=AUTHED, allowlist=["1", "2"])  # "7" not listed
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "not_allowlisted"


async def test_history_open_to_regular_user(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users=AUTHED)
    handler.conversations.get_recent = lambda uid, limit=None: [
        SimpleNamespace(role="user", content="hi", timestamp=1.0)]
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.get("/chat/history?telegram_id=7", headers=HDRS)
        assert r.status == 200
        msgs = (await r.json())["messages"]
        assert msgs == [{"role": "user", "content": "hi", "timestamp": 1.0}]


# ── Web-only identities ("web:<id>") ────────────────────────────────────────

async def test_chat_web_id_auto_provisions(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users={})  # unknown user
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={"telegram_id": "web:42", "text": "hi",
                                        "name": "alice"},
                         headers=HDRS)
        assert r.status == 200
        assert ("web:42", "alice") in handler.users.register_calls
        assert handler.users.get("web:42")["role"] == "trader"


async def test_web_id_bypasses_allowlist_but_stranger_does_not(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users=AUTHED, allowlist=["1"])
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={"telegram_id": "web:42", "text": "hi"},
                         headers=HDRS)
        assert r.status == 200  # web id: JWT-authenticated upstream
        r2 = await c.post("/chat", json={"telegram_id": "7", "text": "hi"},
                          headers=HDRS)
        assert r2.status == 403  # telegram-shaped stranger still allowlisted out
        assert (await r2.json())["error"] == "not_allowlisted"


async def test_malformed_web_id_rejected(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with gateway_client(FakeEngine(), FakeHandler(users={})) as c:
        for bad in ("web:abc", "web:", "web:1x", "web:-3"):
            r = await c.post("/chat", json={"telegram_id": bad, "text": "hi"},
                             headers=HDRS)
            assert r.status == 400, bad
            assert (await r.json())["error"] == "invalid_web_id"


async def test_web_id_propose_is_always_paper(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
        telegram=SimpleNamespace(admin_ids=""),
        is_live=lambda: True))
    # Even with a (bogus) live-enabled store entry, web ids stay paper.
    users = {"web:9": {"authorized": True, "role": "trader", "can_trade_live": True}}
    handler = FakeHandler(users=users, live=True)
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/trade/propose",
                         json={"telegram_id": "web:9", "direction": "LONG",
                               "symbol": "SOL", "entry": 71.0, "sl": 70.0,
                               "tp": 76.0},
                         headers=HDRS)
        assert r.status == 200
        pt = (await r.json())["pending_trade"]
        assert pt["mode"] == "PAPER"
        assert pt["live_allowed"] is False


async def test_web_id_confirm_live_blocked_even_with_stale_admin_flag(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    # Tampered store: web id claims admin + live. Must still be blocked.
    users = {"web:9": {"authorized": True, "role": "admin", "can_trade_live": True}}
    handler = FakeHandler(users=users, live=True)
    async with gateway_client(engine, handler) as c:
        tid = await _propose(c, tg="web:9")
        monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
            telegram=SimpleNamespace(admin_ids=""),
            is_live=lambda: True))
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "web:9", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "live_not_enabled"
        assert engine.confirm_calls == []


async def test_web_id_confirm_executes_paper_when_not_live(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users={})) as c:
        tid = await _propose(c, tg="web:42")
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "web:42", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 200
        assert engine.confirm_calls == [(tid, "web:42")]


def test_real_can_trade_live_blocks_web_ids():
    """The real single authority (TelegramHandler._can_trade_live and
    UserStore.can_trade_live) must refuse web ids even with a live flag set
    and no allowlist configured."""
    from bot.utils.user_store import UserStore
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        store = UserStore(path=os.path.join(d, "users.json"))
        store.register("web:1")
        store._users["web:1"]["can_trade_live"] = True  # tampered flag
        assert store.can_trade_live("web:1") is False
    # TelegramHandler._can_trade_live is exercised unbound with a stub self.
    from bot.skills.telegram_handler import TelegramHandler
    stub = SimpleNamespace(
        _allowlist_ids=lambda: set(),
        users=SimpleNamespace(can_trade_live=lambda tg: True))
    assert TelegramHandler._can_trade_live(stub, "web:1") is False


# ── Trade: propose / confirm / cancel (telegram ids unchanged) ──────────────

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


# ── Portfolio snapshot ───────────────────────────────────────────────────────

async def test_portfolio_returns_user_snapshot(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with gateway_client(FakeEngine(), FakeHandler(users={})) as c:
        r = await c.get("/portfolio?telegram_id=web:42", headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["equity"] == 10_000.0
        assert data["mode"] == "PAPER"
        assert data["open_positions"] == []
        assert data["closed_trades"] == []


async def test_portfolio_requires_identity(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with gateway_client(FakeEngine(), FakeHandler(users={})) as c:
        r = await c.get("/portfolio", headers=HDRS)
        assert r.status == 400


# ── Public (anonymous) chat: account-free, LLM-only ─────────────────────────
#
# The security invariant: /chat/public accepts ONLY {text}, never provisions a
# user, never dispatches a skill, never creates a pending trade, and always
# calls _llm_chat with public=True, user_id="", is_admin=False — so no account
# data or trade action is reachable no matter what the anonymous client sends.

async def test_public_chat_llm_only_no_account_no_registration(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    # A skill + a matching intent are wired: the public path must IGNORE both.
    handler = FakeHandler(users={},
                          intent=FakeIntent("get_portfolio", 1.0),
                          skills={"get_portfolio": FakeSkill()},
                          allowlist=["1"])  # allowlist must be irrelevant here
    engine = FakeEngine()
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat/public", json={"text": "what is runeclaw?"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data == {"reply_html": "llm answer", "intent": "chat"}
    # LLM was called in public mode, anonymously, non-admin.
    assert handler.llm_calls == [("what is runeclaw?", "", False, True)]
    # No user was ever provisioned, and nothing was dispatched/stored.
    assert handler.users.register_calls == []
    assert handler.conversations.appended == []
    assert engine.confirm_calls == []


async def test_public_chat_ignores_trade_text(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users={})
    engine = FakeEngine()
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat/public",
                         json={"text": "buy SOL 71 sl 70 tp 76"}, headers=HDRS)
        assert r.status == 200
        data = await r.json()
        # Trade-shaped text is treated as an ordinary question — NO pending trade.
        assert "pending_trade" not in data
        assert data["intent"] == "chat"
    assert engine._pending_ideas == {}
    assert handler.llm_calls[0][3] is True  # public=True


async def test_public_chat_validates_text(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users={})
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat/public", json={}, headers=HDRS)
        assert r.status == 400
        assert (await r.json())["error"] == "text required"
        r = await c.post("/chat/public", json={"text": "x" * 2001}, headers=HDRS)
        assert r.status == 400
        assert (await r.json())["error"] == "message too long"
    assert handler.llm_calls == []


async def test_public_chat_still_requires_service_secret(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with gateway_client(FakeEngine(), FakeHandler(users={})) as c:
        r = await c.post("/chat/public", json={"text": "hi"},
                         headers={"X-Gateway-Secret": "x" * 32})
        assert r.status == 403
        r2 = await c.post("/chat/public", json={"text": "hi"})
        assert r2.status == 403
